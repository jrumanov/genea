"""GEDCOM 5.5.1 dates -> inclusive ISO date ranges.

ABT/CAL/EST widen by ±5 years, BEF reaches 100 years back, AFT 100 years forward.
Julian dates are treated as Gregorian (best effort); French/Hebrew calendars are not parsed.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_CAL_RE = re.compile(r"@#D([A-Z ]+)@\s*")
_SIMPLE_RE = re.compile(r"^(?:(\d{1,2})\s+)?(?:([A-Z]{3})\s+)?(\d{1,4})(?:/(\d{1,2}))?$")
_RANGE_RE = re.compile(r"^(BET|FROM)\s+(.+?)\s+(AND|TO)\s+(.+)$")


@dataclass(frozen=True)
class DateRange:
    date_from: str | None = None
    date_to: str | None = None
    qualifier: str | None = None


def _shift(d: date, years: int) -> date:
    year = min(max(d.year + years, 1), 9999)
    try:
        return d.replace(year=year)
    except ValueError:  # 29 Feb in a non-leap year
        return d.replace(year=year, day=28)


def _simple(text: str) -> tuple[date, date] | None:
    match = _SIMPLE_RE.match(text.strip())
    if not match:
        return None
    day, month, year_text, dual = match.groups()
    year = int(year_text)
    if dual:  # "1731/32" means 1732 in today's calendar
        year = int(year_text[: len(year_text) - len(dual)] + dual)
    if year < 1:
        return None
    if month is None:
        if day:
            return None
        return date(year, 1, 1), date(year, 12, 31)
    if month not in MONTHS:
        return None
    m = MONTHS[month]
    if day is None:
        return date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
    try:
        d = date(year, m, int(day))
    except ValueError:
        return None
    return d, d


def parse_gedcom_date(raw: str | None) -> DateRange:
    if not raw or not raw.strip():
        return DateRange()
    text = " ".join(raw.upper().split())
    if any(c.strip() not in ("GREGORIAN", "JULIAN") for c in _CAL_RE.findall(text)):
        return DateRange()
    text = _CAL_RE.sub("", text).strip()
    if text.startswith("("):
        return DateRange(qualifier="PHRASE")

    qualifier = None
    if text.startswith("INT "):
        qualifier = "INT"
        text = text[4:].split("(")[0].strip()

    match = _RANGE_RE.match(text)
    if match:
        start, end = _simple(match.group(2)), _simple(match.group(4))
        if start and end:
            return DateRange(start[0].isoformat(), end[1].isoformat(), match.group(1))
        return DateRange(qualifier=match.group(1))

    word, _, rest = text.partition(" ")
    if word in ("ABT", "CAL", "EST", "BEF", "AFT", "FROM", "TO") and rest:
        parsed = _simple(rest)
        if not parsed:
            return DateRange(qualifier=word)
        lo, hi = parsed
        if word in ("ABT", "CAL", "EST"):
            lo, hi = _shift(lo, -5), _shift(hi, 5)
        elif word == "BEF":
            lo = _shift(lo, -100)
        elif word == "AFT":
            hi = _shift(hi, 100)
        return DateRange(lo.isoformat(), hi.isoformat(), word)

    parsed = _simple(text)
    if parsed:
        return DateRange(parsed[0].isoformat(), parsed[1].isoformat(), qualifier)
    return DateRange(qualifier=qualifier)
