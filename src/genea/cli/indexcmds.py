"""`genea index` and `genea status`."""

from __future__ import annotations

import sqlite3

import typer
from rich.markup import escape
from rich.table import Table

from genea.cli import common
from genea.cli.app import app
from genea.index.db import SchemaMismatch
from genea.index.indexer import index_kb


@app.command()
def index(
    ctx: typer.Context,
    rebuild: bool = typer.Option(False, "--rebuild", help="Delete the index and build it from scratch."),
) -> None:
    """Index new and changed files of the knowledge base."""
    kb = common.open_kb(ctx)
    try:
        report = index_kb(kb, common.get_embedder(kb), rebuild=rebuild,
                          on_file=lambda rel: common.console.print(f"[dim]indexing {escape(rel)}[/dim]"))
    except SchemaMismatch as exc:
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
    """Show what is indexed and which files had problems."""
    kb = common.open_kb(ctx)
    if not kb.index_path.exists():
        common.console.print("[yellow]Not indexed yet. Run `genea index`.[/yellow]")
        raise typer.Exit(1)
    conn = sqlite3.connect(kb.index_path)
    conn.row_factory = sqlite3.Row
    try:
        counts = conn.execute(
            "SELECT kind, status, COUNT(*) AS n FROM files GROUP BY kind, status ORDER BY kind, status"
        ).fetchall()
        problems = conn.execute(
            "SELECT path, status, error_msg FROM files WHERE status != 'ok' OR error_msg IS NOT NULL ORDER BY path"
        ).fetchall()
        people = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    finally:
        conn.close()
    table = Table("kind", "status", "files")
    for row in counts:
        table.add_row(row["kind"], row["status"], str(row["n"]))
    common.console.print(table)
    common.console.print(f"{people} people in the family-tree index")
    for row in problems:
        common.console.print(f"[yellow]{escape(row['path'])}[/yellow] ({row['status']}): "
                             f"{escape(row['error_msg'] or '')}")
