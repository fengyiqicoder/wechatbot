from engine.ax import AXElement
from butler.memory import MemoryStore
from butler.observe import Observer
from butler.resolve import Resolver
from tests.butler.test_observe import AX, FakeDriver


def make(tmp_path):
    d, m = FakeDriver(), MemoryStore(tmp_path)
    ob = Observer(d, m)
    return d, m, ob, Resolver(ob, m)


def test_memory_hit_verifies_anchor_without_ax(tmp_path):
    d, m, ob, r = make(tmp_path)
    ob.observe(deep=True)
    res = r.resolve("私信", ob.observe(deep=False))
    assert res.status == "ok" and res.method == "memory" and res.rect == [10, 100, 40, 15]
    assert res.confidence >= 0.88 and d.ax_calls == 1


def test_unknown_screen_falls_to_ax_and_writes_memory(tmp_path):
    d, m, ob, r = make(tmp_path)
    res = r.resolve("私信", ob.observe(deep=False))
    assert res.status == "ok" and res.method == "ax" and d.ax_calls == 1 and res.confidence == 0.99
    assert m.find_element(res.observation.screen_id, "私信")


def test_ambiguous_and_not_found(tmp_path):
    d, m, ob, r = make(tmp_path)
    d_els = list(AX) + [AXElement("Button", "关注 Max Feng", "FollowButton", "", 0, 150, 40, 15),
                        AXElement("Button", "正在关注 Max Feng", "FollowButton2", "", 50, 150, 40, 15)]
    d.elements = lambda: d_els
    res = r.resolve("关注", ob.observe(deep=False))
    assert res.status == "ambiguous" and {c["label"] for c in res.candidates} == {"关注 Max Feng", "正在关注 Max Feng"}
    res = r.resolve("不存在的按钮", ob.observe(deep=False))
    assert res.status == "not_found" and res.candidates and res.rect is None


def test_resolve_coords_names_element(tmp_path):
    d, m, ob, r = make(tmp_path)
    ob.observe(deep=True)
    res = r.resolve_coords(20, 105, ob.observe(deep=False))
    assert res.method == "coords" and res.name == "私信"
    res = r.resolve_coords(90, 90, ob.observe(deep=False))
    assert res.method == "coords" and res.name is None and res.confidence == 0
