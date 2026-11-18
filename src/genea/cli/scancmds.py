"""`genea scans` and `genea render-pdf`: find scanned PDFs and render their pages for reading."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from genea.cli import common
from genea.cli.app import app
from genea.ingest.scans import ScanError, render_pages, unread_scans


@app.command()
def scans(ctx: typer.Context) -> None:
    """List indexed PDFs without a text layer that have no md next to them yet."""
    kb = common.open_kb(ctx)
    if not kb.index_path.exists():
        common.console.print("[yellow]Not indexed yet. Run `genea index`.[/yellow]")
        raise typer.Exit(1)
    pending = unread_scans(kb)
    if not pending:
        common.console.print("No unread scanned PDFs.")
        return
    for rel in pending:
        common.console.print(escape(rel), soft_wrap=True)
    common.console.print(f"{len(pending)} scanned PDF(s) without an md")


@app.command("render-pdf")
def render_pdf(
    pdf: Path = typer.Argument(..., help="The PDF to render."),
    out_dir: Path = typer.Argument(..., help="Folder for page-001.png, page-002.png, ... (not the knowledge base)."),
    dpi: int = typer.Option(200, "--dpi", min=36, max=600, help="Resolution of the page images."),
) -> None:
    """Render the pages of a PDF to PNG files so they can be read with vision."""
    if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
        common.console.print(f"[red]Not a PDF file: {escape(str(pdf))}[/red]")
        raise typer.Exit(2)
    try:
        paths = render_pages(pdf, out_dir.expanduser(), dpi)
    except ScanError as exc:
        common.console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    for path in paths:
        common.console.print(escape(str(path)), soft_wrap=True)
