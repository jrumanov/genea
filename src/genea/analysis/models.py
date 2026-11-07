"""Analysis records: findings from the detectors and proposals from Claude or the user."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from genea.tools.base import ToolError
from genea.tools.models import SourceRef, ToolResult

Severity = Literal["error", "needs_review", "info"]
SEVERITIES = ("error", "needs_review", "info")
PAGE_SIZE = 10  # findings per analyze/read_analysis result, so MCP results stay under tool_output_chars


class ScopeSpec(BaseModel):
    kind: Literal["person", "family", "branch"]
    id: str  # 'tree.ged#@I12@' or 'tree.ged#@F3@'
    direction: Literal["ancestors", "descendants", "both"] = "both"
    depth: int = 4


class Finding(BaseModel):
    id: str
    kind: str
    severity: Severity
    label: str  # "Anna Weiss (tree.ged#@I5@)", for grouping in the report
    message: str
    person_ids: list[str] = Field(default_factory=list)
    family_id: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


Operation = Literal["add_event", "update_event", "add_note", "add_source_citation", "link_child", "add_person"]


class WrittenObject(BaseModel):
    kind: str  # "people", "events", ...
    handle: str
    gramps_id: str | None
    created: bool
    transaction: int  # newest id in /transactions/history/ for this object after the request


class WriteRecord(BaseModel):
    at: str
    objects: list[WrittenObject]
    pr: str | None = None


class Conflict(BaseModel):
    """The proposal disagrees with a value the tree already has; the user decides in the review."""
    line: int  # GEDCOM line of the existing event
    existing: str  # one line: what the tree says
    decision: Literal["undecided", "keep", "replace", "both"] = "undecided"


class Proposal(BaseModel):
    id: str
    finding_id: str | None = None
    author: Literal["claude", "user"]
    operation: Operation  # a closed list: build calls the builder method of this name
    payload: dict[str, Any]
    anchor: dict[str, str | None] | None = None  # update_event, conflicts: the event as it was {type, description, date, place}
    conflict: Conflict | None = None  # add_event / add_note only
    base_hash: str | None = None  # sha256 of the tree the proposal was made against (None: made before it was kept)
    sources: list[SourceRef]
    rationale: str
    created: str
    status: Literal["active", "withdrawn", "stale", "written", "failed"] = "active"
    stale_reason: str | None = None
    written: WriteRecord | None = None
    error: str | None = None


class LastBuild(BaseModel):
    version: int
    base_hash: str  # sha256 of the real tree the build was made from
    proposals: list[str]  # ids of the proposals the build applied


class AnalysisNote(BaseModel):
    created: str
    text: str


class Analysis(BaseModel):
    name: str
    scope: ScopeSpec
    ged_file: str
    base_hash: str
    index_state: str
    created: str
    updated: str
    people: list[str] = Field(default_factory=list)
    boundary: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    dismissed: dict[str, str] = Field(default_factory=dict)  # finding id -> reason ("" if none given)
    notes: list[AnalysisNote] = Field(default_factory=list)
    iterations: int = 0
    last_build: LastBuild | None = None

    def finding_status(self, finding_id: str) -> str:
        if finding_id in self.dismissed:
            return "dismissed"
        if any(p.finding_id == finding_id and p.status == "active" for p in self.proposals):
            return "proposed"
        return "open"

    def counts(self) -> dict[str, int]:
        out = {s: sum(f.severity == s for f in self.findings) for s in SEVERITIES}
        out["proposals"] = sum(p.status == "active" for p in self.proposals)
        out["stale"] = sum(p.status == "stale" for p in self.proposals)
        out["written"] = sum(p.status == "written" for p in self.proposals)
        out["failed"] = sum(p.status == "failed" for p in self.proposals)
        out["dismissed"] = len(self.dismissed)
        return out


class FindingView(Finding):
    status: str


class AnalysisPage(ToolResult):
    name: str
    scope: ScopeSpec
    ged_file: str
    index_state: str
    counts: dict[str, int]
    page: int
    pages: int
    findings: list[FindingView]
    proposals: list[Proposal]
    report: str
    not_in_tree: list[str] = Field(default_factory=list)  # written proposals whose change the index does not show (where the index can show it)
    message: str | None = None


class AnalysisUpdate(ToolResult):
    name: str
    message: str
    proposal_id: str | None = None
    status: str | None = None


class BuildResult(ToolResult):
    name: str
    version: int
    iteration: str
    diff: str
    report: str
    applied: list[str]
    stale: list[str]
    checks: int
    message: str


class AnalysisSummary(BaseModel):
    name: str
    scope: ScopeSpec
    counts: dict[str, int]
    iterations: int
    updated: str


class AnalysisList(ToolResult):
    analyses: list[AnalysisSummary]


def finding_id(kind: str, person_ids: list[str], family_id: str | None, key: str) -> str:
    """Stable id: never built from the message, the evidence or line numbers."""
    text = "|".join([kind, ",".join(sorted(person_ids)), family_id or "", key])
    return "f-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


_CITE = re.compile(r"^\[(?P<file>[^\]]+?)(?::L(?P<line>\d+)| p\.(?P<page>\d+)| @(?P<time>\d+(?::\d{2}){1,2}))?\]$")


def parse_cite(text: str) -> SourceRef:
    """'[tree.ged:L12]', '[docs/x.pdf p.3]', '[recordings/x.txt @14:20]' or '[photos/x.jpg]' -> SourceRef."""
    match = _CITE.match((text or "").strip())
    if not match:
        raise ToolError(f"Not a citation: {text!r}. Write it like [tree.ged:L1042], [docs/x.pdf p.3], "
                        "[recordings/x.txt @14:20] or [photos/x.jpg].")
    start_s = None
    if match["time"]:
        parts = [int(p) for p in match["time"].split(":")]
        start_s = float(sum(p * 60 ** i for i, p in enumerate(reversed(parts))))
    return SourceRef(file=match["file"], line=int(match["line"]) if match["line"] else None,
                     page=int(match["page"]) if match["page"] else None, start_s=start_s)
