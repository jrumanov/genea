"""Rule-based consistency checks over the family tree and the documents linked to it.

Date ranges are compared conservatively: a rule fires only if it is violated for every
date inside the ranges (e.g. 'ABT 1850' never triggers on a borderline case).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from genea.tools.base import ToolContext, parse_person_id, person_id
from genea.tools.models import Conflict, ConflictResult, SourceRef
from genea.tools.people import (BIRTH_TYPES, event_rows, families_as_child, families_as_spouse,
                                first_event, summary)

YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
ALLOWED_AFTER_DEATH = {"DEAT", "BURI", "CREM", "PROB"}


def _d(value: str) -> date:
    return date.fromisoformat(value)


def _years(start: str, end: str) -> float:
    return (_d(end) - _d(start)).days / 365.25


def find_conflicts(ctx: ToolContext, pid: str | None = None, limit: int = 100) -> ConflictResult:
    if pid:
        people = [parse_person_id(ctx, pid)]
    else:
        people = [(r["ged_file"], r["xref"]) for r in
                  ctx.conn.execute("SELECT ged_file, xref FROM persons ORDER BY id")]
    found: list[Conflict] = []
    for ged, xref in people:
        found += _check_person(ctx.conn, ged, xref)
        if len(found) >= limit:
            break
    found = found[:max(1, limit)]
    sources = list({s.cite(): s for c in found for s in c.sources}.values())
    return ConflictResult(conflicts=found, sources=sources)


def _check_person(conn: sqlite3.Connection, ged: str, xref: str) -> list[Conflict]:
    pid = person_id(ged, xref)
    events = event_rows(conn, ged, xref)
    birth = first_event(events, ("BIRT",))
    born = first_event(events, BIRTH_TYPES)
    death = first_event(events, ("DEAT",))
    out: list[Conflict] = []

    def ref(*rows) -> list[SourceRef]:
        return [SourceRef(file=ged, line=r["line_no"]) for r in rows if r is not None]

    def add(kind: str, message: str, sources: list[SourceRef]) -> None:
        out.append(Conflict(kind=kind, severity="error", person_id=pid, message=message, sources=sources))

    if death is not None and death["date_to"]:
        for e in events:
            if e["type"] not in ALLOWED_AFTER_DEATH and e["date_from"] and e["date_from"] > death["date_to"]:
                add("event_after_death",
                    f"{e['type']} ({e['date_raw']}) is dated after death ({death['date_raw']})", ref(e, death))

    if birth is not None and birth["date_from"]:
        for e in events:
            if e["type"] in ("BAPM", "CHR") and e["date_to"] and (_d(birth["date_from"]) - _d(e["date_to"])).days > 365:
                add("baptism_before_birth",
                    f"{e['type']} ({e['date_raw']}) is more than a year before birth ({birth['date_raw']})",
                    ref(e, birth))

    if born is not None and born["date_from"]:
        for family in families_as_child(conn, ged, xref):
            for role, parent in (("father", family["husband_xref"]), ("mother", family["wife_xref"])):
                if not parent:
                    continue
                parent_birth = first_event(event_rows(conn, ged, parent), BIRTH_TYPES)
                if parent_birth is None or not parent_birth["date_from"]:
                    continue
                name = summary(conn, ged, parent).name
                if _years(parent_birth["date_from"], born["date_to"]) < 12:
                    add("parent_too_young", f"The {role} {name} was under 12 at this person's birth",
                        ref(born, parent_birth))
                if role == "mother" and _years(parent_birth["date_to"], born["date_from"]) > 55:
                    add("mother_too_old", f"The mother {name} was over 55 at this person's birth",
                        ref(born, parent_birth))
        for family in families_as_spouse(conn, ged, xref):
            for e in event_rows(conn, ged, family["xref"]):
                if e["type"] == "MARR" and e["date_to"] and _years(born["date_from"], e["date_to"]) < 12:
                    add("married_too_young", f"Married ({e['date_raw']}) before the age of 12", ref(e, born))

    out += _document_mismatches(conn, ged, xref, pid, events, born)
    return out


def _document_mismatches(conn, ged, xref, pid, events, born) -> list[Conflict]:
    """Flag linked passages whose years match none of the person's GEDCOM events."""
    rows = list(events)
    for family in families_as_spouse(conn, ged, xref):
        rows += event_rows(conn, ged, family["xref"])
    ranges = [(int(e["date_from"][:4]), int(e["date_to"][:4])) for e in rows if e["date_from"] and e["date_to"]]
    if not ranges:
        return []
    passages = conn.execute(
        "SELECT c.text, c.page, c.chunk_no, c.start_s, f.path FROM links l "
        "JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id "
        "JOIN files f ON f.id = c.file_id "
        "WHERE l.person_ged_file = ? AND l.person_xref = ? AND l.confidence >= 1.0 "
        "ORDER BY f.path, c.chunk_no", (ged, xref)).fetchall()
    out = []
    for passage in passages:
        years = sorted({int(y) for y in YEAR_RE.findall(passage["text"])})
        if years and not any(lo <= y <= hi for y in years for lo, hi in ranges):
            sources = [SourceRef(file=passage["path"], page=passage["page"], chunk=passage["chunk_no"],
                                 start_s=passage["start_s"])]
            if born is not None:
                sources.append(SourceRef(file=ged, line=born["line_no"]))
            out.append(Conflict(
                kind="document_mismatch", severity="needs_review", person_id=pid,
                message=(f"{passage['path']} mentions this person with year(s) "
                         f"{', '.join(map(str, years))}, which match none of the GEDCOM events. "
                         "Check whether it is the same person and which date is right."),
                sources=sources,
            ))
    return out
