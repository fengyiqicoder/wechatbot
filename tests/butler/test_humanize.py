from butler.humanize import DEFAULT_LIMITS, Humanizer, RateLimiter, in_quiet_hours


def test_pre_delay_and_reading_pause():
    h = Humanizer(rng=lambda a, b: a, randint=lambda a, b: a)
    assert h.pre_delay("tap") == 0.8
    assert h.pre_delay("read", text_len=200) == 0.8 + 3.0
    assert h.pre_delay("read", text_len=10000) == 0.8 + 8.0


def test_tap_point_inside_30_70():
    h = Humanizer(rng=lambda a, b: (a + b) / 2, randint=lambda a, b: (a + b) // 2)
    x, y = h.tap_point([100, 200, 50, 20])
    assert 115 <= x <= 135 and 206 <= y <= 214


def test_type_chunks_3_to_8():
    h = Humanizer(randint=lambda a, b: 3)
    assert h.type_chunks("abcdefgh") == ["abc", "def", "gh"]


def test_quiet_hours_wraps_midnight():
    w = {"start": "23:00", "end": "07:30"}
    assert in_quiet_hours("23:30", w) and in_quiet_hours("02:00", w) and in_quiet_hours("07:29", w)
    assert not in_quiet_hours("07:30", w) and not in_quiet_hours("12:00", w)


def test_rate_limiter_hourly_window():
    t = [1000.0]
    rl = RateLimiter({"like": 2}, clock=lambda: t[0])
    assert rl.check("x", "like") is None
    rl.record("x", "like"); rl.record("x", "like")
    assert rl.check("x", "like") == 3600
    t[0] += 3601
    assert rl.check("x", "like") is None
    assert rl.check("x", "unknown_kind") is None
    assert DEFAULT_LIMITS["dm"] == 20
