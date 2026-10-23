from engine.ax import AXElement, parse_elements, find_by_label

SAMPLE = """<?xml version="1.0"?>
<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="设置" x="0" y="0" width="390" height="844" visible="true">
  <XCUIElementTypeStaticText type="XCUIElementTypeStaticText" label="通用" name="通用" x="16" y="200" width="100" height="30" visible="true"/>
  <XCUIElementTypeButton type="XCUIElementTypeButton" label="返回" name="返回" x="10" y="60" width="44" height="44" visible="true"/>
  <XCUIElementTypeOther type="XCUIElementTypeOther" x="0" y="0" width="390" height="844" visible="true"/>
  <XCUIElementTypeStaticText type="XCUIElementTypeStaticText" label="隐藏项" x="16" y="400" width="100" height="30" visible="false"/>
</XCUIElementTypeApplication>"""


def test_parse_returns_labeled_visible_elements():
    els = parse_elements(SAMPLE)
    labels = [e.label or e.name for e in els]
    assert "通用" in labels and "返回" in labels
    assert "隐藏项" not in labels          # visible=false 被过滤
    assert all(isinstance(e, AXElement) for e in els)


def test_element_center():
    els = parse_elements(SAMPLE)
    el = find_by_label(els, "通用")
    assert el.center == (16 + 50, 200 + 15)


def test_find_by_label_exact_beats_contains():
    els = parse_elements(SAMPLE)
    assert find_by_label(els, "返回").tag == "Button"
    assert find_by_label(els, "不存在") is None


def test_parse_garbage_returns_empty():
    assert parse_elements("") == []
    assert parse_elements("<not-xml") == []
