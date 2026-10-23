"""动作后验证：等屏幕稳定、判断是否变化、核对 Agent 声明的预期。"""
from __future__ import annotations
import io

from PIL import Image, ImageChops, ImageStat

from butler.extract import text_blocks
from butler.names import match, normalize

_SMALL = (41, 89)


def _gray(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("L").resize(_SMALL)


def diff_ratio(a: bytes, b: bytes) -> float:
    return ImageStat.Stat(ImageChops.difference(_gray(a), _gray(b))).mean[0] / 255.0


def wait_stable(shoot, sleep, clock, max_s: float = 3.0, poll: float = 0.3, thr: float = 0.01) -> bytes:
    prev = shoot()
    deadline = clock() + max_s
    while clock() < deadline:
        sleep(poll)
        cur = shoot()
        if diff_ratio(prev, cur) < thr:
            return cur
        prev = cur
    return prev


def screen_changed(before: bytes, after: bytes, thr: float = 0.02) -> bool:
    return diff_ratio(before, after) >= thr


def check_expect(expect, obs, changed: bool) -> tuple[bool, str]:
    if expect in (None, ""):
        return True, ""
    if expect == "screen_change":
        return (True, "") if changed else (False, "预期屏幕变化，但屏幕没变")
    if expect == "same_screen":
        return (True, "") if not changed else (False, "预期停留原屏幕，但屏幕变了")
    if isinstance(expect, dict) and "element" in expect:
        names = [e.get("name") or e.get("label") or "" for e in obs.elements]
        labels = [e.get("label") or "" for e in obs.elements]
        ok = match(expect["element"], names).tier in ("exact", "ci") or \
            match(expect["element"], labels).tier in ("exact", "ci")
        return (True, "") if ok else (False, f"未找到元素「{expect['element']}」")
    if isinstance(expect, dict) and "text" in expect:
        t = normalize(expect["text"]).lower()
        ok = any(t in normalize(b["text"]).lower() for b in text_blocks(obs.elements))
        return (True, "") if ok else (False, f"屏幕上没有文本「{expect['text']}」")
    return False, f"不认识的 expect：{expect!r}"
