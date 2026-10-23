from engine.ax import AXElement
from butler.memory import MemoryStore
from butler.observe import Observer
from tests.butler.test_screen import BOXES, png


AX = [AXElement("Button", "返回", "NavigationBackButton", "", 5, 5, 20, 20),
      AXElement("Other", "聊天", "TFNTitleView", "", 40, 5, 20, 20),
      AXElement("Other", "主页 标签", "Timelines_tab", "", 0, 180, 25, 20),
      AXElement("Other", "聊天 标签", "DM_tab", "", 75, 180, 25, 20),
      AXElement("Button", "私信", "私信", "", 10, 100, 40, 15)]


class FakeDriver:
    def __init__(self):
        self.ax_calls = 0
    def screenshot_png(self): return png(boxes=BOXES)
    def window_size(self): return (100, 200)
    def elements(self):
        self.ax_calls += 1; return AX
    def foreground_bundle(self): return "x"


def test_deep_writes_memory_then_fast_hits(tmp_path):
    d, m = FakeDriver(), MemoryStore(tmp_path)
    ob = Observer(d, m)
    o1 = ob.observe(deep=True)
    assert o1.deep and o1.source == "ax" and o1.bundle == "x" and o1.screen_label == "聊天"
    assert {e["name"] for e in o1.elements} >= {"私信", "DM_tab"} and d.ax_calls == 1
    o2 = ob.observe(deep=False)
    assert o2.source == "memory" and o2.screen_id == o1.screen_id and d.ax_calls == 1
    assert any(e["name"] == "私信" and e["rect"] == [10, 100, 40, 15] for e in o2.elements)


def test_fast_unknown_when_no_memory(tmp_path):
    o = Observer(FakeDriver(), MemoryStore(tmp_path)).observe(deep=False)
    assert o.source == "unknown" and o.screen_id is None and o.elements == []
