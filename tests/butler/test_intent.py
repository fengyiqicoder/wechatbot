"""意图复验（点前 NCC）、安全落点、任务级重试、观察前保前台、条件等待。"""
import time

from butler import paths as P
from butler.humanize import Humanizer
from butler.paths.ctx import Ctx, PathBlocked, safe_point
from tests.butler.test_actions import Driver, make
from tests.butler.test_screen import BOXES, png

BTN = [10, 100, 40, 15]          # 「私信」按钮的矩形（BOXES 里有一块带纹理的方块）


def _intent(svc, shot_png, rect, name="私信"):
    return {"name": name, "tag": "Button", "rect": rect, "shot": svc.save_shot(shot_png)}


def test_coords_tap_with_matching_intent_is_verified(tmp_path):
    d, svc = make(tmp_path)
    r = svc.act({"op": "tap", "x": 30, "y": 107, "intent": _intent(svc, png(boxes=BOXES), BTN)})
    assert r["ok"] and r["intent"]["status"] == "verified" and r["intent"]["sim"] > 0.95
    assert d.calls and d.calls[0][0] == "tap"


def test_coords_tap_with_changed_content_is_blocked_moved(tmp_path):
    d, svc = make(tmp_path)
    other = png(boxes=[b for b in BOXES if b[:2] != (10, 100)] + [(12, 100, 36, 15, (0, 0, 0))])   # 同位置换了内容
    r = svc.act({"op": "tap", "x": 30, "y": 107, "intent": _intent(svc, other, BTN)})
    assert not r["ok"] and r["blocked"]["reason"] == "moved" and d.calls == []
    assert r["blocked"]["detail"]["sim"] < 0.8


def test_coords_tap_relocates_when_target_shifted(tmp_path):
    d, svc = make(tmp_path)
    shifted = png(boxes=[b for b in BOXES if b[:2] != (10, 100)] + [(10, 94, 40, 15, (120, 120, 120))])   # 截图里按钮高 6 pt
    rect = [10, 94, 40, 15]
    r = svc.act({"op": "tap", "x": 30, "y": 101, "intent": _intent(svc, shifted, rect)})
    assert r["ok"] and r["intent"]["status"] == "relocated" and r["intent"]["dy"] == 6
    assert 100 <= d.calls[0][2] <= 115                     # 真正按下的点落在当前按钮上


def test_intent_without_shot_is_unchecked_but_taps(tmp_path):
    d, svc = make(tmp_path)
    r = svc.act({"op": "tap", "x": 30, "y": 107, "intent": {"name": "私信", "rect": BTN, "shot": "nope.png"}})
    assert r["ok"] and r["intent"]["status"] == "unchecked" and d.calls


def test_nav_taps_get_short_delay_and_risky_taps_keep_long(tmp_path):
    from engine.ax import AXElement
    d = Driver()
    d.elements = lambda: [AXElement("Button", "私信", "私信", "", 10, 100, 40, 15),
                          AXElement("Button", "关注 Max", "FollowButton", "", 60, 100, 30, 15)]
    _, svc = make(tmp_path, d)
    r = svc.act({"op": "tap", "target": "私信"})
    assert r["ok"] and r["humanized_ms"] == 300
    r = svc.act({"op": "tap", "target": "关注 Max", "confirm": True})
    assert r["ok"] and r["humanized_ms"] == 800


def test_humanizer_configure_from_settings():
    h = Humanizer(rng=lambda a, b: a, randint=lambda a, b: a)
    h.configure({"human_delay_nav": [0.1, 0.2], "human_scroll_pause": [1, 2], "human_delay": "bad"})
    assert h.pre_delay("nav") == 0.1 and h.after_scroll() == 1.0 and h.pre_delay("tap") == 0.8


def test_safe_point_avoids_button_inside_cell():
    cell = {"name": "row", "tag": "Cell", "rect": [0, 100, 400, 80]}
    like = {"name": "like", "tag": "Button", "rect": [180, 120, 40, 40]}        # 正中间是个赞按钮
    text = {"name": "标题文字", "tag": "StaticText", "rect": [16, 110, 120, 20]}
    pt = safe_point(cell, [cell, like, text])
    assert pt == (76, 120)                                                     # 落在文本中心
    assert safe_point(cell, [cell, text]) == (200, 140)                       # 没有可点子元素就取中心


# ---- 路径层 ----
class _Spec:
    app, name, kind, writes = "wechat", "t", "collect", False
    bundle = "com.tencent.xin"


class _Actions:
    """最小假 ActionService：可编排每次 observe 的返回，记录 act 请求。"""
    def __init__(self, screens=None, fg="com.tencent.xin"):
        self.screens = list(screens or [])
        self.fg = fg
        self.acts = []
        self.depth_override = None
        self.human = Humanizer(rng=lambda a, b: a, randint=lambda a, b: a)
        self.launched = 0
        drv = self

        class _Drv:
            def foreground_bundle(self_): return drv.fg
            def launch_app(self_, b): drv.launched += 1; drv.fg = b
            def terminate_app(self_, b): return True
            def tap_press(self_, *a): pass
        self._drv = _Drv()

    def provider(self): return self._drv
    def is_stale(self, e): return False
    def observe(self, deep):
        d = self.screens.pop(0) if len(self.screens) > 1 else (self.screens[0] if self.screens else {"elements": []})
        return dict(d, deep=deep, screenshot="s.png")
    def act(self, req):
        self.acts.append(req)
        return {"ok": True, "verification": {}, "screen": {}}


def _ctx(acts, job=None):
    return Ctx(acts, None, _Spec(), job if job is not None else {}, sleeper=lambda s: None, clock=time.monotonic)


def test_screen_relaunches_app_when_it_left_foreground():
    acts = _Actions(screens=[{"elements": [{"name": "首页", "tag": "Button", "rect": [0, 0, 10, 10]}]}], fg="com.apple.springboard")
    ctx = _ctx(acts, job={"notes": []})
    d = ctx.screen()
    assert acts.launched == 1 and d["elements"] and any("拉回来" in n["msg"] for n in ctx.job["notes"])


def test_tap_el_sends_intent_and_refinds_on_moved():
    el = {"name": "私信", "tag": "Button", "rect": [10, 100, 40, 15]}
    acts = _Actions(screens=[{"elements": [el], "screenshot": "a.png"}])
    moved = {"ok": False, "blocked": {"reason": "moved", "detail": {"sim": 0.1}}}
    seq = [moved, {"ok": True}]
    acts.act = lambda req: (acts.acts.append(req), seq.pop(0))[1]
    ctx = _ctx(acts, job={"notes": []})
    ctx.screen()
    r = ctx.tap_el(el)
    assert r["ok"] and len(acts.acts) == 2 and acts.acts[0]["intent"]["rect"] == [10, 100, 40, 15]
    assert acts.acts[0]["intent"]["shot"] == "s.png" and acts.acts[1]["intent"]["name"] == "私信"


def test_tap_el_raises_moved_when_element_gone():
    el = {"name": "私信", "tag": "Button", "rect": [10, 100, 40, 15]}
    acts = _Actions(screens=[{"elements": [el]}, {"elements": []}])
    acts.act = lambda req: {"ok": False, "blocked": {"reason": "moved", "detail": {"sim": 0.1}}}
    ctx = _ctx(acts, job={"notes": []})
    ctx.screen()
    try:
        ctx.tap_el(el)
        assert False, "应抛 moved"
    except PathBlocked as b:
        assert b.reason == "moved"


def test_until_polls_fast_then_deep_until_pred():
    tab = {"name": "首页", "tag": "Button", "rect": [0, 0, 10, 10], "value": "1"}
    acts = _Actions(screens=[{"elements": []}, {"elements": []}, {"elements": [tab]}])
    ctx = _ctx(acts)
    d = ctx.until(lambda d: any(e.get("value") == "1" for e in d["elements"]), timeout=5)
    assert d is not None and d["elements"][0]["name"] == "首页"


def test_until_fast_path_ignores_stale_dynamic_memory_elements():
    card = {"name": "旧标题", "tag": "StaticText", "rect": [0, 200, 200, 20], "dynamic": True}
    tab = {"name": "首页", "tag": "Button", "rect": [0, 0, 10, 10], "dynamic": False}
    acts = _Actions(screens=[{"elements": [card, tab], "source": "memory"}, {"elements": [tab], "source": "ax"}])
    ctx = _ctx(acts)
    assert ctx.until(lambda d: any(e["name"] == "旧标题" for e in d["elements"]), timeout=1, poll=0.1, fast=True) is None
    acts2 = _Actions(screens=[{"elements": [card, tab], "source": "memory"}, {"elements": [tab], "source": "ax"}])
    d = _ctx(acts2).until(lambda d: any(e["name"] == "首页" for e in d["elements"]), timeout=1, fast=True)
    assert d is not None and d["source"] == "memory" and [e["name"] for e in d["elements"]] == ["首页"]


def test_tap_el_from_memory_observation_taps_by_name():
    tab = {"name": "首页", "tag": "Button", "rect": [0, 0, 10, 10], "dynamic": False}
    acts = _Actions(screens=[{"elements": [tab], "source": "memory"}])
    ctx = _ctx(acts)
    ctx.until(lambda d: True, timeout=1, fast=True)
    r = ctx.tap_el(tab)
    assert r["ok"] and acts.acts[-1] == {"op": "tap", "target": "首页"}
    acts2 = _Actions(screens=[{"elements": []}])
    t = [0.0]
    ctx2 = Ctx(acts2, None, _Spec(), {}, sleeper=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert ctx2.until(lambda d: False, timeout=2, poll=1) is None


def test_confirmed_tap_marks_wrote():
    acts = _Actions(screens=[{"elements": []}])
    ctx = _ctx(acts)
    ctx.tap(x=1, y=1, confirm=True)
    assert ctx.wrote


# ---- 任务级重试 ----
def _job_client(tmp_path):
    from tests.butler.test_paths import _setup
    return _setup(tmp_path)


def _wait(c, jid):
    j = {}
    for _ in range(200):
        j = c.get(f"/jobs/{jid}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.02)
    return j


def test_job_retries_after_blocked_then_succeeds(tmp_path):
    calls = {"n": 0}

    @P.path("wechat", "_flaky", doc="t")
    def flaky(ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PathBlocked("nav_failed", {"try": calls["n"]})
        ctx.emit({"id": "ok"})

    d, c = _job_client(tmp_path)
    j = _wait(c, c.post("/jobs", json={"app": "wechat", "path": "_flaky"}).json()["id"])
    assert j["status"] == "done" and j["attempt"] == 3 and j["attempts"] == 3 and j["count"] == 1
    assert [e["reason"] for e in j["attempt_errors"]] == ["nav_failed", "nav_failed"]


def test_job_gives_up_after_attempts_and_respects_override(tmp_path):
    @P.path("wechat", "_always", doc="t")
    def always(ctx):
        raise PathBlocked("nav_failed")

    d, c = _job_client(tmp_path)
    j = _wait(c, c.post("/jobs", json={"app": "wechat", "path": "_always", "attempts": 2}).json()["id"])
    assert j["status"] == "blocked" and j["attempt"] == 2 and len(j["attempt_errors"]) == 1


def test_job_does_not_retry_after_write_or_unfixable_reason(tmp_path):
    n = {"w": 0, "q": 0}

    @P.path("wechat", "_wrote", kind="action", writes=True, doc="t")
    def wrote(ctx, mode="send"):
        n["w"] += 1
        ctx.ledger_record("dm", "Max", "hi")
        raise PathBlocked("verify_failed")

    @P.path("wechat", "_quiet", doc="t")
    def quiet(ctx):
        n["q"] += 1
        raise PathBlocked("quiet_hours")

    d, c = _job_client(tmp_path)
    j = _wait(c, c.post("/jobs", json={"app": "wechat", "path": "_wrote", "params": {"mode": "send"}}).json()["id"])
    assert j["status"] == "blocked" and n["w"] == 1
    j = _wait(c, c.post("/jobs", json={"app": "wechat", "path": "_quiet"}).json()["id"])
    assert j["status"] == "blocked" and n["q"] == 1
