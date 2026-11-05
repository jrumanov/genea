"""`genea gramps ...`, `genea snapshot`, `genea analysis apply` and `undo`: write approved proposals to Gramps Web."""

from __future__ import annotations

from typing import NoReturn

import typer
from rich.markup import escape

from genea.analysis.apply import ApplyResult, Plan, apply_analysis, prepare, snapshot_only, undo_proposal
from genea.changes.export import describe_operation
from genea.cli import common
from genea.cli.app import analysis_app, app, gramps_app
from genea.gramps.client import GrampsClient, GrampsError, _check_url, open_client, tree_for
from genea.gramps.credentials import CredentialsMissing, store_password
from genea.gramps.snapshot import SnapshotError, default_runner
from genea.tools.base import ToolError

TRANSPORT = None  # tests inject an httpx transport
RUNNER = default_runner  # tests inject a git/gh runner
_ERRORS = (ToolError, GrampsError, SnapshotError, CredentialsMissing)


def _fail(exc: Exception) -> NoReturn:
    common.console.print(f"[red]{escape(str(exc))}[/red]")
    raise typer.Exit(1) from exc


@gramps_app.command()
def login(ctx: typer.Context, ged_file: str = typer.Argument(..., metavar="GED", help="The KB-relative .ged")) -> None:
    """Store the Gramps Web password of a tree's user in the OS keyring, after a test login."""
    kb = common.open_kb(ctx)
    try:
        user = tree_for(kb.settings, ged_file)
        if not kb.settings.gramps_web_url:
            raise GrampsError("gramps_web_url is not set in the KB config")
        _check_url(kb.settings.gramps_web_url)
        password = typer.prompt(f"Gramps Web password of '{user}'", hide_input=True)
        with GrampsClient(kb.settings.gramps_web_url, user, password, kb.meta_dir / "gramps_web.json",
                          TRANSPORT) as client:
            tree = client.login()
            version = client.info()["version"]
        store_password(user, password)
    except (GrampsError, ImportError) as exc:
        _fail(exc)
    common.console.print(f"Logged in as {escape(user)}: tree {escape(tree)}, Gramps Web API {escape(version)}. "
                         f"The password is stored in the keyring.")


@gramps_app.command()
def check(ctx: typer.Context,
          ged_file: str | None = typer.Argument(None, metavar="GED", help="One .ged; default: every mapped one")) -> None:
    """Log in and show the user, tree, version and write permissions for each mapped tree."""
    kb = common.open_kb(ctx)
    files = [ged_file] if ged_file else sorted(kb.settings.gramps_web_trees)
    if not files:
        _fail(GrampsError("No tree is mapped; add it under [gramps_web_trees] in the KB config"))
    for ged in files:
        try:
            with open_client(kb, ged, TRANSPORT) as client:
                info = client.info()
        except _ERRORS as exc:
            _fail(exc)
        user, perms = tree_for(kb.settings, ged), info["permissions"]
        common.console.print(f"[bold]{escape(ged)}[/bold]: user {escape(user)}, tree {escape(info['tree'])}, "
                             f"Gramps Web API {escape(info['version'])}")
        for perm in ("AddObject", "EditObject", "DeleteObject"):
            common.console.print(f"  {perm}: {'yes' if perm in perms else 'no'}")
        if "DeleteObject" not in perms:
            common.console.print("[yellow]DeleteObject is missing: clean-up and undo need it. "
                                 "Give the user the Editor role or higher.[/yellow]")


def _with_tools(ctx: typer.Context, fn, *args):
    kb = common.open_kb(ctx)
    tools = common.open_tool_context(kb)
    try:
        return fn(tools, *args)
    except _ERRORS as exc:
        _fail(exc)
    finally:
        tools.conn.close()


@app.command()
def snapshot(ctx: typer.Context, ged_file: str = typer.Argument(..., metavar="GED", help="The KB-relative .ged")) -> None:
    """Open a PR with the tree as it is in Gramps Web (no genea writes)."""
    common.console.print(escape(_with_tools(ctx, lambda tools, g: snapshot_only(tools, g, TRANSPORT, RUNNER),
                                            ged_file)))


def _print_plan(plan: Plan) -> None:
    console = common.console
    for p in plan.proposals:
        console.print(f"[bold]{p.id}[/bold]  {escape(describe_operation(p.operation, p.payload))}")
        console.print(f"  Evidence: {escape(', '.join(s.cite() for s in p.sources) or 'no sources')}")
        hunk = plan.diff_hunks.get(p.id)
        if hunk:
            console.print(escape(hunk), highlight=False)


def _print_result(result: ApplyResult) -> None:
    console = common.console
    console.print(escape(result.message))
    for pid in result.written:
        console.print(f"written: {pid}")
    for pid, why in result.stale.items():
        console.print(f"[yellow]stale: {pid}: {escape(why)}[/yellow]")
    for pid, why in result.failed.items():
        console.print(f"[red]failed: {pid}: {escape(why)}[/red]")


@analysis_app.command()
def apply(ctx: typer.Context, name: str,
          proposal: list[str] | None = typer.Option(None, "--proposal", help="Only this proposal; repeatable"),
          dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written and stop"),
          yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation")) -> None:
    """Write the active proposals of the last build to Gramps Web and open a snapshot PR."""
    plan = _with_tools(ctx, lambda tools: prepare(tools, name, proposal or None, RUNNER))
    _print_plan(plan)
    if dry_run:
        return
    if not yes and not typer.confirm(f"Write {len(plan.proposals)} change(s) to Gramps Web?"):
        common.console.print("Nothing was written.")
        raise typer.Exit(1)
    result = _with_tools(ctx, lambda tools: apply_analysis(tools, plan, TRANSPORT, RUNNER))
    _print_result(result)
    if result.pr:
        common.console.print("Merge the PR, git pull, then genea index.")


@analysis_app.command()
def undo(ctx: typer.Context, name: str, proposal_id: str,
         yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation")) -> None:
    """Undo a written proposal in Gramps Web (its transactions) and open a snapshot PR."""
    if not yes and not typer.confirm(f"Undo {proposal_id} of {name} in Gramps Web?"):
        common.console.print("Nothing was undone.")
        raise typer.Exit(1)
    result = _with_tools(ctx, lambda tools: undo_proposal(tools, name, proposal_id, TRANSPORT, RUNNER))
    common.console.print(escape(result.message))
    if result.failed:
        raise typer.Exit(1)
