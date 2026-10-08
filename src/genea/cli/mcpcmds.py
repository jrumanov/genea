"""`genea mcp`: serve the knowledge base to Claude over MCP (stdio)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape

from genea.cli import common
from genea.cli.app import app


@app.command("mcp")
def mcp_serve(ctx: typer.Context) -> None:
    """Serve the knowledge base to Claude (Code or Desktop) as a read-only MCP server over stdio."""
    err = Console(stderr=True)
    try:
        from genea.mcp_server import serve
    except ImportError as exc:
        err.print(f"[red]The MCP server needs the optional extra: uv sync --extra mcp ({escape(str(exc))})[/red]")
        raise typer.Exit(1) from exc
    # stdout carries the protocol, so every message from the shared CLI helpers must go to stderr.
    stdout_console = common.console
    common.console = err
    try:
        kb = common.open_kb(ctx)
        tools = common.open_tool_context(kb)
        if kb.settings.privacy_notice:
            # Rich wraps at 80 columns; `will be sent` stays on the first line, where the test looks for it.
            err.print("[yellow]Note: tool results (your family-tree data) will be sent to the connected "
                      "Claude client. (set privacy_notice = false to hide this)[/yellow]")
        serve(tools)
    finally:
        common.console = stdout_console
