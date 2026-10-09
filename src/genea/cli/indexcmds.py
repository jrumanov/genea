"""`genea index` and `genea status`."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from genea.cli import common
from genea.cli.app import app
from genea.index.db import SchemaMismatch
from genea.index.indexer import index_kb
from genea.index.progress import IndexBusy, IndexDbLocked, IndexInUse, IndexProgress
from genea.index.status import index_summary

CURRENT_WIDTH = 50  # characters of the current file's path shown next to the bar


def _short(rel: str | None) -> str:
    rel = rel or ""
    return rel if len(rel) <= CURRENT_WIDTH else "…" + rel[-(CURRENT_WIDTH - 1):]


@app.command()
def index(
    ctx: typer.Context,
    rebuild: bool = typer.Option(False, "--rebuild", help="Delete the index and build it from scratch."),
    no_summaries: bool = typer.Option(False, "--no-summaries",
                                      help="Do not summarize transcripts in this run (a later run adds them)."),
) -> None:
    """Index new and changed files of the knowledge base."""
    kb = common.open_kb(ctx)
    summarizer = None if no_summaries else common.get_summarizer(kb)
    if summarizer is None and not no_summaries:
        common.console.print("[yellow]Transcript summaries are off: set summary_model in config.toml "
                             "(an Ollama model name).[/yellow]")
    columns = (SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
               TimeRemainingColumn())
    with Progress(*columns, console=common.console, transient=True) as bar:
        task = bar.add_task("scanning", total=None)

        def show(p: IndexProgress) -> None:
            if p.phase == "scanning":
                bar.update(task, description=f"scanning ({p.done} files)")
            elif p.phase == "indexing":
                bar.update(task, total=p.total, completed=p.done, description=escape(_short(p.current)))
            elif p.phase == "summarizing":
                bar.update(task, total=p.total, completed=p.done, description="summarizing " + escape(_short(p.current)))
            else:
                bar.update(task, total=None, completed=0, description="linking people to documents and photos")

        try:
            report = index_kb(kb, common.get_embedder(kb), rebuild=rebuild, on_progress=show,
                              summarizer=summarizer)
        except (SchemaMismatch, IndexBusy, IndexInUse, IndexDbLocked) as exc:
            bar.stop()
            common.console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc
    common.console.print(
        f"ok {report.ok} · needs OCR {report.needs_ocr} · errors {report.errors} · "
        f"unchanged {report.skipped} · removed {report.removed}"
    )
    for rel, message in report.problems:
        common.console.print(f"  [yellow]{escape(rel)}[/yellow]: {escape(message)}")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show what is indexed, whether indexing is running, and which files had problems."""
    kb = common.open_kb(ctx)
    summary = index_summary(kb)
    if summary.state == "missing":
        common.console.print("[yellow]Not indexed yet. Run `genea index`.[/yellow]")
        raise typer.Exit(1)
    if summary.state in ("building", "interrupted"):
        common.console.print(f"[yellow]{escape(summary.headline())}[/yellow]")
    if summary.counts is None:
        return
    table = Table("kind", "status", "files")
    for kind, file_status, n in summary.counts:
        table.add_row(kind, file_status, str(n))
    common.console.print(table)
    common.console.print(f"{summary.people} people in the family-tree index")
    for path, file_status, message in summary.problems:
        common.console.print(f"[yellow]{escape(path)}[/yellow] ({file_status}): {escape(message)}")
