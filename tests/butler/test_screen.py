import io
from PIL import Image, ImageDraw
from butler import screen as S


def png(size=(100, 200), boxes=(), bg=(255, 255, 255), texture=True):
    """画测试图。真实地标裁剪里必有图标与边缘，所以默认给每个方块加一条反色内纹，
    否则纯色裁剪 stddev=0 会被 has_texture 判定为不可用作地标（那是生产上要的行为）。"""
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    for (x, y, w, h, c) in boxes:
        d.rectangle((x, y, x + w - 1, y + h - 1), fill=c)
        if texture:
            inv = tuple(255 - v for v in c)
            d.rectangle((x + 2, y + 2, x + max(3, w // 2), y + max(3, h // 2)), fill=inv)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def el(name, tag, rect, label=""):
    return {"name": name, "label": label or name, "value": "", "tag": tag, "rect": list(rect)}


ELS = [el("NavigationBackButton", "Button", (5, 5, 20, 20)), el("TFNTitleView", "Other", (40, 5, 20, 20), "聊天"),
       el("Timelines_tab", "Other", (0, 180, 25, 20)), el("DM_tab", "Other", (75, 180, 25, 20)),
       el("Max Feng。 @Feng_YQ", "Cell", (0, 40, 100, 30)), el("私信", "Button", (10, 100, 40, 15))]
BOXES = [(5, 5, 20, 20, (200, 0, 0)), (40, 5, 20, 20, (0, 200, 0)), (0, 180, 25, 20, (0, 0, 200)),
         (75, 180, 25, 20, (0, 0, 0)), (10, 100, 40, 15, (120, 120, 120))]


def test_appearance_and_classification():
    assert S.appearance(png(bg=(10, 10, 10))) == "dark" and S.appearance(png()) == "light"
    assert S.is_identifier("Timelines_tab") and not S.is_identifier("关注 Max Feng")
    assert S.is_dynamic("Cell", "Max Feng") and not S.is_dynamic("Button", "私信") and not S.is_dynamic("Other", "DM_tab")


def test_pick_landmarks_top_and_bottom():
    lm = S.pick_landmarks(ELS, win_h=200)
    assert [l["name"] for l in lm] == ["NavigationBackButton", "TFNTitleView", "Timelines_tab", "DM_tab"]
    assert S.pick_landmarks(ELS[4:], win_h=200) == []


def test_build_and_identify_roundtrip():
    rec = S.build_record("x", png(boxes=BOXES), 1.0, ELS)
    assert rec["id"].startswith("x/") and rec["label"] == "聊天"
    assert "私信" in rec["elements"] and rec["elements"]["私信"]["anchor"]["light"]
    assert "Max Feng。 @Feng_YQ" in rec["elements"] and rec["elements"]["Max Feng。 @Feng_YQ"]["dynamic"]
    sid, score = S.identify(png(boxes=BOXES), 1.0, [rec])
    assert sid == rec["id"] and score >= 0.9
    other = png(boxes=[(5, 5, 20, 20, (0, 0, 0)), (40, 5, 20, 20, (0, 0, 0))])
    assert S.identify(other, 1.0, [rec])[0] is None


def test_keyboard_region_excluded_from_landmarks():
    els = ELS[:2] + [el("UIKeyboardLayoutStar Preview", "Other", (0, 120, 100, 60)),
                     el("dictation", "Button", (70, 185, 20, 15)), el("下一个键盘", "Button", (5, 185, 20, 15))]
    lm = S.pick_landmarks(els, win_h=200)
    assert [l["name"] for l in lm] == ["NavigationBackButton", "TFNTitleView"]
    assert S.keyboard_top(els) == 120 and S.keyboard_top(ELS) is None


def test_landmarks_dedupe_same_rect_and_prefer_discriminating_second():
    els = [el("NavigationBarDashButton", "Button", (5, 5, 20, 20)), el("TUIAvatarImageView.1", "Button", (5, 5, 20, 20)),
           el("搜索", "SearchField", (40, 5, 50, 20)), el("Timelines_tab", "Other", (0, 180, 25, 20)),
           el("DM_tab", "Other", (75, 180, 25, 20))]
    assert [l["name"] for l in S.pick_landmarks(els, 200)] == ["NavigationBarDashButton", "搜索", "Timelines_tab", "DM_tab"]


def test_identify_prefers_more_matched_landmarks_over_similarity():
    rec = S.build_record("x", png(boxes=BOXES), 1.0, ELS)                       # 4 地标
    three = {**rec, "id": "x/three", "landmarks": {"light": rec["landmarks"]["light"][:3]}}
    assert S.identify(png(boxes=BOXES), 1.0, [three, rec])[0] == rec["id"]
    # 只有 3 个地标出现在屏幕上（第 4 个被遮）：3/3 的记录应胜过 3/4 的记录
    partial = png(boxes=BOXES[:3] + [(75, 180, 25, 20, (255, 255, 255))])
    assert S.identify(partial, 1.0, [rec, three])[0] == "x/three"


def test_blank_landmarks_rejected():
    """纯色地标必须被拒绝：否则深色 App 之间会互相误判成同一屏。"""
    blank = png(boxes=[(5, 5, 20, 20, (0, 0, 0)), (40, 5, 20, 20, (0, 0, 0)),
                       (0, 180, 25, 20, (0, 0, 0)), (75, 180, 25, 20, (0, 0, 0))],
                bg=(0, 0, 0), texture=False)
    assert S.build_record("blankapp", blank, 1.0, ELS) is None      # 全纯黑 → 建不出记录
    assert S.has_texture(png(boxes=BOXES), [5, 5, 20, 20], 1.0)     # 有纹理的照常可用
    assert not S.has_texture(blank, [5, 5, 20, 20], 1.0)


def test_geometric_landmarks_for_self_drawn_apps():
    """AX 无可用地标的自绘 App（如 Swiftgram）应退回几何区域建档，且不同画面得到不同屏幕 id。"""
    naked = [el("SomeRoot", "Other", (0, 0, 100, 200))]        # 顶底栏没有任何具名元素
    a = png(boxes=[(20, 45, 60, 40, (200, 40, 40)), (5, 110, 40, 55, (40, 200, 40)),
                   (55, 110, 40, 55, (40, 40, 200))])
    b = png(boxes=[(20, 45, 60, 40, (40, 200, 200)), (5, 110, 40, 55, (200, 200, 40)),
                   (55, 110, 40, 55, (200, 40, 200))])
    ra = S.build_record("app.swiftgram.ios", a, 1.0, naked)
    rb = S.build_record("app.swiftgram.ios", b, 1.0, naked)
    assert ra and rb and ra["id"] != rb["id"]                  # 画面不同 → 屏幕 id 不同
    assert [l["name"] for l in ra["landmarks"]["light"]][0].startswith("chrome:")
    assert S.identify(a, 1.0, [ra, rb])[0] == ra["id"]         # 能认回来
