"""拟人层参数（DogPlanet / 引擎既有值）、写类动作限额、静默窗。纯逻辑，睡眠由调用方执行。"""
from __future__ import annotations
import random
import time
from collections import defaultdict, deque

PRESS_MS = 80
DEFAULT_LIMITS = {"like": 30, "comment": 10, "dm": 20, "follow": 20, "post": 5}
WINDOW_S = 3600


NAV_DELAY = (0.3, 0.7)        # 导航类点击（标签栏/返回/进详情）：人也是不假思索地点，长停顿只会拉长 X 闪退窗口内的步数
SCROLL_PAUSE = (1.5, 6.0)     # 翻一屏后的「阅读」停顿


class Humanizer:
    def __init__(self, rng=random.uniform, randint=random.randint, delay=(0.8, 2.0), nav_delay=NAV_DELAY,
                 scroll_pause=SCROLL_PAUSE):
        self.rng, self.randint, self.delay = rng, randint, delay
        self.nav_delay, self.scroll_pause = nav_delay, scroll_pause

    def configure(self, settings: dict) -> None:
        """按 settings 覆盖三档停顿（human_delay / human_delay_nav / human_scroll_pause），缺省不动。"""
        for key, attr in (("human_delay", "delay"), ("human_delay_nav", "nav_delay"), ("human_scroll_pause", "scroll_pause")):
            v = settings.get(key)
            if isinstance(v, (list, tuple)) and len(v) == 2:
                setattr(self, attr, (float(v[0]), float(v[1])))

    def pre_delay(self, kind: str, text_len: int = 0) -> float:
        base = self.rng(*(self.nav_delay if kind == "nav" else self.delay))
        if kind == "read" and text_len > 0:
            base += min(8.0, text_len / 100 * 1.5)
        return base

    def tap_point(self, rect) -> tuple[int, int]:
        x, y, w, h = rect
        return (x + self.randint(int(w * 0.3), max(int(w * 0.3), int(w * 0.7))),
                y + self.randint(int(h * 0.3), max(int(h * 0.3), int(h * 0.7))))

    def type_chunks(self, text: str) -> list[str]:
        out, i = [], 0
        while i < len(text):
            n = self.randint(3, 8)
            out.append(text[i:i + n])
            i += n
        return out

    def chunk_gap(self) -> float:
        return self.rng(0.15, 0.4)

    def swipe_ms(self) -> int:
        return 400

    def after_scroll(self) -> float:
        return self.rng(*self.scroll_pause)


def in_quiet_hours(now_hhmm: str, window: dict) -> bool:
    start, end = window.get("start", "23:00"), window.get("end", "07:30")
    if start <= end:
        return start <= now_hhmm < end
    return now_hhmm >= start or now_hhmm < end


class RateLimiter:
    def __init__(self, limits: dict | None = None, clock=time.time):
        self.limits = dict(DEFAULT_LIMITS if limits is None else limits)
        self.clock = clock
        self._events: dict[tuple[str, str], deque] = defaultdict(deque)

    def _prune(self, key) -> deque:
        q = self._events[key]
        now = self.clock()
        while q and now - q[0] >= WINDOW_S:
            q.popleft()
        return q

    def check(self, bundle: str, kind: str) -> int | None:
        limit = self.limits.get(kind)
        if limit is None:
            return None
        q = self._prune((bundle, kind))
        if len(q) < limit:
            return None
        if not q:
            return WINDOW_S
        return int(WINDOW_S - (self.clock() - q[0]))

    def record(self, bundle: str, kind: str) -> None:
        self._prune((bundle, kind)).append(self.clock())
