from butler.sbdriver import SBDriver


class FakeAlert:
    def __init__(self, exists): self._e = exists
    @property
    def exists(self): return self._e
    @property
    def text(self): return "允许“X”访问相册？"
    def buttons(self): return ["不允许", "允许"]


class FakeSel:
    def __init__(self, value): self._v = value
    def get(self, timeout=None):
        if self._v is None:
            raise RuntimeError("no element")
        class E: value = self._v
        return E()


class FakeSession:
    def __init__(self, alert=False, value="Feng_YQ"):
        self.posts, self.swipes, self.alert, self._value = [], [], FakeAlert(alert), value
        class H:
            def post(_s, path, payload): self.posts.append((path, payload))
        self._session_http = H()
    def swipe(self, x1, y1, x2, y2, duration=0): self.swipes.append((x1, y1, x2, y2, duration))
    def __call__(self, **kw): return FakeSel(self._value)
    def app_current(self): return {"bundleId": "com.atebits.Tweetie2"}


def test_tap_press_posts_w3c_actions():
    s = FakeSession(); d = SBDriver(s)
    d.tap_press(10, 20, ms=80)
    path, payload = s.posts[0]
    assert path == "/actions"
    acts = payload["actions"][0]["actions"]
    assert acts[0]["x"] == 10 and acts[2] == {"type": "pause", "duration": 80}


def test_alert_info():
    assert SBDriver(FakeSession(alert=False)).alert_info() is None
    assert SBDriver(FakeSession(alert=True)).alert_info() == {"title": "允许“X”访问相册？", "buttons": ["不允许", "允许"]}


def test_find_value_and_foreground():
    assert SBDriver(FakeSession()).find_value("SearchField", "搜索") == "Feng_YQ"
    assert SBDriver(FakeSession(value=None)).find_value("SearchField", "搜索") is None
    assert SBDriver(FakeSession()).foreground_bundle() == "com.atebits.Tweetie2"


def test_swipe_and_tap_reject_non_finite():
    import math, pytest
    d = SBDriver(FakeSession())
    with pytest.raises(ValueError):
        d.tap_press(math.inf, 10)
    with pytest.raises(ValueError):
        d.swipe_ms(0, 0, math.nan, 5)


def test_swipe_uses_dragfromto_with_int_coords():
    s = FakeSession(); SBDriver(s).swipe_ms(10.9, 20.1, 30.5, 40.0, 400)
    assert s.swipes == [(10, 20, 30, 40, 0.4)]


def test_new_session_applies_snapshot_settings(monkeypatch):
    from butler import sbdriver as M
    applied = {}

    class FakeSession:
        def appium_settings(self, v): applied.update(v)

    SESSION = FakeSession()

    class FakeClient:
        def __init__(self, url): pass
        def session(self): return SESSION

    import wda
    monkeypatch.setattr(wda, "Client", FakeClient)
    assert M.SBDeviceManager()._new_session() is SESSION
    assert applied["snapshotMaxDepth"] == 20 and applied["waitForIdleTimeout"] == 0
