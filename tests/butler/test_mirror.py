"""镜像：最近一帧、空闲才真抓、最近落点、/mirror 与 /mirror.png。"""
import time

from tests.butler.test_actions import Driver, make
from tests.butler.test_server import client


def test_observe_and_act_update_last_frame_and_tap(tmp_path):
    d, svc = make(tmp_path)
    assert svc.last_frame["ts"] == 0.0
    svc.observe(deep=True)
    assert svc.last_frame["png"] and svc.last_frame["source"] == "observe"
    r = svc.act({"op": "tap", "target": "私信"})
    assert r["ok"] and svc.last_frame["source"] == "action"
    assert svc.last_tap["name"] == "私信" and svc.last_tap["x"] > 0
    info = svc.mirror_info()
    assert info["tap"]["name"] == "私信" and info["busy"] is False and info["foreground"] == "x"


def test_mirror_frame_grabs_fresh_only_when_idle_and_stale(tmp_path):
    d, svc = make(tmp_path)
    calls = {"n": 0}
    orig = d.screenshot_png

    def counting():
        calls["n"] += 1
        return orig()
    d.screenshot_png = counting
    d.foreground_bundle = lambda: "x"
    f1 = svc.mirror_frame(max_age_s=0.5)
    assert f1["png"] and calls["n"] == 1 and f1["source"] == "mirror"
    f2 = svc.mirror_frame(max_age_s=0.5)                      # 0.5 s 内不重抓
    assert calls["n"] == 1 and f2["ts"] == f1["ts"]
    svc.last_frame["ts"] = time.time() - 5
    with svc._lock:                                            # 引擎正忙：给旧帧，不抓
        f3 = svc.mirror_frame(max_age_s=0.5)
    assert calls["n"] == 1 and f3["source"] == "mirror"
    assert svc.last_state["locked"] is False


def test_mirror_endpoints(tmp_path):
    d, c = client(tmp_path)
    assert c.get("/mirror.png").status_code == 200
    m = c.get("/mirror").json()
    assert m["wda_connected"] and m["device"]["name"] == "BroiPhone" and m["engine_busy"] is False and m["job"] is None
    assert m["age_ms"] is not None and m["foreground"] == "x" and m["size_pt"] == [100, 200]
    r = c.get("/mirror.png")
    assert r.headers["content-type"] == "image/png" and r.headers["cache-control"] == "no-store"


def test_mirror_png_404_without_device(tmp_path):
    _, c = client(tmp_path, driver=None)
    assert c.get("/mirror.png").status_code == 404
    assert c.get("/mirror").json()["wda_connected"] is False


def test_reconnect_resets_session_and_reports(tmp_path):
    d, c = client(tmp_path)
    r = c.post("/reconnect").json()
    assert r["ok"] and r["wda_connected"] and r["device"]["name"] == "BroiPhone"
    _, c2 = client(tmp_path, driver=None)
    r = c2.post("/reconnect").json()
    assert not r["ok"] and "WDA" in r["hint"]


def test_mirror_grab_is_not_reported_as_busy(tmp_path):
    d, svc = make(tmp_path)
    seen = {}
    orig = d.screenshot_png

    def peek():
        seen["busy_during_grab"] = svc.busy()
        return orig()
    d.screenshot_png = peek
    d.foreground_bundle = lambda: "x"
    svc.mirror_frame(max_age_s=0)
    assert seen["busy_during_grab"] is False and svc.busy() is False
