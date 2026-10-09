"""Helpers shared by CLI command modules."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.markup import escape

from genea.config import KBNotFound
from genea.index.db import SchemaMismatch
from genea.index.embed import Embedder, make_embedder
from genea.ingest.summarize import Summarizer, make_summarizer
from genea.kb import KnowledgeBase
from genea.tools.base import ToolContext, open_tools

console = Console()


def use_utf8_stdout() -> None:
    """A redirected Windows stdout defaults to cp1252, which cannot encode the check marks.

    Called when the CLI runs (not at import). stderr and its error handler are left alone.
    """
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def open_kb(ctx: typer.Context) -> KnowledgeBase:
    try:
        return KnowledgeBase.open(ctx.obj["kb"])
    except KBNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def get_embedder(kb: KnowledgeBase) -> Embedder:
    return make_embedder(kb.settings)


def get_summarizer(kb: KnowledgeBase) -> Summarizer | None:
    return make_summarizer(kb.settings)


def open_tool_context(kb: KnowledgeBase) -> ToolContext:
    if not kb.index_path.exists():
        console.print("[red]This knowledge base is not indexed yet. Run `genea index` first.[/red]")
        raise typer.Exit(1)
    try:
        return open_tools(kb, get_embedder(kb))
    except SchemaMismatch as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
