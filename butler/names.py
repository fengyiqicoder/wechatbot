"""元素名归一化与分层匹配。纯函数，无 IO。"""
from __future__ import annotations
import difflib
import re
import unicodedata
from dataclasses import dataclass, field

_BIDI = re.compile("[⁦-⁩‪-‮‎‏]")
FUZZY_MIN = 0.75


def normalize(s: str) -> str:
    s = _BIDI.sub("", s or "")
    s = unicodedata.normalize("NFKC", s)
    return " ".join(s.split())


@dataclass
class Match:
    tier: str
    hits: list[int] = field(default_factory=list)
    nearest: list[int] = field(default_factory=list)


def match(target: str, names: list[str]) -> Match:
    t = normalize(target)
    norm = [normalize(n) for n in names]
    if not t:
        return Match("none")
    exact = [i for i, n in enumerate(norm) if n == t]
    if exact:
        return Match("exact", exact)
    ci = [i for i, n in enumerate(norm) if n.lower() == t.lower()]
    if ci:
        return Match("ci", ci)
    contains = [i for i, n in enumerate(norm) if n and t.lower() in n.lower()]
    if contains:
        return Match("contains", contains)
    scored = sorted(((difflib.SequenceMatcher(None, t.lower(), n.lower()).ratio(), i)
                     for i, n in enumerate(norm) if n), reverse=True)
    fuzzy = [i for r, i in scored if r >= FUZZY_MIN]
    if fuzzy:
        return Match("fuzzy", fuzzy, [i for _, i in scored[:5]])
    return Match("none", [], [i for _, i in scored[:5]])
