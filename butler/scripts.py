"""脚本沉淀：动作日志 → 引擎 workflow（确定性回放，不经 LLM）。"""
from __future__ import annotations
import time

from engine import anchor


def entries_to_workflow(entries: list[dict], name: str, wf_id: str, scale: float, load_png) -> dict:
    steps, anchors = [], {}
    for e in entries:
        if not e.get("ok") or e.get("blocked"):
            continue
        op = e.get("op")
        if op == "open":
            o = e.get("open") or {}
            steps.append({"op": "launch_app", "bundle_id": o["value"]} if o.get("kind") == "launch"
                         else {"op": "open_url", "url": o.get("value", "")})
        elif op in ("tap", "wait_for"):
            r = e.get("resolved") or {}
            rect = r.get("rect")
            if not rect:
                continue
            if r.get("method") == "coords" and not r.get("name"):
                steps.append({"op": "tap", "rect": rect, "no_anchor": True})
                continue
            png = load_png(e["before_png"]) if e.get("before_png") else None
            if png:
                key = f"a{len(anchors) + 1}"
                anchors[key] = {"png": anchor.make_anchor(png, rect, scale), "threshold": 0.88}
                steps.append({"op": op, "rect": rect, "anchor": key, "selector": {"ax_id": r.get("name")}, "timeout": 10})
            else:
                steps.append({"op": "tap", "rect": rect, "no_anchor": True, "selector": {"ax_id": r.get("name")}})
        elif op == "type":
            steps.append({"op": "type", "text": (e.get("req") or {}).get("text", "")})
        elif op == "swipe" and e.get("swipe"):
            x1, y1, x2, y2 = e["swipe"]
            steps.append({"op": "swipe", "from": [x1, y1], "to": [x2, y2]})
        elif op == "home":
            steps.append({"op": "home"})
    return {"id": wf_id, "name": name, "app": "wechatbot", "source": "user", "version": 1, "category": "script",
            "params": [], "trigger": {"mode": "manual"}, "steps": steps, "anchors": anchors, "created_at": time.time()}


class ScriptService:
    def __init__(self, workflows, runs, queue, actions):
        self.workflows, self.runs, self.queue, self.actions = workflows, runs, queue, actions

    def create(self, name: str, last_n: int) -> dict:
        entries = self.actions.recent(last_n)
        wf_id = f"sb.{int(time.time())}"
        wf = entries_to_workflow(entries, name, wf_id, self.actions.scale(), self._load_png)
        if not wf["steps"]:
            raise ValueError("最近的动作里没有可沉淀的成功步骤")
        self.workflows.save(wf)
        return self.workflows.get(wf_id, full=False)

    def _load_png(self, name: str) -> bytes | None:
        p = self.actions.shots_dir / name
        return p.read_bytes() if p.exists() else None

    def list(self) -> list[dict]:
        return [w for w in self.workflows.list() if w.get("app") == "wechatbot"]

    def run(self, wf_id: str, params: dict) -> dict:
        return self.queue.enqueue(wf_id, params)

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)
