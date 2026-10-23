"""屏幕记忆：memory/<bundle>/screens/<hash>.json（锚点 png 以 base64 内嵌，文件即真相，可手工修）。"""
from __future__ import annotations
import copy
import json
import shutil
import time
from pathlib import Path

from engine.store import _atomic_write_text


class MemoryStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sid: str) -> Path:
        bundle, h = sid.split("/", 1)
        return self.dir / bundle / "screens" / f"{h}.json"

    # 内存缓存（sid → 记录）。文件仍是真相：写盘同时更新缓存；外部手改文件后重启引擎即可。
    # 识别每个动作要跑七八次，188 屏每次读盘 + 解码曾把一个动作拖到 50 s。
    _cache: dict | None = None

    def _load_all(self) -> dict:
        if self._cache is None:
            self._cache = {}
            for p in sorted(self.dir.glob("*/screens/*.json")):
                try:
                    rec = json.loads(p.read_text())
                    self._cache[rec["id"]] = rec
                except Exception:  # noqa: BLE001 — 坏文件跳过
                    continue
        return self._cache

    def _invalidate(self) -> None:
        self._cache = None

    def save_screen(self, rec: dict) -> None:
        rec.setdefault("created_at", time.time())
        rec["updated_at"] = time.time()
        p = self._path(rec["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(p, json.dumps(rec, ensure_ascii=False))
        self._load_all()[rec["id"]] = copy.deepcopy(rec)

    def get_screen(self, sid: str) -> dict | None:
        rec = self._load_all().get(sid)
        return copy.deepcopy(rec) if rec is not None else None

    def screens(self, bundle: str | None = None) -> list[dict]:
        recs = list(self._load_all().values())
        return [r for r in recs if r.get("bundle") == bundle] if bundle else recs

    def upsert_elements(self, sid: str, elements: dict) -> None:
        rec = self.get_screen(sid)
        if rec is None:
            return
        for k, v in elements.items():
            old = rec["elements"].get(k, {})
            merged = {**old, **v}
            merged.setdefault("hits", 0)
            merged.setdefault("fails", 0)
            merged.setdefault("last_verified", 0)
            if old.get("anchor") and v.get("anchor"):
                merged["anchor"] = {**old["anchor"], **v["anchor"]}
            rec["elements"][k] = merged
        self.save_screen(rec)

    def find_element(self, sid: str, norm_name: str) -> dict | None:
        rec = self.get_screen(sid)
        return (rec or {}).get("elements", {}).get(norm_name)

    def bump(self, sid: str, norm_name: str, ok: bool) -> None:
        rec = self.get_screen(sid)
        if not rec or norm_name not in rec["elements"]:
            return
        e = rec["elements"][norm_name]
        key = "hits" if ok else "fails"
        e[key] = e.get(key, 0) + 1
        if ok:
            e["last_verified"] = time.time()
        self.save_screen(rec)

    def clear_app(self, bundle: str) -> None:
        self._invalidate()
        shutil.rmtree(self.dir / bundle, ignore_errors=True)

    def summary(self) -> dict:
        s = self.screens()
        return {"apps": len({r["bundle"] for r in s}), "screens": len(s)}

    def app_elements(self, bundle: str) -> dict:
        """应用级元素：底部标签栏这种全 App 常驻的控件，学一次就跨屏可用。"""
        p = self.dir / bundle / "app.json"
        return json.loads(p.read_text()).get("elements", {}) if p.exists() else {}

    def save_app_element(self, bundle: str, key: str, rec: dict) -> None:
        p = self.dir / bundle / "app.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text()) if p.exists() else {"bundle": bundle, "elements": {}}
        old = data["elements"].get(key, {})
        merged = {**old, **rec}
        if old.get("anchor") and rec.get("anchor"):
            merged["anchor"] = {**old["anchor"], **rec["anchor"]}
        data["elements"][key] = merged
        _atomic_write_text(p, json.dumps(data, ensure_ascii=False))

    def load_apps(self) -> dict:
        p = self.dir / "apps.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_apps(self, apps: dict) -> None:
        _atomic_write_text(self.dir / "apps.json", json.dumps(apps, ensure_ascii=False, indent=1))
