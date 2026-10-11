"""`genea analyze` and `genea analysis ...`: check part of the tree, record proposals, build iterations."""

from __future__ import annotations

import json
from typing import NoReturn

import typer
from rich.markup import escape
from rich.table import Table

from genea.analysis import api
from genea.analysis.models import AnalysisPage
from genea.cli import common
from genea.cli.app import analysis_app, app
from genea.tools.base import ToolError


def _fail(exc: Exception) -> NoReturn:
    common.console.print(f"[red]{escape(str(exc))}[/red]")
    raise typer.Exit(1) from exc


def _with_tools(ctx: typer.Context, fn, *args):
    kb = common.open_kb(ctx)
    tools = common.open_tool_context(kb)
    try:
        return fn(tools, *args)
    except ToolError as exc:
        _fail(exc)
    finally:
        tools.conn.close()


def _print_page(result: AnalysisPage) -> None:
    console, c = common.console, result.counts
    console.print(f"[bold]{escape(result.name)}[/bold]  {result.scope.kind} {escape(result.scope.id)}  "
                  f"index: {result.index_state}")
    console.print(f"{c['error']} error(s), {c['needs_review']} to review, {c['info']} gap(s); "
                  f"{c['proposals']} proposal(s), {c['stale']} stale, {c['dismissed']} dismissed")
    if result.message:
        console.print(f"[yellow]{escape(result.message)}[/yellow]")
    table = Table("id", "severity", "person", "finding", "status")
    for f in result.findings:
        table.add_row(f.id, f.severity, escape(f.label), escape(f.message), f.status)
    console.print(table)
    console.print(f"page {result.page}/{result.pages} · report: {escape(result.report)}")


@app.command()
def analyze(
    ctx: typer.Context,
    kind: str = typer.Argument(..., help="person, family or branch"),
    target_id: str = typer.Argument(..., metavar="ID", help="A person id (tree.ged#@I12@) or, for family, a family id"),
    direction: str = typer.Option("both", help="For branch: ancestors, descendants or both"),
    depth: int = typer.Option(4, help="For branch: generations to walk (1-6)"),
) -> None:
    """Check a person, family or branch for conflicts and gaps and write a report. The tree is not changed."""
    _print_page(_with_tools(ctx, api.analyze, kind, target_id, direction, depth))


@analysis_app.command("list")
def list_command(ctx: typer.Context) -> None:
    """List the analyses of this knowledge base."""
    result = _with_tools(ctx, api.list_all)
    table = Table()
    table.add_column("name", no_wrap=True)
    for heading in ("scope", "errors", "review", "gaps", "proposals", "iterations"):
        table.add_column(heading, overflow="fold")
    for a in result.analyses:
        c = a.counts
        table.add_row(escape(a.name), f"{a.scope.kind} {escape(a.scope.id)}", str(c["error"]),
                      str(c["needs_review"]), str(c["info"]), str(c["proposals"]), str(a.iterations))
    common.console.print(table)


@analysis_app.command()
def show(ctx: typer.Context, name: str,
         severity: str | None = typer.Option(None, help="error, needs_review or info"),
         page: int = typer.Option(1, help="Page of findings")) -> None:
    """Show the findings of an analysis."""
    _print_page(_with_tools(ctx, api.read_analysis, name, severity, page))


@analysis_app.command()
def propose(
    ctx: typer.Context,
    name: str,
    operation: str = typer.Option(..., help="add_event, update_event, add_note, add_source_citation, "
                                            "link_child or add_person"),
    payload: str = typer.Option(..., help='JSON object, e.g. \'{"person_id": "tree.ged#@I3@", "text": "..."}\''),
    source: list[str] = typer.Option(..., "--source", help="A citation like [docs/matrika.pdf p.3]; repeatable"),
    rationale: str = typer.Option(..., help="Why the change is right"),
    finding: str | None = typer.Option(None, help="The finding id this proposal answers"),
) -> None:
    """Record a proposed change in an analysis. The tree is not changed."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        _fail(ToolError(f"--payload is not valid JSON: {exc}"))
    if not isinstance(data, dict):
        _fail(ToolError("--payload must be a JSON object"))
    result = _with_tools(ctx, api.propose, name, operation, data, source, rationale, finding, "user")
    common.console.print(escape(result.message))


@analysis_app.command()
def withdraw(ctx: typer.Context, name: str, proposal_id: str) -> None:
    """Withdraw a proposal; later iterations leave it out."""
    common.console.print(escape(_with_tools(ctx, api.withdraw, name, proposal_id).message))


@analysis_app.command()
def dismiss(ctx: typer.Context, name: str, finding_id: str,
            reason: str = typer.Option("", help="Why the finding is not a problem")) -> None:
    """Mark a finding as not a problem; it gets no CHECK note and stays dismissed on re-runs."""
    common.console.print(escape(_with_tools(ctx, api.dismiss, name, finding_id, reason).message))


@analysis_app.command()
def build(ctx: typer.Context, name: str) -> None:
    """Write the next iteration: a copy of the tree with all active proposals applied, plus a diff and report."""
    result = _with_tools(ctx, api.build_iteration, name)
    common.console.print(escape(result.message))
    for label, path in (("tree", result.iteration), ("diff", result.diff), ("report", result.report)):
        common.console.print(f"{label}: {escape(path)}")
    for proposal_id in result.stale:
        common.console.print(f"[yellow]{proposal_id} not applied; see the report.[/yellow]")
