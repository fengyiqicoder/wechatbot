"""轮询监听：每隔 every_s 跑一次采集路径，发现新条目就记事件；配了 then 就为每条新条目生成一份草稿。"""
from __future__ import annotations
import json
import threading
import time
import uuid
from pathlib import Path


class Watcher:
    def __init__(self, jobs, drafts, root: Path):
        self.jobs, self.drafts = jobs, drafts
        self.path = Path(root) / "paths" / "watches.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path = Path(root) / "paths" / "watch_events.jsonl"
        self.watches: dict[str, dict] = self._load()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.watches, ensure_ascii=False, indent=1))

    def add(self, app: str, path: str, params: dict, every_s: int = 300, then: dict | None = None) -> dict:
        w = {"id": uuid.uuid4().hex[:8], "app": app, "path": path, "params": params, "every_s": int(every_s),
             "then": then, "seen": [], "last_run": 0, "runs": 0, "new_total": 0, "enabled": True}
        self.watches[w["id"]] = w
        self._save()
        return w

    def remove(self, wid: str) -> bool:
        if wid in self.watches:
            del self.watches[wid]
            self._save()
            return True
        return False

    def list(self) -> list[dict]:
        return list(self.watches.values())

    def events(self, limit: int = 100) -> list[dict]:
        if not self.events_path.exists():
            return []
        rows = [json.loads(l) for l in self.events_path.read_text().splitlines() if l.strip()]
        return rows[-limit:]

    def _event(self, ev: dict) -> None:
        ev["ts"] = int(time.time())
        with self.events_path.open("a") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def run_once(self, w: dict) -> int:
        """跑一轮，返回新条目数。同步等任务结束。"""
        if self.jobs.running():
            return 0
        j = self.jobs.start(w["app"], w["path"], w["params"])
        j = self.jobs.wait(j["id"])
        w["last_run"], w["runs"] = int(time.time()), w["runs"] + 1
        if j.get("status") != "done":
            self._event({"watch": w["id"], "kind": "error", "job": j["id"], "status": j.get("status"),
                         "error": j.get("error")})
            self._save()
            return 0
        seen = set(w["seen"])
        new = [it for it in self.jobs.items(j["id"]) if it.get("id") not in seen]
        for it in new:
            seen.add(it.get("id"))
            self._event({"watch": w["id"], "kind": "new", "job": j["id"], "item": it})
            then = w.get("then")
            if then:
                params = dict(then.get("params") or {})
                for k, src in (then.get("map") or {}).items():
                    params[k] = it.get(src)
                params.setdefault("mode", "draft")
                d = self.drafts.add(w["app"], then["path"], params,
                                    f"watch {w['id']} → {then['path']}: {str(it)[:80]}")
                self._event({"watch": w["id"], "kind": "draft", "draft": d["id"]})
        w["seen"] = sorted(seen, key=str)[-5000:]
        w["new_total"] += len(new)
        self._save()
        return len(new)

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            for w in list(self.watches.values()):
                if w.get("enabled") and now - w["last_run"] >= w["every_s"]:
                    try:
                        self.run_once(w)
                    except Exception as e:  # noqa: BLE001
                        self._event({"watch": w["id"], "kind": "error", "error": f"{type(e).__name__}: {e}"[:300]})
            seqs = getattr(self, "sequences", None)
            if seqs is not None:
                try:
                    for rec in seqs.tick():
                        self._event({"kind": "sequence", **rec})
                except Exception as e:  # noqa: BLE001
                    self._event({"kind": "error", "error": f"sequence: {type(e).__name__}: {e}"[:300]})
            self._stop.wait(5)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
