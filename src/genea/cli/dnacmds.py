"""`genea dna setup|import|analyze|status`: the only place DNA is computed."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from genea.cli import common
from genea.cli.app import dna_app
from genea.dna import api, runtime
from genea.dna.store import DnaError, HaploCall, list_people, load_haplogroups

_TOOL_FIXES = ("genea dna setup", "uv sync")


def _fail(exc: DnaError) -> typer.Exit:
    common.console.print(f"[red]{escape(str(exc))}[/red]")
    return typer.Exit(1)


def _call_text(call: HaploCall) -> str:
    if call.haplogroup is None:
        return f"{call.line}: skipped — {escape(call.skipped_reason or '')}"
    extra = f" ({call.detail})" if call.detail else ""
    quality = f", quality {call.quality:.2f}" if call.quality is not None else ""
    tool = f" [{call.tool}]" if call.tool else ""
    return f"{call.line}: {call.haplogroup}{extra} from {call.markers_used} markers{quality}{escape(tool)}"


@dna_app.command("setup")
def setup_cmd() -> None:
    """Install Haplogrep 3 (and a portable Java if needed) into ~/.genea/dna-tools; your system Java is not changed."""
    try:
        for message in runtime.setup():
            common.console.print(escape(message))
    except DnaError as exc:
        raise _fail(exc) from exc


@dna_app.command("import")
def import_cmd(ctx: typer.Context, csv: Path = typer.Argument(..., help="MyHeritage raw DNA CSV."),
               person: str = typer.Option(..., "--person", help="Tree id of the donor, like 'tree.ged#@I12@'."),
               name: str | None = typer.Option(None, "--name", help="Short name for the DNA person (default from the id)."),
               replace: bool = typer.Option(False, "--replace", help="Replace an earlier import and its analyses.")) -> None:
    """Import a raw DNA file for one person of the tree (stored under .genea/dna/)."""
    kb = common.open_kb(ctx)
    try:
        result = api.import_file(kb, csv, person, name, replace)
    except DnaError as exc:
        raise _fail(exc) from exc
    meta = result.meta
    who = f"{meta.tree_name} ({meta.gedcom_id})" if meta.tree_name else meta.gedcom_id
    common.console.print(f"Imported {meta.qc.total:,} SNPs, call rate {meta.qc.call_rate:.1%}, DNA sex {meta.sex}.")
    common.console.print(f"Person: {escape(who)}")
    common.console.print(f"DNA name: [bold]{meta.slug}[/bold]")
    for warning in result.warnings:
        common.console.print(f"[yellow]{escape(warning)}[/yellow]")
    common.console.print(f"Next: genea dna analyze {meta.slug}")


@dna_app.command("analyze")
def analyze_cmd(ctx: typer.Context, person: str = typer.Argument(..., help="DNA person name or tree id.")) -> None:
    """Call the mtDNA and Y haplogroups with Haplogrep and yhaplo."""
    kb = common.open_kb(ctx)
    try:
        result = api.analyze(kb, person)
    except DnaError as exc:
        raise _fail(exc) from exc
    missing_tool = False
    for call in result.calls:
        common.console.print(_call_text(call))
        missing_tool |= bool(call.skipped_reason and any(fix in call.skipped_reason for fix in _TOOL_FIXES))
    if missing_tool:
        raise typer.Exit(1)


@dna_app.command("status")
def status_cmd(ctx: typer.Context) -> None:
    """List the imported DNA people of this knowledge base."""
    kb = common.open_kb(ctx)
    people = list_people(kb)
    if not people:
        common.console.print("No DNA imported. Use: genea dna import <csv> --person <tree id>")
        return
    for meta in people:
        who = meta.tree_name or meta.gedcom_id
        common.console.print(f"[bold]{meta.slug}[/bold] {escape(who)}: {meta.qc.total:,} SNPs, call rate "
                             f"{meta.qc.call_rate:.1%}, DNA sex {meta.sex}")
        result = load_haplogroups(kb, meta.slug)
        if result is None:
            common.console.print(f"  not analysed yet: genea dna analyze {meta.slug}")
        else:
            for call in result.calls:
                common.console.print("  " + _call_text(call))
