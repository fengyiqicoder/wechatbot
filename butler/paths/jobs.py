"""任务管理：一条路径一个线程，状态与条目落盘，可查、可取消、可续跑。同一时刻只跑一个任务。

失败自动重试：一次路径执行的单次成功率哪怕只有 90%，复位后独立重跑 3 次也能到 99.9%。
所以任务级默认 3 次尝试；两次之间先调 App 模块的 reset 回一级页，第二次起改为杀掉重开 App（最干净的复位）。
不重试的情形：已做过带 confirm 的写动作（避免重复发送）、取消、以及重试也没用的原因（无设备 / 锁屏 / 静默窗 / 限额 / 台账拦截）。
"""
from __future__ import annotations
import json
import threading
import time
import traceback
import uuid
from pathlib import Path

from butler import paths as P
from butler.paths.ctx import Cancelled, Ctx, DraftMade, PathBlocked

DEFAULT_ATTEMPTS = 3
NO_RETRY = {"no_device", "locked", "quiet_hours", "rate_limit", "blocklisted", "already_sent", "needs_confirm", "bad_request",
            "text_only_unavailable", "photo_permission", "not_enough_photos", "wechat_crash_guard"}


class JobManager:
    def __init__(self, actions, catalog, root: Path, ledger, drafts, settings_loader, hold=None, release=None):
        self.actions, self.catalog = actions, catalog
        self.root = Path(root) / "paths" / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger, self.drafts, self.settings_loader = ledger, drafts, settings_loader
        self.hold, self.release = hold or (lambda *_: None), release or (lambda *_: None)
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._current: str | None = None

    # ---- 持久化 ----
    def public(self, job: dict) -> dict:
        return {k: v for k, v in job.items() if not k.startswith("_") and k not in ("seen", "cancel")}

    def _persist(self, job: dict) -> None:
        (self.root / f"{job['id']}.json").write_text(json.dumps(self.public(job), ensure_ascii=False, indent=1))

    def _sink_for(self, job: dict):
        f = (self.root / f"{job['id']}.jsonl").open("a")

        def sink(item):
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            f.flush()
        job["_file"] = f
        return sink

    # ---- 生命周期 ----
    def current(self) -> str | None:
        return self._current

    def running(self) -> bool:
        return self._current is not None and self.jobs[self._current]["status"] in ("queued", "running")

    def start(self, app: str, path: str, params: dict | None = None, seen: set | None = None,
              resume_of: str | None = None, attempts: int | None = None) -> dict:
        spec = P.get(app, path)
        if spec is None:
            raise KeyError(f"没有这条路径：{app}/{path}")
        if attempts is None:
            attempts = int((self.settings_loader() or {}).get("job_attempts", DEFAULT_ATTEMPTS))
        with self._lock:
            if self.running():
                raise RuntimeError("busy")
            job = {"id": uuid.uuid4().hex[:10], "app": app, "path": path, "params": dict(params or {}),
                   "kind": spec.kind, "writes": spec.writes, "status": "queued", "created": int(time.time()),
                   "count": 0, "notes": [], "seen": set(seen or ()), "cancel": False, "resume_of": resume_of,
                   "attempts": max(1, int(attempts)), "attempt": 0, "attempt_errors": []}
            self.jobs[job["id"]] = job
            self._current = job["id"]
        self._persist(job)
        t = threading.Thread(target=self._run, args=(spec, job), daemon=True, name=f"job-{job['id']}")
        job["_thread"] = t
        t.start()
        return self.public(job)

    @staticmethod
    def _retryable(final: dict, ctx: Ctx) -> bool:
        if final.get("status") not in ("blocked", "failed"):
            return False
        if ctx.wrote:
            return False
        reason = (final.get("error") or {}).get("reason")
        return reason not in NO_RETRY

    @staticmethod
    def _module_reset(spec):
        import sys
        return getattr(sys.modules.get(spec.fn.__module__), "reset", None)

    def _recover(self, spec, ctx: Ctx, attempt: int) -> None:
        """两次尝试之间的复位：第一次失败先用模块 reset 回一级页；再失败（或 reset 自己失败）就杀掉重开 App。"""
        # App 模块可声明 RESTART_OK = False：杀进程会触发它自己的异常保护（微信「连续异常」修复页），只用 reset 复位
        restart_ok = getattr(__import__("sys").modules.get(spec.fn.__module__), "RESTART_OK", True)
        if attempt >= 2 and restart_ok:
            try:
                ctx.restart_app()
            except Exception as e:  # noqa: BLE001
                ctx.note(f"重开 App 失败：{type(e).__name__}: {e}"[:200])
        reset = self._module_reset(spec)
        if reset is None:
            return
        try:
            reset(ctx)
        except Exception as e:  # noqa: BLE001
            ctx.note(f"reset 失败：{type(e).__name__}: {e}"[:200])
            if attempt < 2 and restart_ok:
                try:
                    ctx.restart_app()
                except Exception as e2:  # noqa: BLE001
                    ctx.note(f"重开 App 失败：{type(e2).__name__}: {e2}"[:200])

    def _attempt(self, spec, ctx: Ctx, job: dict) -> dict:
        try:
            if not ctx.ensure_app():
                raise PathBlocked("app_gone", {"bundle": spec.bundle, "hint": "拉不起目标 App"})
            out = spec.fn(ctx, **job["params"])
            return {"status": "done", "result": out if spec.kind == "action" else {"count": len(ctx.items)}}
        except DraftMade as d:
            return {"status": "done", "result": {"draft": d.draft}}
        except Cancelled:
            return {"status": "cancelled"}
        except PathBlocked as b:
            return {"status": "blocked", "error": {"reason": b.reason, "detail": b.detail, "req": b.req}}
        except Exception as e:  # noqa: BLE001
            return {"status": "failed", "error": {"error": f"{type(e).__name__}: {e}"[:500],
                                                  "trace": traceback.format_exc()[-1500:]}}

    def _run(self, spec, job: dict) -> None:
        job["status"], job["started"] = "running", int(time.time())
        job["_sink"] = self._sink_for(job)
        job["_on_update"] = lambda: self._persist(job)
        self.hold("job")
        ctx = Ctx(self.actions, self.catalog, spec, job, self.ledger, self.drafts, self.settings_loader)
        final: dict = {}
        try:
            attempts = int(job.get("attempts") or 1)
            for attempt in range(1, attempts + 1):
                job["attempt"] = attempt
                final = self._attempt(spec, ctx, job)
                if final.get("status") == "done" or attempt >= attempts or not self._retryable(final, ctx):
                    break
                err = final.get("error") or {}
                job["attempt_errors"].append({"attempt": attempt, "status": final["status"],
                                              "reason": err.get("reason") or (err.get("error") or "")[:120]})
                ctx.note(f"第 {attempt} 次失败（{err.get('reason') or (err.get('error') or '')[:60]}），复位后重试")
                self._persist(job)
                self._recover(spec, ctx, attempt)
                ctx.check_cancel_soft()
        finally:
            # 失败别把 App 留在半路：调模块的 reset 复位。必须在改终态之前做——
            # 状态一旦变成 blocked，监听/跑分器就会起下一个任务，复位动作会和它撞车（实证：两任务互报 busy）。
            if final.get("status") in ("blocked", "failed", "cancelled"):
                try:
                    reset = self._module_reset(spec)
                    if reset is not None:
                        reset(ctx)
                except Exception as e:  # noqa: BLE001
                    job.setdefault("notes", []).append({"t": time.time(), "msg": f"reset 失败：{type(e).__name__}: {e}"[:200]})
            job["ended"] = int(time.time())
            job["last_screen"] = ctx.last.get("screenshot")
            job["count"] = len(ctx.items)
            try:
                job["_file"].close()
            except Exception:  # noqa: BLE001
                pass
            job.update(final)
            self.release("job")
            self._persist(job)

    def get(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        if j is not None:
            return self.public(j)
        p = self.root / f"{jid}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def items(self, jid: str, limit: int = 1000) -> list[dict]:
        p = self.root / f"{jid}.jsonl"
        if not p.exists():
            return []
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return rows[:limit]

    def list(self, limit: int = 50) -> list[dict]:
        files = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        out = []
        for f in files:
            jid = f.stem
            out.append(self.public(self.jobs[jid]) if jid in self.jobs else json.loads(f.read_text()))
        return out

    def cancel(self, jid: str) -> bool:
        j = self.jobs.get(jid)
        if j is None or j["status"] not in ("queued", "running"):
            return False
        j["cancel"] = True
        return True

    def resume(self, jid: str) -> dict:
        old = self.get(jid)
        if old is None:
            raise KeyError(jid)
        seen = {it.get("id") for it in self.items(jid)}
        seen.discard(None)
        return self.start(old["app"], old["path"], old.get("params"), seen=seen, resume_of=jid)

    def wait(self, jid: str, timeout: float = 3600, poll: float = 0.5) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.jobs.get(jid)
            if j is None or j["status"] not in ("queued", "running"):
                return self.get(jid)
            time.sleep(poll)
        return self.get(jid)
