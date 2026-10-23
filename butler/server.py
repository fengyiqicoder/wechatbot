"""微信管家引擎：Agent 用的本地 HTTP API（仅 127.0.0.1，默认端口 8790）。"""
import os
import json
import queue
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from engine.runner import RunQueue
from engine.store import RunStore, WorkflowStore, load_settings, save_settings
from butler.actions import ActionService
from butler.apps import AppCatalog, device_info
from butler import paths as P
from butler.memory import MemoryStore
from butler.paths.jobs import JobManager
from butler.paths.sequence import SequenceStore
from butler.paths.store import DraftStore, Ledger
from butler.paths.watch import Watcher
from butler.scripts import ScriptService
from butler.verify import wait_stable

DEFAULT_ROOT = Path(os.environ.get("WECHATBOT_HOME") or Path.home() / "Library" / "Application Support" / "WeChatBot")
PORT = int(os.environ.get("WECHATBOT_PORT", "8790"))
API_URL = f"http://127.0.0.1:{PORT}"
SKILL_PATH = Path(__file__).parent / "SKILL.md"


class OpenBody(BaseModel):
    app: str | None = None
    url: str | None = None


class ScriptBody(BaseModel):
    name: str
    last_n: int = 20


class RunBody(BaseModel):
    params: dict = {}


def _strip(rec: dict) -> dict:
    out = {k: v for k, v in rec.items() if k not in ("landmarks", "elements")}
    out["landmarks"] = {m: [{k: v for k, v in lm.items() if k != "png"} for lm in ls]
                        for m, ls in rec.get("landmarks", {}).items()}
    out["elements"] = {k: {kk: vv for kk, vv in e.items() if kk != "anchor"} for k, e in rec.get("elements", {}).items()}
    return out


def create_app(driver_provider=None, root: Path | None = None, start_queue: bool = False, lister=None,
               device_info_fn=None, invalidate=None) -> FastAPI:
    app = FastAPI(title="wechatbot")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    provider = driver_provider or (lambda: None)
    root = Path(root or DEFAULT_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    memory = MemoryStore(root)
    actions = ActionService(provider, memory, root, lambda: load_settings(root), invalidate=invalidate)
    catalog = AppCatalog(memory, lister)
    workflows, runs = WorkflowStore(root), RunStore(root)
    rq = RunQueue(provider, workflows, runs, root, invalidate=invalidate)
    if start_queue:
        rq.start()
    scripts = ScriptService(workflows, runs, rq, actions)
    dev_info = device_info_fn or device_info
    cache: dict = {"device": None}
    # 路径层：采集/写操作任务、草稿、台账、监听
    P.load_all()
    ledger, drafts = Ledger(root), DraftStore(root)
    jobs = JobManager(actions, catalog, root, ledger, drafts, lambda: load_settings(root),
                      hold=rq.hold, release=rq.release)
    watcher = Watcher(jobs, drafts, root)
    sequences = SequenceStore(jobs, root)
    watcher.sequences = sequences
    if start_queue:
        watcher.start()
    app.state.actions, app.state.memory, app.state.queue = actions, memory, rq
    app.state.jobs, app.state.drafts, app.state.ledger, app.state.watcher = jobs, drafts, ledger, watcher

    def require_driver():
        d = provider()
        if d is None:
            raise HTTPException(status_code=503, detail="device not connected")
        return d

    @app.get("/status")
    def status():
        d = provider()
        fg, locked = None, None
        if d is not None:
            if cache["device"] is None:
                cache["device"] = dev_info()
            try:
                locked = bool(d.locked())
            except Exception:  # noqa: BLE001
                locked = None
            try:
                o = actions.observe(deep=False)
                fg = {"bundle": o.get("bundle"), "screen_id": o.get("screen_id"), "screen_label": o.get("screen_label")}
            except Exception:  # noqa: BLE001
                fg = None
        return {"engine": "ok", "wda_connected": d is not None, "locked": locked,
                "device": cache["device"] if d is not None else None, "foreground": fg,
                "busy": rq.current() is not None or actions._lock.locked(), "api": API_URL,
                "memory": memory.summary(), "skill_path": str(SKILL_PATH),
                "queue": {"pending": rq.pending(), "current": (rq.current() or {}).get("id")}}

    @app.get("/mirror")
    def mirror():
        """窗口用：最近一帧的元数据 + 最近落点 + 引擎/设备状态 + 当前任务。轻量，不抓 AX。"""
        d = provider()
        info = actions.mirror_info()
        info["wda_connected"] = d is not None
        if d is not None and cache["device"] is None:
            cache["device"] = dev_info()
        info["device"] = cache["device"] if d is not None else None
        try:
            info["size_pt"] = list(d.window_size()) if d is not None else None    # 驱动里有缓存，不碰 AX
        except Exception:  # noqa: BLE001
            info["size_pt"] = None
        info["engine_busy"] = rq.current() is not None or jobs.running() or actions.busy()
        job = None
        cur = jobs.current()
        if cur:
            j = jobs.get(cur) or {}
            job = {k: j.get(k) for k in ("id", "app", "path", "status", "count", "attempt", "attempts", "params")}
            job["note"] = (j.get("notes") or [{}])[-1].get("msg") if j.get("notes") else None
        info["job"] = job
        info["app_name"] = catalog.name_of(info.get("foreground")) if info.get("foreground") else None
        return info

    @app.post("/reconnect")
    def reconnect():
        """窗口的「重新连接」：作废当前 WDA 会话，重新探测并建会话。WDA 进程本身不在这里拉（那是「启动 WDA」）。"""
        actions.reset_session()
        cache["device"] = None
        d = provider()
        if d is not None and cache["device"] is None:
            cache["device"] = dev_info()
        return {"ok": d is not None, "wda_connected": d is not None, "device": cache["device"] if d is not None else None,
                "hint": None if d is not None else "WDA 没有响应：手机是否连着、解锁了？必要时点「启动 WDA」"}

    @app.get("/mirror.png")
    def mirror_png():
        f = actions.mirror_frame()
        if not f["png"]:
            raise HTTPException(status_code=404, detail="还没有画面")
        from fastapi.responses import Response
        return Response(content=f["png"], media_type="image/png",
                        headers={"Cache-Control": "no-store", "X-Frame-Ts": str(f["ts"]), "X-Frame-Source": f["source"]})

    @app.get("/screen")
    def screen(fast: int = 0):
        require_driver()
        return actions.observe(deep=not fast)

    @app.post("/act")
    def act(body: dict):
        if rq.current() is not None:
            return actions._blocked(body, "busy", {"hint": "脚本回放中"}, None, 0, 0)
        if jobs.running():
            return actions._blocked(body, "busy", {"hint": f"任务 {jobs.current()} 在跑，先取消它"}, None, 0, 0)
        rq.hold("act")
        try:
            return actions.act(body)
        finally:
            rq.release("act")

    @app.post("/open")
    def open_(body: OpenBody):
        d = require_driver()
        try:
            return _do_open(d, body)
        except Exception as e:  # noqa: BLE001 — session 失效不会自愈，作废后用新 session 再来一次
            if not actions.is_stale(e):
                return {"ok": False, "blocked": {"reason": "device_error",
                                                 "detail": {"error": f"{type(e).__name__}: {e}"[:300]}}}
            actions.reset_session()
            return _do_open(require_driver(), body)

    def _do_open(d, body: OpenBody):
        if body.url:
            d.open_url(body.url)
            kind, value = "url", body.url
        elif body.app:
            bundle, cands = catalog.resolve(body.app)
            if bundle is None:
                reason = "ambiguous" if cands else "not_found"
                actions.log_open("launch", body.app, False, reason)
                return {"ok": False, "blocked": {"reason": reason, "candidates": cands, "detail": {}}}
            d.launch_app(bundle)
            kind, value = "launch", bundle
        else:
            raise HTTPException(status_code=400, detail="需要 app 或 url")
        png = wait_stable(d.screenshot_png, time.sleep, time.monotonic)
        sid, rec = actions._observer(d).identify(png)
        actions.log_open(kind, value, True)
        return {"ok": True, "open": {"kind": kind, "value": value},
                "screen": {"bundle": rec["bundle"] if rec else None, "screen_id": sid,
                           "screen_label": (rec or {}).get("label", ""), "screenshot": actions.save_shot(png)}}

    class LearnBody(BaseModel):
        name: str
        rect: list[int]
        app_wide: bool = False
        fixed: bool = False

    @app.post("/memory/learn")
    def memory_learn(body: LearnBody):
        """给当前屏上一个 AX 无名元素起名并记住（截图锚点）。自绘 UI 的 App 靠它才能用名字操作。"""
        if len(body.rect) != 4:
            raise HTTPException(status_code=400, detail="rect 必须是 [x, y, w, h]")
        out = actions.learn(body.name, body.rect, app_wide=body.app_wide, fixed=body.fixed)
        if not out.get("ok"):
            raise HTTPException(status_code=503 if "connected" in str(out.get("error")) else 400,
                                detail=out.get("error"))
        return out

    class SaveBody(BaseModel):
        name: str
        shot: str | None = None

    @app.post("/shots/save")
    def shots_save(body: SaveBody):
        """把截图归档留存（shots/ 只是滚动缓存，要长期保留必须存出来）。"""
        out = actions.save_as(body.name, body.shot)
        if not out.get("ok"):
            raise HTTPException(status_code=404 if "不在缓存" in str(out.get("error")) else 503,
                                detail=out.get("error"))
        return out

    @app.get("/shots/saved")
    def shots_saved(limit: int = 50):
        return actions.saved_list(limit)

    @app.get("/memory/screens")
    def memory_screens(bundle: str | None = None):
        return [_strip(r) for r in memory.screens(bundle)]

    @app.delete("/memory/apps/{bundle}")
    def memory_clear(bundle: str):
        memory.clear_app(bundle)
        return {"ok": True}

    @app.get("/settings")
    def settings_get():
        return load_settings(root)

    @app.put("/settings")
    def settings_put(body: dict):
        """深度一层合并（同引擎 save_settings）。跑分/调试需要临时挪开静默窗时用。"""
        return save_settings(root, body)

    @app.get("/apps")
    def apps(refresh: int = 0):
        return catalog.refresh() if refresh else (catalog.apps() or catalog.refresh())

    @app.post("/scripts", status_code=201)
    def scripts_create(body: ScriptBody):
        try:
            return scripts.create(body.name, body.last_n)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/scripts")
    def scripts_list():
        return scripts.list()

    @app.post("/scripts/{wf_id}/run", status_code=201)
    def scripts_run(wf_id: str, body: RunBody):
        if workflows.get(wf_id) is None:
            raise HTTPException(status_code=404, detail="script not found")
        try:
            return scripts.run(wf_id, body.params)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/runs/{run_id}")
    def run_get(run_id: str):
        r = scripts.get_run(run_id)
        if r is None:
            raise HTTPException(status_code=404, detail="run not found")
        return r

    @app.get("/shots/{name}")
    def shot(name: str):
        p = actions.shots_dir / Path(name).name
        if not p.exists():
            raise HTTPException(status_code=404)
        return FileResponse(p, media_type="image/png")

    # ---- 路径层 ----
    class JobBody(BaseModel):
        app: str
        path: str
        params: dict = {}
        attempts: int | None = None      # 失败自动复位重试的总次数；缺省取 settings.job_attempts（默认 3）

    @app.get("/paths")
    def paths_list(app_: str | None = None):
        return P.listing(app_)

    @app.post("/jobs", status_code=201)
    def jobs_start(body: JobBody):
        try:
            return jobs.start(body.app, body.path, body.params, attempts=body.attempts)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError:
            raise HTTPException(status_code=409, detail="已有任务在跑，等它结束或先取消")

    @app.get("/jobs")
    def jobs_list(limit: int = 50):
        return jobs.list(limit)

    @app.get("/jobs/{jid}")
    def jobs_get(jid: str):
        j = jobs.get(jid)
        if j is None:
            raise HTTPException(status_code=404, detail="job not found")
        return j

    @app.get("/jobs/{jid}/items")
    def jobs_items(jid: str, limit: int = 1000):
        if jobs.get(jid) is None:
            raise HTTPException(status_code=404, detail="job not found")
        return jobs.items(jid, limit)

    @app.post("/jobs/{jid}/cancel")
    def jobs_cancel(jid: str):
        return {"ok": jobs.cancel(jid)}

    @app.post("/jobs/{jid}/resume", status_code=201)
    def jobs_resume(jid: str):
        try:
            return jobs.resume(jid)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        except RuntimeError:
            raise HTTPException(status_code=409, detail="已有任务在跑")

    @app.get("/drafts")
    def drafts_list(status: str | None = "pending"):
        return drafts.list(None if status == "all" else status)

    @app.post("/drafts/{did}/send", status_code=201)
    def drafts_send(did: str):
        """把草稿真的发出去：用同一条路径、同样参数、mode=send 起一个任务。"""
        d = drafts.get(did)
        if d is None:
            raise HTTPException(status_code=404, detail="draft not found")
        if d.get("status") != "pending":
            raise HTTPException(status_code=409, detail=f"草稿状态是 {d.get('status')}")
        params = dict(d["params"])
        params["mode"] = "send"
        try:
            j = jobs.start(d["app"], d["path"], params)
        except RuntimeError:
            raise HTTPException(status_code=409, detail="已有任务在跑")
        drafts.set_status(did, "sending", {"job": j["id"]})
        return j

    @app.delete("/drafts/{did}")
    def drafts_discard(did: str):
        if drafts.set_status(did, "discarded") is None:
            raise HTTPException(status_code=404, detail="draft not found")
        return {"ok": True}

    @app.get("/ledger")
    def ledger_list(app_: str | None = None, kind: str | None = None, limit: int = 200):
        return ledger.list(app_, kind, limit)

    class ListsBody(BaseModel):
        app: str
        block: list[str] | None = None
        allow: list[str] | None = None

    @app.put("/ledger/lists")
    def ledger_lists(body: ListsBody):
        return ledger.set_lists(body.app, body.block, body.allow)

    class WatchBody(BaseModel):
        app: str
        path: str
        params: dict = {}
        every_s: int = 300
        then: dict | None = None

    @app.post("/watch", status_code=201)
    def watch_add(body: WatchBody):
        if P.get(body.app, body.path) is None:
            raise HTTPException(status_code=404, detail="没有这条路径")
        return watcher.add(body.app, body.path, body.params, body.every_s, body.then)

    @app.get("/watch")
    def watch_list():
        return {"watches": watcher.list(), "events": watcher.events(50)}

    @app.post("/watch/{wid}/run")
    def watch_run(wid: str):
        w = watcher.watches.get(wid)
        if w is None:
            raise HTTPException(status_code=404, detail="watch not found")
        return {"new": watcher.run_once(w)}

    @app.delete("/watch/{wid}")
    def watch_remove(wid: str):
        return {"ok": watcher.remove(wid)}

    class SequenceBody(BaseModel):
        app: str
        name: str
        steps: list[dict]
        targets: list[str]
        mode: str = "draft"

    @app.post("/sequences", status_code=201)
    def sequences_add(body: SequenceBody):
        for st in body.steps:
            if P.get(body.app, st.get("path", "")) is None:
                raise HTTPException(status_code=404, detail=f"没有这条路径：{body.app}/{st.get('path')}")
        return sequences.add(body.app, body.name, body.steps, body.targets, body.mode)

    @app.get("/sequences")
    def sequences_list():
        return sequences.list()

    @app.post("/sequences/{sid}/tick")
    def sequences_tick(sid: str):
        """手动推进一个到期目标（不等 Watcher 循环）。"""
        s = sequences.get(sid)
        if s is None:
            raise HTTPException(status_code=404, detail="sequence not found")
        due = [t for (ss, t) in sequences.due() if ss["id"] == sid]
        if not due:
            return {"advanced": None, "hint": "没有到期的目标"}
        return {"advanced": due[0], **sequences.advance(s, due[0])}

    @app.delete("/sequences/{sid}")
    def sequences_remove(sid: str):
        return {"ok": sequences.remove(sid)}

    @app.get("/skill")
    def skill():
        return PlainTextResponse(SKILL_PATH.read_text(), media_type="text/markdown")

    @app.get("/events")
    def events():
        q = actions.subscribe()

        def gen():
            try:
                while True:
                    try:
                        ev = q.get(timeout=15)
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        yield ": ping\n\n"
            finally:
                actions.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def main():
    import uvicorn
    from butler.sbdriver import sb_driver_provider, sb_invalidate
    uvicorn.run(create_app(sb_driver_provider, start_queue=True, invalidate=sb_invalidate), host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
