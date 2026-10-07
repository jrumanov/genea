"""`genea person` (and, from Task 11, `genea conflicts`): direct tool access without an LLM."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from genea.cli import common
from genea.cli.app import app
from genea.tools.base import ToolError
from genea.tools.conflicts import find_conflicts
from genea.tools.models import PersonDetail
from genea.tools.people import find_person, get_person


def _print_person(d: PersonDetail) -> None:
    console = common.console
    console.print(f"[bold]{escape(d.name)}[/bold]  {escape(d.id)}  {d.sex or ''}")
    if d.name_variants[1:]:
        console.print("also known as: " + escape(", ".join(d.name_variants[1:])))
    table = Table("event", "date", "place", "source")
    for e in d.events:
        table.add_row(e.type, escape(e.date or ""), escape(e.place or ""), escape(e.source.cite()))
    console.print(table)
    for label, people in (("parents", d.parents), ("spouses", d.spouses), ("children", d.children)):
        if people:
            console.print(f"{label}: " + escape("; ".join(f"{p.name} ({p.id})" for p in people)))
    if d.notes:
        console.print("notes: " + escape(d.notes))


@app.command()
def person(ctx: typer.Context,
           query: str = typer.Argument(..., help="A name, or an id such as tree.ged#@I12@")) -> None:
    """Look up a person in the family tree (no LLM involved)."""
    kb = common.open_kb(ctx)
    tools = common.open_tool_context(kb)
    try:
        if "#" in query or query.strip().startswith("@"):
            _print_person(get_person(tools, query))
            return
        matches = find_person(tools, query).candidates
        if not matches:
            common.console.print("No matches.")
            raise typer.Exit(1)
        if len(matches) == 1:
            _print_person(get_person(tools, matches[0].id))
            return
        table = Table("id", "name", "born", "died")
        for m in matches:
            table.add_row(escape(m.id), escape(m.name), escape(m.birth or ""), escape(m.death or ""))
        common.console.print(table)
    except ToolError as exc:
        common.console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    finally:
        tools.conn.close()


@app.command()
def conflicts(ctx: typer.Context,
              pid: str | None = typer.Argument(None, metavar="[PERSON_ID]",
                                               help="Check one person; default: everyone.")) -> None:
    """List impossible or suspicious data in the tree (no LLM involved)."""
    kb = common.open_kb(ctx)
    tools = common.open_tool_context(kb)
    try:
        result = find_conflicts(tools, pid)
    except ToolError as exc:
        common.console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    finally:
        tools.conn.close()
    if not result.conflicts:
        common.console.print("No conflicts found.")
        return
    for c in result.conflicts:
        color = "red" if c.severity == "error" else "yellow"
        cites = " ".join(s.cite() for s in c.sources)
        common.console.print(f"[{color}]{c.severity}[/{color}] {c.kind} {escape(c.person_id)}: "
                             f"{escape(c.message)} {escape(cites)}")
