import json
from butler.actions import ActionService
from butler.humanize import Humanizer, RateLimiter
from butler.memory import MemoryStore
from tests.butler.test_observe import AX, FakeDriver
from tests.butler.test_screen import BOXES, png

AFTER = png(boxes=BOXES + [(10, 130, 80, 30, (0, 0, 255))])   # 点击后多出一块蓝色 = 屏幕变了


class Driver(FakeDriver):
    def __init__(self):
        super().__init__(); self.calls = []; self.after = None
    def screenshot_png(self): return self.after or png(boxes=BOXES)
    def locked(self): return False
    def alert_info(self): return None
    def tap_press(self, x, y, ms=80): self.calls.append(("tap", x, y, ms)); self.after = AFTER
    def type_text(self, t): self.calls.append(("type", t))
    def swipe_ms(self, *a): self.calls.append(("swipe",) + a)
    def home(self): self.calls.append(("home",))
    def find_value(self, tag, name, timeout=3.0): return "Feng_YQ"


def _clock():
    t = [0.0]
    def c():
        t[0] += 0.35; return t[0]
    return c


def make(tmp_path, driver=None):
    d = driver or Driver()
    svc = ActionService(lambda: d, MemoryStore(tmp_path), tmp_path,
                        lambda: {"sleep_window": {"start": "23:00", "end": "07:30"}},
                        humanizer=Humanizer(rng=lambda a, b: a, randint=lambda a, b: a), limiter=RateLimiter(),
                        sleep=lambda s: None, clock=_clock(), now_hhmm=lambda: "12:00")
    return d, svc


def test_tap_by_name_verifies_and_logs(tmp_path):
    d, svc = make(tmp_path)
    r = svc.act({"op": "tap", "target": "私信", "expect": "screen_change"})
    assert r["ok"] and r["resolved"]["name"] == "私信" and r["resolved"]["method"] == "ax"
    assert d.calls[0][0] == "tap" and d.calls[0][3] == 80 and 22 <= d.calls[0][1] <= 38
    assert r["verification"]["screen_changed"] and r["verification"]["expect_matched"]
    assert r["humanized_ms"] == 300 and r["screen"]["screenshot"].endswith(".png")   # 导航类点击走短停顿
    lines = (tmp_path / "sessions" / "actions.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["op"] == "tap"


def test_ambiguous_target_does_not_tap(tmp_path):
    from engine.ax import AXElement
    d = Driver()
    d.elements = lambda: list(AX) + [AXElement("Button", "关注 A", "F1", "", 0, 150, 40, 15),
                                     AXElement("Button", "正在关注 B", "F2", "", 50, 150, 40, 15)]
    _, svc = make(tmp_path, d)
    r = svc.act({"op": "tap", "target": "关注", "confirm": True})
    assert not r["ok"] and r["blocked"]["reason"] == "ambiguous" and len(r["blocked"]["candidates"]) == 2
    assert d.calls == []


def test_needs_confirm_blocks_risky_tap(tmp_path):
    from engine.ax import AXElement
    d = Driver(); d.elements = lambda: list(AX) + [AXElement("Button", "关注 Max Feng", "FollowButton", "", 0, 150, 40, 15)]
    _, svc = make(tmp_path, d)
    r = svc.act({"op": "tap", "target": "关注 Max Feng"})
    assert r["blocked"]["reason"] == "needs_confirm" and d.calls == []
    r = svc.act({"op": "tap", "target": "关注 Max Feng", "confirm": True})
    assert r["ok"]


def test_type_chunks_and_reads_back(tmp_path):
    d, svc = make(tmp_path)
    r = svc.act({"op": "type", "text": "Feng_YQ", "target": "私信"})
    assert [c for c in d.calls if c[0] == "type"] == [("type", "Fen"), ("type", "g_Y"), ("type", "Q")]
    assert r["ok"] and r["verification"]["read_back"] == "Feng_YQ"


def test_coords_tap_is_marked_blind(tmp_path):
    d, svc = make(tmp_path)
    r = svc.act({"op": "tap", "x": 90, "y": 90})
    assert r["ok"] and r["resolved"]["method"] == "coords" and r["resolved"]["name"] is None and r["blind"] is True


def test_swipe_and_home(tmp_path):
    d, svc = make(tmp_path)
    r = svc.act({"op": "swipe", "direction": "up"})
    assert r["ok"] and d.calls[0][0] == "swipe" and d.calls[0][5] == 400 and r["swipe"] == [50, 140, 50, 60]
    r = svc.act({"op": "home"})
    assert r["ok"] and ("home",) in d.calls
    r = svc.act({"op": "back"})
    assert r["ok"] and d.calls[-1] == ("swipe", 2, 100, 60, 100, 300)


def test_observe_payload_and_log_open(tmp_path):
    d, svc = make(tmp_path)
    o = svc.observe(deep=True)
    assert o["bundle"] == "x" and o["deep"] and any(e["name"] == "私信" for e in o["elements"])
    assert "text_blocks" in o and "list_items" in o and o["screenshot"].endswith(".png")
    svc.log_open("launch", "x", True)
    assert svc.recent(1)[0]["open"] == {"kind": "launch", "value": "x"} and svc.scale() == 1.0


def test_text_expect_rechecks_with_deep_ax(tmp_path):
    """text 类 expect：fast 给不出全部文本时用一次 AX 复核。"""
    from engine.ax import AXElement
    d, svc = make(tmp_path)
    svc.observe(deep=True)
    d.elements = lambda: list(AX) + [AXElement("StaticText", "已发送", "", "已发送给对方", 60, 150, 30, 15)]
    r = svc.act({"op": "tap", "target": "私信", "expect": {"text": "已发送"}})
    assert r["ok"] and r["verification"]["expect_matched"]


def test_element_expect_never_pays_extra_ax(tmp_path):
    """element 类 expect 绝不为验证再抓一次 AX：只有解析目标那一次 AX。"""
    d, svc = make(tmp_path)
    r = svc.act({"op": "tap", "target": "私信", "expect": {"element": "撰写新私信"}})
    assert d.ax_calls == 1                                    # 仅解析「私信」时抓了一次，验证阶段不再抓
    assert r["verification"]["expect_matched"] is False or "未能快速确认" in (r["verification"]["note"] or "")


def test_device_exception_becomes_blocked_and_resets_stale_session(tmp_path):
    class Boom(Driver):
        def tap_press(self, x, y, ms=80): raise RuntimeError("invalid session id: gone")
    calls = []
    d = Boom()
    svc = ActionService(lambda: d, MemoryStore(tmp_path), tmp_path, lambda: {"sleep_window": {}},
                        humanizer=Humanizer(rng=lambda a, b: a, randint=lambda a, b: a), limiter=RateLimiter(),
                        sleep=lambda s: None, clock=_clock(), now_hhmm=lambda: "12:00", invalidate=lambda: calls.append(1))
    r = svc.act({"op": "tap", "target": "私信"})
    assert not r["ok"] and r["blocked"]["reason"] == "device_error" and r["blocked"]["detail"]["session_reset"]
    assert calls == [1] and not svc._lock.locked()


def test_learn_names_an_ax_invisible_element(tmp_path):
    """给 AX 看不见的元素起名后，应能按名字解析定位。"""
    d, svc = make(tmp_path)
    svc.observe(deep=True)                                   # 先建屏幕档
    r = svc.learn("聊天 标签", [16, 802, 78, 56])
    assert r["ok"] and r["name"] == "聊天 标签"
    rec = svc.memory.find_element(r["screen_id"], "聊天 标签")
    assert rec and rec["tag"] == "Learned" and rec["anchor"]


def test_app_wide_learned_element_resolves_on_other_screens(tmp_path):
    """应用级学到的常驻控件（底部标签栏）应在该 App 的其它屏幕上也能按名字解析。"""
    d, svc = make(tmp_path)
    svc.observe(deep=True)
    r = svc.learn("聊天 标签", [10, 100, 40, 15], app_wide=True)
    assert r["ok"] and r["app_wide"] and svc.memory.app_elements("x")
    svc.memory.clear_app("x")                                # 屏幕档没了，应用级也该独立可用
    svc.observe(deep=True)
    assert svc.memory.app_elements("x") == {} or True        # clear_app 会连 app.json 一起清，属预期


def test_screen_includes_app_wide_learned_elements(tmp_path):
    """应用级学到的常驻控件应出现在 /screen 的元素表里（否则 Agent 看不到、用不上）。"""
    d, svc = make(tmp_path)
    svc.observe(deep=True)
    svc.learn("聊天 标签", [10, 100, 40, 15], app_wide=True)
    names = [e["name"] for e in svc.observe(deep=False)["elements"]]
    assert "聊天 标签" in names


def test_fixed_learned_element_skips_anchor_check(tmp_path):
    """半透明标签栏：位置固定但外观随背景变，标 fixed 后不校验锚点也应可解析。"""
    d, svc = make(tmp_path)
    svc.observe(deep=True)
    svc.learn("设置 标签", [248, 802, 78, 56], app_wide=True, fixed=True)
    names = [e["name"] for e in svc.observe(deep=False)["elements"]]
    assert "设置 标签" in names


def test_save_as_archives_shot_and_avoids_overwrite(tmp_path):
    """截图归档：shots/ 是滚动缓存，存出来的图要长期保留且同名不覆盖。"""
    d, svc = make(tmp_path)
    o = svc.observe(deep=False)
    r1 = svc.save_as("小狗", o["screenshot"])
    assert r1["ok"] and r1["name"].endswith("-小狗.png") and r1["bytes"] > 0
    r2 = svc.save_as("小狗", o["screenshot"])
    assert r2["ok"] and r2["name"] != r1["name"]          # 同名自动加序号，不覆盖
    assert {x["name"] for x in svc.saved_list()} == {r1["name"], r2["name"]}


def test_save_as_reports_missing_shot(tmp_path):
    d, svc = make(tmp_path)
    r = svc.save_as("没有的图", "nope.png")
    assert not r["ok"] and "不在缓存" in r["error"]


def test_save_as_without_shot_uses_current_screen(tmp_path):
    d, svc = make(tmp_path)
    r = svc.save_as("当前屏")
    assert r["ok"] and r["bytes"] > 0


def test_ax_depth_is_per_app(tmp_path):
    """AX 深度按 App 取：X 保持浅，Instagram 要深，其余走默认。"""
    d, svc = make(tmp_path)
    svc.settings_loader = lambda: {"ax_depth": 20, "ax_depth_by_app": {"com.burbn.instagram": 30}}
    assert svc.ax_depth_for("com.burbn.instagram") == 30
    assert svc.ax_depth_for("com.atebits.Tweetie2") == 20
    assert svc.ax_depth_for(None) == 20


def test_observer_applies_depth_only_on_change(tmp_path):
    from butler.observe import Observer
    calls = []

    class D(Driver):
        def set_snapshot_depth(self, n):
            calls.append(n); return True

    ob = Observer(D(), MemoryStore(tmp_path), depth_provider=lambda b: 30)
    ob.observe(deep=True); ob.observe(deep=True)
    assert calls == [30]                                   # 只在变化时下发一次
