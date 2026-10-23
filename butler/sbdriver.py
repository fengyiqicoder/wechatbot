"""SBDriver：在 ActionDriver 上加 微信管家需要的四件事——80 ms 点按（W3C actions）、
系统弹窗探测、单元素读值（不抓全树）、前台 App（慢，只做兜底）。"""
from __future__ import annotations
from engine.device import DeviceManager
from engine.driver import ActionDriver


import math


def _finite(*vals) -> None:
    for v in vals:
        if not math.isfinite(v):
            raise ValueError(f"坐标非有限值：{vals}")


def _finger(steps):
    return {"actions": [{"type": "pointer", "id": "f1", "parameters": {"pointerType": "touch"}, "actions": steps}]}


class SBDriver(ActionDriver):
    def tap_press(self, x: int, y: int, ms: int = 80) -> None:
        _finite(x, y)
        self._s._session_http.post("/actions", _finger([
            {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": int(ms)},
            {"type": "pointerUp", "button": 0}]))

    def swipe_ms(self, x1, y1, x2, y2, ms: int = 400) -> None:
        # 用 /wda/dragfromtoforduration（facebook-wda swipe）而非 W3C /actions：后者在 X 这类
        # 界面上、WDA 被重 AX 拖累后会偶发把点算成 INFINITY（velocity=距离/0）报 status 110；
        # dragfromtoforduration 稳定，是 DogPlanet 一直在用的滑动端点。整数坐标（float 会被当百分比）。
        _finite(x1, y1, x2, y2)
        self._s.swipe(int(x1), int(y1), int(x2), int(y2), duration=min(ms / 1000.0, 0.5))

    def set_snapshot_depth(self, depth: int) -> bool:
        """按 App 调整 AX 快照深度。不同 App 差别很大：X 必须浅（深了会把它卡到自杀），
        Instagram 要 30 才读得到作者与正文（20 只有结构名）。"""
        try:
            self._s.appium_settings({"snapshotMaxDepth": int(depth)})
            return True
        except Exception:  # noqa: BLE001
            return False

    def alert_info(self) -> dict | None:
        try:
            a = self._s.alert
            if not a.exists:
                return None
            return {"title": a.text or "", "buttons": list(a.buttons() or [])}
        except Exception:  # noqa: BLE001 — 弹窗探测失败按「无弹窗」处理
            return None

    def find_value(self, tag: str, name: str, timeout: float = 3.0) -> str | None:
        try:
            el = self._s(className=f"XCUIElementType{tag}", name=name).get(timeout=timeout)
            return el.value
        except Exception:  # noqa: BLE001
            return None

    def window_size(self) -> tuple[int, int]:
        if self._size is not None:
            return self._size
        w, h = self._s.window_size()
        w, h = int(w), int(h)
        if not (0 < w < 10000 and 0 < h < 10000):
            raise ValueError(f"window_size 异常：{(w, h)}（WDA 可能已降级，需重跑 start_wda.sh）")
        self._size = (w, h)
        return self._size

    def terminate_app(self, bundle: str) -> bool:
        """杀掉 App（卡在退不出的全屏页时的最后手段），成功返回 True。"""
        try:
            self._s.app_terminate(bundle)
            return True
        except Exception:  # noqa: BLE001
            return False

    def foreground_bundle(self) -> str | None:
        try:
            return (self._s.app_current() or {}).get("bundleId")
        except Exception:  # noqa: BLE001
            return None


# WDA 抓 AX 是在被测 App 主线程上遍历无障碍树，默认参数在 X 信息流上要 17-22 s，会把主线程按死到
# 触发 X 自己的看门狗（com.twitter.TLTWatchdog）abort。真机实测（iPhone 11 / X 12.21）：
#   深度 30 → 18.9s | 深度 25 → 16.2s | **深度 20 → 2.6s（仍含 Cell 与正文）** | 深度 15 → 2.0s（无 Cell）
# 深度 20 是拿到完整内容的最省点位；waitForIdleTimeout 归零是因为信息流有自动播放视频永不空闲。
WDA_SNAPSHOT_SETTINGS = {
    "snapshotMaxDepth": 20,
    "snapshotMaxChildren": 100,
    "waitForIdleTimeout": 0,
    "animationCoolOffTimeout": 0,
    "customSnapshotTimeout": 30,
}


class SBDeviceManager(DeviceManager):
    def _new_session(self):
        import wda
        from engine.device import WDA_URL
        # 这些设置是**按会话**生效的：必须先建会话、再往这个会话上设，否则新会话一建就回默认值
        session = wda.Client(WDA_URL).session()
        try:
            session.appium_settings(dict(WDA_SNAPSHOT_SETTINGS))
        except Exception:  # noqa: BLE001 — 设置失败只是慢，不该挡住建会话
            pass
        return session

    def get_driver(self):
        d = super().get_driver()
        if d is None:
            return None
        if not isinstance(d, SBDriver):
            sb = SBDriver(d._s)
            sb._size = d._size
            self._driver = sb
            d = sb
        return d


_manager = SBDeviceManager()


def sb_driver_provider() -> SBDriver | None:
    return _manager.get_driver()


def sb_invalidate() -> None:
    _manager.invalidate()
