"""Analysis operations shared by the CLI and the MCP server. Each takes a ToolContext and returns a ToolResult."""

from __future__ import annotations

import hashlib
import math

from genea.analysis import store
from genea.analysis.detectors import detect
from genea.analysis.models import (PAGE_SIZE, SEVERITIES, Analysis, AnalysisList, AnalysisPage, AnalysisSummary,
                                   FindingView)
from genea.analysis.report import STATE_WARNINGS, render_report
from genea.analysis.scope import resolve_scope
from genea.index.progress import index_state
from genea.tools.base import ToolContext, ToolError, person_id


def tree_hash(ctx: ToolContext, ged_file: str) -> str:
    return hashlib.sha256(ctx.kb.resolve_inside(ged_file).read_bytes()).hexdigest()


def tree_index_state(ctx: ToolContext, ged_file: str, digest: str) -> str:
    """'ready' only if indexing finished and the index was built from exactly this version of the tree."""
    state = index_state(ctx.kb)
    if state != "ready":
        return state
    row = ctx.conn.execute("SELECT sha256 FROM files WHERE path = ?", (ged_file,)).fetchone()
    return "ready" if row is not None and row["sha256"] == digest else "outdated"


def write_report(ctx: ToolContext, analysis: Analysis) -> None:
    store.write_atomic(store.analysis_dir(ctx.kb, analysis.name) / "report.md", render_report(analysis))


def analyze(ctx: ToolContext, kind: str, target_id: str, direction: str = "both", depth: int = 4) -> AnalysisPage:
    spec, scope = resolve_scope(ctx, kind, target_id, direction, depth)
    name = store.scope_name(scope.ged_file, spec)
    old = store.load(ctx.kb, name) if store.exists(ctx.kb, name) else None
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
    return AnalysisPage(
        name=analysis.name, scope=analysis.scope, ged_file=analysis.ged_file, index_state=analysis.index_state,
        counts=analysis.counts(), page=page, pages=pages,
        findings=[FindingView(**f.model_dump(), status=analysis.finding_status(f.id)) for f in chunk],
        proposals=proposals, report=ctx.kb.rel(folder / "report.md"),
        message=STATE_WARNINGS.get(analysis.index_state), sources=sources,
    )
