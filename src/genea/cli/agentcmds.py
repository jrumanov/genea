"""`genea ask` and `genea chat`."""

from __future__ import annotations

import asyncio

import typer
from rich.markdown import Markdown
from rich.markup import escape

from genea.agent.core import AgentFailed, Answer, build_agent
from genea.agent.core import ask as ask_agent
from genea.agent.model import build_model, is_local
from genea.cli import common
from genea.cli.app import app

SHOW_TOOLS = typer.Option(False, "--show-tools", help="Print every tool call the agent makes.")


def _session(ctx: typer.Context):
    kb = common.open_kb(ctx)
    tools = common.open_tool_context(kb)
    if not is_local(kb.settings) and kb.settings.privacy_notice:
        provider = kb.settings.model.split(":", 1)[0]
        # Two short lines: Rich wraps at 80 columns and the test looks for the first line verbatim.
        common.console.print(f"[yellow]Note: questions and knowledge-base data will be sent to {provider}.[/yellow]")
        common.console.print("[yellow](set privacy_notice = false to hide this)[/yellow]")
    return tools, build_agent(build_model(kb.settings))


def _print(answer: Answer, show_tools: bool) -> None:
    if show_tools:
        for name, args in answer.tool_calls:
            common.console.print(f"[dim]→ {name} {escape(args)}[/dim]")
    common.console.print(Markdown(answer.text))


@app.command()
def ask(ctx: typer.Context, question: str, show_tools: bool = SHOW_TOOLS) -> None:
    """Ask one question about the knowledge base."""
    tools, agent = _session(ctx)
    try:
        answer = ask_agent(agent, tools, question)
    except AgentFailed as exc:
        common.console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    finally:
        tools.conn.close()
    _print(answer, show_tools)


@app.command()
def chat(ctx: typer.Context, show_tools: bool = SHOW_TOOLS) -> None:
    """Interactive conversation with the agent (type 'exit' to quit)."""
    tools, agent = _session(ctx)
    history = None
    runner = asyncio.Runner()  # one event loop for the whole session (see agent.core.ask)
    common.console.print("Ask about the family history; type 'exit' to quit.")
    try:
        while True:
            try:
                question = common.console.input("[bold green]you>[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() in ("exit", "quit"):
                break
            if not question:
                continue
            try:
                answer = ask_agent(agent, tools, question, history, runner=runner)
            except AgentFailed as exc:
                common.console.print(f"[red]{escape(str(exc))}[/red]")
                continue
            history = answer.messages
            _print(answer, show_tools)
    finally:
        runner.close()
        tools.conn.close()
