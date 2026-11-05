"""Markdown reports of an analysis. An iteration's report also embeds each proposal's diff hunk."""

from __future__ import annotations

from genea.analysis.models import SEVERITIES, Analysis, Proposal
from genea.changes.export import describe_operation

STATE_WARNINGS = {
    "outdated": "The tree changed after it was indexed. Run `genea index` and analyze again before proposing "
                "changes: line numbers here may be wrong.",
    "building": "The index is still being built: findings may be incomplete, and a missing record is not "
                "evidence that it does not exist.",
    "interrupted": "The last indexing run did not finish: findings may be incomplete, and a missing record is "
                   "not evidence that it does not exist.",
    "missing": "The knowledge base is not indexed.",
}


def _cites(sources) -> str:
    return ", ".join(s.cite() for s in sources) or "no sources"


def _proposal_lines(p: Proposal, hunks: dict[str, list[str]] | None, missing: set[str]) -> list[str]:
    status = "written, not in the current tree" if p.id in missing else p.status
    lines = [f"  - Proposal **{p.id}** ({p.author}, {status}): {describe_operation(p.operation, p.payload)}",
             f"    - Why: {p.rationale}", f"    - Evidence: {_cites(p.sources)}"]
    if p.stale_reason:
        lines.append(f"    - Not applied: {p.stale_reason}")
    if p.status == "failed" and p.error:
        lines.append(f"    - Not written: {p.error}")
    if p.written and p.written.pr:
        lines.append(f"    - Written {p.written.at}, snapshot {p.written.pr}")
    if hunks and hunks.get(p.id):
        lines += ["", "    ```diff", *[f"    {h}" for h in hunks[p.id]], "    ```", ""]
    return lines


def render_report(a: Analysis, version: int | None = None, hunks: dict[str, list[str]] | None = None,
                  missing: set[str] | None = None) -> str:
    """`missing`: ids of written proposals whose change the indexed tree does not show."""
    missing = missing or set()
    scope = f"{a.scope.kind} {a.scope.id}"
    if a.scope.kind == "branch":
        scope += f" ({a.scope.direction}, depth {a.scope.depth})"
    c = a.counts()
    out = [f"# Analysis {a.name}", "", f"- Scope: {scope}", f"- Tree: {a.ged_file} (sha256 {a.base_hash[:12]})",
           f"- Index: {a.index_state}",
           f"- Findings: {c['error']} error(s), {c['needs_review']} to review, {c['info']} gap(s); "
           f"{c['proposals']} active proposal(s), {c['stale']} stale, {c['dismissed']} dismissed"]
    if version:
        out.append(f"- Iteration: v{version} (`v{version}.ged`, `v{version}.diff`)")
    if a.boundary:
        out.append("- Stopped at the depth limit: " + ", ".join(a.boundary))
    if a.index_state in STATE_WARNINGS:
        out += ["", f"> {STATE_WARNINGS[a.index_state]}"]

    by_finding: dict[str | None, list[Proposal]] = {}
    for p in a.proposals:
        if p.status != "withdrawn":
            by_finding.setdefault(p.finding_id, []).append(p)
    undecided = [f for f in a.findings if f.severity != "info" and a.finding_status(f.id) == "open"]
    out += ["", "## Needs a decision", ""]
    out += [f"- **{f.id}** {f.label}: {f.message}" for f in undecided] or ["Nothing."]

    out += ["", "## Findings"]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    label = None
    for f in sorted(a.findings, key=lambda f: (f.label, order[f.severity], f.kind)):
        if f.label != label:
            label = f.label
            out += ["", f"### {label}", ""]
        status = a.finding_status(f.id)
        if a.dismissed.get(f.id):
            status += f" ({a.dismissed[f.id]})"
        out.append(f"- [{f.severity}] **{f.id}** {f.kind}: {f.message} {_cites(f.sources)} — {status}")
        for p in by_finding.get(f.id, []):
            out += _proposal_lines(p, hunks, missing)

    known = {f.id for f in a.findings}
    other = [p for fid, ps in by_finding.items() if fid not in known for p in ps]
    if other:
        out += ["", "## Other proposals", ""]
        for p in other:
            out.append("- free-standing:" if p.finding_id is None else f"- finding {p.finding_id} is no longer reported:")
            out += _proposal_lines(p, hunks, missing)
    if a.notes:
        out += ["", "## Notes", ""] + [f"- {n.created}: {n.text}" for n in a.notes]
    return "\n".join(out) + "\n"
