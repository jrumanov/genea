"""Apply an analysis' proposals to Gramps Web, undo one, or only snapshot the tree (spec §3, §5, §8, §9).

Every precondition is checked before the first Gramps request, and the analysis is saved after each proposal,
so a crash keeps the record of what was written.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from genea.analysis import store
from genea.analysis.api import tree_hash, write_report
from genea.analysis.models import Analysis, Proposal
from genea.changes.export import describe_operation
from genea.gramps.client import Conflict, GrampsClient, GrampsError, open_client, tree_for
from genea.gramps.credentials import CredentialsMissing
from genea.gramps.resolve import Resolver, Stale
from genea.gramps.snapshot import (Exports, Runner, SnapshotError, check_repo, default_runner, open_pr, repo_of,
                                   same_tree)
from genea.gramps.write import SINGULAR, WriteLog, write_proposal
from genea.tools.base import ToolContext, ToolError

_MARKER = re.compile(r"GENEA v\d+ \S+ (p-\d+) \(")
_GRAMPS_ID = re.compile(r"^[PS]\d+$")  # a place or source named by Gramps id, which the index does not hold


@dataclass
class Plan:
    analysis: Analysis
    proposals: list[Proposal]
    diff_hunks: dict[str, str]  # proposal id -> its hunks of v<version>.diff
    repo: Path
    ged_rel: Path  # the .ged relative to repo


@dataclass
class ApplyResult:
    written: list[str]
    stale: dict[str, str]
    failed: dict[str, str]
    pr: str | None
    message: str


def _xref(ref: str) -> str:
    return ref.split("#", 1)[-1]


def _hunks(diff: str) -> dict[str, str]:
    """Split a unified diff into hunks and file each under the proposal ids its GENEA markers name."""
    hunks: list[list[str]] = []
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
    out: dict[str, list[str]] = {}
    for hunk in hunks:
        for pid in dict.fromkeys(m.group(1) for line in hunk for m in _MARKER.finditer(line)):
            out.setdefault(pid, []).extend(hunk)
    return {pid: "\n".join(lines) for pid, lines in out.items()}


def _repo(ctx: ToolContext, ged_file: str, run: Runner) -> tuple[Path, Path]:
    """The tree's git repo and the .ged inside it, after check_repo passed."""
    try:
        tree_for(ctx.kb.settings, ged_file)
    except GrampsError as exc:
        raise ToolError(str(exc)) from exc
    path = ctx.kb.resolve_inside(ged_file).resolve()
    try:
        repo = repo_of(path, run)
        check_repo(repo, run)
    except SnapshotError as exc:
        raise ToolError(f"Nothing was written to Gramps Web: {exc}") from exc
    return repo, path.relative_to(repo.resolve())


def _client(ctx: ToolContext, ged_file: str, transport: httpx.BaseTransport | None) -> GrampsClient:
    try:
        return open_client(ctx.kb, ged_file, transport)
    except (GrampsError, CredentialsMissing) as exc:
        raise ToolError(f"Nothing was written to Gramps Web: {exc}") from exc


def _exports(ctx: ToolContext, client: GrampsClient) -> Exports:
    xml = client.export("gramps") if ctx.kb.settings.gramps_web_xml_snapshot else None
    return Exports(ged=client.export("ged"), xml=xml)


def _save(ctx: ToolContext, analysis: Analysis) -> None:
    analysis.updated = store.now()
    store.save(ctx.kb, analysis)


def prepare(ctx: ToolContext, name: str, only: list[str] | None = None, run: Runner = default_runner) -> Plan:
    """Check every precondition of apply (spec §3.1); no Gramps request is made."""
    analysis = store.load(ctx.kb, name)
    build = analysis.last_build
    if build is None:
        raise ToolError(f"{name} has no build yet; run `genea analysis build {name}` and review it first.")
    if build.base_hash != tree_hash(ctx, analysis.ged_file):
        raise ToolError(f"The tree changed after the last analysis build (v{build.version}); run "
                        f"`genea analysis build {name}` again and review it first.")
    active = {p.id for p in analysis.proposals if p.status == "active"}
    chosen = set(only) if only is not None else active
    unknown = [pid for pid in (only or []) if pid not in active]
    if unknown:
        raise ToolError(f"{', '.join(unknown)} is not an active proposal of {name}.")
    undecided = [p.id for p in analysis.proposals
                 if p.id in chosen and p.conflict is not None and p.conflict.decision == "undecided"]
    if undecided:
        raise ToolError(f"{', '.join(undecided)} have an undecided conflict; decide keep, replace or both in the "
                        "review PR.")
    if chosen & set(build.proposals) != chosen or (only is None and set(build.proposals) != active):
        raise ToolError(f"The proposals changed after the last analysis build (v{build.version}); run "
                        f"`genea analysis build {name}` again and review it first.")
    if not chosen:
        raise ToolError(f"{name} has no active proposals to apply.")
    repo, ged_rel = _repo(ctx, analysis.ged_file, run)
    diff = store.analysis_dir(ctx.kb, name) / f"v{build.version}.diff"
    hunks = _hunks(diff.read_text(encoding="utf-8")) if diff.is_file() else {}
    proposals = [p for p in analysis.proposals if p.id in chosen]
    return Plan(analysis=analysis, proposals=proposals, diff_hunks=hunks, repo=repo, ged_rel=ged_rel)


def _created_person(p: Proposal) -> str | None:
    if p.operation != "add_person" or p.written is None:
        return None
    return next((o.handle for o in p.written.objects if o.kind == "people" and o.created), None)


def _body(analysis: Analysis, written: list[Proposal], stale: dict[str, str], failed: dict[str, str]) -> str:
    out = []
    for p in written:
        out += [f"- {p.id}: {describe_operation(p.operation, p.payload)}", f"  Why: {p.rationale}",
                f"  Evidence: {', '.join(s.cite() for s in p.sources) or 'no sources'}"]
    if stale or failed:
        out += ["", "Not written:"]
        out += [f"- {pid} (stale): {why}" for pid, why in stale.items()]
        out += [f"- {pid} (failed): {why}" for pid, why in failed.items()]
    return "\n".join(out)


def _write_all(ctx: ToolContext, analysis: Analysis, plan: Plan, client: GrampsClient, resolver: Resolver,
               log: WriteLog, new_people: dict[str, str], written: list[Proposal], stale: dict[str, str],
               failed: dict[str, str]) -> None:
    for p in sorted(plan.proposals, key=lambda p: p.operation != "add_person"):  # new people first
        try:
            record = write_proposal(client, resolver.resolve(p, new_people), log)
        except Stale as exc:
            p.status, p.stale_reason, p.error = "stale", str(exc), None
            stale[p.id] = p.stale_reason
        except Conflict as exc:
            if "could not be restored" in str(exc):  # partly applied: not stale, the user must check it
                p.status, p.stale_reason, p.error = "failed", None, f"changed in Gramps while writing: {exc}"
                failed[p.id] = p.error
            else:
                p.status, p.stale_reason, p.error = "stale", f"changed in Gramps while writing: {exc}", None
                stale[p.id] = p.stale_reason
        except (ValueError, GrampsError) as exc:
            p.status, p.stale_reason, p.error = "failed", None, str(exc)
            failed[p.id] = p.error
        else:
            p.status, p.stale_reason, p.error, p.written = "written", None, None, record
            written.append(p)
            if any(o.kind == "places" for o in record.objects):
                resolver.forget_places()  # the next proposal of this run may use the new place
            if handle := _created_person(p):
                new_people[p.id] = handle
        _save(ctx, analysis)  # after each proposal, so a crash keeps what was written


def apply_analysis(ctx: ToolContext, plan: Plan, transport: httpx.BaseTransport | None = None,
                   run: Runner = default_runner) -> ApplyResult:
    analysis = plan.analysis
    try:
        check_repo(plan.repo, run)  # again: the user may have taken a while to confirm the preview
    except SnapshotError as exc:
        raise ToolError(f"Nothing was written to Gramps Web: {exc}") from exc
    written: list[Proposal] = []
    stale: dict[str, str] = {}
    failed: dict[str, str] = {}
    pr, note = None, ""
    with _client(ctx, analysis.ged_file, transport) as client:
        try:
            before = _exports(ctx, client)
        except GrampsError as exc:
            raise ToolError(f"Nothing was written to Gramps Web: the export failed: {exc}") from exc
        resolver, log = Resolver(client), WriteLog(store.analysis_dir(ctx.kb, analysis.name) / "writes.jsonl")
        new_people = {p.id: h for p in analysis.proposals if (h := _created_person(p))}
        try:
            _write_all(ctx, analysis, plan, client, resolver, log, new_people, written, stale, failed)
        except Exception as exc:
            if not written:
                raise
            ids = ", ".join(p.id for p in written)
            raise ToolError(f"The write stopped ({type(exc).__name__}: {exc}) after {ids} were written to Gramps "
                            f"Web; they are recorded. Run `genea snapshot {analysis.ged_file}` for the PR.") from exc
        if written:
            title = f"genea: {analysis.name} ({len(written)} changes)"
            try:
                after = _exports(ctx, client)
                pr = open_pr(plan.repo, plan.ged_rel, analysis.name, before, after,
                             _body(analysis, written, stale, failed), title, run)
            except (GrampsError, SnapshotError) as exc:
                note = f" The changes are in Gramps Web, but the snapshot PR failed: {exc}"
            else:
                for p in written:
                    p.written.pr = pr
    _save(ctx, analysis)
    write_report(ctx, analysis)
    message = f"Wrote {len(written)} proposal(s) to Gramps Web, {len(stale)} stale, {len(failed)} failed."
    if pr:
        message += f" Snapshot PR: {pr}. Merge it, `git pull`, then run `genea index`."
    elif not written:
        message += " Nothing was written, so no snapshot PR was opened."
    return ApplyResult(written=[p.id for p in written], stale=stale, failed=failed, pr=pr, message=message + note)


def _name(kind: str, gramps_id: str | None, handle: str) -> str:
    return f"{SINGULAR.get(kind, kind)} {gramps_id or handle}"


def undo_proposal(ctx: ToolContext, name: str, proposal_id: str, transport: httpx.BaseTransport | None = None,
                  run: Runner = default_runner) -> ApplyResult:
    analysis = store.load(ctx.kb, name)
    p = next((x for x in analysis.proposals if x.id == proposal_id), None)
    if p is None:
        raise ToolError(f"No proposal {proposal_id} in {name}.")
    if p.status != "written" or p.written is None:
        raise ToolError(f"{proposal_id} is {p.status}; only a written proposal can be undone.")
    users = [x.id for x in analysis.proposals
             if x.status == "written" and x.payload.get("child_id") == f"proposal:{proposal_id}"]
    if users:
        raise ToolError(f"{', '.join(users)} link(s) the person {proposal_id} added; undo {users[0]} first.")
    unknown = [o for o in p.written.objects if o.transaction == 0]
    if unknown:
        raise ToolError(f"The Gramps transaction of {_name(unknown[0].kind, unknown[0].gramps_id, unknown[0].handle)} "
                        f"is unknown, so {proposal_id} cannot be undone through Gramps Web; fix it in Gramps by hand.")
    repo, ged_rel = _repo(ctx, analysis.ged_file, run)
    failed: dict[str, str] = {}
    undone = 0
    pr, note = None, ""
    with _client(ctx, analysis.ged_file, transport) as client:
        try:
            before = _exports(ctx, client)
        except GrampsError as exc:
            raise ToolError(f"Nothing was undone: the export failed: {exc}") from exc
        done: set[int] = set()
        total = len({o.transaction for o in p.written.objects})
        for obj in sorted(p.written.objects, key=lambda o: -o.transaction):  # newest first
            if obj.transaction not in done:
                try:
                    client.undo(obj.transaction)
                except GrampsError as exc:
                    what = _name(obj.kind, obj.gramps_id, obj.handle)
                    failed[p.id] = f"Gramps refused to undo {what} ({exc}); fix it in Gramps by hand"
                    if undone:
                        failed[p.id] += (f". {undone} of {total} transaction(s) were undone in Gramps, the rest "
                                         f"stay; fix {what} in Gramps")
                    break
                done.add(obj.transaction)
                undone += 1
            p.written.objects.remove(obj)  # a retry does not undo it twice
            _save(ctx, analysis)
        if not failed:
            p.status, p.written = "active", None
            _save(ctx, analysis)
        if undone:
            body = f"Undid {proposal_id} of {name}: {describe_operation(p.operation, p.payload)}."
            if failed:
                body += f"\n\nNot undone: {failed[p.id]}."
            try:
                pr = open_pr(repo, ged_rel, name, before, _exports(ctx, client), body,
                             f"genea: undo {name} {proposal_id}", run)
            except (GrampsError, SnapshotError) as exc:
                note = f" The snapshot PR failed: {exc}"
    write_report(ctx, analysis)
    if failed:
        message = f"{proposal_id} stays written: {failed[p.id]}."
    else:
        message = f"Undid {proposal_id} in Gramps Web; it is active again."
    if pr:
        message += f" Snapshot PR: {pr}."
    return ApplyResult(written=[], stale={}, failed=failed, pr=pr, message=message + note)


def snapshot_only(ctx: ToolContext, ged_file: str, transport: httpx.BaseTransport | None = None,
                  run: Runner = default_runner) -> str:
    """Open a snapshot PR of the Gramps Web tree as it is; it holds no genea writes."""
    repo, ged_rel = _repo(ctx, ged_file, run)
    with _client(ctx, ged_file, transport) as client:
        try:
            now = _exports(ctx, client)
        except GrampsError as exc:
            raise ToolError(f"The export failed: {exc}") from exc
    on_main = (repo / ged_rel).read_bytes() if (repo / ged_rel).exists() else None
    xml_path = repo / ged_rel.with_suffix(".gramps")
    same_xml = now.xml is None or (xml_path.exists() and xml_path.read_bytes() == now.xml)
    if on_main is not None and same_tree(now.ged, on_main) and same_xml:
        return "Gramps Web matches main; no snapshot PR was opened."
    today = date.today().isoformat()
    body = "A snapshot of the tree as it is in Gramps Web. It holds no genea writes."
    # `before` is main itself, so open_pr makes only the one snapshot commit
    try:
        url = open_pr(repo, ged_rel, "snapshot", Exports(ged=on_main or b"", xml=None), now, body,
                      f"Snapshot {today} from Gramps Web", run)
    except SnapshotError as exc:
        raise ToolError(str(exc)) from exc
    return f"Snapshot PR: {url}"


def written_but_missing(ctx: ToolContext, analysis: Analysis, p: Proposal) -> bool:
    """Whether a written proposal's change is not visible in the indexed tree (where the index can show it).
    False when it cannot be checked."""
    if p.status != "written":
        return False
    ged, pl, q = analysis.ged_file, p.payload, ctx.conn.execute
    if p.operation in ("add_event", "add_note") and not pl.get("person_id"):
        return False  # a family's events and notes are not in the index
    if p.operation in ("update_event", "add_event"):
        tag = (p.anchor or {}).get("type") if p.operation == "update_event" else pl.get("type")
        rows = q("SELECT date_raw, place FROM events WHERE ged_file = ? AND owner_xref = ? AND type = ?",
                 (ged, _xref(pl["person_id"]), tag)).fetchall()
        place = pl.get("place")
        if place and _GRAMPS_ID.match(place):
            place = None
        return not any((not pl.get("date") or r["date_raw"] == pl["date"]) and (not place or r["place"] == place)
                       for r in rows)
    if p.operation == "add_note":
        row = q("SELECT notes FROM persons WHERE ged_file = ? AND xref = ?",
                (ged, _xref(pl["person_id"]))).fetchone()
        return row is None or pl["text"].strip() not in (row["notes"] or "")
    if p.operation == "add_source_citation":
        if not pl.get("event_type") or _GRAMPS_ID.match(pl["source"]):
            return False  # person-level citations and Gramps ids are not in the index
        rows = q("SELECT source_citations FROM events WHERE ged_file = ? AND owner_xref = ? AND type = ?",
                 (ged, _xref(pl["person_id"]), pl["event_type"])).fetchall()
        return not any(c.get("source") == pl["source"] for r in rows for c in json.loads(r["source_citations"]))
    if p.operation == "link_child":
        child = pl["child_id"]
        if child.startswith("proposal:"):
            ref = next((x for x in analysis.proposals if x.id == child.split(":", 1)[1]), None)
            gid = next((o.gramps_id for o in (ref.written.objects if ref and ref.written else [])
                        if o.kind == "people" and o.created), None)
            if not gid:
                return False
            child = f"@{gid}@"
        row = q("SELECT 1 FROM family_children WHERE ged_file = ? AND family_xref = ? AND child_xref = ?",
                (ged, _xref(pl["family_id"]), _xref(child))).fetchone()
        return row is None
    if p.operation == "add_person":
        gid = next((o.gramps_id for o in (p.written.objects if p.written else [])
                    if o.kind == "people" and o.created), None)
        if gid:
            row = q("SELECT 1 FROM persons WHERE ged_file = ? AND xref = ?", (ged, f"@{gid}@")).fetchone()
        else:
            row = q("SELECT 1 FROM persons WHERE ged_file = ? AND given = ? AND surname = ?",
                    (ged, pl["given"], pl["surname"])).fetchone()
        return row is None
    return False
