"""`genea changes ...`: review, apply, reject, undo and export proposed changes."""

from __future__ import annotations

from typing import NoReturn

import typer
from rich.markup import escape

from genea.changes.apply import ReindexFailed, apply_change, describe_change, mark_manual, undo_change
from genea.changes.export import export_gedcom_changes
from genea.changes.store import STATUSES, ChangeError, ChangeStore
from genea.cli import common
from genea.cli.app import changes_app
from genea.exiftool import ExifToolError


def _fail(exc: Exception) -> NoReturn:
    common.console.print(f"[red]{escape(str(exc))}[/red]")
    raise typer.Exit(1)


def _reindex_failed(done: str, exc: ReindexFailed) -> None:
    common.console.print(f"[yellow]{done} {exc.change.id}; re-index failed ({escape(str(exc))}) "
                         f"— run `genea index`[/yellow]")


def _store(ctx: typer.Context):
    kb = common.open_kb(ctx)
    return kb, ChangeStore(kb)


@changes_app.command("list")
def list_changes(ctx: typer.Context,
                 status: str = typer.Option("pending", "--status",
                                            help="pending, applied, rejected, stale or all")) -> None:
    """List changes (pending by default)."""
    _, store = _store(ctx)
    if status != "all" and status not in STATUSES:
        _fail(ChangeError(f"Unknown status {status!r}; use {', '.join(STATUSES)} or all"))
    changes = store.list(None if status == "all" else status)
    if not changes:
        common.console.print(f"No {status} changes.")
        return
    for c in changes:
        common.console.print(f"{c.id}  {c.status:<8}  {c.operation:<20}  {escape(c.target['path'])}  "
                             f"— {escape(c.rationale[:80])}")


@changes_app.command()
def show(ctx: typer.Context, change_id: str) -> None:
    """Show one change: why, sources, and a before/after view."""
    kb, store = _store(ctx)
    try:
        change = store.get(change_id)
    except ChangeError as exc:
        _fail(exc)
    console = common.console
    console.print(f"[bold]{change.id}[/bold] ({change.status}) {change.operation} → {escape(change.target['path'])}")
    console.print(f"why: {escape(change.rationale)}")
    if change.sources:
        console.print("sources: " + escape(" ".join(s.cite() for s in change.sources)))
    if change.reason:
        console.print(f"reason: {escape(change.reason)}")
    for field, before, after in describe_change(kb, change):
        console.print(f"  {escape(field)}")
        if before:
            console.print(f"    [red]- {escape(before)}[/red]")
        console.print(f"    [green]+ {escape(after)}[/green]")


@changes_app.command("apply")
def apply_command(
    ctx: typer.Context,
    change_id: str | None = typer.Argument(None),
    all_pending: bool = typer.Option(False, "--all", help="Apply every pending image change."),
    manual: bool = typer.Option(False, "--manual",
                                help="Mark a GEDCOM change as done by hand in your genealogy program."),
) -> None:
    """Apply a pending image change (writes the XMP sidecar, with a backup)."""
    kb, store = _store(ctx)
    if manual:
        if not change_id:
            _fail(ChangeError("--manual needs a change id"))
        try:
            done = mark_manual(kb, store, change_id)
        except ChangeError as exc:
            _fail(exc)
        common.console.print(f"{done.id} marked as applied manually.")
        return
    if all_pending:
        ids = [c.id for c in store.list("pending") if c.target["kind"] == "image_xmp"]
    elif change_id:
        ids = [change_id]
    else:
        _fail(ChangeError("Give a change id or --all"))
    embedder = common.get_embedder(kb)
    failed = False
    for cid in ids:
        try:
            applied = apply_change(kb, store, cid, embedder)
            common.console.print(f"[green]applied[/green] {applied.id}")
        except ReindexFailed as exc:
            failed = True
            _reindex_failed("Applied", exc)
        except (ChangeError, ExifToolError) as exc:
            failed = True
            common.console.print(f"[red]{cid}: {escape(str(exc))}[/red]")
    if failed:
        raise typer.Exit(1)


@changes_app.command()
def reject(ctx: typer.Context, change_id: str,
           reason: str = typer.Option("", "--reason", help="Why; kept so the agent won't propose it again.")) -> None:
    """Reject a pending change."""
    _, store = _store(ctx)
    try:
        store.reject(change_id, reason)
    except ChangeError as exc:
        _fail(exc)
    common.console.print(f"{change_id} rejected.")


@changes_app.command()
def undo(ctx: typer.Context, change_id: str) -> None:
    """Undo an applied image change by restoring the sidecar from its backup."""
    kb, store = _store(ctx)
    try:
        undo_change(kb, store, change_id, common.get_embedder(kb))
    except ReindexFailed as exc:
        _reindex_failed("Undone", exc)
        raise typer.Exit(1) from exc
    except ChangeError as exc:
        _fail(exc)
    common.console.print(f"{change_id} undone.")


@changes_app.command()
def export(ctx: typer.Context) -> None:
    """Write pending GEDCOM changes as a checklist (.md) and a GEDCOM fragment (.ged)."""
    kb, store = _store(ctx)
    result = export_gedcom_changes(kb, store)
    if result is None:
        common.console.print("No pending GEDCOM changes.")
        return
    md_path, ged_path = result
    common.console.print(f"checklist: {escape(str(md_path))}\nGEDCOM fragment: {escape(str(ged_path))}")
