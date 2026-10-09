"""`genea summaries pending` and `genea summaries import`."""

from __future__ import annotations

import typer
from rich.markup import escape

from genea.cli import common
from genea.cli.app import summaries_app
from genea.index.summary_work import import_results, write_requests


@summaries_app.command()
def pending(ctx: typer.Context) -> None:
    """Write the sections of every transcript without a summary to .genea/summary-work/."""
    kb = common.open_kb(ctx)
    requests = write_requests(kb)
    if not requests:
        common.console.print("Every transcript has a summary.")
        return
    sections = sum(r.sections for r in requests)
    common.console.print(f"{len(requests)} transcript(s), {sections} section(s) to summarize in "
                         f"{escape(str(kb.summary_work_dir))} (see INSTRUCTIONS.md there):")
    for r in requests:
        common.console.print(f"  {r.digest[:12]}  {escape(r.path)} ({r.sections})")


@summaries_app.command("import")
def import_(
    ctx: typer.Context,
    model: str = typer.Option("claude-code", "--model", help="Recorded as the model that wrote the summaries."),
) -> None:
    """Store the finished results from .genea/summary-work/ in the summary cache."""
    kb = common.open_kb(ctx)
    report = import_results(kb, model)
    common.console.print(f"imported {len(report.imported)} · failed {len(report.failed)}")
    for name, reason in report.failed:
        common.console.print(f"  [yellow]{escape(name)}[/yellow]: {escape(reason)}")
    if report.imported:
        common.console.print("Run `genea index` to make the new summaries searchable.")
    if report.failed:
        raise typer.Exit(1)
