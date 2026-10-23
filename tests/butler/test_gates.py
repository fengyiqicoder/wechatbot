from butler.gates import Blocked, pre_gates, risk_kind
from butler.humanize import RateLimiter


class D:
    def __init__(self, locked=False, alert=None): self._l, self._a = locked, alert
    def locked(self): return self._l
    def alert_info(self): return self._a


SET = {"sleep_window": {"start": "23:00", "end": "07:30"}}


def test_risk_kind():
    assert risk_kind("关注 Max Feng") == "follow" and risk_kind("已发送") == "dm" and risk_kind("私信") is None
    assert risk_kind("Like") == "like" and risk_kind(None) is None


def test_gate_order_locked_alert_confirm_quiet_rate():
    rl = RateLimiter({"follow": 0})
    assert pre_gates(D(locked=True), {"op": "tap"}, "私信", "x", rl, SET, "12:00").reason == "locked"
    alert_els = [{"tag": "Alert", "name": "", "label": "", "rect": [20, 300, 374, 200]},
                 {"tag": "StaticText", "name": "允许?", "label": "允许?", "rect": [40, 320, 300, 20]},
                 {"tag": "Button", "name": "不允许", "label": "不允许", "rect": [40, 450, 100, 40]},
                 {"tag": "Button", "name": "允许", "label": "允许", "rect": [240, 450, 100, 40]},
                 {"tag": "Button", "name": "外面", "label": "外面", "rect": [0, 0, 50, 50]}]
    b = pre_gates(D(), {"op": "tap"}, "私信", "x", rl, SET, "12:00", elements=alert_els)
    assert b.reason == "alert" and b.detail == {"title": "允许?", "buttons": ["不允许", "允许"]}
    assert pre_gates(D(), {"op": "tap"}, "关注 Max Feng", "x", rl, SET, "12:00").reason == "needs_confirm"
    assert pre_gates(D(), {"op": "tap", "confirm": True}, "关注 Max Feng", "x", rl, SET, "23:30").reason == "quiet_hours"
    assert pre_gates(D(), {"op": "tap", "confirm": True}, "关注 Max Feng", "x", rl, SET, "12:00").reason == "rate_limit"
    assert pre_gates(D(), {"op": "tap"}, "私信", "x", rl, SET, "23:30") is None
    assert pre_gates(D(), {"op": "type", "text": "hi"}, None, "x", rl, SET, "23:30") is None   # 输入不算写：发布要再点确认键


def test_list_row_preview_text_does_not_trigger_confirm():
    """微信的列表行叫 "Max,回复,13:29"，预览里的「回复」不该被当成发评论而要求确认。"""
    rl = RateLimiter()
    assert pre_gates(D(), {"op": "tap"}, "Max,回复,13:29", "wx", rl, SET, "12:00", target_tag="Cell") is None
    # 真正的按钮仍然要确认
    assert pre_gates(D(), {"op": "tap"}, "回复", "wx", rl, SET, "12:00", target_tag="Button").reason == "needs_confirm"
