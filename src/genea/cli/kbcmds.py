"""`genea init` and `genea doctor`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from genea.cli.app import app
from genea.cli.common import console
from genea.config import KBNotFound, load_settings, resolve_kb_root
from genea.doctor import run_checks
from genea.kb import KnowledgeBase, init_kb
from genea.transcribe.check import loop_summary


@app.command()
def init(ctx: typer.Context) -> None:
    """Create .genea/ in the knowledge-base folder (--kb, default: current folder)."""
    root = Path(ctx.obj["kb"] or ".").expanduser()
    if not root.is_dir():
        console.print(f"[red]Folder not found: {root}[/red]")
        raise typer.Exit(2)
    kb = init_kb(root)
    console.print(f"Initialized [bold]{kb.meta_dir}[/bold]")


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check that ExifTool, Ollama and the configured models are available."""
    try:
        root = resolve_kb_root(ctx.obj["kb"])
    except KBNotFound:
        root = None
    settings = load_settings(root)
    failed = False
    for check in run_checks(settings):
        mark = "[green]✓[/green]" if check.ok else "[red]✗[/red]"
        console.print(f"{mark} {check.name}: {check.detail}")
        if not check.ok:
            failed = True
            if check.fix:
                console.print(f"    fix: {check.fix}")
    if root is not None:
        try:
            note = loop_summary(KnowledgeBase(root, settings))
        except Exception as exc:  # noqa: BLE001 - informational: one unreadable file must not break doctor
            note = f"transcripts not checked: {exc}"
        if note:
            console.print(f"[dim]{escape(note)}[/dim]")  # informational: it never changes the exit code
    raise typer.Exit(1 if failed else 0)
