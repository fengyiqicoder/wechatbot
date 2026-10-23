"""意图复验用的 NCC 与重定位。"""
import io

from PIL import Image, ImageDraw

from engine import anchor


def _text_row_png(y_text: int = 40, text_x: int = 12, size=(200, 120), other: bool = False) -> bytes:
    """白底、一行「文字」（几段黑色小方块模拟字形）。other=True 换一种笔画布局代表换了一行内容。"""
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    glyphs = [(0, 3, 4), (8, 2, 6), (16, 5, 3), (24, 1, 7), (34, 4, 4)] if not other else \
        [(2, 1, 7), (10, 6, 2), (14, 2, 5), (28, 3, 3), (36, 5, 6)]
    for gx, gw, gh in glyphs:
        d.rectangle((text_x + gx, y_text, text_x + gx + gw, y_text + gh), fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


RECT = [8, 36, 60, 14]


def test_ncc_separates_same_shift_and_other_where_mean_diff_cannot():
    base = _text_row_png()
    a = anchor.crop(base, RECT, 1.0)
    same = anchor.ncc(a, anchor.crop(base, RECT, 1.0))
    shifted = anchor.ncc(a, anchor.crop(_text_row_png(y_text=46), RECT, 1.0))
    other = anchor.ncc(a, anchor.crop(_text_row_png(other=True), RECT, 1.0))
    assert same > 0.99 and shifted < 0.7 and other < 0.7
    # 均值差把「换了内容」判得比 NCC 高得多——这正是不能用它做复验的原因
    assert anchor.similarity(a, anchor.crop(_text_row_png(other=True), RECT, 1.0)) > other + 0.3


def test_ncc_none_for_flat_regions():
    flat = _text_row_png()
    a = anchor.crop(flat, [100, 5, 40, 10], 1.0)
    assert anchor.ncc(a, a) is None


def test_locate_finds_shifted_row():
    base = _text_row_png()
    tpl = anchor.crop(base, RECT, 1.0)
    moved = _text_row_png(y_text=46, text_x=15)             # 下移 6 pt、右移 3 pt
    best, dx, dy = anchor.locate(moved, tpl, RECT, 1.0, radius=12)
    assert best > 0.95 and (dx, dy) == (3, 6)
    best0, dx0, dy0 = anchor.locate(base, tpl, RECT, 1.0)
    assert best0 > 0.99 and (dx0, dy0) == (0, 0)


def test_locate_reports_low_score_when_content_changed():
    base = _text_row_png()
    tpl = anchor.crop(base, RECT, 1.0)
    best, _, _ = anchor.locate(_text_row_png(other=True), tpl, RECT, 1.0)
    assert best < anchor.NCC_THR
