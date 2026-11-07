"""Analysis operations shared by the CLI and the MCP server. Each takes a ToolContext and returns a ToolResult."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from genea.analysis import store
from genea.analysis.detectors import detect
from genea.analysis.gedwrite import CONC_LIMIT
from genea.analysis.iteration import build
from genea.analysis.models import (PAGE_SIZE, SEVERITIES, Analysis, AnalysisList, AnalysisNote, AnalysisPage,
                                   AnalysisSummary, AnalysisUpdate, BuildResult, Conflict, FindingView, Proposal,
                                   parse_cite)
from genea.analysis.report import STATE_WARNINGS, render_report
from genea.analysis.scope import resolve_scope
from genea.changes.store import ChangeError, validate_change
from genea.dna.store import citation_exists
from genea.index.progress import index_state
from genea.ingest.gedcom.mapper import FAMILY_WRITE_TAGS, INDIVIDUAL_EVENT_TAGS
from genea.tools.base import ToolContext, ToolError, parse_family_id, parse_person_id, person_id


def tree_hash(ctx: ToolContext, ged_file: str) -> str:
    return hashlib.sha256(ctx.kb.resolve_inside(ged_file).read_bytes()).hexdigest()


def tree_index_state(ctx: ToolContext, ged_file: str, digest: str) -> str:
    """'ready' only if indexing finished and the index was built from exactly this version of the tree."""
    state = index_state(ctx.kb)
    if state != "ready":
        return state
    row = ctx.conn.execute("SELECT sha256 FROM files WHERE path = ?", (ged_file,)).fetchone()
    return "ready" if row is not None and row["sha256"] == digest else "outdated"


def current_state(ctx: ToolContext, analysis: Analysis) -> str:
    """The analysis' index state now: 'outdated' once the tree changed after it, or it was made on an old index."""
    try:
        digest = tree_hash(ctx, analysis.ged_file)
    except (OSError, ValueError):
        return "outdated"
    if digest != analysis.base_hash:
        return "outdated"
    state = tree_index_state(ctx, analysis.ged_file, digest)
    if state != "ready":
        return state
    return "ready" if analysis.index_state == "ready" else "outdated"


def not_in_tree(ctx: ToolContext, analysis: Analysis) -> set[str]:
    """Ids of written proposals whose change the indexed tree does not show (where the index can show it)."""
    from genea.analysis.apply import written_but_missing  # apply.py imports this module

    return {p.id for p in analysis.proposals if written_but_missing(ctx, analysis, p)}


def write_report(ctx: ToolContext, analysis: Analysis) -> None:
    store.write_atomic(store.analysis_dir(ctx.kb, analysis.name) / "report.md",
                       render_report(analysis, missing=not_in_tree(ctx, analysis)))


def analyze(ctx: ToolContext, kind: str, target_id: str, direction: str = "both", depth: int = 4) -> AnalysisPage:
    spec, scope = resolve_scope(ctx, kind, target_id, direction, depth)
    name = store.scope_name(scope.ged_file, spec)
    old = store.load(ctx.kb, name) if store.exists(ctx.kb, name) else None
    if old is not None and old.ged_file != scope.ged_file:  # another tree with the same file name
        name = store.scope_name(scope.ged_file, spec, unique=True)
        old = store.load(ctx.kb, name) if store.exists(ctx.kb, name) else None
    if old is not None and old.ged_file != scope.ged_file:
        raise ToolError(f"The analysis {name!r} belongs to {old.ged_file}, not {scope.ged_file}; its proposals "
                        "cannot be moved to another tree. Rename or remove its folder under .genea/analyses.")
    digest = tree_hash(ctx, scope.ged_file)
    stamp = store.now()
    analysis = Analysis(
        name=name, scope=spec, ged_file=scope.ged_file, base_hash=digest,
        index_state=tree_index_state(ctx, scope.ged_file, digest),
        created=old.created if old else stamp, updated=stamp,
        people=[person_id(scope.ged_file, x) for x in scope.people],
        boundary=[person_id(scope.ged_file, x) for x in scope.boundary],
        findings=detect(ctx, scope),
        proposals=old.proposals if old else [], dismissed=old.dismissed if old else {},
        notes=old.notes if old else [], iterations=old.iterations if old else 0,
    )
    store.save(ctx.kb, analysis)
    write_report(ctx, analysis)
    return _page(ctx, analysis, 1, None)


def read_analysis(ctx: ToolContext, name: str, severity: str | None = None, page: int = 1) -> AnalysisPage:
    return _page(ctx, store.load(ctx.kb, name), page, severity)


def list_all(ctx: ToolContext) -> AnalysisList:
    return AnalysisList(analyses=[
        AnalysisSummary(name=a.name, scope=a.scope, counts=a.counts(), iterations=a.iterations, updated=a.updated)
        for a in store.list_analyses(ctx.kb)])


def _page(ctx: ToolContext, analysis: Analysis, page: int, severity: str | None) -> AnalysisPage:
    if severity is not None and severity not in SEVERITIES:
        raise ToolError(f"Unknown severity {severity!r}; use {', '.join(SEVERITIES)}.")
    findings = [f for f in analysis.findings if severity is None or f.severity == severity]
    pages = max(1, math.ceil(len(findings) / PAGE_SIZE))
    if not 1 <= page <= pages:
        raise ToolError(f"page must be between 1 and {pages}")
    chunk = findings[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    shown = {f.id for f in chunk}
    known = {f.id for f in analysis.findings}
    proposals = [p for p in analysis.proposals
                 if p.finding_id in shown or (page == 1 and p.finding_id not in known)]
    sources = list({s.cite(): s for f in chunk for s in f.sources}.values())
    folder = store.analysis_dir(ctx.kb, analysis.name)
    state = current_state(ctx, analysis)
    return AnalysisPage(
        name=analysis.name, scope=analysis.scope, ged_file=analysis.ged_file, index_state=state,
        counts=analysis.counts(), page=page, pages=pages,
        findings=[FindingView(**f.model_dump(), status=analysis.finding_status(f.id)) for f in chunk],
        proposals=proposals, report=ctx.kb.rel(folder / "report.md"),
        not_in_tree=sorted(not_in_tree(ctx, analysis)), message=STATE_WARNINGS.get(state), sources=sources,
    )


NOTE_LIMIT = 4000  # characters of one analysis note


def _save(ctx: ToolContext, analysis: Analysis) -> None:
    analysis.updated = store.now()
    store.save(ctx.kb, analysis)
    write_report(ctx, analysis)


def _require_fresh(ctx: ToolContext, analysis: Analysis) -> None:
    """Proposals carry line numbers from the index, so the index must match the tree and the analysis."""
    digest = tree_hash(ctx, analysis.ged_file)
    if tree_index_state(ctx, analysis.ged_file, digest) != "ready":
        raise ToolError("The index does not match the tree (it changed after indexing, or indexing is not "
                        "complete). Run `genea index`, then analyze again before proposing.")
    if digest != analysis.base_hash:
        raise ToolError(f"The tree changed after this analysis was made. Run analyze again for "
                        f"{analysis.scope.kind} {analysis.scope.id} first.")
    if analysis.index_state != "ready":
        raise ToolError(f"This analysis was made while the index was {analysis.index_state}. Run analyze again for "
                        f"{analysis.scope.kind} {analysis.scope.id} first.")


MULTILINE = {("add_note", "text"), ("add_event", "note"), ("add_source_citation", "source")}  # payload values that may hold line breaks


def _check_ids(ctx: ToolContext, analysis: Analysis, operation: str, payload: dict[str, Any]) -> None:
    """Check the values are single lines of text, normalize ids to 'file#@X@' and check they exist in the tree."""
    for key, value in payload.items():
        if key == "line" or value is None:  # line is checked by _anchor
            continue
        if not isinstance(value, str):
            raise ToolError(f"{key} must be text, not {type(value).__name__}.")
        if (operation, key) in MULTILINE:  # written as CONT/CONC lines
            continue
        if "\n" in value or "\r" in value:
            raise ToolError(f"{key} must be one line of text.")
        if len(value) > CONC_LIMIT:
            raise ToolError(f"{key} is longer than {CONC_LIMIT} characters.")
    if operation in ("add_event", "add_note") and bool(payload.get("person_id")) == bool(payload.get("family_id")):
        raise ToolError("Give exactly one of person_id or family_id.")
    ged = analysis.ged_file
    for key in ("person_id", "child_id"):
        value = payload.get(key)
        if not value:
            continue
        if key == "child_id" and value.startswith("proposal:"):
            ref = value.split(":", 1)[1]
            if not any(p.id == ref and p.operation == "add_person" and p.status == "active"
                       for p in analysis.proposals):
                raise ToolError(f"{value} is not an active add_person proposal of this analysis.")
            continue
        file, xref = parse_person_id(ctx, value)
        if file != ged:
            raise ToolError(f"{value} is not in {ged}, the tree of this analysis.")
        payload[key] = person_id(file, xref)
    if payload.get("family_id"):
        file, xref = parse_family_id(ctx, payload["family_id"])
        if file != ged:
            raise ToolError(f"{payload['family_id']} is not in {ged}, the tree of this analysis.")
        payload["family_id"] = person_id(file, xref)
    if operation == "add_event" and payload.get("family_id") and payload["type"] not in FAMILY_WRITE_TAGS:
        raise ToolError(f"{payload['type']!r} is not a family event tag; use one of "
                        f"{', '.join(sorted(FAMILY_WRITE_TAGS))}.")
    if operation == "add_event" and payload.get("person_id") and payload["type"] not in INDIVIDUAL_EVENT_TAGS:
        raise ToolError(f"{payload['type']!r} is not an individual event tag; use one of "
                        f"{', '.join(sorted(INDIVIDUAL_EVENT_TAGS))}.")
    if operation == "add_event" and payload["type"] == "RESI" and not payload.get("address"):
        raise ToolError("RESI needs an address (one line), like 'Hlavná 5, Nitra'.")
    if operation == "add_person" and payload.get("sex") not in (None, "", "M", "F", "U"):
        raise ToolError("sex must be M, F or U.")


def _event_anchor(ctx: ToolContext, owner: str, value: Any) -> tuple[int, dict[str, str | None]]:
    """The line number and the event as the index holds it, for an event of `owner` (a person or family id)."""
    xref = owner.rsplit("#", 1)[-1]
    try:
        line = int(value)
    except (TypeError, ValueError):
        raise ToolError("line must be the GEDCOM line number of the event (get_person lists it).") from None
    row = ctx.conn.execute("SELECT type, description, date_raw, place FROM events "
                           "WHERE ged_file = ? AND owner_xref = ? AND line_no = ?",
                           (owner.rsplit("#", 1)[0], xref, line)).fetchone()
    if row is None:
        raise ToolError(f"Line {line} is not an event of {owner}; get_person lists its events with their lines.")
    # empty values are None, as the builder reads them from the tree
    return line, {"type": row["type"], "description": row["description"] or None, "date": row["date_raw"] or None,
                  "place": row["place"] or None}


def _anchor(ctx: ToolContext, payload: dict[str, Any]) -> dict[str, str | None]:
    line, anchor = _event_anchor(ctx, payload["person_id"], payload["line"])
    if not payload.get("date") and not payload.get("place"):
        raise ToolError("update_event needs a new date or place.")
    payload["line"] = line
    return anchor


def _show_existing(anchor: dict[str, str | None]) -> str:
    text = f"{anchor['type']} {anchor['date'] or 'no date'}"
    if anchor["place"]:
        text += f" in {anchor['place']}"
    return text + (f" ({anchor['description']})" if anchor["description"] else "")


def propose(ctx: ToolContext, name: str, operation: str, payload: dict[str, Any], sources: list[str],
            rationale: str, finding_id: str | None = None, author: str = "user",
            conflict_line: int | None = None) -> AnalysisUpdate:
    analysis = store.load(ctx.kb, name)
    _require_fresh(ctx, analysis)
    if finding_id is not None and finding_id not in {f.id for f in analysis.findings}:
        raise ToolError(f"No finding {finding_id} in {name}.")
    if not (rationale or "").strip():
        raise ToolError("Give a rationale: why is this change right?")
    refs = [parse_cite(s) for s in sources or []]
    if not refs:
        raise ToolError("Cite at least one source, like [docs/matrika.pdf p.3].")
    for ref in refs:
        indexed = ctx.conn.execute("SELECT 1 FROM files WHERE path = ?", (ref.file,)).fetchone() is not None
        if not indexed and not citation_exists(ctx.kb, ref.file):
            raise ToolError(f"{ref.file} is not an indexed file of this knowledge base; cite what the tools returned.")
    payload = dict(payload or {})
    try:
        validate_change(ctx.kb, "gedcom", analysis.ged_file, operation, payload)
    except ChangeError as exc:
        raise ToolError(str(exc)) from exc
    _check_ids(ctx, analysis, operation, payload)
    anchor = _anchor(ctx, payload) if operation == "update_event" else None
    conflict = None
    if conflict_line is not None:
        if operation not in ("add_event", "add_note"):
            raise ToolError("conflict_line only goes with add_event and add_note.")
        line, anchor = _event_anchor(ctx, payload.get("person_id") or payload["family_id"], conflict_line)
        conflict = Conflict(line=line, existing=_show_existing(anchor))
    for p in analysis.proposals:
        if p.status in ("active", "stale") and p.operation == operation and p.payload == payload:
            return AnalysisUpdate(name=name, proposal_id=p.id, status=p.status, sources=p.sources,
                                  message=f"An identical proposal already exists ({p.id}); not recorded again.")
    proposal = Proposal(id=f"p-{len(analysis.proposals) + 1}", finding_id=finding_id, author=author,
                        operation=operation, payload=payload, anchor=anchor, conflict=conflict,
                        base_hash=analysis.base_hash,
                        sources=refs, rationale=rationale.strip(), created=store.now())
    analysis.proposals.append(proposal)
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, proposal_id=proposal.id, status=proposal.status, sources=refs,
                          message=f"Recorded {proposal.id}. The tree is unchanged; build_iteration writes a copy "
                                  "with all active proposals applied.")


def withdraw(ctx: ToolContext, name: str, proposal_id: str) -> AnalysisUpdate:
    analysis = store.load(ctx.kb, name)
    proposal = next((p for p in analysis.proposals if p.id == proposal_id), None)
    if proposal is None:
        raise ToolError(f"No proposal {proposal_id} in {name}.")
    if proposal.status == "written":
        raise ToolError(f"{proposal_id} is written to Gramps Web; to undo it run `genea analysis undo {name} "
                        f"{proposal_id}`.")
    users = [p.id for p in analysis.proposals
             if p.status != "withdrawn" and p.payload.get("child_id") == f"proposal:{proposal_id}"]
    if users:
        raise ToolError(f"{', '.join(users)} link(s) this new person; withdraw {users[0]} first.")
    proposal.status = "withdrawn"
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, proposal_id=proposal_id, status="withdrawn",
                          message=f"Withdrew {proposal_id}.")


def dismiss(ctx: ToolContext, name: str, finding_id: str, reason: str = "") -> AnalysisUpdate:
    analysis = store.load(ctx.kb, name)
    if finding_id not in {f.id for f in analysis.findings}:
        raise ToolError(f"No finding {finding_id} in {name}.")
    analysis.dismissed[finding_id] = (reason or "").strip()
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, status="dismissed", message=f"Dismissed {finding_id}.")


def add_note(ctx: ToolContext, name: str, text: str) -> AnalysisUpdate:
    text = (text or "").strip()
    if not text:
        raise ToolError("The note is empty.")
    if len(text) > NOTE_LIMIT:
        raise ToolError(f"Keep a note under {NOTE_LIMIT} characters.")
    analysis = store.load(ctx.kb, name)
    analysis.notes.append(AnalysisNote(created=store.now(), text=text))
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, message="Note added to the report.")


def build_iteration(ctx: ToolContext, name: str) -> BuildResult:
    analysis = store.load(ctx.kb, name)
    result = build(ctx.kb, analysis)
    _save(ctx, analysis)
    return result
