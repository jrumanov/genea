"""Detectors: deterministic checks over a scope. They find problems and never guess a value."""

from __future__ import annotations

import json
from datetime import date

from genea.analysis.models import SEVERITIES, Finding, finding_id
from genea.analysis.scope import Scope
from genea.tools.base import ToolContext, person_id
from genea.tools.conflicts import check_person
from genea.tools.models import Conflict, SourceRef
from genea.tools.people import BIRTH_TYPES, DEATH_TYPES, event_rows, families_as_child, first_event, summary

PROBABLY_DECEASED_YEARS = 110


def detect(ctx: ToolContext, scope: Scope) -> list[Finding]:
    conn, ged = ctx.conn, scope.ged_file
    found: dict[str, Finding] = {}
    for xref in scope.people:
        label = f"{summary(conn, ged, xref).name} ({person_id(ged, xref)})"
        findings = [_from_conflict(conn, ged, c, label) for c in check_person(conn, ged, xref)]
        findings += _gaps(conn, ged, xref, label) + _mentions_without_dates(conn, ged, xref, label)
        for finding in findings:
            found.setdefault(finding.id, finding)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(found.values(), key=lambda f: (order[f.severity], f.label, f.kind, f.id))


def _ref_key(conn, ged: str, ref: SourceRef) -> str:
    """What a source points at, without its line number: 'BIRT 1 JAN 1878' or 'docs/x.md#3'."""
    if ref.file == ged and ref.line is not None:
        row = conn.execute("SELECT type, date_raw FROM events WHERE ged_file = ? AND line_no = ?",
                           (ged, ref.line)).fetchone()
        return f"{row['type']} {row['date_raw'] or ''}".strip() if row else "record"
    return f"{ref.file}#{ref.chunk if ref.chunk is not None else ref.page}"


def _from_conflict(conn, ged: str, conflict: Conflict, label: str) -> Finding:
    key = ";".join(sorted(_ref_key(conn, ged, s) for s in conflict.sources))
    return Finding(id=finding_id(conflict.kind, [conflict.person_id], None, key), kind=conflict.kind,
                   severity=conflict.severity, label=label, message=conflict.message,
                   person_ids=[conflict.person_id], sources=conflict.sources)


def _finding(kind: str, severity: str, ged: str, xref: str, label: str, message: str,
             sources: list[SourceRef], key: str = "") -> Finding:
    pid = person_id(ged, xref)
    return Finding(id=finding_id(kind, [pid], None, key), kind=kind, severity=severity, label=label,
                   message=message, person_ids=[pid], sources=sources)


def _gaps(conn, ged: str, xref: str, label: str) -> list[Finding]:
    row = conn.execute("SELECT sex, line_no FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    record = [SourceRef(file=ged, line=row["line_no"])]
    events = event_rows(conn, ged, xref)
    born = first_event(events, BIRTH_TYPES)
    out = []
    if born is None:
        out.append(_finding("missing_birth", "info", ged, xref, label, "No birth or baptism is recorded", record))
    elif (first_event(events, DEATH_TYPES) is None and born["date_to"]
          and int(born["date_to"][:4]) < date.today().year - PROBABLY_DECEASED_YEARS):
        out.append(_finding("missing_death", "info", ged, xref, label,
                            f"Born {born['date_raw']} but no death or burial is recorded",
                            [SourceRef(file=ged, line=born["line_no"])]))
    if not families_as_child(conn, ged, xref):
        out.append(_finding("missing_parents", "info", ged, xref, label, "No parents are recorded", record))
    if (row["sex"] or "U").upper() not in ("M", "F"):
        out.append(_finding("missing_sex", "info", ged, xref, label, "Sex is not recorded", record))
    for event in events:
        if not json.loads(event["source_citations"]):
            what = f"{event['type']} {event['date_raw'] or ''}".strip()
            out.append(_finding("uncited_event", "info", ged, xref, label,
                                f"{event['type']} ({event['date_raw'] or 'no date'}) has no source citation",
                                [SourceRef(file=ged, line=event["line_no"])], key=what))
    return out


def _mentions_without_dates(conn, ged: str, xref: str, label: str) -> list[Finding]:
    if any(e["date_from"] for e in event_rows(conn, ged, xref)):
        return []  # dated people are covered by the document_mismatch rule
    rows = conn.execute(
        "SELECT c.page, c.chunk_no, c.start_s, f.path FROM links l "
        "JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id "
        "JOIN files f ON f.id = c.file_id "
        "WHERE l.person_ged_file = ? AND l.person_xref = ? AND l.confidence >= 1.0 "
        "ORDER BY f.path, c.chunk_no", (ged, xref)).fetchall()
    if not rows:
        return []
    sources = [SourceRef(file=r["path"], page=r["page"], chunk=r["chunk_no"], start_s=r["start_s"]) for r in rows[:5]]
    return [_finding("mentioned_without_dates", "needs_review", ged, xref, label,
                     f"Linked documents mention this person ({len(rows)} passage(s)) but the tree has no dated "
                     "event for them. Read the passages and propose events if they are about this person.",
                     sources)]
