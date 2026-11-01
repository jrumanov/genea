"""Match face-region names (no diacritics, surname first, optional birth year) to tree people."""

from __future__ import annotations

import re
import sqlite3

from genea.text import normalize
from genea.tools.people import BIRTH_TYPES, event_rows, first_event

_YEAR = re.compile(r"\d{4}")


def region_matches(region_name: str, forms: list[str], birth_year: int | None) -> bool:
    words = normalize(region_name or "").split()
    if len(words) > 1 and _YEAR.fullmatch(words[-1]):
        year = int(words.pop())
        if birth_year is not None and year != birth_year:
            return False
    wanted = set(words)
    return bool(wanted) and any(set(normalize(form).split()) == wanted for form in forms)


def person_forms(conn: sqlite3.Connection, ged: str, xref: str) -> tuple[list[str], int | None]:
    row = conn.execute("SELECT names_norm FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    if row is None:
        return [], None
    forms = [f.strip() for f in row["names_norm"].split("|") if f.strip()]
    birth = first_event(event_rows(conn, ged, xref), BIRTH_TYPES)
    start = (birth["date_from"] or "")[:4] if birth is not None else ""
    return forms, int(start) if start.isdigit() else None


def parse_person_param(conn: sqlite3.Connection, raw: str) -> tuple[str, str] | None:
    ged, sep, xref = (raw or "").strip().partition("#")
    if not sep or not ged or not xref:
        return None
    xref = "@" + xref.strip().strip("@") + "@"
    found = conn.execute("SELECT 1 FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    return (ged, xref) if found else None


def image_regions_for(conn: sqlite3.Connection, rel: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT r.* FROM image_regions r JOIN images i ON i.id = r.image_id JOIN files f ON f.id = i.file_id "
        "WHERE f.path = ? ORDER BY r.id", (rel,),
    ).fetchall()
