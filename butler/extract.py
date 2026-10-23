"""从元素表提取可读内容：文本块（操作 2）与列表条目（操作 3）。纯函数。"""
from __future__ import annotations
_TEXT_TAGS = {"StaticText", "TextView"}
# 自绘 App（Telegram/Swiftgram）的消息气泡是带文字的 Other，不是 StaticText：
# 只认 StaticText 会把整段对话读成空。这些容器不算正文，排除掉。
_NOT_TEXT = {"Application", "Window", "Keyboard", "Key", "NavigationBar", "TabBar"}


def _inside(inner, outer) -> bool:
    x, y, w, h = inner
    X, Y, W, H = outer
    return x >= X and y >= Y and x + w <= X + W and y + h <= Y + H


def _is_leaf_text(e: dict, elements: list[dict]) -> bool:
    """带文字的 Other 只有在「不包含别的带文字元素」时才算一段正文，避免把整屏容器算成一段。"""
    if e["tag"] in _NOT_TEXT or not (e.get("name") or e.get("label") or "").strip():
        return False
    x, y, w, h = e["rect"]
    if w <= 0 or h <= 0 or h > 400:
        return False
    for o in elements:
        if o is e or not (o.get("name") or o.get("label") or "").strip():
            continue
        ox, oy, ow, oh = o["rect"]
        if ox >= x and oy >= y and ox + ow <= x + w and oy + oh <= y + h and (ow * oh) < (w * h):
            return False
    return True


def text_blocks(elements: list[dict]) -> list[dict]:
    out = []
    for e in elements:
        if e["tag"] in _TEXT_TAGS:
            text = (e.get("value") or e.get("label") or "").strip()
        elif e["tag"] == "Other" and _is_leaf_text(e, elements):
            text = (e.get("name") or e.get("label") or "").strip()
        else:
            continue
        if text:
            out.append({"text": text, "rect": e["rect"]})
    return sorted(out, key=lambda b: (b["rect"][1], b["rect"][0]))


def _row_like(e: dict, width_hint: int) -> bool:
    """Telegram/Swiftgram 这类自绘 UI 的列表行是通栏 Other，name 里用换行分隔标题与预览，
    没有 Cell 标签。判定：整宽、行高合理、带文字。"""
    if e["tag"] != "Other":
        return False
    x, y, w, h = e["rect"]
    text = (e.get("name") or e.get("label") or "").strip()
    return bool(text) and x <= 2 and w >= width_hint * 0.9 and 40 <= h <= 140


def list_items(elements: list[dict]) -> list[dict]:
    cells = sorted([e for e in elements if e["tag"] == "Cell"], key=lambda e: (e["rect"][1], e["rect"][0]))
    if not cells:
        width_hint = max((e["rect"][2] for e in elements), default=0)
        rows = [e for e in elements if _row_like(e, width_hint)]
        rows.sort(key=lambda e: e["rect"][1])
        out = []
        for i, r in enumerate(rows, 1):
            text = (r.get("name") or r.get("label") or "").strip()
            parts = [t.strip() for t in text.split("\n") if t.strip()]
            out.append({"index": i, "rect": r["rect"], "label": parts[0] if parts else text, "texts": parts})
        return out
    texts = text_blocks(elements)
    items = []
    for i, c in enumerate(cells, 1):
        items.append({"index": i, "rect": c["rect"], "label": c.get("label", ""),
                      "texts": [t["text"] for t in texts if _inside(t["rect"], c["rect"])]})
    return items
