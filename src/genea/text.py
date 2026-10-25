"""Text normalization used for name matching and searching."""

from __future__ import annotations

import re
import unicodedata

_EXTRA = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "đ": "d",
                        "Đ": "D", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE"})


PREFIX_MIN = 4  # search words this long also match longer words (declined and feminine forms: Fabik -> Fabikovej)


def normalize(text: str) -> str:
    """Lowercase, strip accents, turn punctuation into single spaces."""
    text = unicodedata.normalize("NFKD", text.translate(_EXTRA))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalize like `normalize`, one character at a time, mapping each result character to its original index."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        piece = unicodedata.normalize("NFKD", ch.translate(_EXTRA))
        piece = "".join(c for c in piece if not unicodedata.combining(c)).lower()
        for c in piece:
            if re.match(r"\w", c):
                out.append(c)
                index.append(i)
            elif out and out[-1] != " ":
                out.append(" ")
                index.append(i)
    return "".join(out), index


def snippet(text: str, needles: list[str], width: int) -> str:
    """At most `width` characters of the original text around the earliest needle match. A needle starts at a word
    boundary; one of PREFIX_MIN+ characters may end inside a word (as a prefix), a shorter one must end the word."""
    norm, index = _normalize_with_map(text)
    best: tuple[int, int] | None = None
    for needle in needles:
        if not needle:
            continue
        tail = "" if len(needle) >= PREFIX_MIN else r"(?!\w)"
        m = re.search(r"(?<!\w)" + re.escape(needle) + tail, norm)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.end())
    n = len(text)
    if best is None:
        return text if n <= width else text[:max(width - 1, 0)] + "…"
    start = index[best[0]]
    end = index[best[1] - 1] + 1
    while end < n and unicodedata.combining(text[end]):
        end += 1
    lo = max(0, (start + end) // 2 - width // 2)
    hi = min(n, lo + width)
    lo = max(0, hi - width)
    if lo > 0:
        lo += 1
    if hi < n:
        hi -= 1
    return ("…" if lo > 0 else "") + text[lo:hi] + ("…" if hi < n else "")
