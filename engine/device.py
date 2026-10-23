"""WDA 连接管理：就绪探测 + session 缓存。WDA 进程与端口转发由 scripts/start_wda.sh 负责。

session 失效自愈说明：facebook-wda 的 `wda.Client(url).session()` 返回的 Client 对象，
其 `_callback_fix_invalid_session_id` 仍然绑定在被丢弃的父对象上——一旦 session 真的失效
（App 被系统重启、session 被 GC、内存压力，WDA 进程本身没死），自动重试会不断重建同一个
失效 URL，直到 `@limit_call_depth(4)` 命中，抛出 `RuntimeError("call depth exceed 4")`。
也就是说 facebook-wda **不会**帮我们自动重建 session；缓存的 driver 一旦失效就永远失效，
直到进程重启。所以这里显式提供 `invalidate()`，由调用方（RunQueue）在探测到失效错误时
调用，下一次 `get_driver()` 就会重新建 session。
"""
from __future__ import annotations
import socket

import requests

from engine.driver import ActionDriver

WDA_URL = "http://127.0.0.1:8100"
WDA_HOST, WDA_PORT = "127.0.0.1", 8100


class DeviceManager:
    def __init__(self):
        self._driver = None

    def wda_ready(self, url: str = WDA_URL, http_get=requests.get) -> bool:
        try:
            return '"state"' in http_get(url + "/status", timeout=3).text
        except Exception:
            return False

    def _new_session(self):
        import wda
        return wda.Client(WDA_URL).session()

    def invalidate(self) -> None:
        """标记当前缓存的 driver 失效；下一次 get_driver() 会重新建 session。"""
        self._driver = None

    def port_open(self, host: str = WDA_HOST, port: int = WDA_PORT, timeout: float = 0.2) -> bool:
        """毫秒级 TCP 探活：只看 8100 有没有人在听，不发 HTTP、不进 WDA 的请求循环。
        WDA 正忙（跑 run）时它照样立刻返回 True，所以不会像 /status 探测那样闪断；
        而 WDA 进程一死端口就关，缓存的 driver 能被立刻识别为失效。"""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def get_driver(self) -> ActionDriver | None:
        # 有缓存直接返回：不再逐次探活。wda_ready() 是一次 HTTP /status 请求（3s 超时），
        # window_size() 在 X 这类重界面上等于一次 AX 快照（10–35 s）——两者都不能在
        # 每次 get_driver() 时都跑一遍，否则并发请求（如 run 执行期间的 GET /status）
        # 会被 WDA 正忙拖到超时，导致 UI 一闪而过「设备未连接」。
        if self._driver is not None:
            if self.port_open():
                return self._driver
            # WDA 进程没了（端口已关）：缓存的 driver 必然失效，别再假装在线
            self._driver = None
            return None
        if not self.wda_ready():
            return None
        try:
            driver = ActionDriver(self._new_session())
            # 预热窗口尺寸缓存：连接时通常停在桌面，此时 /window/size 很快；
            # 设备尺寸不会变，建 session 时热一次就够。
            driver.window_size()
        except Exception:
            return None
        self._driver = driver
        return self._driver


_manager = DeviceManager()


def default_driver_provider() -> ActionDriver | None:
    return _manager.get_driver()


def default_invalidate() -> None:
    _manager.invalidate()
