from butler.memory import MemoryStore

REC = {"id": "x/abc", "bundle": "x", "label": "聊天", "landmarks": {"dark": []},
       "elements": {"私信": {"name": "私信", "label": "私信", "tag": "Button", "rect": [1, 2, 3, 4],
                            "dynamic": False, "anchor": {}, "hits": 0, "fails": 0, "last_verified": 0}}}


def test_save_get_list(tmp_path):
    m = MemoryStore(tmp_path)
    m.save_screen(dict(REC))
    assert m.get_screen("x/abc")["label"] == "聊天"
    assert [s["id"] for s in m.screens("x")] == ["x/abc"] and m.screens("other") == []
    assert m.summary() == {"apps": 1, "screens": 1}


def test_upsert_bump_find_clear(tmp_path):
    m = MemoryStore(tmp_path); m.save_screen(dict(REC))
    m.upsert_elements("x/abc", {"关注": {"name": "关注", "label": "关注", "tag": "Button", "rect": [0, 0, 1, 1],
                                         "dynamic": False, "anchor": {}}})
    assert m.find_element("x/abc", "关注")["tag"] == "Button"
    m.bump("x/abc", "关注", ok=True); m.bump("x/abc", "关注", ok=False)
    e = m.find_element("x/abc", "关注")
    assert e["hits"] == 1 and e["fails"] == 1
    m.clear_app("x")
    assert m.screens() == []


def test_apps_roundtrip(tmp_path):
    m = MemoryStore(tmp_path)
    assert m.load_apps() == {}
    m.save_apps({"com.tencent.xin": "微信"})
    assert m.load_apps() == {"com.tencent.xin": "微信"}
