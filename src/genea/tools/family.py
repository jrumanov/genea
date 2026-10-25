"""Sides of one document: the census sheets x_a.md / x_b.md, the scans x172a1.md / x172b2.md."""

from __future__ import annotations

import re

_SIDE = re.compile(r"(?:_[ab]|(?<=\d)[ab]\d)$", re.IGNORECASE)  # x_a, x_B, x172a1 (not kniha1, tab1)


def family_key(path: str) -> str:
    """The same key for every side of one document; a path without a side marker is its own key."""
    stem, dot, ext = path.rpartition(".")
    if not dot or "/" in ext:
        return _SIDE.sub("", path)
    return _SIDE.sub("", stem) + dot + ext
