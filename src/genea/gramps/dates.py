"""GEDCOM dates -> Gramps date objects, and a comparison that ignores server-computed fields."""

from __future__ import annotations

import re
from datetime import date

from genea.ingest.gedcom.dates import MONTHS

_CAL_RE = re.compile(r"@#D([A-Z ]+)@\s*")
_SIMPLE_RE = re.compile(r"^(?:(\d{1,2})\s+)?(?:([A-Z]{3})\s+)?(\d{1,4})$")
_RANGE_RE = re.compile(r"^(BET|FROM)\s+(.+?)\s+(AND|TO)\s+(.+)$")
_MODIFIERS = {"BEF": (1, 0), "AFT": (2, 0), "ABT": (3, 0), "EST": (0, 1), "CAL": (0, 2)}


def _simple(text: str) -> tuple[list, int]:
    """One date with an optional calendar marker directly before it; returns (dateval, calendar)."""
    text = text.strip()
    calendar = 0
    marker = _CAL_RE.match(text)
    if marker:
        name = marker.group(1).strip()
        if name not in ("GREGORIAN", "JULIAN"):
            raise ValueError(f"unsupported calendar in {text!r}")
        calendar = 1 if name == "JULIAN" else 0
        text = text[marker.end():]
    match = _SIMPLE_RE.match(text)
    if not match:
        raise ValueError(f"cannot represent date part {text!r}")
    day, month, year_text = match.groups()
    year = int(year_text)
    if year < 1 or (day and not month):
        raise ValueError(f"cannot represent date part {text!r}")
    if month is not None and month not in MONTHS:
        raise ValueError(f"unknown month in {text!r}")
    m = MONTHS[month] if month else 0
    if day:
        try:
            date(year, m, int(day))
        except ValueError:
            raise ValueError(f"invalid day in {text!r}") from None
    return [int(day) if day else 0, m, year, False], calendar


def to_gramps_date(raw: str) -> dict:
    """Return the Gramps date for a GEDCOM date, or raise ValueError if it is not exactly representable."""
    text = " ".join((raw or "").upper().split())
    if not text:
        raise ValueError("empty date")

    modifier, quality = 0, 0
    match = _RANGE_RE.match(text)
    if match:
        if (match.group(1), match.group(3)) not in (("BET", "AND"), ("FROM", "TO")):
            raise ValueError(f"mismatched range words in {raw!r}")
        modifier = 4 if match.group(1) == "BET" else 5
        (first, cal1), (second, cal2) = _simple(match.group(2)), _simple(match.group(4))
        if cal1 != cal2:  # Gramps holds one calendar per date
            raise ValueError(f"mixed calendars in {raw!r}")
        dateval, calendar = first + second, cal1
    else:
        # a marker may stand before the modifier word or directly before the date, not both
        lead = _CAL_RE.match(text)
        head = text[lead.end():] if lead else text
        word, _, rest = head.partition(" ")
        if word in _MODIFIERS and rest:
            modifier, quality = _MODIFIERS[word]
            if lead:
                rest = text[lead.start():lead.end()] + rest
            text = rest
        dateval, calendar = _simple(text)
    return {"_class": "Date", "calendar": calendar, "modifier": modifier, "quality": quality,
            "dateval": dateval, "text": "", "sortval": 0, "newyear": 0}


def _is_empty(gramps_date: dict | None) -> bool:
    if not gramps_date:
        return True
    dateval = gramps_date.get("dateval") or []
    return not gramps_date.get("modifier") and not any(v for v in dateval if v is not False)


def same_date(gramps_date: dict | None, raw: str | None) -> bool:
    """True if the Gramps date equals the GEDCOM string; sortval and text are not compared."""
    if not raw or not raw.strip():
        return _is_empty(gramps_date)
    if _is_empty(gramps_date):
        return False
    try:  # an unrepresentable raw string never matches: the safe direction for stale checks
        want = to_gramps_date(raw)
    except ValueError:
        return False
    return all(gramps_date.get(k) == want[k] for k in ("calendar", "modifier", "quality", "dateval"))
