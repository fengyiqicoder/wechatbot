"""屏幕身份 = 顶栏/底栏的固定部件小截图（地标）。识别只用截图（约 0.3 s），不抓 AX。"""
from __future__ import annotations
import hashlib
import io
import re

from PIL import Image, ImageStat

from engine import anchor
from butler.names import normalize

LANDMARK_THR, ELEMENT_THR = 0.9, 0.88
LANDMARK_NCC_THR = 0.7           # 地标 NCC 下限（比元素复验的 0.8 松：地标可能带角标/未读数）
# 纯色裁剪（大片纯黑/纯白）跟任何同色区域都「相似」，拿来当地标会跨 App 误判——
# 实测把 Swiftgram 的界面认成了 Shadowrocket 的屏幕。地标必须有纹理：灰度标准差要够。
MIN_LANDMARK_STDDEV = 8.0
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]*$")
_DYNAMIC_TAGS = {"Cell", "StaticText", "TextView", "Image", "Table", "ScrollView", "Window", "Application",
                 "Other", "CollectionView", "WebView", "Key"}
_CHROME = 110  # 顶栏 / 底栏判定带宽（pt）


def appearance(png: bytes) -> str:
    im = Image.open(io.BytesIO(png)).convert("L").resize((32, 64))
    return "dark" if ImageStat.Stat(im).mean[0] < 100 else "light"


def is_identifier(name: str) -> bool:
    return bool(name) and bool(_IDENT.match(name))


def is_dynamic(tag: str, name: str) -> bool:
    if tag in _DYNAMIC_TAGS:
        return not is_identifier(name)
    return False


def _in_chrome(rect, win_h) -> str | None:
    x, y, w, h = rect
    if y + h <= _CHROME:
        return "top"
    if y >= win_h - _CHROME:
        return "bottom"
    return None


def keyboard_top(elements: list[dict]) -> int | None:
    """软键盘容器的顶边（pt）；键盘是临时的，它以下的一切都不能当地标。"""
    tops = [e["rect"][1] for e in elements if "Keyboard" in (e.get("name") or "") or e["tag"] == "Keyboard"]
    return min(tops) if tops else None


def pick_landmarks(elements: list[dict], win_h: int) -> list[dict]:
    top, bottom = [], []
    kb = keyboard_top(elements)
    for e in elements:
        r = e["rect"]
        if not e.get("name") or e["tag"] in ("StaticText", "Cell", "Window", "Application", "Key"):
            continue
        if kb is not None and r[1] >= kb:
            continue
        if not (12 <= r[2] <= 350 and 12 <= r[3] <= 100):
            continue
        band = _in_chrome(r, win_h)
        if band == "top":
            top.append(e)
        elif band == "bottom":
            bottom.append(e)

    def key(e):
        return (0 if is_identifier(e["name"]) else 1, -(e["rect"][2] * e["rect"][3]))

    def dedupe(cands):
        seen, out = set(), []
        for e in sorted(cands, key=key):
            r = tuple(e["rect"])
            if r not in seen:
                seen.add(r)
                out.append(e)
        return out[:2]

    picked = dedupe(top) + dedupe(bottom)
    return picked if len(picked) >= 2 else []


def screen_id(bundle: str, landmarks: list[dict]) -> str:
    sig = "\n".join(sorted(f"{l['name']}|{l.get('label', '')}|{l['tag']}" for l in landmarks))
    return f"{bundle}/{hashlib.sha1(sig.encode()).hexdigest()[:8]}"


def screen_label(elements: list[dict]) -> str:
    for e in elements:
        if "TitleView" in (e.get("name") or "") and e.get("label"):
            return e["label"]
    for e in elements:
        if e["tag"] == "StaticText" and e["rect"][1] < 100 and e.get("label"):
            return e["label"]
    return ""


def _crop64(png, rect, scale) -> str:
    return anchor.encode(anchor.crop(png, rect, scale))


def has_texture(png: bytes, rect, scale: float, min_stddev: float = MIN_LANDMARK_STDDEV) -> bool:
    """裁剪区域是否有足够纹理可作地标（纯色块不行）。"""
    try:
        return ImageStat.Stat(anchor.crop(png, list(rect), scale)).stddev[0] >= min_stddev
    except Exception:  # noqa: BLE001
        return False


def _ahash(img) -> str:
    """8x8 均值哈希：给自绘 App 的屏幕算一个内容指纹，用来区分不同屏幕。"""
    small = img.resize((8, 8))
    px = list(small.getdata())
    avg = sum(px) / len(px)
    bits = "".join("1" if v >= avg else "0" for v in px)
    return f"{int(bits, 2):016x}"


def geometric_landmarks(png: bytes, scale: float) -> list[dict]:
    """AX 拿不出地标时（Telegram/Swiftgram 这类自绘 UI 顶底栏无任何可访问元素），
    改用固定几何区域当地标：顶栏中段 + 底栏左右两段，只保留有纹理的。"""
    im = Image.open(io.BytesIO(png))
    w, h = int(im.size[0] / scale), int(im.size[1] / scale)
    cands = [
        ("chrome:top", [int(w * 0.15), 40, int(w * 0.70), 48]),
        ("chrome:bottom-left", [0, h - 96, int(w * 0.5), 60]),
        ("chrome:bottom-right", [int(w * 0.5), h - 96, int(w * 0.5), 60]),
    ]
    return [{"name": n, "label": "", "tag": "Region", "rect": r}
            for n, r in cands if has_texture(png, r, scale)]


def build_record(bundle: str, png: bytes, scale: float, elements: list[dict]) -> dict | None:
    im = Image.open(io.BytesIO(png))
    win_h = int(im.size[1] / scale)
    lms = [l for l in pick_landmarks(elements, win_h) if has_texture(png, l["rect"], scale)]
    geometric = False
    if len(lms) < 2:
        lms = geometric_landmarks(png, scale)
        geometric = True
    if len(lms) < 2:
        return None
    mode = appearance(png)
    if geometric:
        # 几何地标每屏名字都一样，屏幕身份必须来自画面内容指纹，否则全 App 只会有一个屏幕
        fp = "".join(_ahash(anchor.crop(png, lm["rect"], scale)) for lm in lms)
        sid = f"{bundle}/{hashlib.sha1(fp.encode()).hexdigest()[:8]}"
    else:
        sid = screen_id(bundle, lms)
    rec = {"id": sid, "bundle": bundle, "label": screen_label(elements),
           "landmarks": {mode: [{"name": l["name"], "label": l.get("label", ""), "tag": l["tag"],
                                 "rect": l["rect"], "png": _crop64(png, l["rect"], scale)} for l in lms]},
           "elements": {}}
    for e in elements:
        key = normalize(e.get("name") or e.get("label") or "")
        if not key or e["rect"][2] <= 0 or e["rect"][3] <= 0:
            continue
        dyn = is_dynamic(e["tag"], e.get("name") or "")
        rec["elements"][key] = {"name": e.get("name", ""), "label": e.get("label", ""), "tag": e["tag"],
                                "rect": e["rect"], "dynamic": dyn,
                                "anchor": {} if dyn else {mode: _crop64(png, e["rect"], scale)},
                                "hits": 0, "fails": 0, "last_verified": 0}
    return rec


_TPL_CACHE: dict[str, tuple] = {}          # 地标 png(b64) → (灰度图, 像素向量)；文件即真相，进程内缓存解码结果
_TPL_CACHE_MAX = 4000


def _template(b64: str):
    hit = _TPL_CACHE.get(b64)
    if hit is None:
        img = anchor.decode(b64)
        hit = (img, anchor.vec(img))
        if len(_TPL_CACHE) >= _TPL_CACHE_MAX:
            _TPL_CACHE.clear()
        _TPL_CACHE[b64] = hit
    return hit


def identify(png: bytes, scale: float, records: list[dict]) -> tuple[str | None, float]:
    mode = appearance(png)
    best, best_key, best_score = None, (0, 0.0, 0.0), 0.0
    full = anchor.decode_png(png)              # 整张截图只解码一次：之前每裁一块都重新解码 1 MB PNG，识别要 3 s
    crops: dict[tuple, tuple] = {}             # 同一矩形（一百多个屏共用的底栏）只裁一次、只算一次向量
    memo: dict[tuple, tuple] = {}              # (矩形, 地标图) → 比对结果

    def current(rect):
        key = tuple(rect)
        hit = crops.get(key)
        if hit is None:
            img = anchor.crop(full, list(rect), scale)
            hit = (img, anchor.vec(img))
            crops[key] = hit
        return hit

    for rec in records:
        lms = rec.get("landmarks", {}).get(mode) or []
        if len(lms) < 2:
            continue
        # 地标复核同时看均值差与 NCC：顶栏「消息」和「我」这种只差几个字的屏，均值差分不开
        checks = []
        for l in lms:
            memo_key = (tuple(l["rect"]), l["png"])          # 一百多个屏存的是同一块底栏的同一张图：只比一次
            res = memo.get(memo_key)
            if res is None:
                cur_img, cur_vec = current(l["rect"])
                tpl_img, tpl_vec = _template(l["png"])
                res = anchor.looks_same(cur_img, tpl_img, LANDMARK_THR, LANDMARK_NCC_THR, cur_vec, tpl_vec)
                memo[memo_key] = res
            checks.append(res)
        hit = [s for ok, s in checks if ok]
        if len(hit) >= 2 and len(hit) >= 0.6 * len(lms):
            score = sum(hit) / len(hit)
            # 多个屏幕共享顶栏/底栏时：匹配个数多的赢，再比匹配比例，最后才比相似度
            key = (len(hit), len(hit) / len(lms), score)
            if key > best_key:
                best, best_key, best_score = rec["id"], key, score
    return best, best_score
