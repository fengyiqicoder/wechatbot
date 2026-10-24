"""观察器：fast = 截图 + 记忆地标（≈0.3 s）；deep = 截图 + AX（1-50 s），并把看到的写进记忆。"""
from __future__ import annotations
import io
import time
from dataclasses import dataclass, field

from PIL import Image

from butler import screen as S


@dataclass
class Observation:
    bundle: str | None
    screen_id: str | None
    screen_label: str
    appearance: str
    deep: bool
    elements: list[dict] = field(default_factory=list)
    png: bytes = b""
    scale: float = 1.0
    ax_ms: int = 0
    source: str = "unknown"


def _ax_to_dicts(els) -> list[dict]:
    return [{"name": e.name, "label": e.label, "value": e.value, "tag": e.tag, "traits": e.traits,
             "rect": [e.x, e.y, e.width, e.height], "dynamic": S.is_dynamic(e.tag, e.name), "source": "ax"}
            for e in els]


def _mem_to_dicts(rec: dict) -> list[dict]:
    return [{"name": v["name"], "label": v["label"], "value": "", "tag": v["tag"], "rect": v["rect"],
             "dynamic": v["dynamic"], "source": "memory"} for v in rec.get("elements", {}).values()]


class Observer:
    # 连续抓 AX 会把被测 App 的主线程按死：X 的自我看门狗（com.twitter.TLTWatchdog）会因此
    # abort 掉 X 自己（今日真机崩溃报告实证）。两次深观察之间至少留这么久让 App 喘口气。
    MIN_AX_GAP_S = 3.5
    # 冷却只为 X 这类有看门狗的 App；小红书采集实测不怕连续抓 AX，按 App 放开。
    AX_GAP_BY_APP = {"com.xingin.discover": 0.0}

    def __init__(self, driver, memory, clock=time.monotonic, sleeper=time.sleep, depth_provider=None):
        self.driver, self.memory, self.clock = driver, memory, clock
        self._sleep = sleeper
        self._last_ax: float | None = None
        self._scale: float | None = None
        self._last_bundle: str | None = None
        self._depth: int | None = None
        self.depth_provider = depth_provider          # bundle -> 该 App 的 AX 快照深度

    def scale(self, png: bytes | None = None) -> float:
        if self._scale is None:
            png = png or self.driver.screenshot_png()
            w_px = Image.open(io.BytesIO(png)).size[0]
            w_pt = self.driver.window_size()[0]
            self._scale = w_px / w_pt if w_pt else 1.0
        return self._scale

    def identify(self, png: bytes, bundle: str | None = None) -> tuple[str | None, dict | None]:
        """只和 bundle（默认：WDA 报告的前台 App）的屏比对：既快（188 屏 → 几十屏）又杜绝跨 App 误认。"""
        if bundle is None:
            bundle = self._true_foreground()
        recs = self.memory.screens(bundle) if bundle else self.memory.screens()
        sid, _ = S.identify(png, self.scale(png), recs)
        rec = self.memory.get_screen(sid) if sid else None
        if rec:
            self._last_bundle = rec.get("bundle")          # 记住当前 App，供深度选择用
        return sid, rec

    def _true_foreground(self) -> str | None:
        fn = getattr(self.driver, "foreground_bundle", None)
        if fn is None:
            return None
        try:
            b = fn()
        except Exception:  # noqa: BLE001
            return None
        if b:
            self._last_bundle = b
        return b

    def _apply_depth(self) -> None:
        """抓 AX 前按当前 App 调深度（只在变化时下发，省一次往返）。"""
        if self.depth_provider is None or not hasattr(self.driver, "set_snapshot_depth"):
            return
        want = int(self.depth_provider(self._last_bundle))
        if want != self._depth and self.driver.set_snapshot_depth(want):
            self._depth = want

    def settle(self, tries: int = 4, gap: float = 0.6, sleeper=None) -> "Observation":
        """反复 fast 观察直到认出屏幕或试完；专治「刚切换、界面还在加载」时 fast 认不出。"""
        import time as _t
        sleeper = sleeper or _t.sleep
        o = self.observe(deep=False)
        if o.bundle in self.AX_GAP_BY_APP:      # 放开限制的 App（小红书）：认不出也不反复等
            return o
        for _ in range(tries - 1):
            if o.source == "memory":
                return o
            sleeper(gap)
            o = self.observe(deep=False)
        return o

    def observe(self, deep: bool) -> Observation:
        png = self.driver.screenshot_png()
        scale = self.scale(png)
        mode = S.appearance(png)
        if not deep:
            fg = self._true_foreground()
            sid, rec = self.identify(png, fg)           # 只在前台 App 的屏里认，杜绝跨 App 误认
            if rec is None:
                return Observation(fg, None, "", mode, False, [], png, scale, 0, "unknown")
            return Observation(rec["bundle"], sid, rec.get("label", ""), mode, False, _mem_to_dicts(rec), png, scale,
                               0, "memory")
        # 深度按 App 取，但首次深观察时还不知道在哪个 App——先用开头这张截图认一下屏
        # （identify 只比对地标，不花 AX），认出来再决定深度，否则会用默认值读得太浅。
        if self._last_bundle is None:
            self.identify(png)
        self._apply_depth()
        if self._last_ax is not None:
            min_gap = self.AX_GAP_BY_APP.get(self._last_bundle or "", self.MIN_AX_GAP_S)
            gap = min_gap - (self.clock() - self._last_ax)
            if gap > 0:
                self._sleep(gap)
        t0 = self.clock()
        els = _ax_to_dicts(self.driver.elements())
        self._last_ax = self.clock()
        ax_ms = int((self.clock() - t0) * 1000)
        bundle = self.driver.foreground_bundle() or "unknown"
        self._last_bundle = bundle
        rec = S.build_record(bundle, png, scale, els)
        sid, label = None, S.screen_label(els)
        if rec is not None:
            sid = rec["id"]
            existing = self.memory.get_screen(sid)
            if existing is None:
                self.memory.save_screen(rec)
            else:
                existing["landmarks"][mode] = rec["landmarks"][mode]
                if rec["label"]:
                    existing["label"] = rec["label"]
                self.memory.save_screen(existing)
                self.memory.upsert_elements(sid, rec["elements"])
        return Observation(bundle, sid, label, mode, True, els, png, scale, ax_ms, "ax")
