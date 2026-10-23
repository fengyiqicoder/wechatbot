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


def parse_elements(xml_source: str) -> list["AXElement"]:
    if not xml_source:
        return []
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        if (el.get("visible") or "true") != "true":
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
