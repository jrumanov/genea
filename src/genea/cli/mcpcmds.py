"""`genea mcp`: serve the knowledge base to Claude over MCP (stdio)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape

from genea.cli import common
from genea.cli.app import app
from genea.index.status import index_summary
from genea.tools.base import open_tools


@app.command("mcp")
def mcp_serve(ctx: typer.Context) -> None:
    """Serve the knowledge base to Claude (Code or Desktop) as a read-only MCP server over stdio."""
    err = Console(stderr=True)
    try:
        from genea.mcp_server import IndexGate, serve
    except ImportError as exc:
        err.print(f"[red]The MCP server needs the optional extra: uv sync --extra mcp ({escape(str(exc))})[/red]")
        raise typer.Exit(1) from exc
    # stdout carries the protocol, so every message from the shared CLI helpers must go to stderr.
    stdout_console = common.console
    common.console = err
    try:
        kb = common.open_kb(ctx)  # a missing knowledge base is a setup error: exit before the handshake
        # The index is opened on the first tool call, so Claude can connect before or during indexing
        # and the tools explain the state instead of the connection failing.
        gate = IndexGate(kb, lambda: open_tools(kb, common.get_embedder(kb)))
        summary = index_summary(kb)
        if summary.state != "ready":
            err.print(f"[yellow]{escape(summary.headline())}[/yellow]")
        if kb.settings.privacy_notice:
            # Rich wraps at 80 columns; `will be sent` stays on the first line, where the test looks for it.
            err.print("[yellow]Note: tool results (your family-tree data) will be sent to the connected "
                      "Claude client. (set privacy_notice = false to hide this)[/yellow]")
        serve(gate, lambda url: err.print(f"Citation viewer: {escape(url)} (links in Claude's answers open here)"))
    finally:
        common.console = stdout_console
