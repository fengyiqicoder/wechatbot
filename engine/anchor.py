"""截图锚点：裁剪、灰度相似度、base64 编解码。回放期只靠它判断界面是否正确。"""
from __future__ import annotations
import base64
import io

from PIL import Image, ImageChops, ImageStat


def decode_png(png: bytes) -> Image.Image:
    """把整张截图解码一次（约 20 ms）。要连裁很多块时先调这个，再把 Image 传给 crop。"""
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


def crop(png: "bytes | Image.Image", rect: list[int], scale: float) -> Image.Image:
    x, y, w, h = rect
    img = png if isinstance(png, Image.Image) else Image.open(io.BytesIO(png))
    box = (round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale))
    return img.crop(box).convert("L")


def similarity(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b)
    mean = ImageStat.Stat(diff).mean[0]
    return 1.0 - mean / 255.0


def encode(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def decode(s: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(s))).convert("L")


def make_anchor(png: bytes, rect: list[int], scale: float) -> str:
    return encode(crop(png, rect, scale))


# ---- 意图复验：归一化互相关（NCC）----
# 均值差相似度对「白底黑字」的行完全无效：真机实测同一行偏移 4 pt 与换了一行内容都是 0.88。
# NCC 能分开：相同 1.0、偏移 4 pt 0.2-0.65、换内容 ≈0.0。点前复验与重定位只用 NCC。
NCC_THR = 0.8
_SHRINK = 2       # 复验前把裁剪缩小到 1/2，纯 Python 算 NCC 才够快（一次约 2-4 ms）


def _vec(img: Image.Image) -> list[int]:
    w, h = img.size
    if _SHRINK > 1 and w >= 2 * _SHRINK and h >= 2 * _SHRINK:
        img = img.resize((w // _SHRINK, h // _SHRINK))
    return list(img.getdata())


def _ncc_vec(pa: list[int], pb: list[int]) -> float | None:
    n = min(len(pa), len(pb))
    if n == 0:
        return None
    pa, pb = pa[:n], pb[:n]
    ma, mb = sum(pa) / n, sum(pb) / n
    va = sum((x - ma) * (x - ma) for x in pa)
    vb = sum((y - mb) * (y - mb) for y in pb)
    if va < 1e-6 or vb < 1e-6:                      # 纯色块：无纹理，NCC 无定义
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(pa, pb)) / ((va * vb) ** 0.5)


def looks_same(a: Image.Image, b: Image.Image, md_thr: float, ncc_thr: float = NCC_THR,
               a_vec: list[int] | None = None, b_vec: list[int] | None = None) -> tuple[bool, float]:
    """锚点 / 地标复核：均值差过阈值，且（有纹理时）NCC 也过阈值。返回 (是否相同, 均值差相似度)。
    只看均值差会把「白底换了一行字」当成相同；只看 NCC 又在纯色块上无定义，两者合用。
    a_vec / b_vec：调用方缓存好的像素向量（同一屏 100 多条记录共用同一块底栏地标时，别反复算）。"""
    sim = similarity(a, b)
    if sim < md_thr:
        return False, sim
    if a.size != b.size:
        b = b.resize(a.size)
        b_vec = None
    v = _ncc_vec(a_vec if a_vec is not None else _vec(a), b_vec if b_vec is not None else _vec(b))
    if v is not None and v < ncc_thr:
        return False, sim
    return True, sim


def vec(img: Image.Image) -> list[int]:
    """给 looks_same 预算像素向量用（与内部缩放一致）。"""
    return _vec(img)


def ncc(a: Image.Image, b: Image.Image) -> float | None:
    """两张灰度裁剪的归一化互相关，[-1, 1]；任一方无纹理返回 None。"""
    if a.size != b.size:
        b = b.resize(a.size)
    return _ncc_vec(_vec(a), _vec(b))


def locate(png: bytes, template: Image.Image, rect: list[int], scale: float,
           radius: int = 12, step: int = 2) -> tuple[float | None, int, int]:
    """在 png 的 rect 附近找 template：先按 step 粗搜 ±radius，再在最佳点周围 ±(step-1) 细搜。
    文字行只有 1-2 pt 粗的笔画，粗搜步长必须 ≤2，否则真机上 18 pt 高的标题行会漏掉（实测 step=4 只得 0.30）。
    返回 (最佳 NCC, dx, dy)，dx/dy 单位 pt。template 无纹理时返回 (None, 0, 0)。"""
    tv = _vec(template)
    if _ncc_vec(tv, tv) is None:
        return None, 0, 0
    x, y, w, h = rect
    img = Image.open(io.BytesIO(png)).convert("L")
    W, H = img.size

    def score(dx: int, dy: int) -> float:
        bx, by = round((x + dx) * scale), round((y + dy) * scale)
        if bx < 0 or by < 0 or bx + round(w * scale) > W or by + round(h * scale) > H:
            return -2.0
        c = img.crop((bx, by, bx + round(w * scale), by + round(h * scale)))
        if c.size != template.size:
            c = c.resize(template.size)
        v = _ncc_vec(tv, _vec(c))
        return -2.0 if v is None else v

    best, bdx, bdy = score(0, 0), 0, 0
    for dy in range(-radius, radius + 1, step):
        for dx in range(-radius, radius + 1, step):
            s = score(dx, dy)
            if s > best:
                best, bdx, bdy = s, dx, dy
    cx, cy = bdx, bdy
    for dy in range(cy - step + 1, cy + step):
        for dx in range(cx - step + 1, cx + step):
            if (dx, dy) == (cx, cy):
                continue
            s = score(dx, dy)
            if s > best:
                best, bdx, bdy = s, dx, dy
    return (None if best <= -2.0 else best), bdx, bdy
