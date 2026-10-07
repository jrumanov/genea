"""Family-tree queries: find people, person details, relatives and timelines."""

from __future__ import annotations

import json
import sqlite3

from genea.text import normalize
from genea.tools.base import ToolContext, ToolError, parse_person_id, person_id
from genea.tools.models import (EventOut, PersonDetail, PersonMatches, PersonSummary,
                                RelativeOut, RelativesResult, SourceRef, Timeline, TimelineEntry)

BIRTH_TYPES = ("BIRT", "CHR", "BAPM")
DEATH_TYPES = ("DEAT", "BURI", "CREM")
RELATIONS = ("parents", "children", "siblings", "spouses", "ancestors", "descendants")


def event_rows(conn: sqlite3.Connection, ged: str, owner: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE ged_file = ? AND owner_xref = ? "
        "ORDER BY COALESCE(date_from, '9999'), line_no", (ged, owner),
    ).fetchall()


def first_event(events, types) -> sqlite3.Row | None:
    for event_type in types:
        for event in events:
            if event["type"] == event_type:
                return event
    return None


def _describe(event) -> str | None:
    if event is None:
        return None
    parts = [p for p in (event["date_raw"], event["place"]) if p]
    return ", ".join(parts) or event["type"]


def summary(conn: sqlite3.Connection, ged: str, xref: str, events=None) -> PersonSummary:
    row = conn.execute("SELECT * FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    if row is None:
        return PersonSummary(id=person_id(ged, xref), name=xref, source=SourceRef(file=ged))
    events = event_rows(conn, ged, xref) if events is None else events
    name = " ".join(f"{row['given'] or ''} {row['surname'] or ''}".split()) or xref
    return PersonSummary(
        id=person_id(ged, xref), name=name, sex=row["sex"],
        birth=_describe(first_event(events, BIRTH_TYPES)),
        death=_describe(first_event(events, DEATH_TYPES)),
        source=SourceRef(file=ged, line=row["line_no"]),
    )


def event_out(ged: str, event) -> EventOut:
    return EventOut(
        type=event["type"], description=event["description"], date=event["date_raw"],
        date_from=event["date_from"], date_to=event["date_to"], place=event["place"],
        citations=json.loads(event["source_citations"]),
        source=SourceRef(file=ged, line=event["line_no"]),
    )


def families_as_spouse(conn, ged, xref) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM families WHERE ged_file = ? AND (husband_xref = ? OR wife_xref = ?) ORDER BY line_no",
        (ged, xref, xref),
    ).fetchall()


def families_as_child(conn, ged, xref) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.* FROM family_children fc JOIN families f "
        "ON f.ged_file = fc.ged_file AND f.xref = fc.family_xref "
        "WHERE fc.ged_file = ? AND fc.child_xref = ? ORDER BY f.line_no", (ged, xref),
    ).fetchall()


def _children_in(conn, ged, family_xref) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT child_xref FROM family_children WHERE ged_file = ? AND family_xref = ? ORDER BY rowid",
        (ged, family_xref))]


def _unique(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def parents_of(conn, ged, xref) -> list[str]:
    return _unique(x for f in families_as_child(conn, ged, xref) for x in (f["husband_xref"], f["wife_xref"]))


def children_of(conn, ged, xref) -> list[str]:
    return _unique(c for f in families_as_spouse(conn, ged, xref) for c in _children_in(conn, ged, f["xref"]))


def spouses_of(conn, ged, xref) -> list[str]:
    return _unique(f["wife_xref"] if f["husband_xref"] == xref else f["husband_xref"]
                   for f in families_as_spouse(conn, ged, xref))


def siblings_of(conn, ged, xref) -> list[str]:
    return _unique(c for f in families_as_child(conn, ged, xref)
                   for c in _children_in(conn, ged, f["xref"]) if c != xref)


def find_person(ctx: ToolContext, name: str, born_around: int | None = None,
                place: str | None = None, limit: int = 10) -> PersonMatches:
    tokens = normalize(name).split()
    if not tokens:
        raise ToolError("Give a name to search for.")
    rows = ctx.conn.execute(
        "SELECT ged_file, xref FROM persons WHERE " + " AND ".join("names_norm LIKE ?" for _ in tokens),
        [f"%{t}%" for t in tokens],
    ).fetchall()
    place_norm = normalize(place) if place else ""
    scored = []
    for row in rows:
        events = event_rows(ctx.conn, row["ged_file"], row["xref"])
        score = 0.0
        if born_around is not None:
            birth = first_event(events, BIRTH_TYPES)
            if birth is not None and birth["date_from"]:
                middle = (int(birth["date_from"][:4]) + int(birth["date_to"][:4])) / 2
                score -= abs(middle - born_around)
            else:
                score -= 100
        if place_norm and any(place_norm in normalize(e["place"] or "") for e in events):
            score += 10
        scored.append((score, summary(ctx.conn, row["ged_file"], row["xref"], events)))
    scored.sort(key=lambda item: (-item[0], item[1].name, item[1].id))
    candidates = [s for _, s in scored[: max(1, min(limit, 50))]]
    return PersonMatches(query=name, candidates=candidates, sources=[c.source for c in candidates])


def get_person(ctx: ToolContext, pid: str) -> PersonDetail:
    ged, xref = parse_person_id(ctx, pid)
    conn = ctx.conn
    row = conn.execute("SELECT * FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    spouse_families = families_as_spouse(conn, ged, xref)
    events = list(event_rows(conn, ged, xref))
    for family in spouse_families:
        events += event_rows(conn, ged, family["xref"])
    events.sort(key=lambda e: (e["date_from"] or "9999", e["line_no"]))
    me = summary(conn, ged, xref)
    event_models = [event_out(ged, e) for e in events]
    return PersonDetail(
        id=me.id, name=me.name, given=row["given"], surname=row["surname"], sex=row["sex"],
        name_variants=json.loads(row["name_variants"]), notes=row["notes"] or None,
        events=event_models,
        parents=[summary(conn, ged, x) for x in parents_of(conn, ged, xref)],
        spouses=[summary(conn, ged, x) for x in spouses_of(conn, ged, xref)],
        children=[summary(conn, ged, x) for x in children_of(conn, ged, xref)],
        families_as_spouse=[person_id(ged, f["xref"]) for f in spouse_families],
        families_as_child=[person_id(ged, f["xref"]) for f in families_as_child(conn, ged, xref)],
        sources=[me.source] + [e.source for e in event_models],
    )


def relatives(ctx: ToolContext, pid: str, relation: str, depth: int = 1) -> RelativesResult:
    if relation not in RELATIONS:
        raise ToolError(f"Unknown relation {relation!r}; use one of {', '.join(RELATIONS)}.")
    ged, xref = parse_person_id(ctx, pid)
    conn = ctx.conn
    depth = max(1, min(depth, 6))
    found: list[RelativeOut] = []
    direct = {"parents": (parents_of, 1), "children": (children_of, -1),
              "siblings": (siblings_of, 0), "spouses": (spouses_of, 0)}
    if relation in direct:
        step, generation = direct[relation]
        found = [RelativeOut(generation=generation, person=summary(conn, ged, x)) for x in step(conn, ged, xref)]
    else:
        step, sign = (parents_of, 1) if relation == "ancestors" else (children_of, -1)
        seen, frontier = {xref}, [xref]
        for generation in range(1, depth + 1):
            next_frontier = []
            for current in frontier:
                for other in step(conn, ged, current):
                    if other not in seen:
                        seen.add(other)
                        next_frontier.append(other)
                        found.append(RelativeOut(generation=sign * generation, person=summary(conn, ged, other)))
            frontier = next_frontier
    return RelativesResult(person=summary(conn, ged, xref), relation=relation, relatives=found,
                           sources=[r.person.source for r in found])


def timeline(ctx: ToolContext, pid: str) -> Timeline:
    ged, xref = parse_person_id(ctx, pid)
    conn = ctx.conn
    names: dict[str, str] = {}
    entries: list[TimelineEntry] = []
    life = set(BIRTH_TYPES) | set(DEATH_TYPES)

    def name_of(who: str) -> str:
        if who not in names:
            names[who] = summary(conn, ged, who).name
        return names[who]

    def add(owner: str, role: str, who: str, types: set[str] | None = None) -> None:
        for event in event_rows(conn, ged, owner):
            if types is None or event["type"] in types:
                entries.append(TimelineEntry(
                    date=event["date_raw"], date_from=event["date_from"], role=role,
                    person_id=person_id(ged, who), person_name=name_of(who), type=event["type"],
                    place=event["place"], source=SourceRef(file=ged, line=event["line_no"]),
                ))

    add(xref, "self", xref)
    for family in families_as_spouse(conn, ged, xref):
        add(family["xref"], "self", xref, {"MARR", "DIV", "ENGA"})
    for family in families_as_child(conn, ged, xref):
        for parent in (family["husband_xref"], family["wife_xref"]):
            if parent:
                add(parent, "parent", parent, life)
        who = family["husband_xref"] or family["wife_xref"]
        if who:
            add(family["xref"], "parent", who, {"MARR"})
    for spouse in spouses_of(conn, ged, xref):
        add(spouse, "spouse", spouse, life)
    for child in children_of(conn, ged, xref):
        add(child, "child", child, life)
        for family in families_as_spouse(conn, ged, child):
            add(family["xref"], "child", child, {"MARR"})
    entries.sort(key=lambda e: (e.date_from or "9999", e.source.line or 0))
    return Timeline(person=summary(conn, ged, xref), entries=entries, sources=[e.source for e in entries])
