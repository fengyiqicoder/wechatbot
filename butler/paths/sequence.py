"""序列编排：对一批目标按步骤推进（第 0 天关注 → 第 3 天 DM → 未回第 7 天再发）。

一个序列 = app + steps + targets。每个目标独立记录「到第几步、下次何时」。步骤：
  {"path": "send", "params": {"to": "{target}", "text": "..."}, "after_days": 3,
   "only_if": {"path": "messages", "params": {"contact": "{target}"}, "no_item_from": "{target}"}}
- params 里的 "{target}" 会替换成目标名；
- only_if：先跑一条采集路径，若结果里有「对方发的条目」(who != 我) 就跳过这步（对方已回复）；
- mode：draft（默认，写类步骤只出草稿）或 send。
由 Watcher 的循环每 5 s 检查一次到期目标；同一时刻只推进一个目标（设备是独占资源）。
"""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path


def _fill(obj, target: str):
    if isinstance(obj, str):
        return obj.replace("{target}", target)
    if isinstance(obj, dict):
        return {k: _fill(v, target) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, target) for v in obj]
    return obj


class SequenceStore:
    def __init__(self, jobs, root: Path):
        self.jobs = jobs
        self.path = Path(root) / "paths" / "sequences.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seqs: dict[str, dict] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.seqs, ensure_ascii=False, indent=1))

    def add(self, app: str, name: str, steps: list[dict], targets: list[str], mode: str = "draft") -> dict:
        now = int(time.time())
        first_delay = int(float((steps[0] if steps else {}).get("after_days", 0)) * 86400)
        s = {"id": uuid.uuid4().hex[:8], "app": app, "name": name, "steps": steps, "mode": mode, "created": now,
             "enabled": True,
             "targets": {t: {"step": 0, "next_at": now + first_delay, "done": False, "history": []} for t in targets}}
        self.seqs[s["id"]] = s
        self._save()
        return s

    def list(self) -> list[dict]:
        return list(self.seqs.values())

    def get(self, sid: str) -> dict | None:
        return self.seqs.get(sid)

    def remove(self, sid: str) -> bool:
        if sid in self.seqs:
            del self.seqs[sid]
            self._save()
            return True
        return False

    def due(self) -> list[tuple[dict, str]]:
        now = time.time()
        out = []
        for s in self.seqs.values():
            if not s.get("enabled"):
                continue
            for t, st in s["targets"].items():
                if not st["done"] and st["next_at"] <= now:
                    out.append((s, t))
        # 轮转：刚推进过的目标排后面，让同批目标齐头并进
        out.sort(key=lambda p: (p[0]["targets"][p[1]]["history"] or [{"ts": 0}])[-1]["ts"])
        return out

    def _run(self, app: str, path: str, params: dict) -> tuple[dict, list]:
        j = self.jobs.start(app, path, params)
        j = self.jobs.wait(j["id"])
        items = self.jobs.items(j["id"]) if j.get("kind") == "collect" else []
        return j, items

    def advance(self, s: dict, target: str) -> dict:
        """推进一个目标一步。返回本步记录。"""
        st = s["targets"][target]
        step = s["steps"][st["step"]]
        rec = {"ts": int(time.time()), "step": st["step"], "path": step["path"]}
        cond = step.get("only_if")
        if cond:
            j, items = self._run(s["app"], cond["path"], _fill(cond.get("params") or {}, target))
            replied = any((it.get("who") or "") not in ("我", "me", "") for it in items)
            if cond.get("no_item_from") is not None and replied:
                rec["skipped"] = "对方已回复"
                st["done"] = True                       # 已回复：序列对该目标结束
                st["history"].append(rec)
                self._save()
                return rec
        params = _fill(step.get("params") or {}, target)
        if s.get("mode") == "send":
            params["mode"] = "send"
        else:
            params.setdefault("mode", "draft")
        try:
            j, _ = self._run(s["app"], step["path"], params)
            rec["job"], rec["status"] = j["id"], j.get("status")
            rec["result"] = j.get("result")
        except RuntimeError:
            rec["status"] = "busy"
            self._save()
            return rec
        st["step"] += 1
        st["history"].append(rec)
        if st["step"] >= len(s["steps"]):
            st["done"] = True
        else:
            st["next_at"] = int(time.time()) + int(float(s["steps"][st["step"]].get("after_days", 0)) * 86400)
        self._save()
        return rec

    def tick(self, max_targets: int = 1) -> list[dict]:
        out = []
        for s, t in self.due()[:max_targets]:
            if self.jobs.running():
                break
            out.append({"sequence": s["id"], "target": t, **self.advance(s, t)})
        return out
