from butler.names import normalize, match


def test_normalize_strips_bidi_and_space():
    assert normalize("⁦‪@Feng_YQ‬⁩ ") == "@Feng_YQ"
    assert normalize("ＡＢＣ") == "ABC"


def test_match_tiers():
    names = ["关注 Max Feng", "正在关注 Max Feng", "私信", "Compose"]
    assert match("私信", names).tier == "exact" and match("私信", names).hits == [2]
    assert match("compose", names).tier == "ci" and match("compose", names).hits == [3]
    m = match("关注", names)
    assert m.tier == "contains" and m.hits == [0, 1]
    m = match("Compsoe", names)
    assert m.tier == "fuzzy" and m.hits == [3]
    m = match("不存在", names)
    assert m.tier == "none" and m.hits == [] and len(m.nearest) <= 5
