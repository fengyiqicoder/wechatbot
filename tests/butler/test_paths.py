import time

from butler import paths as P
from butler.gates import pre_gates
from butler.humanize import RateLimiter
from butler.paths.ctx import PathBlocked
from tests.butler.test_server import client


class FgDriver:
    """给路径层用的假驱动：foreground_bundle 可控，其余继承测试用 Driver。"""


def _setup(tmp_path):
    from tests.butler.test_server import OpenDriver
    d = OpenDriver()
    d.fg = "com.tencent.xin"
    d.foreground_bundle = lambda: d.fg
    d.launch_app = lambda b: setattr(d, "fg", b) or d.launched.append(b)
    _, c = client(tmp_path, driver=d)
    return d, c


def _register_fake():
    @P.path("wechat", "_fake_collect", doc="test", params={"n": "条数"})
    def fake(ctx, n=3):
        for i in range(n):
            ctx.emit({"id": f"m{i}", "text": f"msg{i}"})
        ctx.note("done")

    @P.path("wechat", "_fake_send", kind="action", writes=True, params={"to": "谁", "text": "内容"})
    def fake_send(ctx, to, text, mode="draft", allow_repeat=False):
        ctx.draft_or_go(mode, f"发给 {to}: {text}", {"to": to, "text": text})
        ctx.ledger_guard("dm", to, allow_repeat)
        ctx.ledger_record("dm", to, text)
        return {"sent": True, "to": to}


def test_paths_listing_and_collect_job(tmp_path):
    _register_fake()
    d, c = _setup(tmp_path)
    ps = c.get("/paths?app_=wechat").json()
    assert any(p["name"] == "_fake_collect" and p["params"] == {"n": "条数"} for p in ps)
    j = c.post("/jobs", json={"app": "wechat", "path": "_fake_collect", "params": {"n": 2}}).json()
    for _ in range(50):
        j = c.get(f"/jobs/{j['id']}").json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j["status"] == "done" and j["count"] == 2 and j["notes"][-1]["msg"] == "done"
    items = c.get(f"/jobs/{j['id']}/items").json()
    assert [i["id"] for i in items] == ["m0", "m1"] and items[0]["app"] == "wechat"
    assert c.get("/jobs").json()[0]["id"] == j["id"]
    assert c.post("/jobs", json={"app": "wechat", "path": "nope"}).status_code == 404
    # 续跑：已抓的 id 不再出现
    r = c.post(f"/jobs/{j['id']}/resume").json()
    for _ in range(50):
        r = c.get(f"/jobs/{r['id']}").json()
        if r["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert r["resume_of"] == j["id"] and r["count"] == 0


def test_launches_app_when_not_foreground(tmp_path):
    _register_fake()
    d, c = _setup(tmp_path)
    d.fg = "com.apple.springboard"
    j = c.post("/jobs", json={"app": "wechat", "path": "_fake_collect", "params": {"n": 1}}).json()
    for _ in range(50):
        j = c.get(f"/jobs/{j['id']}").json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j["status"] == "done" and "com.tencent.xin" in d.launched


def test_write_path_draft_then_send_and_ledger(tmp_path):
    _register_fake()
    d, c = _setup(tmp_path)
    j = c.post("/jobs", json={"app": "wechat", "path": "_fake_send", "params": {"to": "Max", "text": "hi"}}).json()
    for _ in range(50):
        j = c.get(f"/jobs/{j['id']}").json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j["status"] == "done" and j["result"]["draft"]["summary"] == "发给 Max: hi"
    drafts = c.get("/drafts").json()
    assert len(drafts) == 1 and drafts[0]["status"] == "pending"
    s = c.post(f"/drafts/{drafts[0]['id']}/send").json()
    for _ in range(50):
        s = c.get(f"/jobs/{s['id']}").json()
        if s["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert s["status"] == "done" and s["result"] == {"sent": True, "to": "Max"}
    led = c.get("/ledger?app_=wechat").json()
    assert len(led) == 1 and led[0]["target"] == "Max" and led[0]["kind"] == "dm"
    # 台账挡住第二次
    j2 = c.post("/jobs", json={"app": "wechat", "path": "_fake_send",
                               "params": {"to": "Max", "text": "again", "mode": "send"}}).json()
    for _ in range(50):
        j2 = c.get(f"/jobs/{j2['id']}").json()
        if j2["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j2["status"] == "blocked" and j2["error"]["reason"] == "already_sent"
    # 黑名单
    c.put("/ledger/lists", json={"app": "wechat", "block": ["Bob"]})
    j3 = c.post("/jobs", json={"app": "wechat", "path": "_fake_send",
                               "params": {"to": "Bob", "text": "x", "mode": "send"}}).json()
    for _ in range(50):
        j3 = c.get(f"/jobs/{j3['id']}").json()
        if j3["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j3["status"] == "blocked" and j3["error"]["reason"] == "blocklisted"
    assert c.delete("/drafts/nope").status_code == 404


def test_watch_creates_drafts_for_new_items(tmp_path):
    _register_fake()
    d, c = _setup(tmp_path)
    w = c.post("/watch", json={"app": "wechat", "path": "_fake_collect", "params": {"n": 2}, "every_s": 9999,
                               "then": {"path": "_fake_send", "params": {"text": "auto"}, "map": {"to": "text"}}}).json()
    assert c.post(f"/watch/{w['id']}/run").json()["new"] == 2
    assert c.post(f"/watch/{w['id']}/run").json()["new"] == 0          # 第二轮没有新的
    st = c.get("/watch").json()
    assert st["watches"][0]["new_total"] == 2
    drafts = c.get("/drafts").json()
    assert len(drafts) == 2 and drafts[0]["params"]["to"] == "msg0" and drafts[0]["params"]["mode"] == "draft"
    assert c.delete(f"/watch/{w['id']}").json()["ok"]


def test_act_busy_while_job_running(tmp_path):
    @P.path("wechat", "_slow")
    def slow(ctx):
        time.sleep(0.6)

    d, c = _setup(tmp_path)
    j = c.post("/jobs", json={"app": "wechat", "path": "_slow"}).json()
    time.sleep(0.1)
    assert c.post("/act", json={"op": "tap", "target": "私信"}).json()["blocked"]["reason"] == "busy"
    assert c.post("/jobs", json={"app": "wechat", "path": "_slow"}).status_code == 409
    assert c.post(f"/jobs/{j['id']}/cancel").json()["ok"]


def test_quiet_bypass_until_expires():
    class D:
        def locked(self): return False
    st = {"sleep_window": {"start": "23:00", "end": "07:30"}, "quiet_bypass_until": time.time() + 60}
    req = {"op": "tap", "confirm": True}                      # 输入不算写操作；静默窗管的是带 confirm 的写动作
    assert pre_gates(D(), req, "关注 Max", "b", RateLimiter(), st, "23:30") is None
    st["quiet_bypass_until"] = time.time() - 1
    assert pre_gates(D(), req, "关注 Max", "b", RateLimiter(), st, "23:30").reason == "quiet_hours"


def test_ctx_coord_tap_requires_foreground(tmp_path):
    from butler.paths.ctx import Ctx
    d, c = _setup(tmp_path)
    actions = c.app.state.actions
    spec = P.get("wechat", "_fake_collect") or (_register_fake() or P.get("wechat", "_fake_collect"))
    ctx = Ctx(actions, None, spec, {"seen": set()}, sleeper=lambda s: None)
    d.fg = "com.apple.springboard"
    try:
        ctx.tap(x=10, y=10)
        assert False, "应当拒绝盲点"
    except PathBlocked as b:
        assert b.reason == "not_in_app"


def test_sequence_advances_and_stops_when_replied(tmp_path):
    _register_fake()

    @P.path("wechat", "_fake_msgs")
    def fake_msgs(ctx, contact):
        if contact == "Bob":
            ctx.emit({"id": "r1", "who": "Bob", "text": "已回复"})
        ctx.emit({"id": "m1", "who": "我", "text": "hi"})

    d, c = _setup(tmp_path)
    s = c.post("/sequences", json={
        "app": "wechat", "name": "跟进", "mode": "send",
        "steps": [{"path": "_fake_send", "params": {"to": "{target}", "text": "第一步"}},
                  {"path": "_fake_send", "params": {"to": "{target}", "text": "第二步", "allow_repeat": True},
                   "after_days": 0,
                   "only_if": {"path": "_fake_msgs", "params": {"contact": "{target}"}, "no_item_from": "{target}"}}],
        "targets": ["Alice", "Bob"]}).json()
    assert s["targets"]["Alice"]["step"] == 0
    r1 = c.post(f"/sequences/{s['id']}/tick").json()
    assert r1["advanced"] == "Alice" and r1["status"] == "done"
    r2 = c.post(f"/sequences/{s['id']}/tick").json()
    assert r2["advanced"] == "Bob" and r2["status"] == "done"
    r3 = c.post(f"/sequences/{s['id']}/tick").json()          # Alice 第二步：未回复 → 发
    assert r3["advanced"] == "Alice" and r3["status"] == "done"
    r4 = c.post(f"/sequences/{s['id']}/tick").json()          # Bob 第二步：已回复 → 跳过并结束
    assert r4["advanced"] == "Bob" and r4.get("skipped") == "对方已回复"
    st = c.get("/sequences").json()[0]["targets"]
    assert st["Alice"]["done"] and st["Bob"]["done"]
    assert c.post(f"/sequences/{s['id']}/tick").json()["advanced"] is None
    assert c.post("/sequences", json={"app": "wechat", "name": "x", "steps": [{"path": "nope"}], "targets": []}).status_code == 404
    assert c.delete(f"/sequences/{s['id']}").json()["ok"]
