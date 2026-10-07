"""`genea init` and `genea doctor`."""

from __future__ import annotations

from pathlib import Path

import typer

from genea.cli.app import app
from genea.cli.common import console
from genea.config import KBNotFound, load_settings, resolve_kb_root
from genea.doctor import run_checks
from genea.kb import init_kb


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
    failed = False
    for check in run_checks(load_settings(root)):
        mark = "[green]✓[/green]" if check.ok else "[red]✗[/red]"
        console.print(f"{mark} {check.name}: {check.detail}")
        if not check.ok:
            failed = True
            if check.fix:
                console.print(f"    fix: {check.fix}")
    raise typer.Exit(1 if failed else 0)
