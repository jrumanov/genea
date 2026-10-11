"""`genea analyze` and `genea analysis ...`: check part of the tree, record proposals, build iterations."""

from __future__ import annotations

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
