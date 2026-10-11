"""Which people and families an analysis looks at."""

from __future__ import annotations

from dataclasses import dataclass, field

from genea.analysis.models import ScopeSpec
from genea.tools.base import ToolContext, ToolError, parse_family_id, parse_person_id, person_id
from genea.tools.people import children_in, children_of, families_as_child, families_as_spouse, parents_of

KINDS = ("person", "family", "branch")
DIRECTIONS = ("ancestors", "descendants", "both")


@dataclass
class Scope:
    ged_file: str
    people: list[str]  # xrefs, root first
    families: list[str]  # xrefs
    boundary: list[str] = field(default_factory=list)  # people at the depth limit with relatives beyond it


def _unique(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def _walk(conn, ged: str, root: str, step, depth: int) -> tuple[list[str], list[str]]:
    seen, frontier, found = {root}, [root], []
    for _ in range(depth):
        next_frontier = []
        for current in frontier:
            for other in step(conn, ged, current):
                if other not in seen:
                    seen.add(other)
                    next_frontier.append(other)
                    found.append(other)
        frontier = next_frontier
    return found, [x for x in frontier if step(conn, ged, x)]


def resolve_scope(ctx: ToolContext, kind: str, target_id: str, direction: str = "both",
                  depth: int = 4) -> tuple[ScopeSpec, Scope]:
    if kind not in KINDS:
        raise ToolError(f"Unknown scope {kind!r}; use person, family or branch.")
    if direction not in DIRECTIONS:
        raise ToolError(f"Unknown direction {direction!r}; use ancestors, descendants or both.")
    depth = max(1, min(int(depth), 6))
    conn = ctx.conn
    if kind == "family":
        ged, xref = parse_family_id(ctx, target_id)
        row = conn.execute("SELECT * FROM families WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
        spouses = _unique([row["husband_xref"], row["wife_xref"]])
        people = _unique(spouses + children_in(conn, ged, xref))
        families = _unique([xref] + [f["xref"] for s in spouses for f in families_as_child(conn, ged, s)])
        return ScopeSpec(kind="family", id=person_id(ged, xref)), Scope(ged, people, families)
    ged, xref = parse_person_id(ctx, target_id)
    spec = ScopeSpec(kind=kind, id=person_id(ged, xref), direction=direction, depth=depth)
    if kind == "person":
        families = _unique([f["xref"] for f in families_as_child(conn, ged, xref)]
                           + [f["xref"] for f in families_as_spouse(conn, ged, xref)])
        return spec, Scope(ged, [xref], families)
    found, boundary = [], []
    if direction in ("ancestors", "both"):
        more, edge = _walk(conn, ged, xref, parents_of, depth)
        found, boundary = found + more, boundary + edge
    if direction in ("descendants", "both"):
        more, edge = _walk(conn, ged, xref, children_of, depth)
        found, boundary = found + more, boundary + edge
    people = _unique([xref] + found)
    families = _unique([f["xref"] for f in families_as_child(conn, ged, xref)]
                       + [f["xref"] for p in people for f in families_as_spouse(conn, ged, p)])
    return spec, Scope(ged, people, families, _unique(boundary))
