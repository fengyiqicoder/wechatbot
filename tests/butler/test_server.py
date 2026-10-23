from fastapi.testclient import TestClient
from butler.server import create_app
from tests.butler.test_actions import Driver

APPS = {"com.atebits.Tweetie2": "X", "com.tencent.xin": "微信"}


class OpenDriver(Driver):
    def __init__(self):
        super().__init__(); self.launched = []; self.urls = []
    def launch_app(self, b): self.launched.append(b)
    def open_url(self, u): self.urls.append(u)


def client(tmp_path, driver="default"):
    d = OpenDriver() if driver == "default" else driver
    app = create_app(driver_provider=lambda: d, root=tmp_path, lister=lambda: APPS,
                     device_info_fn=lambda: {"name": "BroiPhone", "ios": "18.7.8"})
    return d, TestClient(app)


def test_status_and_screen(tmp_path):
    d, c = client(tmp_path)
    s = c.get("/status").json()
    assert s["engine"] == "ok" and s["wda_connected"] and s["device"]["name"] == "BroiPhone" and s["api"].endswith(":8790")
    o = c.get("/screen").json()
    assert o["deep"] and any(e["name"] == "私信" for e in o["elements"])
    assert c.get("/screen?fast=1").json()["source"] == "memory"
    assert c.get(f"/shots/{o['screenshot']}").headers["content-type"] == "image/png"


def test_status_without_device(tmp_path):
    _, c = client(tmp_path, driver=None)
    assert c.get("/status").json()["wda_connected"] is False
    assert c.get("/screen").status_code == 503


def test_act_open_scripts_run(tmp_path):
    d, c = client(tmp_path)
    r = c.post("/open", json={"app": "微信"}).json()
    assert r["ok"] and d.launched == ["com.tencent.xin"]
    assert c.post("/open", json={"app": "不存在"}).json()["blocked"]["reason"] == "not_found"
    r = c.post("/act", json={"op": "tap", "target": "私信"}).json()
    assert r["ok"] and r["resolved"]["name"] == "私信"
    assert c.get("/memory/screens").json()[0]["bundle"] == "x"
    s = c.post("/scripts", json={"name": "试", "last_n": 10}).json()
    assert [st["op"] for st in s["steps"]] == ["launch_app", "tap"]
    assert c.get("/scripts").json()[0]["id"] == s["id"]
    run = c.post(f"/scripts/{s['id']}/run", json={}).json()
    assert run["status"] == "queued" and c.get(f"/runs/{run['id']}").status_code == 200
    assert c.get("/runs/nope").status_code == 404
    assert "微信管家" in c.get("/skill").text
    assert c.delete("/memory/apps/x").json()["ok"] and c.get("/memory/screens").json() == []


def test_settings_get_put(tmp_path):
    _, c = client(tmp_path)
    s = c.get("/settings").json()
    assert s["sleep_window"]["start"] == "23:00"
    out = c.put("/settings", json={"sleep_window": {"start": "03:00", "end": "03:01"}}).json()
    assert out["sleep_window"] == {"start": "03:00", "end": "03:01"}
    assert c.get("/settings").json()["sleep_window"]["end"] == "03:01"


def test_open_recovers_from_stale_session(tmp_path):
    """session 失效时 /open 应作废重建再试一次，而不是抛 500。"""
    class Flaky(OpenDriver):
        def __init__(self):
            super().__init__(); self.calls = 0
        def launch_app(self, b):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("invalid session id: Session does not exist")
            self.launched.append(b)
    d = Flaky()
    app = create_app(driver_provider=lambda: d, root=tmp_path, lister=lambda: APPS,
                     device_info_fn=lambda: {"name": "BroiPhone", "ios": "18.7.8"})
    r = TestClient(app).post("/open", json={"app": "微信"}).json()
    assert r["ok"] and d.launched == ["com.tencent.xin"] and d.calls == 2
