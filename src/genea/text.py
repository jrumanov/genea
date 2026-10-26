"""Text normalization used for name matching and searching."""

from __future__ import annotations

import bisect
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


_END = re.compile(r"[.?!]|\n[ \t\r]*\n")  # sentence end: punctuation or a blank line


def _window(start: int, end: int, n: int, width: int) -> tuple[int, int]:
    """The `width`-character window of an n-character text centred on [start, end), minus one character at each cut
    end to make room for the "…"."""
    lo = max(0, (start + end) // 2 - width // 2)
    hi = min(n, lo + width)
    lo = max(0, hi - width)
    if lo > 0:
        lo += 1
    if hi < n:
        hi -= 1
    return lo, hi


def _find_matches(text: str, needles: list[str]) -> list[tuple[int, int, str]]:
    """All needle matches as (start, end, needle) in the original text, sorted. A needle starts at a word boundary;
    one of PREFIX_MIN+ characters may end inside a word (as a prefix), a shorter one must end the word."""
    norm, index = _normalize_with_map(text)
    n = len(text)
    matches: list[tuple[int, int, str]] = []
    for needle in dict.fromkeys(needles):
        if not needle:
            continue
        tail = "" if len(needle) >= PREFIX_MIN else r"(?!\w)"
        for m in re.finditer(r"(?<!\w)" + re.escape(needle) + tail, norm):
            start = index[m.start()]
            end = index[m.end() - 1] + 1
            while end < n and unicodedata.combining(text[end]):
                end += 1
            matches.append((start, end, needle))
    matches.sort()
    return matches


def match_span(text: str, needles: list[str], max_chars: int = 400) -> tuple[int, int] | None:
    """(start, end) in `text` of the sentence (ended by . ? ! or a blank line) holding the first needle match, matched
    like `snippet`; a longer sentence is cut to a `max_chars` window around the match. None when nothing matches."""
    matches = _find_matches(text, needles)
    if not matches:
        return None
    mstart, mend, _ = matches[0]
    n = len(text)
    lo = 0
    for m in _END.finditer(text, 0, mstart):
        lo = m.end()
    nxt = _END.search(text, mend)
    hi = nxt.end() if nxt and text[nxt.start()] in ".?!" else (nxt.start() if nxt else n)
    while lo < hi and text[lo].isspace():
        lo += 1
    while hi > lo and text[hi - 1].isspace():
        hi -= 1
    if hi - lo > max_chars:
        lo2 = max(lo, min((mstart + mend) // 2 - max_chars // 2, hi - max_chars))
        lo, hi = lo2, lo2 + max_chars
    return lo, hi


def snippet(text: str, needles: list[str], width: int) -> str:
    """At most `width` characters of the original text around needle matches: the window, centred on one match, that
    shows the most distinct needles; ties go to the longer needles (rarer words), then to the earliest window.
    Matching rules: see `_find_matches`."""
    n = len(text)
    matches = _find_matches(text, needles)
    if not matches:
        return text if n <= width else text[:max(width - 1, 0)] + "…"
    starts = [s for s, _, _ in matches]
    best: tuple[int, int, int] | None = None  # (distinct needles, their total length, -lo)
    best_window = (0, 0)
    for start, end, _ in matches:
        lo, hi = _window(start, end, n, width)
        inside = matches[bisect.bisect_left(starts, lo):bisect.bisect_left(starts, hi)]
        shown = {needle for _, e, needle in inside if e <= hi}
        score = (len(shown), sum(len(needle) for needle in shown), -lo)
        if best is None or score > best:
            best, best_window = score, (lo, hi)
    lo, hi = best_window
    return ("…" if lo > 0 else "") + text[lo:hi] + ("…" if hi < n else "")
