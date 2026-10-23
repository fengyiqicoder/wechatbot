"""目标解析：记忆锚点（快）→ AX（慢，顺手回写记忆）。不唯一不给矩形。"""
from __future__ import annotations
from dataclasses import dataclass, field

from engine import anchor
from butler import screen as S
from butler.names import match, normalize
from butler.observe import Observation

_CONF = {"exact": 0.99, "ci": 0.95, "contains": 0.9, "fuzzy": 0.8}
_CONTAINERS = ("Window", "Application", "Other", "Table", "ScrollView", "CollectionView")


@dataclass
class Resolution:
    status: str
    observation: Observation
    name: str | None = None
    label: str = ""
    rect: list[int] | None = None
    method: str = ""
    confidence: float = 0.0
    candidates: list[dict] = field(default_factory=list)


def _cand(e: dict) -> dict:
    return {"name": e.get("name", ""), "label": e.get("label", ""), "tag": e["tag"], "rect": e["rect"]}


def _key(e: dict) -> str:
    return e.get("name") or e.get("label") or ""


class Resolver:
    def __init__(self, observer, memory):
        self.observer, self.memory = observer, memory

    @staticmethod
    def match_elements(target: str, els: list[dict]):
        """先按 name（含 label 回退）匹配；全空再按 label 单独匹配（有些控件 name 是 id、label 才是人话）。"""
        m = match(target, [_key(e) for e in els])
        if m.tier == "none":
            m2 = match(target, [e.get("label") or "" for e in els])
            if m2.tier != "none":
                return m2
        return m

    def resolve(self, target: str, obs: Observation) -> Resolution:
        if obs.source == "memory":
            m = self.match_elements(target, obs.elements)
            if m.tier != "none" and len(m.hits) == 1:
                e = obs.elements[m.hits[0]]
                sim = self._verify_anchor(obs, e)
                if sim is not None:
                    self.memory.bump(obs.screen_id, normalize(_key(e)), ok=True)
                    return Resolution("ok", obs, _key(e), e.get("label") or "", e["rect"], "memory", round(sim, 3))
                self.memory.bump(obs.screen_id, normalize(_key(e)), ok=False)
        app_hit = self._resolve_app_wide(target, obs)
        if app_hit is not None:
            return app_hit
        deep = self.observer.observe(deep=True)
        m = self.match_elements(target, deep.elements)
        if m.tier == "none":
            return Resolution("not_found", deep, candidates=[_cand(deep.elements[i]) for i in m.nearest])
        if len(m.hits) > 1:
            return Resolution("ambiguous", deep, candidates=[_cand(deep.elements[i]) for i in m.hits])
        e = deep.elements[m.hits[0]]
        return Resolution("ok", deep, _key(e), e.get("label") or "", e["rect"], "ax", _CONF[m.tier])

    def _resolve_app_wide(self, target: str, obs: Observation) -> Resolution | None:
        """屏幕级记忆没命中时，试应用级常驻元素（底部标签栏这类）。锚点必须在当前截图上对得上。"""
        if not obs.bundle or not obs.png:
            return None
        els = self.memory.app_elements(obs.bundle)
        if not els:
            return None
        names = list(els.keys())
        m = match(target, names)
        if m.tier == "none" or len(m.hits) != 1:
            return None
        rec = els[names[m.hits[0]]]
        if rec.get("fixed"):                                # 位置固定、外观随背景变（半透明标签栏）
            return Resolution("ok", obs, rec["name"], rec.get("label", ""), rec["rect"], "memory", 0.9)
        b64 = (rec.get("anchor") or {}).get(obs.appearance)
        if not b64:
            return None
        ok, sim = anchor.looks_same(anchor.crop(obs.png, rec["rect"], obs.scale), anchor.decode(b64), S.ELEMENT_THR)
        if not ok:
            return None
        return Resolution("ok", obs, rec["name"], rec.get("label", ""), rec["rect"], "memory", round(sim, 3))

    def _verify_anchor(self, obs: Observation, e: dict) -> float | None:
        rec = self.memory.find_element(obs.screen_id, normalize(_key(e)))
        if not rec or rec.get("dynamic"):
            return None
        b64 = (rec.get("anchor") or {}).get(obs.appearance)
        if not b64:
            return None
        ok, sim = anchor.looks_same(anchor.crop(obs.png, rec["rect"], obs.scale), anchor.decode(b64), S.ELEMENT_THR)
        return sim if ok else None

    def resolve_coords(self, x: int, y: int, obs: Observation) -> Resolution:
        hits = [e for e in obs.elements
                if e["rect"][0] <= x <= e["rect"][0] + e["rect"][2] and e["rect"][1] <= y <= e["rect"][1] + e["rect"][3]
                and e["tag"] not in _CONTAINERS]
        if not hits:
            return Resolution("ok", obs, None, "", [x, y, 1, 1], "coords", 0.0)
        e = min(hits, key=lambda e: e["rect"][2] * e["rect"][3])
        return Resolution("ok", obs, _key(e), e.get("label") or "", e["rect"], "coords", 0.5)
