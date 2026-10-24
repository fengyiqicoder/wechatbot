from butler.paths.wechat import _tab_active


def _screen(**btn):
    return {"elements": [{"tag": "Button", "name": "微信", "label": "微信", "rect": [0, 806, 104, 90], **btn}]}


def test_badge_value_is_not_selection():
    # 有 2 条未读时 value 是角标数，选中与否只看 traits
    assert _tab_active(_screen(value="2", traits="Selected, Button"), "微信")
    assert not _tab_active(_screen(value="1", traits="Button"), "微信")


def test_fallback_without_traits():
    assert _tab_active(_screen(value="1"), "微信")
    assert not _tab_active(_screen(value=""), "微信")
