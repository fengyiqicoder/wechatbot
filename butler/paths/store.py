"""草稿队列与台账：都是 root/paths/ 下的 jsonl，可读可手改。"""
from __future__ import annotations
import json
import threading
import time
import uuid
from pathlib import Path


class DraftStore:
    def __init__(self, root: Path):
        self.path = Path(root) / "paths" / "drafts.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def _write(self, rows: list[dict]) -> None:
        self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def add(self, app: str, path: str, params: dict, summary: str) -> dict:
        rec = {"id": uuid.uuid4().hex[:10], "app": app, "path": path, "params": params, "summary": summary,
               "status": "pending", "created": int(time.time())}
        with self._lock:
            rows = self._all()
            rows.append(rec)
            self._write(rows)
        return rec

    def list(self, status: str | None = "pending") -> list[dict]:
        return [r for r in self._all() if status is None or r.get("status") == status]

    def get(self, did: str) -> dict | None:
        return next((r for r in self._all() if r["id"] == did), None)

    def set_status(self, did: str, status: str, extra: dict | None = None) -> dict | None:
        with self._lock:
            rows = self._all()
            for r in rows:
                if r["id"] == did:
                    r["status"] = status
                    r["updated"] = int(time.time())
                    if extra:
                        r.update(extra)
                    self._write(rows)
                    return r
        return None


class Ledger:
    """谁、何时、发了什么。每 (app, kind, target) 默认只发一次；黑白名单也在这。"""
    def __init__(self, root: Path):
        base = Path(root) / "paths"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "ledger.jsonl"
        self.lists = base / "lists.json"
        self._lock = threading.Lock()

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def record(self, app: str, kind: str, target: str, content: str, extra: dict | None = None) -> dict:
        rec = {"ts": int(time.time()), "app": app, "kind": kind, "target": target, "content": content[:500]}
        if extra:
            rec.update(extra)
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def has(self, app: str, kind: str, target: str) -> bool:
        return any(r["app"] == app and r["kind"] == kind and r["target"] == target for r in self._rows())

    def list(self, app: str | None = None, kind: str | None = None, limit: int = 200) -> list[dict]:
        rows = [r for r in self._rows() if (app is None or r["app"] == app) and (kind is None or r["kind"] == kind)]
        return rows[-limit:]

    def _lists(self) -> dict:
        if self.lists.exists():
            return json.loads(self.lists.read_text())
        return {"block": {}, "allow": {}}

    def set_lists(self, app: str, block: list[str] | None = None, allow: list[str] | None = None) -> dict:
        d = self._lists()
        if block is not None:
            d.setdefault("block", {})[app] = sorted(set(block))
        if allow is not None:
            d.setdefault("allow", {})[app] = sorted(set(allow))
        self.lists.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        return d

    def blocked(self, app: str, target: str) -> bool:
        d = self._lists()
        if target in d.get("block", {}).get(app, []):
            return True
        allow = d.get("allow", {}).get(app)
        return bool(allow) and target not in allow
