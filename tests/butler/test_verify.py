from butler.observe import Observation
from butler.verify import check_expect, diff_ratio, screen_changed, wait_stable
from tests.butler.test_screen import png


def test_diff_and_changed():
    a, b = png(), png(boxes=[(0, 0, 100, 100, (0, 0, 0))])
    assert diff_ratio(a, a) == 0 and diff_ratio(a, b) > 0.3
    assert not screen_changed(a, a) and screen_changed(a, b)


def test_wait_stable_returns_when_two_frames_agree():
    frames = [png(boxes=[(0, 0, 10, 10, (0, 0, 0))]), png(boxes=[(0, 0, 50, 50, (0, 0, 0))]), png(), png()]
    t = [0.0]
    out = wait_stable(shoot=lambda: frames.pop(0), sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert out == png() and len(frames) == 0


def test_check_expect():
    obs = Observation("x", "x/1", "", "dark", True, [{"name": "正在关注 Max Feng", "label": "正在关注 Max Feng",
                                                         "value": "", "tag": "Button", "rect": [0, 0, 1, 1]},
                                                        {"name": "", "label": "你好世界", "value": "你好世界",
                                                         "tag": "StaticText", "rect": [0, 0, 1, 1]}])
    assert check_expect("screen_change", obs, changed=True) == (True, "")
    assert check_expect("same_screen", obs, changed=True)[0] is False
    assert check_expect({"element": "正在关注 Max Feng"}, obs, True) == (True, "")
    assert check_expect({"element": "关注 Max Feng"}, obs, True)[0] is False
    assert check_expect({"text": "你好"}, obs, True)[0] is True
    assert check_expect({"text": "再见"}, obs, True)[0] is False
