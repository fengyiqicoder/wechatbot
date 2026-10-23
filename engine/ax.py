"""AX 树解析：WDA XML source -> 结构化元素列表。迁移自 DogPlanet get_els()。"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class AXElement:
    tag: str
    label: str
    name: str
    value: str
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


def parse_elements(xml_source: str, hidden_within: tuple[int, int] | None = None) -> list["AXElement"]:
    """hidden_within=(w, h)：把 visible=false 但矩形整个落在屏幕内的元素也收进来。
    微信朋友圈相册等列表会把屏上可见的行误报为 visible=false；只给只读抽取用，点击定位不要传。"""
    if not xml_source:
        return []
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        if (el.get("visible") or "true") != "true":
            if hidden_within is None:
                continue
            x, y, w, h = (int(el.get(k) or 0) for k in ("x", "y", "width", "height"))
            if not (w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= hidden_within[0] and y + h <= hidden_within[1]):
                continue
        label = (el.get("label") or "").strip()
        name = (el.get("name") or "").strip()
        if not label and not name:
            continue
        out.append(AXElement(
            tag=el.tag.replace("XCUIElementType", ""),
            label=label, name=name, value=(el.get("value") or "").strip(),
            x=int(el.get("x") or 0), y=int(el.get("y") or 0),
            width=int(el.get("width") or 0), height=int(el.get("height") or 0),
        ))
    return out


def find_by_label(els: list[AXElement], text: str) -> AXElement | None:
    for e in els:
        if e.label == text or e.name == text:
            return e
    for e in els:
        if text in e.label or text in e.name:
            return e
    return None
