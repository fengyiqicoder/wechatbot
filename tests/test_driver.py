from engine.driver import ActionDriver


class FakeSession:
    def __init__(self, source_results=None):
        self.calls = []
        self._source_results = source_results or ["<x/>"]
        self._source_i = 0

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def swipe(self, x1, y1, x2, y2, duration):
        self.calls.append(("swipe", x1, y1, x2, y2, duration))

    def send_keys(self, text):
        self.calls.append(("send_keys", text))

    def source(self):
        r = self._source_results[min(self._source_i, len(self._source_results) - 1)]
        self._source_i += 1
        self.calls.append(("source",))
        if isinstance(r, Exception):
            raise r
        return r

    def window_size(self):
        return (390, 844)

    def screenshot(self):
        class Img:
            def save(self, buf, fmt):
                buf.write(b"PNGDATA")
        return Img()

    def open_url(self, url):
        self.calls.append(("open_url", url))

    def app_state(self, bundle_id):
        self.calls.append(("app_state", bundle_id))
        return {"value": getattr(self, "state", 1)}

    def app_activate(self, bundle_id):
        self.calls.append(("app_activate", bundle_id))

    def locked(self):
        return getattr(self, "is_locked", False)

    def home(self):
        self.calls.append(("home",))


def test_tap_and_swipe_delegate():
    fs = FakeSession()
    d = ActionDriver(fs, sleeper=lambda s: None)
    d.tap(10, 20)
    d.swipe(1, 2, 3, 4)
    assert ("tap", 10, 20) in fs.calls
    assert ("swipe", 1, 2, 3, 4, 0.3) in fs.calls


def test_source_retries_then_gives_up():
    fs = FakeSession(source_results=[RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    waits = []
    d = ActionDriver(fs, sleeper=waits.append)
    assert d.source() == ""
    assert fs.calls.count(("source",)) == 3
    assert waits == [5, 5]


def test_source_recovers_on_second_try():
    fs = FakeSession(source_results=[RuntimeError("boom"), "<ok/>"])
    d = ActionDriver(fs, sleeper=lambda s: None)
    assert d.source() == "<ok/>"


def test_elements_parses_source():
    xml = '<XCUIElementTypeApplication x="0" y="0" width="390" height="844" visible="true">' \
          '<XCUIElementTypeButton label="返回" x="10" y="60" width="44" height="44" visible="true"/>' \
          '</XCUIElementTypeApplication>'
    fs = FakeSession(source_results=[xml])
    d = ActionDriver(fs, sleeper=lambda s: None)
    els = d.elements()
    assert any(e.label == "返回" for e in els)


def test_screenshot_png_returns_bytes():
    d = ActionDriver(FakeSession(), sleeper=lambda s: None)
    assert d.screenshot_png() == b"PNGDATA"


def test_open_url_and_locked_delegate():
    fs = FakeSession()
    d = ActionDriver(fs, sleeper=lambda s: None)
    d.open_url("twitter://post?message=hi")
    assert ("open_url", "twitter://post?message=hi") in fs.calls
    assert d.locked() is False
    fs.is_locked = True
    assert d.locked() is True


def test_launch_app_skips_when_already_foreground():
    fs = FakeSession()
    fs.state = 4
    d = ActionDriver(fs, sleeper=lambda s: None)
    d.launch_app("com.atebits.Tweetie2")
    assert ("app_activate", "com.atebits.Tweetie2") not in fs.calls
    fs.state = 1
    d.launch_app("com.atebits.Tweetie2")
    assert ("app_activate", "com.atebits.Tweetie2") in fs.calls


def test_window_size_is_cached_after_first_call():
    fs = FakeSession()
    calls = []
    fs.window_size = lambda: (calls.append(1), (414, 896))[1]
    d = ActionDriver(fs, sleeper=lambda s: None)
    assert d.window_size() == (414, 896)
    assert d.window_size() == (414, 896)
    assert calls == [1]


def test_home_delegates_to_client_homescreen():
    fs = FakeSession()
    d = ActionDriver(fs, sleeper=lambda s: None)
    d.home()
    assert ("home",) in fs.calls
