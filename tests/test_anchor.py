import io
from PIL import Image, ImageDraw

from engine import anchor


def png_with_square(color=(255, 0, 0), box=(10, 10, 30, 30), size=(100, 100)) -> bytes:
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).rectangle(box, fill=color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_crop_uses_scale_and_returns_grayscale():
    img = anchor.crop(png_with_square(), [10, 10, 20, 20], scale=1.0)
    assert img.size == (20, 20)
    assert img.mode == "L"
    big = anchor.crop(png_with_square(size=(200, 200), box=(20, 20, 60, 60)), [10, 10, 20, 20], scale=2.0)
    assert big.size == (40, 40)


def test_similarity_identical_is_one_and_different_is_low():
    a = anchor.crop(png_with_square(), [10, 10, 20, 20], 1.0)
    b = anchor.crop(png_with_square(), [10, 10, 20, 20], 1.0)
    c = anchor.crop(png_with_square(color=(255, 255, 255)), [10, 10, 20, 20], 1.0)  # 白底无方块
    assert anchor.similarity(a, b) == 1.0
    assert anchor.similarity(a, c) < 0.8


def test_similarity_resizes_mismatched_sizes():
    a = anchor.crop(png_with_square(), [10, 10, 20, 20], 1.0)
    b = anchor.crop(png_with_square(size=(200, 200), box=(20, 20, 60, 60)), [10, 10, 20, 20], 2.0)
    assert anchor.similarity(a, b) > 0.95


def test_encode_decode_roundtrip_and_make_anchor():
    a = anchor.crop(png_with_square(), [10, 10, 20, 20], 1.0)
    s = anchor.encode(a)
    assert isinstance(s, str) and len(s) > 20
    back = anchor.decode(s)
    assert back.size == a.size and anchor.similarity(a, back) == 1.0
    assert anchor.decode(anchor.make_anchor(png_with_square(), [10, 10, 20, 20], 1.0)).size == (20, 20)
