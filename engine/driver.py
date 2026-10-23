"""ActionDriver：对 WDA session 的动作封装 + safe_source 自愈（DogPlanet 内核迁移）。"""
from __future__ import annotations
import io
import time

from engine.ax import AXElement, parse_elements

SOURCE_RETRY_WAIT = 5  # 秒；WDA 偶发 "Cannot get 'xml' source"，就地重试不上抛


class ActionDriver:
    def __init__(self, session, sleeper=time.sleep):
        self._s = session
        self._sleep = sleeper
        self._size: tuple[int, int] | None = None

    def tap(self, x: int, y: int) -> None:
        self._s.tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        self._s.swipe(x1, y1, x2, y2, duration)

    def type_text(self, text: str) -> None:
        self._s.send_keys(text)

    def screenshot_png(self) -> bytes:
        buf = io.BytesIO()
        self._s.screenshot().save(buf, "PNG")
        return buf.getvalue()

    def source(self, retries: int = 3) -> str:
        for attempt in range(retries):
            try:
                return self._s.source()
            except Exception:
                if attempt == retries - 1:
                    return ""
                self._sleep(SOURCE_RETRY_WAIT)
        return ""

    def elements(self) -> list[AXElement]:
        return parse_elements(self.source())

    def window_size(self) -> tuple[int, int]:
        # 缓存：WDA /window/size 需要活动应用的 AX 快照，在 X 上可达 10–35 s；设备尺寸不会变。
        if self._size is None:
            w, h = self._s.window_size()
            self._size = (int(w), int(h))
        return self._size

    def open_url(self, url: str) -> None:
        self._s.open_url(url)

    def launch_app(self, bundle_id: str) -> None:
        # app_state 4 = 前台运行；此时不重启，避免打断当前界面
        state = self._s.app_state(bundle_id)
        if isinstance(state, dict):
            state = state.get("value")
        if state == 4:
            return
        self._s.app_activate(bundle_id)

    def home(self) -> None:
        # /wda/homescreen（client 级、不依赖 session），与真机验证过的路径一致
        self._s.home()

    def locked(self) -> bool:
        return bool(self._s.locked())
