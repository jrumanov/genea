# Analyze and Propose Changes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `genea analyze` (CLI) and `analyze`/`propose`/`build_iteration` (MCP) find conflicts and gaps in a person,
family or branch, record cited proposals from Claude or the user, and build a full-copy GEDCOM iteration with
every proposal applied and marked by a `GENEA` note — all under `.genea/analyses/`, never touching the real tree.

**Architecture:** A new package `genea.analysis`: `scope` resolves the people, `detectors` (reusing
`tools/conflicts.py`) produce `Finding`s, `store` keeps `analysis.json` per scope, `api` holds the operations
shared by CLI and MCP, `gedwrite` inserts/replaces lines in a losslessly parsed GEDCOM, `iteration` applies the
proposals to a copy, `report` renders Markdown. Every iteration is rebuilt from the real `.ged`.

**Tech Stack:** Python 3, pydantic models, typer CLI, the MCP Python SDK (`mcp.server.mcpserver`), SQLite index,
pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-10-11-analyze-propose-changes-design.md`

## Global Constraints

- Writes happen only inside `<kb>/.genea/analyses/`; the real `.ged`, documents and photos are never modified.
- Analysis names match `^[\w.-]+$`, are not `.`/`..`, and resolve to a direct child of `kb.analyses_dir`.
- Proposals use only the six existing GEDCOM operations: `add_event`, `update_event`, `add_note`,
  `add_source_citation`, `link_child`, `add_person`, validated with `genea.changes.store.validate_change`.
- Every proposal needs at least one citation of an indexed file and a non-empty rationale.
- Applied proposals are marked with a `NOTE` starting `GENEA v<N> <YYYY-MM-DD> <proposal-id> (<author>…)`;
  unresolved `error`/`needs_review` findings get `GENEA CHECK v<N> …`; `info` gaps go only into the report.
- New records get xrefs `@GENEA<n>@`, unique in the file.
- Notes split with `CONT` at newlines and `CONC` at 248 characters, never at a space.
- Writable encodings: UTF-8 (with or without BOM) and ANSI/CP1250. ANSEL and UTF-16 files are refused.
- Detector thresholds: probably deceased = born more than 110 years ago; siblings flagged when more than 7 and
  less than 270 days apart (`needs_review`); born after mother's death compares `BIRT` only (`error`).
- Branch scope: default `both`, depth 4, clamped to 1–6.
- Finding ids hash kind + person/family + a key (event type and raw date, or passage) — never line numbers.
- MCP results stay under `tool_output_chars` (12,000): `analyze`/`read_analysis` return 10 findings per page.
- MCP has no `dismiss` tool; dismissing is the user's decision (CLI only).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A tree with no `0 TRLR`, or whose last line has no line ending — new records must still land on their own
   lines and the file must stay valid (test in Task 7).
2. The same event twice in one record and a Gramps re-export that shifts the lines — the proposal must become
   `stale`, never be applied to the other copy (test in Task 8).
3. A CP1250 tree and proposal text with characters CP1250 cannot hold (Cyrillic, the `→` arrow in descriptions)
   — the arrow becomes `->`, an unencodable proposal becomes `stale` with a clear reason, the others still apply
   (test in Task 8).
4. The tree edited after `genea index` — `analyze` must report `outdated` and `propose` must refuse, since its
   line numbers would be wrong (tests in Tasks 5 and 6).
5. A person deleted from the tree in Gramps after a proposal was recorded — that proposal becomes `stale`
   ("no longer in the tree"), its CHECK note is skipped, every other proposal is still applied (test in Task 8).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/genea/analysis/__init__.py` | empty package marker |
| `src/genea/analysis/models.py` | `ScopeSpec`, `Finding`, `Proposal`, `Analysis`, result models, `finding_id`, `parse_cite` |
| `src/genea/analysis/store.py` | names, folder safety, atomic writes, load/save/list, `now()` |
| `src/genea/analysis/scope.py` | `Scope`, `resolve_scope` |
| `src/genea/analysis/detectors.py` | `detect(ctx, scope)` → findings |
| `src/genea/analysis/report.py` | `render_report(analysis, version, hunks)` |
| `src/genea/analysis/gedwrite.py` | `split_value`, `text_lines`, `GedEditor`, `GedWriteError` |
| `src/genea/analysis/iteration.py` | `build(kb, analysis)` → writes `vN.ged/.diff/.md` |
| `src/genea/analysis/api.py` | `analyze`, `read_analysis`, `list_all`, `propose`, `withdraw`, `dismiss`, `add_note`, `build_iteration` |
| `src/genea/cli/analysiscmds.py` | `genea analyze`, `genea analysis list/show/propose/withdraw/dismiss/build` |
| Modify `src/genea/kb.py` | `analyses_dir`, `"analyses"` in `SUBDIRS` |
| Modify `src/genea/tools/people.py` | rename `_children_in` → `children_in` |
| Modify `src/genea/tools/conflicts.py` | public `check_person`, two new rules |
| Modify `src/genea/changes/export.py` | public `describe_operation` |
| Modify `src/genea/ingest/gedcom/mapper.py` | skip `GENEA` notes |
| Modify `src/genea/mcp_server.py`, `src/genea/agent/prompt.py` | analysis tools, rule 4 |
| Modify `src/genea/cli/app.py`, `src/genea/cli/main.py` | register commands |
| `tests/fixtures/analysis_tree.py` | test tree, `make_analysis_kb`, `event_line` |
| Modify `tests/conftest.py` | `analysis_kb`, `actx` fixtures |
| Tests | `test_analysis_store.py`, `test_analysis_scope.py`, `test_analysis_detectors.py`, `test_analysis_api.py`, `test_analysis_proposals.py`, `test_gedwrite.py`, `test_analysis_iteration.py`, `test_mcp_analysis.py`; edits to `test_conflicts.py`, `test_gedcom_mapper.py`, `test_mcp_server.py`, `test_prompt.py` |

---

### Task 1: Analysis models and store

**Files:**
- Create: `src/genea/analysis/__init__.py`, `src/genea/analysis/models.py`, `src/genea/analysis/store.py`
- Modify: `src/genea/kb.py` (SUBDIRS and a new property next to `summaries_dir`)
- Test: `tests/test_analysis_store.py`

**Interfaces:**
- Produces: `ScopeSpec(kind, id, direction="both", depth=4)`; `Finding(id, kind, severity, label, message,
  person_ids, family_id, sources)`; `Proposal(id, finding_id, author, operation, payload, anchor, sources,
  rationale, created, status, stale_reason)`; `AnalysisNote(created, text)`; `Analysis(...)` with
  `finding_status(fid) -> "open"|"proposed"|"dismissed"` and `counts() -> dict[str, int]`; `FindingView(Finding
  + status)`; `AnalysisPage`, `AnalysisUpdate`, `BuildResult`, `AnalysisSummary`, `AnalysisList` (all
  `ToolResult`s); `finding_id(kind, person_ids, family_id, key) -> "f-xxxxxxxx"`; `parse_cite(text) -> SourceRef`;
  `PAGE_SIZE = 10`; `SEVERITIES`.
- Produces (store): `analysis_dir(kb, name) -> Path`, `scope_name(ged_file, spec) -> str`,
  `write_atomic(path, data: str | bytes)`, `exists(kb, name)`, `load(kb, name) -> Analysis`,
  `save(kb, analysis) -> Path`, `list_analyses(kb) -> list[Analysis]`, `now() -> str`.
- Produces (kb): `KnowledgeBase.analyses_dir` (`<root>/.genea/analyses`).

- [ ] **Step 1: Write the failing tests**

`tests/test_analysis_store.py`:

```python
import pytest

from genea.analysis import store
from genea.analysis.models import Analysis, ScopeSpec, finding_id, parse_cite
from genea.kb import init_kb
from genea.tools.base import ToolError
from genea.tools.models import SourceRef


def _analysis(name="tree-person-I1"):
    return Analysis(name=name, scope=ScopeSpec(kind="person", id="tree.ged#@I1@"), ged_file="tree.ged",
                    base_hash="ab" * 32, index_state="ready", created="2026-10-11T10:00:00",
                    updated="2026-10-11T10:00:00")


def test_init_creates_the_analyses_folder(tmp_path):
    kb = init_kb(tmp_path)
    assert kb.analyses_dir == tmp_path.resolve() / ".genea" / "analyses"
    assert kb.analyses_dir.is_dir()


def test_save_and_load_round_trip(tmp_path):
    kb = init_kb(tmp_path)
    analysis = _analysis()
    analysis.dismissed["f-1"] = "known"
    path = store.save(kb, analysis)
    assert path == kb.analyses_dir / "tree-person-I1" / "analysis.json"
    assert store.load(kb, "tree-person-I1") == analysis
    assert [a.name for a in store.list_analyses(kb)] == ["tree-person-I1"]


@pytest.mark.parametrize("name", ["", ".", "..", "...", "../x", "a/b", "a\\b", "x y", "C:x"])
def test_bad_names_are_refused(tmp_path, name):
    kb = init_kb(tmp_path)
    with pytest.raises(ToolError, match="Invalid analysis name"):
        store.analysis_dir(kb, name)


def test_missing_and_damaged_analyses(tmp_path):
    kb = init_kb(tmp_path)
    with pytest.raises(ToolError, match="No analysis named"):
        store.load(kb, "nothing")
    folder = kb.analyses_dir / "broken"
    folder.mkdir()
    (folder / "analysis.json").write_text("{", encoding="utf-8")
    with pytest.raises(ToolError, match="damaged"):
        store.load(kb, "broken")
    assert store.list_analyses(kb) == []


def test_scope_names():
    assert store.scope_name("tree.ged", ScopeSpec(kind="person", id="tree.ged#@I12@")) == "tree-person-I12"
    assert store.scope_name("sub dir/my tree.ged", ScopeSpec(kind="family", id="sub dir/my tree.ged#@F3@")) \
        == "my_tree-family-F3"
    spec = ScopeSpec(kind="branch", id="tree.ged#@I1@", direction="ancestors", depth=3)
    assert store.scope_name("tree.ged", spec) == "tree-branch-I1-ancestors-3"


def test_finding_ids_are_stable_and_distinct():
    a = finding_id("missing_birth", ["tree.ged#@I1@"], None, "")
    assert a == finding_id("missing_birth", ["tree.ged#@I1@"], None, "")
    assert a != finding_id("missing_birth", ["tree.ged#@I2@"], None, "")
    assert a.startswith("f-") and len(a) == 10


@pytest.mark.parametrize(("text", "ref"), [
    ("[tree.ged:L1042]", SourceRef(file="tree.ged", line=1042)),
    ("[docs/matrika.pdf p.3]", SourceRef(file="docs/matrika.pdf", page=3)),
    ("[recordings/x y.txt @14:20]", SourceRef(file="recordings/x y.txt", start_s=860.0)),
    ("[recordings/x.txt @1:02:03]", SourceRef(file="recordings/x.txt", start_s=3723.0)),
    (" [photos/x.jpg] ", SourceRef(file="photos/x.jpg")),
])
def test_parse_cite(text, ref):
    assert parse_cite(text) == ref


@pytest.mark.parametrize("text", ["tree.ged:L1", "[]", "[a] b", ""])
def test_parse_cite_rejects(text):
    with pytest.raises(ToolError, match="Not a citation"):
        parse_cite(text)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analysis_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.analysis'`

- [ ] **Step 3: Add the folder to the knowledge base**

In `src/genea/kb.py` change `SUBDIRS` to:

```python
SUBDIRS = ("notes", "changes/pending", "changes/applied", "changes/rejected",
           "changes/stale", "backups", "logs", "exports", "summaries", "analyses")
```

and add after the `summaries_dir` property:

```python
    @property
    def analyses_dir(self) -> Path:
        return self.meta_dir / "analyses"
```

- [ ] **Step 4: Write the models**

`src/genea/analysis/__init__.py`: empty file.

`src/genea/analysis/models.py`:

```python
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


class Proposal(BaseModel):
    id: str
    finding_id: str | None = None
    author: Literal["claude", "user"]
    operation: str
    payload: dict[str, Any]
    anchor: dict[str, str | None] | None = None  # update_event: the event as it was {type, date, place}
    sources: list[SourceRef]
    rationale: str
    created: str
    status: Literal["active", "withdrawn", "stale"] = "active"
    stale_reason: str | None = None


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
```

- [ ] **Step 5: Write the store**

`src/genea/analysis/store.py`:

```python
"""Analyses on disk: .genea/analyses/<name>/analysis.json, with the reports and iterations next to it."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from genea.analysis.models import Analysis, ScopeSpec
from genea.kb import KnowledgeBase
from genea.tools.base import ToolError

NAME_RE = re.compile(r"[\w.-]+")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def analysis_dir(kb: KnowledgeBase, name: str) -> Path:
    """The folder of one analysis. The name comes from MCP clients, so it must not leave analyses_dir."""
    if not NAME_RE.fullmatch(name or "") or set(name) == {"."}:
        raise ToolError(f"Invalid analysis name {name!r}; use a name from read_analysis or `genea analysis list`.")
    base = kb.analyses_dir.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise ToolError(f"Invalid analysis name {name!r}.")
    return path


def scope_name(ged_file: str, spec: ScopeSpec) -> str:
    stem = re.sub(r"[^\w.-]", "_", Path(ged_file).stem)
    xref = spec.id.split("#", 1)[1].strip("@")
    name = f"{stem}-{spec.kind}-{xref}"
    return f"{name}-{spec.direction}-{spec.depth}" if spec.kind == "branch" else name


def write_atomic(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    os.replace(tmp, path)


def exists(kb: KnowledgeBase, name: str) -> bool:
    return (analysis_dir(kb, name) / "analysis.json").is_file()


def load(kb: KnowledgeBase, name: str) -> Analysis:
    path = analysis_dir(kb, name) / "analysis.json"
    if not path.is_file():
        raise ToolError(f"No analysis named {name!r}. Run analyze first.")
    try:
        return Analysis.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError, UnicodeDecodeError) as exc:
        raise ToolError(f"The file analysis.json of {name!r} is damaged: {exc}") from exc


def save(kb: KnowledgeBase, analysis: Analysis) -> Path:
    path = analysis_dir(kb, analysis.name) / "analysis.json"
    write_atomic(path, analysis.model_dump_json(indent=2))
    return path


def list_analyses(kb: KnowledgeBase) -> list[Analysis]:
    """Every readable analysis, sorted by name; damaged ones are skipped (load() reports them)."""
    if not kb.analyses_dir.is_dir():
        return []
    out = []
    for folder in sorted(kb.analyses_dir.iterdir()):
        if (folder / "analysis.json").is_file():
            try:
                out.append(load(kb, folder.name))
            except ToolError:
                continue
    return out
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analysis_store.py tests/test_kb.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/genea/analysis src/genea/kb.py tests/test_analysis_store.py
git commit -m "Add analysis models and store" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Test tree and scope resolution

**Files:**
- Create: `tests/fixtures/analysis_tree.py`, `src/genea/analysis/scope.py`
- Modify: `tests/conftest.py`, `src/genea/tools/people.py` (rename `_children_in` → `children_in`, all uses
  in that file)
- Test: `tests/test_analysis_scope.py`

**Interfaces:**
- Consumes: `ScopeSpec` (Task 1).
- Produces: `Scope(ged_file, people: list[str] xrefs, families: list[str] xrefs, boundary: list[str] xrefs)`;
  `resolve_scope(ctx, kind, target_id, direction="both", depth=4) -> tuple[ScopeSpec, Scope]` (raises
  `ToolError`); `people.children_in(conn, ged, family_xref) -> list[str]`.
- Produces (tests): `TREE`, `DOCS`, `make_analysis_kb(root, tree=TREE, *, encoding="utf-8", newline="\n",
  bom=b"", docs=DOCS) -> KnowledgeBase`, `event_line(ctx, xref, type_) -> int`; fixtures `analysis_kb`, `actx`.

- [ ] **Step 1: Write the test tree**

`tests/fixtures/analysis_tree.py`:

```python
"""A small tree with known problems, for the analysis tests.

I5 is born after her mother's death (error) and has no sex; I4 is born 5 months after I3 (needs review);
I6 was born in 1820 with no death; I7 has no events but is named in docs/zapis.txt.
"""

from pathlib import Path

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase, init_kb

TREE = """0 HEAD
1 SOUR GENEA_TEST
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Karol /Weiss/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Brno
2 SOUR Matrika Brno
1 DEAT
2 DATE 1910
2 SOUR Matrika Brno
1 FAMS @F1@
0 @I2@ INDI
1 NAME Mária /Bielik/
1 SEX F
1 BIRT
2 DATE ABT 1855
1 DEAT
2 DATE 12 MAR 1880
1 FAMS @F1@
1 FAMC @F0@
0 @I3@ INDI
1 NAME Ján /Weiss/
1 SEX M
1 BIRT
2 DATE 1 JAN 1878
2 PLAC Brno
1 FAMC @F1@
0 @I4@ INDI
1 NAME Eva /Weiss/
1 SEX F
1 BIRT
2 DATE 1 JUN 1878
1 FAMC @F1@
0 @I5@ INDI
1 NAME Anna /Weiss/
1 BIRT
2 DATE 2 FEB 1881
1 FAMC @F1@
0 @I6@ INDI
1 NAME Peter /Bielik/
1 SEX M
1 BIRT
2 DATE 1820
1 FAMS @F0@
0 @I7@ INDI
1 NAME Imrich /Novotný/
1 SEX M
0 @F0@ FAM
1 HUSB @I6@
1 CHIL @I2@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1875
1 CHIL @I3@
1 CHIL @I4@
1 CHIL @I5@
0 TRLR
"""

DOCS = {"docs/zapis.txt": "Imrich Novotný bol krstným otcom v Brne.\n"}


def make_analysis_kb(root: Path, tree: str = TREE, *, encoding: str = "utf-8", newline: str = "\n",
                     bom: bytes = b"", docs: dict[str, str] = DOCS) -> KnowledgeBase:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tree.ged").write_bytes(bom + tree.replace("\n", newline).encode(encoding))
    for rel, text in docs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    kb = init_kb(root)
    index_kb(kb, FakeEmbedder())
    return kb


def event_line(ctx, xref: str, type_: str) -> int:
    """GEDCOM line of the person's first event of this type, as the index knows it."""
    row = ctx.conn.execute("SELECT line_no FROM events WHERE ged_file = 'tree.ged' AND owner_xref = ? AND type = ? "
                           "ORDER BY line_no", (xref, type_)).fetchone()
    return row["line_no"]
```

Append to `tests/conftest.py`:

```python
@pytest.fixture
def analysis_kb(tmp_path):
    from fixtures.analysis_tree import make_analysis_kb

    return make_analysis_kb(tmp_path / "akb")


@pytest.fixture
def actx(analysis_kb):
    ctx = open_tools(analysis_kb, FakeEmbedder())
    yield ctx
    ctx.conn.close()
```

- [ ] **Step 2: Write the failing tests**

`tests/test_analysis_scope.py`:

```python
import pytest

from genea.analysis.scope import resolve_scope
from genea.tools.base import ToolError


def test_person_scope(actx):
    spec, scope = resolve_scope(actx, "person", "tree.ged#@I3@")
    assert spec.id == "tree.ged#@I3@" and spec.kind == "person"
    assert scope.ged_file == "tree.ged"
    assert scope.people == ["@I3@"] and scope.families == ["@F1@"] and scope.boundary == []


def test_person_scope_accepts_a_bare_xref(actx):
    spec, _ = resolve_scope(actx, "person", "I3")
    assert spec.id == "tree.ged#@I3@"


def test_family_scope(actx):
    spec, scope = resolve_scope(actx, "family", "tree.ged#@F1@")
    assert spec.id == "tree.ged#@F1@"
    assert scope.people == ["@I1@", "@I2@", "@I3@", "@I4@", "@I5@"]
    assert set(scope.families) == {"@F1@", "@F0@"}


def test_branch_ancestors_stops_at_the_depth(actx):
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I3@", "ancestors", 1)
    assert scope.people == ["@I3@", "@I1@", "@I2@"]
    assert scope.boundary == ["@I2@"]  # her parents are beyond depth 1
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I3@", "ancestors", 2)
    assert scope.people == ["@I3@", "@I1@", "@I2@", "@I6@"] and scope.boundary == []


def test_branch_descendants_and_both(actx):
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I6@", "descendants", 1)
    assert scope.people == ["@I6@", "@I2@"] and scope.boundary == ["@I2@"]
    assert set(scope.families) == {"@F0@", "@F1@"}
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I2@", "both", 1)
    assert set(scope.people) == {"@I2@", "@I6@", "@I3@", "@I4@", "@I5@"}


def test_a_loop_in_the_tree_ends(tmp_path):
    from fixtures.analysis_tree import TREE, make_analysis_kb

    from genea.index.embed import FakeEmbedder
    from genea.tools.base import open_tools

    looped = TREE.replace("0 @F0@ FAM\n1 HUSB @I6@\n", "0 @F0@ FAM\n1 HUSB @I3@\n")  # I2's father is her son
    ctx = open_tools(make_analysis_kb(tmp_path / "loop", looped), FakeEmbedder())
    _, scope = resolve_scope(ctx, "branch", "tree.ged#@I3@", "both", 6)
    ctx.conn.close()
    assert len(scope.people) == len(set(scope.people))
    # ancestors: I1, I2 (her father I3 is the root); descendants via F0: I2, then her children I4, I5
    assert set(scope.people) == {"@I3@", "@I1@", "@I2@", "@I4@", "@I5@"}


def test_depth_is_clamped(actx):
    spec, _ = resolve_scope(actx, "branch", "tree.ged#@I3@", "ancestors", 99)
    assert spec.depth == 6


@pytest.mark.parametrize(("kind", "target", "direction", "match"), [
    ("clan", "tree.ged#@I3@", "both", "person, family or branch"),
    ("branch", "tree.ged#@I3@", "sideways", "ancestors, descendants or both"),
    ("person", "tree.ged#@I99@", "both", "No person"),
    ("family", "tree.ged#@F99@", "both", "No family"),
])
def test_bad_scopes(actx, kind, target, direction, match):
    with pytest.raises(ToolError, match=match):
        resolve_scope(actx, kind, target, direction)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analysis_scope.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.analysis.scope'`

- [ ] **Step 4: Rename the helper in people.py**

In `src/genea/tools/people.py` rename `def _children_in(` to `def children_in(` and replace every
`_children_in(` call in that file with `children_in(`.

Run: `uv run pytest tests/test_people.py -q`
Expected: PASS

- [ ] **Step 5: Write scope.py**

`src/genea/analysis/scope.py`:

```python
"""Which people and families an analysis looks at."""

from __future__ import annotations

from dataclasses import dataclass, field

from genea.analysis.models import ScopeSpec
from genea.tools.base import ToolContext, ToolError, parse_family_id, parse_person_id, person_id
from genea.tools.people import children_in, children_of, families_as_child, families_as_spouse, parents_of

KINDS = ("person", "family", "branch")
DIRECTIONS = ("ancestors", "descendants", "both")


@dataclass
class Scope:
    ged_file: str
    people: list[str]  # xrefs, root first
    families: list[str]  # xrefs
    boundary: list[str] = field(default_factory=list)  # people at the depth limit with relatives beyond it


def _unique(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def _walk(conn, ged: str, root: str, step, depth: int) -> tuple[list[str], list[str]]:
    seen, frontier, found = {root}, [root], []
    for _ in range(depth):
        next_frontier = []
        for current in frontier:
            for other in step(conn, ged, current):
                if other not in seen:
                    seen.add(other)
                    next_frontier.append(other)
                    found.append(other)
        frontier = next_frontier
    return found, [x for x in frontier if step(conn, ged, x)]


def resolve_scope(ctx: ToolContext, kind: str, target_id: str, direction: str = "both",
                  depth: int = 4) -> tuple[ScopeSpec, Scope]:
    if kind not in KINDS:
        raise ToolError(f"Unknown scope {kind!r}; use person, family or branch.")
    if direction not in DIRECTIONS:
        raise ToolError(f"Unknown direction {direction!r}; use ancestors, descendants or both.")
    depth = max(1, min(int(depth), 6))
    conn = ctx.conn
    if kind == "family":
        ged, xref = parse_family_id(ctx, target_id)
        row = conn.execute("SELECT * FROM families WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
        spouses = _unique([row["husband_xref"], row["wife_xref"]])
        people = _unique(spouses + children_in(conn, ged, xref))
        families = _unique([xref] + [f["xref"] for s in spouses for f in families_as_child(conn, ged, s)])
        return ScopeSpec(kind="family", id=person_id(ged, xref)), Scope(ged, people, families)
    ged, xref = parse_person_id(ctx, target_id)
    spec = ScopeSpec(kind=kind, id=person_id(ged, xref), direction=direction, depth=depth)
    if kind == "person":
        families = _unique([f["xref"] for f in families_as_child(conn, ged, xref)]
                           + [f["xref"] for f in families_as_spouse(conn, ged, xref)])
        return spec, Scope(ged, [xref], families)
    found, boundary = [], []
    if direction in ("ancestors", "both"):
        more, edge = _walk(conn, ged, xref, parents_of, depth)
        found, boundary = found + more, boundary + edge
    if direction in ("descendants", "both"):
        more, edge = _walk(conn, ged, xref, children_of, depth)
        found, boundary = found + more, boundary + edge
    people = _unique([xref] + found)
    families = _unique([f["xref"] for f in families_as_child(conn, ged, xref)]
                       + [f["xref"] for p in people for f in families_as_spouse(conn, ged, p)])
    return spec, Scope(ged, people, families, _unique(boundary))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analysis_scope.py tests/test_people.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/analysis_tree.py tests/conftest.py src/genea/analysis/scope.py src/genea/tools/people.py tests/test_analysis_scope.py
git commit -m "Resolve person, family and branch scopes for analyses" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Shared conflict rules and two new rules

**Files:**
- Modify: `src/genea/tools/conflicts.py`
- Test: `tests/test_conflicts.py`

**Interfaces:**
- Consumes: `children_in` (Task 2).
- Produces: `check_person(conn, ged, xref) -> list[Conflict]` (was `_check_person`); new kinds
  `born_after_mother_death` (`error`) and `siblings_too_close` (`needs_review`).

- [ ] **Step 1: Write the failing tests**

Add `from fixtures.analysis_tree import TREE, make_analysis_kb` to the imports at the top of
`tests/test_conflicts.py` (after `import pytest`), then append:

```python
def test_birth_after_mother_death_and_close_siblings(actx):
    found = {(c.kind, c.person_id, c.severity) for c in find_conflicts(actx).conflicts}
    assert ("born_after_mother_death", "tree.ged#@I5@", "error") in found
    assert ("siblings_too_close", "tree.ged#@I4@", "needs_review") in found
    assert not any(k == "siblings_too_close" and p == "tree.ged#@I3@" for k, p, _ in found)


def test_twins_and_baptisms_do_not_fire(tmp_path):
    tree = (TREE.replace("2 DATE 1 JUN 1878", "2 DATE 3 JAN 1878")
            .replace("1 BIRT\n2 DATE 2 FEB 1881", "1 BAPM\n2 DATE 2 FEB 1881"))
    kb = make_analysis_kb(tmp_path / "t", tree)
    ctx = open_tools(kb, FakeEmbedder())
    kinds = {c.kind for c in find_conflicts(ctx).conflicts}
    ctx.conn.close()
    assert "siblings_too_close" not in kinds and "born_after_mother_death" not in kinds
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conflicts.py -v`
Expected: the two new tests FAIL (kinds missing); the old ones PASS.

- [ ] **Step 3: Implement**

In `src/genea/tools/conflicts.py`:

1. Import `children_in` from `genea.tools.people` alongside the existing imports.
2. Rename `_check_person` to `check_person` (and its call in `find_conflicts`), and give it a docstring:
   `"""All rule checks for one person. Shared by find_conflicts and the analysis detectors."""`
3. Change the inner `add` helper to take a severity:

```python
    def add(kind: str, message: str, sources: list[SourceRef], severity: str = "error") -> None:
        out.append(Conflict(kind=kind, severity=severity, person_id=pid, message=message, sources=sources))
```

4. Insert this block just before `out += _document_mismatches(...)`:

```python
    if birth is not None and birth["date_from"]:  # BIRT only: a baptism after the mother's death is normal
        for family in families_as_child(conn, ged, xref):
            if family["wife_xref"]:
                mother_death = first_event(event_rows(conn, ged, family["wife_xref"]), ("DEAT",))
                if mother_death is not None and mother_death["date_to"] and birth["date_from"] > mother_death["date_to"]:
                    add("born_after_mother_death",
                        f"Born ({birth['date_raw']}) after the mother's death ({mother_death['date_raw']})",
                        ref(birth, mother_death))
            for sibling in children_in(conn, ged, family["xref"]):
                if sibling == xref:
                    continue
                other = first_event(event_rows(conn, ged, sibling), ("BIRT",))
                if other is None or not other["date_from"]:
                    continue
                least = (_d(birth["date_from"]) - _d(other["date_to"])).days
                most = (_d(birth["date_to"]) - _d(other["date_from"])).days
                if 7 < least and most < 270:  # more than a week (not twins), less than 9 months
                    add("siblings_too_close",
                        f"Born ({birth['date_raw']}) less than 9 months after the sibling "
                        f"{summary(conn, ged, sibling).name} ({other['date_raw']})",
                        ref(birth, other), severity="needs_review")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_conflicts.py tests/test_cli_query.py tests/test_mcp_server.py -v`
Expected: PASS (the sample tree has no case for either new rule, so the old expectations hold).

- [ ] **Step 5: Commit**

```bash
git add src/genea/tools/conflicts.py tests/test_conflicts.py
git commit -m "Share conflict rules and flag births after the mother's death and close siblings" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Detectors

**Files:**
- Create: `src/genea/analysis/detectors.py`
- Test: `tests/test_analysis_detectors.py`

**Interfaces:**
- Consumes: `Scope`, `resolve_scope` (Task 2); `check_person` (Task 3); `Finding`, `finding_id`, `SEVERITIES`
  (Task 1).
- Produces: `detect(ctx, scope) -> list[Finding]`, sorted error → needs_review → info, then label, kind, id.
  Kinds added here: `missing_birth`, `missing_death`, `missing_parents`, `missing_sex`, `uncited_event` (all
  `info`), `mentioned_without_dates` (`needs_review`).

- [ ] **Step 1: Write the failing tests**

`tests/test_analysis_detectors.py`:

```python
from fixtures.analysis_tree import TREE, make_analysis_kb

from genea.analysis.detectors import detect
from genea.analysis.scope import resolve_scope
from genea.index.embed import FakeEmbedder
from genea.tools.base import open_tools


def _kinds(findings, xref):
    return {f.kind for f in findings if f.person_ids == [f"tree.ged#{xref}"]}


def test_conflicts_and_gaps_for_a_family(actx):
    _, scope = resolve_scope(actx, "family", "tree.ged#@F1@")
    found = detect(actx, scope)
    assert {"born_after_mother_death", "missing_sex", "uncited_event", "missing_death"} <= _kinds(found, "@I5@")
    assert "siblings_too_close" in _kinds(found, "@I4@")
    assert "missing_parents" in _kinds(found, "@I1@")
    assert not {"missing_death", "uncited_event"} & _kinds(found, "@I1@")
    assert found[0].severity == "error"
    assert [f.severity for f in found] == sorted((f.severity for f in found),
                                                 key=["error", "needs_review", "info"].index)
    assert found[0].label == "Anna Weiss (tree.ged#@I5@)"


def test_probably_deceased_without_death(actx):
    _, scope = resolve_scope(actx, "person", "tree.ged#@I6@")
    found = detect(actx, scope)
    [death] = [f for f in found if f.kind == "missing_death"]
    assert death.severity == "info" and "1820" in death.message
    assert death.sources[0].file == "tree.ged" and death.sources[0].line


def test_document_mention_without_dates(actx):
    _, scope = resolve_scope(actx, "person", "tree.ged#@I7@")
    found = detect(actx, scope)
    [mention] = [f for f in found if f.kind == "mentioned_without_dates"]
    assert mention.severity == "needs_review"
    assert mention.sources[0].file == "docs/zapis.txt"
    assert "missing_birth" in {f.kind for f in found}


def test_ids_do_not_depend_on_line_numbers(tmp_path):
    shifted = TREE.replace("0 @I1@ INDI\n", "0 @I1@ INDI\n1 NOTE posunuté\n")
    ids = []
    for root, tree in ((tmp_path / "a", TREE), (tmp_path / "b", shifted)):
        ctx = open_tools(make_analysis_kb(root, tree), FakeEmbedder())
        _, scope = resolve_scope(ctx, "family", "tree.ged#@F1@")
        ids.append({f.id for f in detect(ctx, scope)})
        ctx.conn.close()
    assert ids[0] == ids[1] and ids[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analysis_detectors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.analysis.detectors'`

- [ ] **Step 3: Implement**

`src/genea/analysis/detectors.py`:

```python
"""Detectors: deterministic checks over a scope. They find problems and never guess a value."""

from __future__ import annotations

import json
from datetime import date

from genea.analysis.models import SEVERITIES, Finding, finding_id
from genea.analysis.scope import Scope
from genea.tools.base import ToolContext, person_id
from genea.tools.conflicts import check_person
from genea.tools.models import Conflict, SourceRef
from genea.tools.people import BIRTH_TYPES, DEATH_TYPES, event_rows, families_as_child, first_event, summary

PROBABLY_DECEASED_YEARS = 110


def detect(ctx: ToolContext, scope: Scope) -> list[Finding]:
    conn, ged = ctx.conn, scope.ged_file
    found: dict[str, Finding] = {}
    for xref in scope.people:
        label = f"{summary(conn, ged, xref).name} ({person_id(ged, xref)})"
        findings = [_from_conflict(conn, ged, c, label) for c in check_person(conn, ged, xref)]
        findings += _gaps(conn, ged, xref, label) + _mentions_without_dates(conn, ged, xref, label)
        for finding in findings:
            found.setdefault(finding.id, finding)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(found.values(), key=lambda f: (order[f.severity], f.label, f.kind, f.id))


def _ref_key(conn, ged: str, ref: SourceRef) -> str:
    """What a source points at, without its line number: 'BIRT 1 JAN 1878' or 'docs/x.md#3'."""
    if ref.file == ged and ref.line is not None:
        row = conn.execute("SELECT type, date_raw FROM events WHERE ged_file = ? AND line_no = ?",
                           (ged, ref.line)).fetchone()
        return f"{row['type']} {row['date_raw'] or ''}".strip() if row else "record"
    return f"{ref.file}#{ref.chunk if ref.chunk is not None else ref.page}"


def _from_conflict(conn, ged: str, conflict: Conflict, label: str) -> Finding:
    key = ";".join(sorted(_ref_key(conn, ged, s) for s in conflict.sources))
    return Finding(id=finding_id(conflict.kind, [conflict.person_id], None, key), kind=conflict.kind,
                   severity=conflict.severity, label=label, message=conflict.message,
                   person_ids=[conflict.person_id], sources=conflict.sources)


def _finding(kind: str, severity: str, ged: str, xref: str, label: str, message: str,
             sources: list[SourceRef], key: str = "") -> Finding:
    pid = person_id(ged, xref)
    return Finding(id=finding_id(kind, [pid], None, key), kind=kind, severity=severity, label=label,
                   message=message, person_ids=[pid], sources=sources)


def _gaps(conn, ged: str, xref: str, label: str) -> list[Finding]:
    row = conn.execute("SELECT sex, line_no FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    record = [SourceRef(file=ged, line=row["line_no"])]
    events = event_rows(conn, ged, xref)
    born = first_event(events, BIRTH_TYPES)
    out = []
    if born is None:
        out.append(_finding("missing_birth", "info", ged, xref, label, "No birth or baptism is recorded", record))
    elif (first_event(events, DEATH_TYPES) is None and born["date_to"]
          and int(born["date_to"][:4]) < date.today().year - PROBABLY_DECEASED_YEARS):
        out.append(_finding("missing_death", "info", ged, xref, label,
                            f"Born {born['date_raw']} but no death or burial is recorded",
                            [SourceRef(file=ged, line=born["line_no"])]))
    if not families_as_child(conn, ged, xref):
        out.append(_finding("missing_parents", "info", ged, xref, label, "No parents are recorded", record))
    if (row["sex"] or "U").upper() not in ("M", "F"):
        out.append(_finding("missing_sex", "info", ged, xref, label, "Sex is not recorded", record))
    for event in events:
        if not json.loads(event["source_citations"]):
            what = f"{event['type']} {event['date_raw'] or ''}".strip()
            out.append(_finding("uncited_event", "info", ged, xref, label,
                                f"{event['type']} ({event['date_raw'] or 'no date'}) has no source citation",
                                [SourceRef(file=ged, line=event["line_no"])], key=what))
    return out


def _mentions_without_dates(conn, ged: str, xref: str, label: str) -> list[Finding]:
    if any(e["date_from"] for e in event_rows(conn, ged, xref)):
        return []  # dated people are covered by the document_mismatch rule
    rows = conn.execute(
        "SELECT c.page, c.chunk_no, c.start_s, f.path FROM links l "
        "JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id "
        "JOIN files f ON f.id = c.file_id "
        "WHERE l.person_ged_file = ? AND l.person_xref = ? AND l.confidence >= 1.0 "
        "ORDER BY f.path, c.chunk_no", (ged, xref)).fetchall()
    if not rows:
        return []
    sources = [SourceRef(file=r["path"], page=r["page"], chunk=r["chunk_no"], start_s=r["start_s"]) for r in rows[:5]]
    return [_finding("mentioned_without_dates", "needs_review", ged, xref, label,
                     f"Linked documents mention this person ({len(rows)} passage(s)) but the tree has no dated "
                     "event for them. Read the passages and propose events if they are about this person.",
                     sources)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analysis_detectors.py -v`
Expected: PASS. If `test_document_mention_without_dates` finds no link, check with
`SELECT * FROM links` how the linker matched "Imrich Novotný" and adjust `DOCS` in the fixture (not the
detector) so the full name appears as the linker expects.

- [ ] **Step 5: Commit**

```bash
git add src/genea/analysis/detectors.py tests/test_analysis_detectors.py
git commit -m "Detect conflicts, gaps and undated document mentions in an analysis scope" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: analyze, reports and the `genea analyze` command

**Files:**
- Create: `src/genea/analysis/report.py`, `src/genea/analysis/api.py`, `src/genea/cli/analysiscmds.py`
- Modify: `src/genea/changes/export.py` (public `describe_operation`), `src/genea/cli/app.py`,
  `src/genea/cli/main.py`
- Test: `tests/test_analysis_api.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `describe_operation(operation: str, payload: dict) -> str` in `genea.changes.export`;
  `render_report(analysis, version: int | None = None, hunks: dict[str, list[str]] | None = None) -> str`;
  in `genea.analysis.api`: `tree_hash(ctx, ged_file) -> str`, `tree_index_state(ctx, ged_file, digest) -> str`
  (`ready|outdated|building|interrupted|missing`), `write_report(ctx, analysis)`,
  `analyze(ctx, kind, target_id, direction="both", depth=4) -> AnalysisPage`,
  `read_analysis(ctx, name, severity=None, page=1) -> AnalysisPage`, `list_all(ctx) -> AnalysisList`;
  CLI `analysis_app` in `genea.cli.app`, helpers `_fail`, `_with_tools`, `_print_page` in `analysiscmds`.

- [ ] **Step 1: Write the failing tests**

`tests/test_analysis_api.py`:

```python
import pytest
from typer.testing import CliRunner

from genea.analysis import api, store
from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.tools.base import ToolError

runner = CliRunner()


def test_analyze_writes_the_analysis_and_report(actx, analysis_kb):
    before = (analysis_kb.root / "tree.ged").read_bytes()
    page = api.analyze(actx, "family", "tree.ged#@F1@")
    assert page.name == "tree-family-F1" and page.index_state == "ready" and page.message is None
    assert page.counts["error"] == 1 and page.counts["needs_review"] == 1
    assert page.findings[0].kind == "born_after_mother_death" and page.findings[0].status == "open"
    folder = analysis_kb.analyses_dir / "tree-family-F1"
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "## Needs a decision" in report and page.findings[0].id in report
    assert "### Anna Weiss (tree.ged#@I5@)" in report
    assert page.report == ".genea/analyses/tree-family-F1/report.md"
    assert (analysis_kb.root / "tree.ged").read_bytes() == before


def test_rerun_keeps_dismissals_notes_and_proposals(actx, analysis_kb):
    first = api.analyze(actx, "person", "tree.ged#@I5@")
    saved = store.load(analysis_kb, first.name)
    saved.dismissed[first.findings[0].id] = "known"
    saved.iterations = 2
    store.save(analysis_kb, saved)
    again = api.analyze(actx, "person", "tree.ged#@I5@")
    kept = store.load(analysis_kb, again.name)
    assert kept.dismissed == {first.findings[0].id: "known"} and kept.iterations == 2
    assert again.findings[0].status == "dismissed"


def test_paging(actx, monkeypatch):
    monkeypatch.setattr(api, "PAGE_SIZE", 2)
    first = api.analyze(actx, "family", "tree.ged#@F1@")
    assert first.pages > 1 and len(first.findings) == 2
    second = api.read_analysis(actx, first.name, page=2)
    assert {f.id for f in second.findings}.isdisjoint({f.id for f in first.findings})
    only_info = api.read_analysis(actx, first.name, severity="info")
    assert all(f.severity == "info" for f in only_info.findings)
    with pytest.raises(ToolError, match="page must be between 1 and"):
        api.read_analysis(actx, first.name, page=99)
    with pytest.raises(ToolError, match="severity"):
        api.read_analysis(actx, first.name, severity="fatal")


def test_tree_changed_after_indexing_is_reported(actx, analysis_kb):
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 TRLR", "0 @N9@ NOTE x\n0 TRLR"), encoding="utf-8")
    page = api.analyze(actx, "person", "tree.ged#@I3@")
    assert page.index_state == "outdated" and "genea index" in page.message
    assert "> " in (analysis_kb.analyses_dir / page.name / "report.md").read_text(encoding="utf-8")


def test_unknown_analysis(actx):
    with pytest.raises(ToolError, match="No analysis named"):
        api.read_analysis(actx, "tree-person-I99")


def test_list_all(actx):
    api.analyze(actx, "person", "tree.ged#@I3@")
    api.analyze(actx, "person", "tree.ged#@I5@")
    assert [a.name for a in api.list_all(actx).analyses] == ["tree-person-I3", "tree-person-I5"]


def test_cli_analyze_list_and_show(analysis_kb, monkeypatch):
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    root = str(analysis_kb.root)
    result = runner.invoke(app, ["--kb", root, "analyze", "person", "tree.ged#@I5@"])
    assert result.exit_code == 0, result.output
    assert "tree-person-I5" in result.output and "report:" in result.output
    assert (analysis_kb.analyses_dir / "tree-person-I5" / "analysis.json").is_file()
    result = runner.invoke(app, ["--kb", root, "analysis", "list"])
    assert result.exit_code == 0 and "tree-person-I5" in result.output
    result = runner.invoke(app, ["--kb", root, "analysis", "show", "tree-person-I5", "--severity", "error"])
    assert result.exit_code == 0 and "error" in result.output
    result = runner.invoke(app, ["--kb", root, "analyze", "clan", "tree.ged#@I5@"])
    assert result.exit_code == 1 and "person, family or branch" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analysis_api.py -v`
Expected: FAIL with `ImportError: cannot import name 'api'`

- [ ] **Step 3: Make the change description public**

In `src/genea/changes/export.py` replace `def _instruction(change: Change) -> str:` and its first line
`p, op = change.payload, change.operation` with:

```python
def describe_operation(op: str, p: dict) -> str:
    """One line describing a GEDCOM operation, for checklists, reports and GENEA notes."""
```

keeping the body unchanged, and add below it:

```python
def _instruction(change: Change) -> str:
    return describe_operation(change.operation, change.payload)
```

Run: `uv run pytest tests/test_export.py -q`
Expected: PASS

- [ ] **Step 4: Write the report**

`src/genea/analysis/report.py`:

```python
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


def _proposal_lines(p: Proposal, hunks: dict[str, list[str]] | None) -> list[str]:
    lines = [f"  - Proposal **{p.id}** ({p.author}, {p.status}): {describe_operation(p.operation, p.payload)}",
             f"    - Why: {p.rationale}", f"    - Evidence: {_cites(p.sources)}"]
    if p.stale_reason:
        lines.append(f"    - Not applied: {p.stale_reason}")
    if hunks and hunks.get(p.id):
        lines += ["", "    ```diff", *[f"    {h}" for h in hunks[p.id]], "    ```", ""]
    return lines


def render_report(a: Analysis, version: int | None = None, hunks: dict[str, list[str]] | None = None) -> str:
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
            out += _proposal_lines(p, hunks)

    known = {f.id for f in a.findings}
    other = [p for fid, ps in by_finding.items() if fid not in known for p in ps]
    if other:
        out += ["", "## Other proposals", ""]
        for p in other:
            out.append("- free-standing:" if p.finding_id is None else f"- finding {p.finding_id} is no longer reported:")
            out += _proposal_lines(p, hunks)
    if a.notes:
        out += ["", "## Notes", ""] + [f"- {n.created}: {n.text}" for n in a.notes]
    return "\n".join(out) + "\n"
```

- [ ] **Step 5: Write the analyze part of the API**

`src/genea/analysis/api.py`:

```python
"""Analysis operations shared by the CLI and the MCP server. Each takes a ToolContext and returns a ToolResult."""

from __future__ import annotations

import hashlib
import math

from genea.analysis import store
from genea.analysis.detectors import detect
from genea.analysis.models import (PAGE_SIZE, SEVERITIES, Analysis, AnalysisList, AnalysisPage, AnalysisSummary,
                                   FindingView)
from genea.analysis.report import STATE_WARNINGS, render_report
from genea.analysis.scope import resolve_scope
from genea.index.progress import index_state
from genea.tools.base import ToolContext, ToolError, person_id


def tree_hash(ctx: ToolContext, ged_file: str) -> str:
    return hashlib.sha256(ctx.kb.resolve_inside(ged_file).read_bytes()).hexdigest()


def tree_index_state(ctx: ToolContext, ged_file: str, digest: str) -> str:
    """'ready' only if indexing finished and the index was built from exactly this version of the tree."""
    state = index_state(ctx.kb)
    if state != "ready":
        return state
    row = ctx.conn.execute("SELECT sha256 FROM files WHERE path = ?", (ged_file,)).fetchone()
    return "ready" if row is not None and row["sha256"] == digest else "outdated"


def write_report(ctx: ToolContext, analysis: Analysis) -> None:
    store.write_atomic(store.analysis_dir(ctx.kb, analysis.name) / "report.md", render_report(analysis))


def analyze(ctx: ToolContext, kind: str, target_id: str, direction: str = "both", depth: int = 4) -> AnalysisPage:
    spec, scope = resolve_scope(ctx, kind, target_id, direction, depth)
    name = store.scope_name(scope.ged_file, spec)
    old = store.load(ctx.kb, name) if store.exists(ctx.kb, name) else None
    digest = tree_hash(ctx, scope.ged_file)
    stamp = store.now()
    analysis = Analysis(
        name=name, scope=spec, ged_file=scope.ged_file, base_hash=digest,
        index_state=tree_index_state(ctx, scope.ged_file, digest),
        created=old.created if old else stamp, updated=stamp,
        people=[person_id(scope.ged_file, x) for x in scope.people],
        boundary=[person_id(scope.ged_file, x) for x in scope.boundary],
        findings=detect(ctx, scope),
        proposals=old.proposals if old else [], dismissed=old.dismissed if old else {},
        notes=old.notes if old else [], iterations=old.iterations if old else 0,
    )
    store.save(ctx.kb, analysis)
    write_report(ctx, analysis)
    return _page(ctx, analysis, 1, None)


def read_analysis(ctx: ToolContext, name: str, severity: str | None = None, page: int = 1) -> AnalysisPage:
    return _page(ctx, store.load(ctx.kb, name), page, severity)


def list_all(ctx: ToolContext) -> AnalysisList:
    return AnalysisList(analyses=[
        AnalysisSummary(name=a.name, scope=a.scope, counts=a.counts(), iterations=a.iterations, updated=a.updated)
        for a in store.list_analyses(ctx.kb)])


def _page(ctx: ToolContext, analysis: Analysis, page: int, severity: str | None) -> AnalysisPage:
    if severity is not None and severity not in SEVERITIES:
        raise ToolError(f"Unknown severity {severity!r}; use {', '.join(SEVERITIES)}.")
    findings = [f for f in analysis.findings if severity is None or f.severity == severity]
    pages = max(1, math.ceil(len(findings) / PAGE_SIZE))
    if not 1 <= page <= pages:
        raise ToolError(f"page must be between 1 and {pages}")
    chunk = findings[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    shown = {f.id for f in chunk}
    known = {f.id for f in analysis.findings}
    proposals = [p for p in analysis.proposals
                 if p.finding_id in shown or (page == 1 and p.finding_id not in known)]
    sources = list({s.cite(): s for f in chunk for s in f.sources}.values())
    folder = store.analysis_dir(ctx.kb, analysis.name)
    return AnalysisPage(
        name=analysis.name, scope=analysis.scope, ged_file=analysis.ged_file, index_state=analysis.index_state,
        counts=analysis.counts(), page=page, pages=pages,
        findings=[FindingView(**f.model_dump(), status=analysis.finding_status(f.id)) for f in chunk],
        proposals=proposals, report=ctx.kb.rel(folder / "report.md"),
        message=STATE_WARNINGS.get(analysis.index_state), sources=sources,
    )
```

- [ ] **Step 6: Write the CLI**

In `src/genea/cli/app.py` add after the `transcribe_model_app` lines:

```python
analysis_app = typer.Typer(no_args_is_help=True,
                           help="Review analyses: findings, proposals and iterations of the tree.")
app.add_typer(analysis_app, name="analysis")
```

In `src/genea/cli/main.py` add `analysiscmds,` as the first name in the import list.

`src/genea/cli/analysiscmds.py`:

```python
"""`genea analyze` and `genea analysis ...`: check part of the tree, record proposals, build iterations."""

from __future__ import annotations

from typing import NoReturn

import typer
from rich.markup import escape
from rich.table import Table

from genea.analysis import api
from genea.analysis.models import AnalysisPage
from genea.cli import common
from genea.cli.app import analysis_app, app
from genea.tools.base import ToolError


def _fail(exc: Exception) -> NoReturn:
    common.console.print(f"[red]{escape(str(exc))}[/red]")
    raise typer.Exit(1) from exc


def _with_tools(ctx: typer.Context, fn, *args):
    kb = common.open_kb(ctx)
    tools = common.open_tool_context(kb)
    try:
        return fn(tools, *args)
    except ToolError as exc:
        _fail(exc)
    finally:
        tools.conn.close()


def _print_page(result: AnalysisPage) -> None:
    console, c = common.console, result.counts
    console.print(f"[bold]{escape(result.name)}[/bold]  {result.scope.kind} {escape(result.scope.id)}  "
                  f"index: {result.index_state}")
    console.print(f"{c['error']} error(s), {c['needs_review']} to review, {c['info']} gap(s); "
                  f"{c['proposals']} proposal(s), {c['stale']} stale, {c['dismissed']} dismissed")
    if result.message:
        console.print(f"[yellow]{escape(result.message)}[/yellow]")
    table = Table("id", "severity", "person", "finding", "status")
    for f in result.findings:
        table.add_row(f.id, f.severity, escape(f.label), escape(f.message), f.status)
    console.print(table)
    console.print(f"page {result.page}/{result.pages} · report: {escape(result.report)}")


@app.command()
def analyze(
    ctx: typer.Context,
    kind: str = typer.Argument(..., help="person, family or branch"),
    target_id: str = typer.Argument(..., metavar="ID", help="A person id (tree.ged#@I12@) or, for family, a family id"),
    direction: str = typer.Option("both", help="For branch: ancestors, descendants or both"),
    depth: int = typer.Option(4, help="For branch: generations to walk (1-6)"),
) -> None:
    """Check a person, family or branch for conflicts and gaps and write a report. The tree is not changed."""
    _print_page(_with_tools(ctx, api.analyze, kind, target_id, direction, depth))


@analysis_app.command("list")
def list_command(ctx: typer.Context) -> None:
    """List the analyses of this knowledge base."""
    result = _with_tools(ctx, api.list_all)
    table = Table("name", "scope", "errors", "review", "gaps", "proposals", "iterations")
    for a in result.analyses:
        c = a.counts
        table.add_row(escape(a.name), f"{a.scope.kind} {escape(a.scope.id)}", str(c["error"]),
                      str(c["needs_review"]), str(c["info"]), str(c["proposals"]), str(a.iterations))
    common.console.print(table)


@analysis_app.command()
def show(ctx: typer.Context, name: str,
         severity: str | None = typer.Option(None, help="error, needs_review or info"),
         page: int = typer.Option(1, help="Page of findings")) -> None:
    """Show the findings of an analysis."""
    _print_page(_with_tools(ctx, api.read_analysis, name, severity, page))
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analysis_api.py tests/test_export.py tests/test_cli_basic.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/genea/analysis/report.py src/genea/analysis/api.py src/genea/cli/analysiscmds.py src/genea/cli/app.py src/genea/cli/main.py src/genea/changes/export.py tests/test_analysis_api.py
git commit -m "Add genea analyze with Markdown reports and paged results" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Proposals, withdrawals, dismissals and notes

**Files:**
- Modify: `src/genea/analysis/api.py`, `src/genea/cli/analysiscmds.py`
- Test: `tests/test_analysis_proposals.py`

**Interfaces:**
- Consumes: Task 5 (`tree_hash`, `tree_index_state`, `write_report`), `validate_change`, `GEDCOM_REQUIRED`
  and `ChangeError` from `genea.changes.store`, `INDIVIDUAL_EVENT_TAGS` from `genea.ingest.gedcom.mapper`.
- Produces: `propose(ctx, name, operation, payload, sources: list[str], rationale, finding_id=None,
  author="user") -> AnalysisUpdate` (ids `p-1`, `p-2`, …; `update_event` gets `anchor = {type, date, place}`);
  `withdraw(ctx, name, proposal_id) -> AnalysisUpdate`; `dismiss(ctx, name, finding_id, reason="") ->
  AnalysisUpdate`; `add_note(ctx, name, text) -> AnalysisUpdate`. A `link_child` `child_id` may be
  `proposal:<id>` of an active `add_person` proposal. CLI: `analysis propose/withdraw/dismiss`.

- [ ] **Step 1: Write the failing tests**

`tests/test_analysis_proposals.py`:

```python
import pytest
from fixtures.analysis_tree import event_line
from typer.testing import CliRunner

from genea.analysis import api, store
from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.tools.base import ToolError

CITE = ["[docs/zapis.txt]"]
runner = CliRunner()


@pytest.fixture
def name(actx):
    return api.analyze(actx, "family", "tree.ged#@F1@").name


def test_update_event_records_an_anchor(actx, analysis_kb, name):
    before = (analysis_kb.root / "tree.ged").read_bytes()
    line = event_line(actx, "@I3@", "BIRT")
    result = api.propose(actx, name, "update_event", {"person_id": "@I3@", "line": str(line), "date": "2 JAN 1878"},
                         CITE, "the register says 2 January")
    assert result.proposal_id == "p-1" and result.status == "active"
    [p] = store.load(analysis_kb, name).proposals
    assert p.anchor == {"type": "BIRT", "date": "1 JAN 1878", "place": "Brno"}
    assert p.payload == {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"}
    assert p.author == "user" and p.sources[0].file == "docs/zapis.txt"
    assert (analysis_kb.root / "tree.ged").read_bytes() == before
    assert "p-1" in (analysis_kb.analyses_dir / name / "report.md").read_text(encoding="utf-8")


def test_identical_proposal_is_not_repeated(actx, name):
    payload = {"person_id": "tree.ged#@I3@", "text": "Mlynár"}
    first = api.propose(actx, name, "add_note", payload, CITE, "x")
    again = api.propose(actx, name, "add_note", payload, CITE, "x")
    assert again.proposal_id == first.proposal_id and "already exists" in again.message


@pytest.mark.parametrize(("operation", "payload", "sources", "rationale", "match"), [
    ("add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, [], "why", "Cite at least one source"),
    ("add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, ["[docs/none.pdf p.1]"], "why", "not an indexed file"),
    ("add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, CITE, "  ", "rationale"),
    ("rename", {"person_id": "tree.ged#@I3@"}, CITE, "why", "Unknown operation"),
    ("add_event", {"person_id": "tree.ged#@I3@", "type": "PARTY"}, CITE, "why", "not an individual event"),
    ("add_event", {"person_id": "tree.ged#@I99@", "type": "BAPM"}, CITE, "why", "No person"),
    ("update_event", {"person_id": "tree.ged#@I3@", "line": 1, "date": "1878"}, CITE, "why", "is not an event of"),
    ("update_event", {"person_id": "tree.ged#@I3@", "line": "x", "date": "1878"}, CITE, "why", "line must be"),
    ("link_child", {"family_id": "tree.ged#@F1@", "child_id": "proposal:p-9"}, CITE, "why", "not an active add_person"),
])
def test_bad_proposals_are_refused(actx, name, operation, payload, sources, rationale, match):
    with pytest.raises(ToolError, match=match):
        api.propose(actx, name, operation, payload, sources, rationale)


def test_update_event_needs_a_new_value(actx, name):
    line = event_line(actx, "@I3@", "BIRT")
    with pytest.raises(ToolError, match="new date or place"):
        api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line}, CITE, "why")


def test_unknown_finding(actx, name):
    with pytest.raises(ToolError, match="No finding f-00000000"):
        api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, CITE, "why",
                    finding_id="f-00000000")


def test_refused_when_the_tree_changed_after_indexing(actx, analysis_kb, name):
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 TRLR", "0 @N9@ NOTE x\n0 TRLR"), encoding="utf-8")
    with pytest.raises(ToolError, match="genea index"):
        api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, CITE, "why")


def test_new_person_and_withdraw_order(actx, analysis_kb, name):
    person = api.propose(actx, name, "add_person", {"given": "Mária", "surname": "Weiss", "sex": "F"}, CITE, "why")
    link = api.propose(actx, name, "link_child",
                       {"family_id": "F1", "child_id": f"proposal:{person.proposal_id}"}, CITE, "why")
    assert store.load(analysis_kb, name).proposals[1].payload["family_id"] == "tree.ged#@F1@"
    with pytest.raises(ToolError, match=f"withdraw {link.proposal_id} first"):
        api.withdraw(actx, name, person.proposal_id)
    assert api.withdraw(actx, name, link.proposal_id).status == "withdrawn"
    assert api.withdraw(actx, name, person.proposal_id).status == "withdrawn"
    assert store.load(analysis_kb, name).counts()["proposals"] == 0
    with pytest.raises(ToolError, match="No proposal p-9"):
        api.withdraw(actx, name, "p-9")


def test_dismiss_and_notes_survive_a_rerun(actx, analysis_kb, name):
    finding = api.read_analysis(actx, name).findings[0]
    api.dismiss(actx, name, finding.id, "known from the register")
    api.add_note(actx, name, "Checked the Brno register.")
    api.analyze(actx, "family", "tree.ged#@F1@")
    saved = store.load(analysis_kb, name)
    assert saved.dismissed == {finding.id: "known from the register"}
    assert saved.notes[0].text == "Checked the Brno register."
    report = (analysis_kb.analyses_dir / name / "report.md").read_text(encoding="utf-8")
    assert "dismissed (known from the register)" in report and "## Notes" in report
    with pytest.raises(ToolError, match="No finding"):
        api.dismiss(actx, name, "f-00000000")
    with pytest.raises(ToolError, match="empty"):
        api.add_note(actx, name, " ")


def test_cli_propose_withdraw_dismiss(analysis_kb, monkeypatch, actx, name):
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    root = str(analysis_kb.root)
    result = runner.invoke(app, ["--kb", root, "analysis", "propose", name, "--operation", "add_note",
                                 "--payload", '{"person_id": "tree.ged#@I3@", "text": "Mlynár"}',
                                 "--source", "[docs/zapis.txt]", "--rationale", "the register"])
    assert result.exit_code == 0, result.output
    assert "p-1" in result.output
    assert store.load(analysis_kb, name).proposals[0].author == "user"
    result = runner.invoke(app, ["--kb", root, "analysis", "propose", name, "--operation", "add_note",
                                 "--payload", "[1]", "--source", "[docs/zapis.txt]", "--rationale", "x"])
    assert result.exit_code == 1 and "JSON object" in result.output
    assert runner.invoke(app, ["--kb", root, "analysis", "withdraw", name, "p-1"]).exit_code == 0
    finding = store.load(analysis_kb, name).findings[0].id
    result = runner.invoke(app, ["--kb", root, "analysis", "dismiss", name, finding, "--reason", "known"])
    assert result.exit_code == 0 and store.load(analysis_kb, name).dismissed == {finding: "known"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analysis_proposals.py -v`
Expected: FAIL with `AttributeError: module 'genea.analysis.api' has no attribute 'propose'`

- [ ] **Step 3: Implement the API**

Add to the imports of `src/genea/analysis/api.py`:

```python
from typing import Any

from genea.analysis.models import AnalysisNote, AnalysisUpdate, Proposal, parse_cite
from genea.changes.store import ChangeError, validate_change
from genea.ingest.gedcom.mapper import INDIVIDUAL_EVENT_TAGS
from genea.tools.base import parse_family_id, parse_person_id
```

(merge the `genea.analysis.models` and `genea.tools.base` names into the existing import lines), then append:

```python
NOTE_LIMIT = 4000  # characters of one analysis note


def _save(ctx: ToolContext, analysis: Analysis) -> None:
    analysis.updated = store.now()
    store.save(ctx.kb, analysis)
    write_report(ctx, analysis)


def _require_fresh(ctx: ToolContext, analysis: Analysis) -> None:
    """Proposals carry line numbers from the index, so the index must match the tree and the analysis."""
    digest = tree_hash(ctx, analysis.ged_file)
    if tree_index_state(ctx, analysis.ged_file, digest) != "ready":
        raise ToolError("The index does not match the tree (it changed after indexing, or indexing is not "
                        "complete). Run `genea index`, then analyze again before proposing.")
    if digest != analysis.base_hash:
        raise ToolError(f"The tree changed after this analysis was made. Run analyze again for "
                        f"{analysis.scope.kind} {analysis.scope.id} first.")


def _check_ids(ctx: ToolContext, analysis: Analysis, operation: str, payload: dict[str, Any]) -> None:
    """Normalize ids to 'file#@X@' and check they exist in the analysed tree."""
    ged = analysis.ged_file
    for key in ("person_id", "child_id"):
        value = payload.get(key)
        if not value:
            continue
        if key == "child_id" and str(value).startswith("proposal:"):
            ref = str(value).split(":", 1)[1]
            if not any(p.id == ref and p.operation == "add_person" and p.status == "active"
                       for p in analysis.proposals):
                raise ToolError(f"{value} is not an active add_person proposal of this analysis.")
            continue
        file, xref = parse_person_id(ctx, str(value))
        if file != ged:
            raise ToolError(f"{value} is not in {ged}, the tree of this analysis.")
        payload[key] = person_id(file, xref)
    if payload.get("family_id"):
        file, xref = parse_family_id(ctx, str(payload["family_id"]))
        if file != ged:
            raise ToolError(f"{payload['family_id']} is not in {ged}, the tree of this analysis.")
        payload["family_id"] = person_id(file, xref)
    if operation == "add_event" and payload["type"] not in INDIVIDUAL_EVENT_TAGS:
        raise ToolError(f"{payload['type']!r} is not an individual event tag; use one of "
                        f"{', '.join(sorted(INDIVIDUAL_EVENT_TAGS))}.")
    if operation == "add_person" and payload.get("sex") not in (None, "", "M", "F", "U"):
        raise ToolError("sex must be M, F or U.")


def _anchor(ctx: ToolContext, payload: dict[str, Any]) -> dict[str, str | None]:
    ged, xref = parse_person_id(ctx, payload["person_id"])
    try:
        line = int(payload["line"])
    except (TypeError, ValueError):
        raise ToolError("line must be the GEDCOM line number of the event (get_person lists it).") from None
    row = ctx.conn.execute("SELECT type, date_raw, place FROM events WHERE ged_file = ? AND owner_xref = ? "
                           "AND line_no = ?", (ged, xref, line)).fetchone()
    if row is None:
        raise ToolError(f"Line {line} is not an event of {payload['person_id']}; get_person lists its events "
                        "with their lines.")
    if not payload.get("date") and not payload.get("place"):
        raise ToolError("update_event needs a new date or place.")
    payload["line"] = line
    return {"type": row["type"], "date": row["date_raw"], "place": row["place"]}


def propose(ctx: ToolContext, name: str, operation: str, payload: dict[str, Any], sources: list[str],
            rationale: str, finding_id: str | None = None, author: str = "user") -> AnalysisUpdate:
    analysis = store.load(ctx.kb, name)
    _require_fresh(ctx, analysis)
    if finding_id is not None and finding_id not in {f.id for f in analysis.findings}:
        raise ToolError(f"No finding {finding_id} in {name}.")
    if not (rationale or "").strip():
        raise ToolError("Give a rationale: why is this change right?")
    refs = [parse_cite(s) for s in sources or []]
    if not refs:
        raise ToolError("Cite at least one source, like [docs/matrika.pdf p.3].")
    for ref in refs:
        if ctx.conn.execute("SELECT 1 FROM files WHERE path = ?", (ref.file,)).fetchone() is None:
            raise ToolError(f"{ref.file} is not an indexed file of this knowledge base; cite what the tools returned.")
    payload = dict(payload or {})
    try:
        validate_change(ctx.kb, "gedcom", analysis.ged_file, operation, payload)
    except ChangeError as exc:
        raise ToolError(str(exc)) from exc
    _check_ids(ctx, analysis, operation, payload)
    anchor = _anchor(ctx, payload) if operation == "update_event" else None
    for p in analysis.proposals:
        if p.status == "active" and p.operation == operation and p.payload == payload:
            return AnalysisUpdate(name=name, proposal_id=p.id, status=p.status, sources=p.sources,
                                  message=f"An identical proposal already exists ({p.id}); not recorded again.")
    proposal = Proposal(id=f"p-{len(analysis.proposals) + 1}", finding_id=finding_id, author=author,
                        operation=operation, payload=payload, anchor=anchor, sources=refs,
                        rationale=rationale.strip(), created=store.now())
    analysis.proposals.append(proposal)
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, proposal_id=proposal.id, status=proposal.status, sources=refs,
                          message=f"Recorded {proposal.id}. The tree is unchanged; build_iteration writes a copy "
                                  "with all active proposals applied.")


def withdraw(ctx: ToolContext, name: str, proposal_id: str) -> AnalysisUpdate:
    analysis = store.load(ctx.kb, name)
    proposal = next((p for p in analysis.proposals if p.id == proposal_id), None)
    if proposal is None:
        raise ToolError(f"No proposal {proposal_id} in {name}.")
    users = [p.id for p in analysis.proposals
             if p.status != "withdrawn" and p.payload.get("child_id") == f"proposal:{proposal_id}"]
    if users:
        raise ToolError(f"{', '.join(users)} link(s) this new person; withdraw {users[0]} first.")
    proposal.status = "withdrawn"
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, proposal_id=proposal_id, status="withdrawn",
                          message=f"Withdrew {proposal_id}.")


def dismiss(ctx: ToolContext, name: str, finding_id: str, reason: str = "") -> AnalysisUpdate:
    analysis = store.load(ctx.kb, name)
    if finding_id not in {f.id for f in analysis.findings}:
        raise ToolError(f"No finding {finding_id} in {name}.")
    analysis.dismissed[finding_id] = (reason or "").strip()
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, status="dismissed", message=f"Dismissed {finding_id}.")


def add_note(ctx: ToolContext, name: str, text: str) -> AnalysisUpdate:
    text = (text or "").strip()
    if not text:
        raise ToolError("The note is empty.")
    if len(text) > NOTE_LIMIT:
        raise ToolError(f"Keep a note under {NOTE_LIMIT} characters.")
    analysis = store.load(ctx.kb, name)
    analysis.notes.append(AnalysisNote(created=store.now(), text=text))
    _save(ctx, analysis)
    return AnalysisUpdate(name=name, message="Note added to the report.")
```

- [ ] **Step 4: Add the CLI commands**

Add `import json` to `src/genea/cli/analysiscmds.py`, and append:

```python
@analysis_app.command()
def propose(
    ctx: typer.Context,
    name: str,
    operation: str = typer.Option(..., help="add_event, update_event, add_note, add_source_citation, "
                                            "link_child or add_person"),
    payload: str = typer.Option(..., help='JSON object, e.g. \'{"person_id": "tree.ged#@I3@", "text": "..."}\''),
    source: list[str] = typer.Option(..., "--source", help="A citation like [docs/matrika.pdf p.3]; repeatable"),
    rationale: str = typer.Option(..., help="Why the change is right"),
    finding: str | None = typer.Option(None, help="The finding id this proposal answers"),
) -> None:
    """Record a proposed change in an analysis. The tree is not changed."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        _fail(ToolError(f"--payload is not valid JSON: {exc}"))
    if not isinstance(data, dict):
        _fail(ToolError("--payload must be a JSON object"))
    result = _with_tools(ctx, api.propose, name, operation, data, source, rationale, finding, "user")
    common.console.print(escape(result.message))


@analysis_app.command()
def withdraw(ctx: typer.Context, name: str, proposal_id: str) -> None:
    """Withdraw a proposal; later iterations leave it out."""
    common.console.print(escape(_with_tools(ctx, api.withdraw, name, proposal_id).message))


@analysis_app.command()
def dismiss(ctx: typer.Context, name: str, finding_id: str,
            reason: str = typer.Option("", help="Why the finding is not a problem")) -> None:
    """Mark a finding as not a problem; it gets no CHECK note and stays dismissed on re-runs."""
    common.console.print(escape(_with_tools(ctx, api.dismiss, name, finding_id, reason).message))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analysis_proposals.py tests/test_analysis_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/genea/analysis/api.py src/genea/cli/analysiscmds.py tests/test_analysis_proposals.py
git commit -m "Record cited proposals, withdrawals, dismissals and notes in analyses" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: GEDCOM line editor

**Files:**
- Create: `src/genea/analysis/gedwrite.py`
- Test: `tests/test_gedwrite.py`

**Interfaces:**
- Consumes: `parse_gedcom`, `GedDocument`, `GedLine` from `genea.ingest.gedcom.parser`.
- Produces: `CONC_LIMIT = 248`; `split_value(text, limit=CONC_LIMIT) -> list[str]`;
  `text_lines(level, tag, text) -> list[str]`; `GedWriteError`; `GedEditor(doc)` with `doc`, `codec`,
  `owner: str | None`, `log: dict[str, list[str]]`, `xrefs() -> set[str]`, `record_index(xref) -> int`
  (KeyError if absent), `subtree_end(index) -> int`, `insert_after(index, lines)`, `replace(index, text)`,
  `add_record(lines)`, `snapshot()`, `restore(snap)`, `serialize() -> bytes`. Indexes are positions in
  `doc.lines` (`line_no - 1`).

- [ ] **Step 1: Write the failing tests**

`tests/test_gedwrite.py`:

```python
import pytest

from genea.analysis.gedwrite import CONC_LIMIT, GedEditor, GedWriteError, split_value, text_lines
from genea.ingest.gedcom.parser import parse_gedcom

SMALL = "0 HEAD\r\n1 CHAR UTF-8\r\n0 @I1@ INDI\r\n1 NAME A /B/\r\n1 BIRT\r\n2 DATE 1900\r\n0 TRLR\r\n"


def _doc(tmp_path, text, encoding="utf-8", bom=b""):
    path = tmp_path / "t.ged"
    path.write_bytes(bom + text.encode(encoding))
    return parse_gedcom(path)


def test_split_never_cuts_at_a_space():
    text = ("Žena menom Anna " * 60).strip()
    pieces = split_value(text)
    assert "".join(pieces) == text and len(pieces) > 1
    assert all(len(p) <= CONC_LIMIT for p in pieces)
    for left, right in zip(pieces, pieces[1:]):
        assert not left.endswith(" ") and not right.startswith(" ")


def test_split_keeps_short_text_and_long_words():
    assert split_value("krátky") == ["krátky"]
    assert "".join(split_value("x" * 600)) == "x" * 600


def test_text_lines_use_cont_and_conc():
    assert text_lines(1, "NOTE", "a\nb") == ["1 NOTE a", "2 CONT b"]
    lines = text_lines(2, "NOTE", "y " * 200)
    assert lines[0].startswith("2 NOTE y") and all(line.startswith("3 CONC ") for line in lines[1:])


def test_unchanged_file_is_byte_identical(tmp_path):
    doc = _doc(tmp_path, SMALL, bom=b"\xef\xbb\xbf")
    assert GedEditor(doc).serialize() == doc.path.read_bytes()


def test_insert_after_a_record_uses_the_file_newline(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    editor.insert_after(editor.subtree_end(editor.record_index("@I1@")), ["1 NOTE x"])
    assert editor.serialize() == SMALL.replace("2 DATE 1900\r\n", "2 DATE 1900\r\n1 NOTE x\r\n").encode()


def test_subtree_end_and_replace(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    assert editor.subtree_end(4) == 5  # 1 BIRT -> its 2 DATE
    editor.owner = "p-1"
    editor.replace(5, "2 DATE 1901")
    assert editor.serialize() == SMALL.replace("1900", "1901").encode()
    assert editor.log == {"p-1": ["-2 DATE 1900", "+2 DATE 1901"]}
    with pytest.raises(GedWriteError, match="twice"):
        editor.replace(5, "2 DATE 1902")


def test_new_records_go_before_trlr(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    editor.add_record(["0 @GENEA1@ INDI", "1 NAME C /D/"])
    assert editor.serialize().endswith(b"2 DATE 1900\r\n0 @GENEA1@ INDI\r\n1 NAME C /D/\r\n0 TRLR\r\n")
    assert "@GENEA1@" not in editor.xrefs() and "@I1@" in editor.xrefs()


def test_no_trlr_and_no_final_newline(tmp_path):
    editor = GedEditor(_doc(tmp_path, "0 HEAD\n0 @I1@ INDI\n1 NAME A /B/"))
    editor.add_record(["0 @GENEA1@ INDI"])
    editor.insert_after(2, ["1 SEX M"])
    assert editor.serialize() == b"0 HEAD\n0 @I1@ INDI\n1 NAME A /B/\n1 SEX M\n0 @GENEA1@ INDI\n"


def test_snapshot_and_restore(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    snap = editor.snapshot()
    editor.owner = "p-1"
    editor.insert_after(1, ["1 NOTE x"])
    editor.add_record(["0 @GENEA1@ INDI"])
    editor.restore(snap)
    assert editor.serialize() == SMALL.encode() and editor.log == {}


@pytest.mark.parametrize(("header", "encoding"), [("ANSEL", "utf-8"), ("UTF-8", "utf-16-le")])
def test_unwritable_encodings_are_refused(tmp_path, header, encoding):
    bom = b"\xff\xfe" if encoding == "utf-16-le" else b""
    doc = _doc(tmp_path, f"0 HEAD\n1 CHAR {header}\n0 TRLR\n", encoding, bom)
    with pytest.raises(GedWriteError, match="UTF-8"):
        GedEditor(doc)


def test_cp1250_text_that_cannot_be_encoded(tmp_path):
    editor = GedEditor(_doc(tmp_path, "0 HEAD\n1 CHAR ANSI\n0 @I1@ INDI\n1 NAME Ján /Kováč/\n0 TRLR\n", "cp1250"))
    editor.insert_after(3, ["1 NOTE Poznámka"])
    with pytest.raises(GedWriteError, match="cannot be written"):
        editor.insert_after(3, ["1 NOTE Имя"])
    assert editor.serialize().decode("cp1250").endswith("1 NAME Ján /Kováč/\n1 NOTE Poznámka\n0 TRLR\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gedwrite.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.analysis.gedwrite'`

- [ ] **Step 3: Implement**

`src/genea/analysis/gedwrite.py`:

```python
"""Write-side GEDCOM helpers: insert or replace lines of a losslessly parsed file, keeping every other byte."""

from __future__ import annotations

from collections import defaultdict

from genea.ingest.gedcom.parser import GedDocument

CONC_LIMIT = 248  # characters of a value per line; with level and tag the line stays under GEDCOM's 255
WRITABLE = {"utf-8": "utf-8", "cp1250": "cp1250"}  # parser encoding -> codec used for new lines


class GedWriteError(Exception):
    """The file cannot be written, or an edit cannot be made."""


def split_value(text: str, limit: int = CONC_LIMIT) -> list[str]:
    """Pieces for CONC lines. Never split at a space: importers may trim trailing spaces (GEDCOM 5.5.1)."""
    pieces = []
    while len(text) > limit:
        cut = limit
        while cut > 1 and (text[cut - 1] == " " or text[cut] == " "):
            cut -= 1
        if cut <= 1:  # nothing but spaces around the limit: a split there is unavoidable
            cut = limit
        pieces.append(text[:cut])
        text = text[cut:]
    pieces.append(text)
    return pieces


def text_lines(level: int, tag: str, text: str) -> list[str]:
    out = []
    for n, paragraph in enumerate((text or "").split("\n")):
        first, *rest = split_value(paragraph)
        out.append(f"{level} {tag} {first}".rstrip() if n == 0 else f"{level + 1} CONT {first}".rstrip())
        out += [f"{level + 1} CONC {piece}" for piece in rest]
    return out


class GedEditor:
    """Collects insertions and replacements against the original lines, then writes the new file.

    Indexes are positions in doc.lines (line_no - 1). `owner` names the proposal being applied, so each
    proposal's changes can be shown as a diff hunk (`log`)."""

    def __init__(self, doc: GedDocument) -> None:
        if doc.encoding not in WRITABLE:
            raise GedWriteError(f"genea can only write UTF-8 or ANSI (CP1250) GEDCOM files; {doc.path.name} is "
                                f"{doc.encoding.upper()}. Export the tree from Gramps as UTF-8.")
        self.doc = doc
        self.codec = WRITABLE[doc.encoding]
        first = doc.lines[0].raw_bytes if doc.lines else b"\n"
        self.newline = "\r\n" if first.endswith(b"\r\n") else "\r" if first.endswith(b"\r") else "\n"
        self._records = {line.xref: i for i, line in enumerate(doc.lines) if line.level == 0 and line.xref}
        self._after: dict[int, list[str]] = defaultdict(list)
        self._replace: dict[int, str] = {}
        self._new: list[str] = []
        self.owner: str | None = None
        self.log: dict[str, list[str]] = {}

    def xrefs(self) -> set[str]:
        return set(self._records)

    def record_index(self, xref: str) -> int:
        return self._records[xref]

    def subtree_end(self, index: int) -> int:
        """Index of the last line belonging to the line at `index` (itself if it has no children)."""
        level, end = self.doc.lines[index].level, index
        for j in range(index + 1, len(self.doc.lines)):
            line = self.doc.lines[j]
            if line.level < 0:  # blank or unparsable line: neither ends nor extends the subtree
                continue
            if line.level <= level:
                break
            end = j
        return end

    def insert_after(self, index: int, lines: list[str]) -> None:
        self._check(lines)
        self._after[index].extend(lines)
        self._log([f" {self._text(index)}"] + [f"+{line}" for line in lines])

    def replace(self, index: int, text: str) -> None:
        if index in self._replace:
            raise GedWriteError(f"line {index + 1} would be changed twice")
        self._check([text])
        self._replace[index] = text
        self._log([f"-{self._text(index)}", f"+{text}"])

    def add_record(self, lines: list[str]) -> None:
        self._check(lines)
        self._new.extend(lines)
        self._log([f"+{line}" for line in lines])

    def snapshot(self):
        return ({k: list(v) for k, v in self._after.items()}, dict(self._replace), list(self._new),
                {k: list(v) for k, v in self.log.items()})

    def restore(self, snap) -> None:
        after, replace, new, log = snap
        self._after, self._replace, self._new, self.log = defaultdict(list, after), replace, new, log

    def serialize(self) -> bytes:
        out = bytearray(self.doc.bom)
        start = len(out)

        def emit(lines: list[str]) -> None:
            if not lines:
                return
            if len(out) > start and not out.endswith((b"\n", b"\r")):  # the original last line had no newline
                out.extend(self.newline.encode(self.codec))
            for line in lines:
                out.extend(self._encode(line))

        trailer = next((i for i in range(len(self.doc.lines) - 1, -1, -1)
                        if self.doc.lines[i].level == 0 and self.doc.lines[i].tag == "TRLR"), None)
        for i, line in enumerate(self.doc.lines):
            if i == trailer:
                emit(self._new)
            if i in self._replace:
                out.extend(self._encode(self._replace[i]))
            else:
                out.extend(line.raw_bytes)
            emit(self._after.get(i, []))
        if trailer is None:
            emit(self._new)
        return bytes(out)

    def _text(self, index: int) -> str:
        return self.doc.lines[index].raw_bytes.decode(self.codec, errors="replace").rstrip("\r\n")

    def _encode(self, text: str) -> bytes:
        try:
            return (text + self.newline).encode(self.codec)
        except UnicodeEncodeError:
            raise GedWriteError(f"{text[:60]!r} cannot be written in {self.doc.encoding.upper()}") from None

    def _check(self, lines: list[str]) -> None:
        for line in lines:
            self._encode(line)

    def _log(self, lines: list[str]) -> None:
        if self.owner:
            self.log.setdefault(self.owner, []).extend(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_gedwrite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/genea/analysis/gedwrite.py tests/test_gedwrite.py
git commit -m "Add a lossless GEDCOM line editor for iterations" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Build iterations

**Files:**
- Create: `src/genea/analysis/iteration.py`
- Modify: `src/genea/analysis/api.py`, `src/genea/cli/analysiscmds.py`
- Test: `tests/test_analysis_iteration.py`

**Interfaces:**
- Consumes: Tasks 1, 5, 6, 7; `describe_operation`.
- Produces: `iteration.build(kb, analysis) -> BuildResult` (mutates proposal `status`/`stale_reason` and
  `analysis.iterations`; writes `vN.ged`, `vN.diff`, `vN.md`); `api.build_iteration(ctx, name) -> BuildResult`
  (saves the analysis and `report.md`); CLI `genea analysis build <name>`.

- [ ] **Step 1: Write the failing tests**

`tests/test_analysis_iteration.py`:

```python
import pytest
from fixtures.analysis_tree import TREE, event_line, make_analysis_kb
from typer.testing import CliRunner

from genea.analysis import api, store
from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.tools.base import ToolError, open_tools

CITE = ["[docs/zapis.txt]"]
runner = CliRunner()


def _family(ctx):
    return api.analyze(ctx, "family", "tree.ged#@F1@").name


def _changed(diff_text, sign):
    other = "---" if sign == "-" else "+++"
    return [line for line in diff_text.splitlines() if line.startswith(sign) and not line.startswith(other)]


def test_update_event_replaces_the_date_and_marks_it(actx, analysis_kb):
    name = _family(actx)
    line = event_line(actx, "@I3@", "BIRT")
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                CITE, "the register says 2 January")
    before = (analysis_kb.root / "tree.ged").read_bytes()
    result = api.build_iteration(actx, name)
    assert (analysis_kb.root / "tree.ged").read_bytes() == before
    assert result.version == 1 and result.applied == ["p-1"] and result.stale == []
    assert result.iteration == f".genea/analyses/{name}/v1.ged"
    diff = (analysis_kb.root / result.diff).read_text(encoding="utf-8")
    assert _changed(diff, "-") == ["-2 DATE 1 JAN 1878"]
    added = _changed(diff, "+")
    assert added[0] == "+2 DATE 2 JAN 1878"
    assert added[1].startswith("+2 NOTE GENEA v1 ") and " p-1 (user): " in added[1]
    assert "date -> 2 JAN 1878" in added[1] and "Base value: 1 JAN 1878, Brno" in added[1]
    md = (analysis_kb.root / result.report).read_text(encoding="utf-8")
    assert "```diff" in md and "+2 DATE 2 JAN 1878" in md
    assert store.load(analysis_kb, name).iterations == 1


def test_other_operations(actx, analysis_kb):
    name = _family(actx)
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I1@", "text": "Mlynár"}, CITE, "why")
    api.propose(actx, name, "add_source_citation", {"person_id": "tree.ged#@I1@", "source": "Matrika Brno 1850",
                                                    "page": "f. 12", "event_type": "DEAT"}, CITE, "why")
    api.propose(actx, name, "add_event", {"person_id": "tree.ged#@I4@", "type": "BAPM", "date": "3 JUN 1878",
                                          "place": "Brno"}, CITE, "why")
    person = api.propose(actx, name, "add_person", {"given": "Mária", "surname": "Weiss", "sex": "F",
                                                    "birth_date": "1883"}, CITE, "why")
    api.propose(actx, name, "link_child", {"family_id": "tree.ged#@F1@",
                                           "child_id": f"proposal:{person.proposal_id}"}, CITE, "why")
    result = api.build_iteration(actx, name)
    assert sorted(result.applied) == ["p-1", "p-2", "p-3", "p-4", "p-5"] and result.stale == []
    text = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    assert "1 NOTE Mlynár\n1 NOTE GENEA v1" in text
    assert "1 DEAT\n2 DATE 1910\n2 SOUR Matrika Brno\n2 SOUR Matrika Brno 1850\n3 PAGE f. 12\n2 NOTE GENEA v1" in text
    assert "1 BAPM\n2 DATE 3 JUN 1878\n2 PLAC Brno\n2 NOTE GENEA v1" in text
    assert "0 @GENEA1@ INDI\n1 NAME Mária /Weiss/\n1 SEX F\n1 BIRT\n2 DATE 1883\n1 FAMC @F1@\n1 NOTE GENEA v1" in text
    assert "1 CHIL @I5@\n1 CHIL @GENEA1@\n1 NOTE GENEA v1" in text
    assert text.index("0 @GENEA1@ INDI") < text.index("0 TRLR")


def test_check_notes_and_dismissals(actx, analysis_kb):
    name = _family(actx)
    first = api.build_iteration(actx, name)
    assert first.checks == 2  # born after the mother's death, siblings too close; gaps get none
    text = (analysis_kb.root / first.iteration).read_text(encoding="utf-8")
    assert text.count("1 NOTE GENEA CHECK v1") == 2
    finding = next(f for f in store.load(analysis_kb, name).findings if f.kind == "born_after_mother_death")
    api.dismiss(actx, name, finding.id, "known")
    second = api.build_iteration(actx, name)
    assert second.version == 2 and second.checks == 1
    text = (analysis_kb.root / second.iteration).read_text(encoding="utf-8")
    assert "GENEA v1" not in text and "GENEA CHECK v1" not in text  # always rebuilt from the real tree
    assert finding.id not in text


def test_changed_tree_makes_proposals_stale_not_wrong(actx, analysis_kb):
    name = _family(actx)
    line = event_line(actx, "@I3@", "BIRT")
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                CITE, "why")
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I4@", "text": "x"}, CITE, "why")
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I5@", "text": "y"}, CITE, "why")
    path = analysis_kb.root / "tree.ged"
    text = path.read_text(encoding="utf-8").replace("2 DATE 1 JAN 1878", "2 DATE 3 JAN 1878")
    text = text[:text.index("0 @I5@ INDI")] + text[text.index("0 @I6@ INDI"):]  # I5 deleted in Gramps
    path.write_text(text, encoding="utf-8")
    result = api.build_iteration(actx, name)
    assert result.applied == ["p-2"] and set(result.stale) == {"p-1", "p-3"}
    reasons = {p.id: p.stale_reason for p in store.load(analysis_kb, name).proposals}
    assert "no longer in the record" in reasons["p-1"] and "no longer in the tree" in reasons["p-3"]
    assert "changed since the analysis" in result.message
    built = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    assert "2 DATE 3 JAN 1878" in built and "2 JAN 1878" not in built


def test_shifted_duplicate_event_is_stale(tmp_path):
    tree = TREE.replace("1 BIRT\n2 DATE 1 JUN 1878\n", "1 BIRT\n2 DATE 1 JUN 1878\n1 BIRT\n2 DATE 1 JUN 1878\n")
    kb = make_analysis_kb(tmp_path / "dup", tree)
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I4@").name
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": event_line(ctx, "@I4@", "BIRT"),
                                            "date": "2 JUN 1878"}, CITE, "why")
    path = kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 @I4@ INDI\n", "0 @I4@ INDI\n1 NOTE posun\n"),
                    encoding="utf-8")
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.stale == ["p-1"]
    assert "more than once" in store.load(kb, name).proposals[0].stale_reason


def test_crlf_and_bom_are_kept(tmp_path):
    kb = make_analysis_kb(tmp_path / "crlf", newline="\r\n", bom=b"\xef\xbb\xbf")
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I3@").name
    api.propose(ctx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "Poznámka"}, CITE, "why")
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    data = (kb.root / result.iteration).read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and b"\n" not in data.replace(b"\r\n", b"")
    assert "1 NOTE Poznámka\r\n1 NOTE GENEA v1".encode() in data


def test_cp1250_tree(tmp_path):
    kb = make_analysis_kb(tmp_path / "ansi", TREE.replace("1 CHAR UTF-8", "1 CHAR ANSI"), encoding="cp1250")
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I3@").name
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": event_line(ctx, "@I3@", "BIRT"),
                                            "date": "2 JAN 1878"}, CITE, "matrika")
    api.propose(ctx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "Имя"}, CITE, "why")
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.applied == ["p-1"] and result.stale == ["p-2"]
    assert "cannot be written" in store.load(kb, name).proposals[1].stale_reason
    text = (kb.root / result.iteration).read_bytes().decode("cp1250")
    assert "date -> 2 JAN 1878" in text and "Ján /Weiss/" in text


def test_ansel_tree_is_refused(actx, analysis_kb):
    name = _family(actx)
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("1 CHAR UTF-8", "1 CHAR ANSEL"), encoding="utf-8")
    with pytest.raises(ToolError, match="UTF-8"):
        api.build_iteration(actx, name)


def test_cli_build(analysis_kb, monkeypatch, actx):
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    name = _family(actx)
    result = runner.invoke(app, ["--kb", str(analysis_kb.root), "analysis", "build", name])
    assert result.exit_code == 0, result.output
    assert "v1" in result.output and (analysis_kb.analyses_dir / name / "v1.ged").is_file()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analysis_iteration.py -v`
Expected: FAIL with `AttributeError: module 'genea.analysis.api' has no attribute 'build_iteration'`

- [ ] **Step 3: Implement the builder**

`src/genea/analysis/iteration.py`:

```python
"""Build an iteration: a copy of the real tree with every active proposal applied and marked by a GENEA note.

Always built from the real .ged, never from a previous iteration: proposals point at lines of the real tree.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import date

from genea.analysis import store
from genea.analysis.gedwrite import GedEditor, GedWriteError, text_lines
from genea.analysis.models import Analysis, BuildResult, Finding, Proposal
from genea.analysis.report import render_report
from genea.changes.export import describe_operation
from genea.ingest.gedcom.parser import GedLine, parse_gedcom
from genea.kb import KnowledgeBase
from genea.tools.base import ToolError


class Stale(Exception):
    """The proposal no longer fits the tree."""


def _xref(ref: str) -> str:
    return ref.split("#", 1)[-1]


def _value(line: GedLine, tag: str) -> str | None:
    child = line.first(tag)
    return child.value if child is not None and child.value else None


def _plain(text: str) -> str:
    return text.replace("→", "->")  # describe_operation uses arrows; CP1250 has none


class _Builder:
    def __init__(self, editor: GedEditor, ged_file: str, version: int, day: str) -> None:
        self.editor, self.ged_file, self.version, self.day = editor, ged_file, version, day
        self.lines = editor.doc.lines
        self.new_ids: dict[str, str] = {}  # add_person proposal id -> @GENEA<n>@
        self.famc: dict[str, list[str]] = {}  # new person xref -> family xrefs

    def record(self, ref: str) -> int:
        try:
            return self.editor.record_index(_xref(ref))
        except KeyError:
            raise Stale(f"{ref} is no longer in the tree") from None

    def marker(self, p: Proposal, base: str | None = None) -> str:
        finding = f", finding {p.finding_id}" if p.finding_id else ""
        cites = ", ".join(s.cite() for s in p.sources)
        text = (f"GENEA v{self.version} {self.day} {p.id} ({p.author}{finding}): "
                f"{describe_operation(p.operation, p.payload)}. Why: {p.rationale} Evidence: {cites}.")
        if base:
            text += f" Base value: {base}."
        if base or any(s.line is not None for s in p.sources):
            text += " Line numbers refer to the base file."
        return _plain(text)

    def add_event(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        lines = [f"1 {p.payload['type']}"]
        if p.payload.get("date"):
            lines.append(f"2 DATE {p.payload['date']}")
        if p.payload.get("place"):
            lines.append(f"2 PLAC {p.payload['place']}")
        self.editor.insert_after(self.editor.subtree_end(rec), lines + text_lines(2, "NOTE", self.marker(p)))

    def _find_event(self, rec: int, line_no: int, anchor: dict) -> int:
        end = self.editor.subtree_end(rec)

        def fits(i: int) -> bool:
            line = self.lines[i]
            return (line.level == 1 and line.tag == anchor.get("type") and _value(line, "DATE") == anchor.get("date")
                    and _value(line, "PLAC") == anchor.get("place"))

        if rec < line_no - 1 <= end and fits(line_no - 1):
            return line_no - 1
        matches = [i for i in range(rec + 1, end + 1) if fits(i)]
        if len(matches) == 1:
            return matches[0]
        what = f"the {anchor.get('type')} event ({anchor.get('date') or 'no date'})"
        raise Stale(f"{what} is no longer in the record" if not matches else f"{what} is in the record more than once")

    def update_event(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        anchor = p.anchor or {}
        event = self._find_event(rec, int(p.payload["line"]), anchor)
        line = self.lines[event]
        for field, tag in (("date", "DATE"), ("place", "PLAC")):
            value = p.payload.get(field)
            if not value:
                continue
            child = line.first(tag)
            text = f"{line.level + 1} {tag} {value}"
            if child is None:
                self.editor.insert_after(event, [text])
            else:
                self.editor.replace(child.line_no - 1, text)
        base = (f"{anchor.get('date') or '-'}, {anchor.get('place') or '-'} "
                f"[{self.ged_file}:L{p.payload['line']}, base file]")
        self.editor.insert_after(self.editor.subtree_end(event), text_lines(line.level + 1, "NOTE", self.marker(p, base)))

    def add_note(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        lines = text_lines(1, "NOTE", p.payload["text"]) + text_lines(1, "NOTE", self.marker(p))
        self.editor.insert_after(self.editor.subtree_end(rec), lines)

    def add_source_citation(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        owner, level = rec, 1
        event_type = p.payload.get("event_type")
        if event_type:
            end = self.editor.subtree_end(rec)
            events = [i for i in range(rec + 1, end + 1) if self.lines[i].level == 1 and self.lines[i].tag == event_type]
            if len(events) != 1:
                raise Stale(f"the person has {len(events)} {event_type} events, not exactly one")
            owner, level = events[0], 2
        lines = text_lines(level, "SOUR", p.payload["source"])
        if p.payload.get("page"):
            lines.append(f"{level + 1} PAGE {p.payload['page']}")
        self.editor.insert_after(self.editor.subtree_end(owner), lines + text_lines(level, "NOTE", self.marker(p)))

    def link_child(self, p: Proposal) -> None:
        fam = self.record(p.payload["family_id"])
        fam_xref = _xref(p.payload["family_id"])
        child = p.payload["child_id"]
        new = child.startswith("proposal:")
        if new:
            child_xref = self.new_ids.get(child.split(":", 1)[1])
            if child_xref is None:
                raise Stale(f"{child} was not added to this iteration")
        else:
            child_xref, child_rec = _xref(child), self.record(child)
        if any(c.tag == "CHIL" and c.value == child_xref for c in self.lines[fam].children):
            raise Stale(f"{child} is already a child of this family")
        self.editor.insert_after(self.editor.subtree_end(fam), [f"1 CHIL {child_xref}"] + text_lines(1, "NOTE", self.marker(p)))
        if not new:  # a new person gets its FAMC line in add_person
            self.editor.insert_after(self.editor.subtree_end(child_rec), [f"1 FAMC {fam_xref}"])

    def add_person(self, p: Proposal) -> None:
        xref, pl = self.new_ids[p.id], p.payload
        lines = [f"0 {xref} INDI", f"1 NAME {pl['given']} /{pl['surname']}/"]
        if pl.get("sex"):
            lines.append(f"1 SEX {pl['sex']}")
        if pl.get("birth_date") or pl.get("birth_place"):
            lines.append("1 BIRT")
            if pl.get("birth_date"):
                lines.append(f"2 DATE {pl['birth_date']}")
            if pl.get("birth_place"):
                lines.append(f"2 PLAC {pl['birth_place']}")
        lines += [f"1 FAMC {fam}" for fam in self.famc.get(xref, [])]
        self.editor.add_record(lines + text_lines(1, "NOTE", self.marker(p)))

    def check(self, f: Finding) -> bool:
        target = f.person_ids[0] if f.person_ids else f.family_id
        if not target:
            return False
        try:
            rec = self.record(target)
        except Stale:
            return False
        cites = ", ".join(s.cite() for s in f.sources)
        text = (f"GENEA CHECK v{self.version} {self.day} {f.id} ({f.severity}): {f.message} Evidence: {cites}. "
                "Line numbers refer to the base file.")
        try:
            self.editor.insert_after(self.editor.subtree_end(rec), text_lines(1, "NOTE", _plain(text)))
        except GedWriteError:
            return False
        return True


def _diff(base: bytes, new: bytes, editor: GedEditor, ged_file: str) -> str:
    skip = len(editor.doc.bom)
    old_lines = base[skip:].decode(editor.codec, errors="replace").splitlines()
    new_lines = new[skip:].decode(editor.codec, errors="replace").splitlines()
    return "\n".join(difflib.unified_diff(old_lines, new_lines, f"a/{ged_file}", f"b/{ged_file}", lineterm="")) + "\n"


def build(kb: KnowledgeBase, analysis: Analysis) -> BuildResult:
    path = kb.resolve_inside(analysis.ged_file)
    base = path.read_bytes()
    try:
        editor = GedEditor(parse_gedcom(path))
    except GedWriteError as exc:
        raise ToolError(str(exc)) from exc
    version = analysis.iterations + 1
    b = _Builder(editor, analysis.ged_file, version, date.today().isoformat())
    candidates = [p for p in analysis.proposals if p.status in ("active", "stale")]

    taken, n = editor.xrefs(), 0
    for p in candidates:
        if p.operation == "add_person":
            n += 1
            while f"@GENEA{n}@" in taken:
                n += 1
            b.new_ids[p.id] = f"@GENEA{n}@"
    for p in candidates:  # FAMC lines of new people, written with their record
        child = p.payload.get("child_id", "")
        if p.operation == "link_child" and child.startswith("proposal:"):
            new_xref, fam = b.new_ids.get(child.split(":", 1)[1]), _xref(p.payload["family_id"])
            if new_xref and fam in taken:
                b.famc.setdefault(new_xref, []).append(fam)

    applied, stale = [], []
    for p in sorted(candidates, key=lambda p: p.operation != "add_person"):  # new people first
        snap = editor.snapshot()
        editor.owner = p.id
        try:
            getattr(b, p.operation)(p)
        except (Stale, GedWriteError) as exc:
            editor.restore(snap)
            p.status, p.stale_reason = "stale", str(exc)
            stale.append(p.id)
            b.new_ids.pop(p.id, None)
        else:
            p.status, p.stale_reason = "active", None
            applied.append(p.id)
    editor.owner = None
    answered = {p.finding_id for p in analysis.proposals if p.status == "active"}
    checks = sum(b.check(f) for f in analysis.findings
                 if f.severity != "info" and f.id not in analysis.dismissed and f.id not in answered)

    data = editor.serialize()
    folder = store.analysis_dir(kb, analysis.name)
    ged_out, diff_out, md_out = (folder / f"v{version}.{ext}" for ext in ("ged", "diff", "md"))
    store.write_atomic(ged_out, data)
    store.write_atomic(diff_out, _diff(base, data, editor, analysis.ged_file))
    analysis.iterations = version
    store.write_atomic(md_out, render_report(analysis, version, editor.log))
    message = (f"Wrote v{version}: {len(applied)} proposal(s) applied, {len(stale)} stale, {checks} CHECK note(s). "
               "The real tree is unchanged.")
    if hashlib.sha256(base).hexdigest() != analysis.base_hash:
        message += " The tree changed since the analysis; run analyze again to refresh the findings."
    return BuildResult(name=analysis.name, version=version, iteration=kb.rel(ged_out), diff=kb.rel(diff_out),
                       report=kb.rel(md_out), applied=applied, stale=stale, checks=checks, message=message)
```

- [ ] **Step 4: Add the API function and the CLI command**

Append to `src/genea/analysis/api.py` (and add `from genea.analysis.iteration import build` and `BuildResult`
to the imports):

```python
def build_iteration(ctx: ToolContext, name: str) -> BuildResult:
    analysis = store.load(ctx.kb, name)
    result = build(ctx.kb, analysis)
    _save(ctx, analysis)
    return result
```

Append to `src/genea/cli/analysiscmds.py`:

```python
@analysis_app.command()
def build(ctx: typer.Context, name: str) -> None:
    """Write the next iteration: a copy of the tree with all active proposals applied, plus a diff and report."""
    result = _with_tools(ctx, api.build_iteration, name)
    common.console.print(escape(result.message))
    for label, path in (("tree", result.iteration), ("diff", result.diff), ("report", result.report)):
        common.console.print(f"{label}: {escape(path)}")
    for proposal_id in result.stale:
        common.console.print(f"[yellow]{proposal_id} not applied; see the report.[/yellow]")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analysis_iteration.py tests/test_analysis_proposals.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/genea/analysis/iteration.py src/genea/analysis/api.py src/genea/cli/analysiscmds.py tests/test_analysis_iteration.py
git commit -m "Build tree iterations with proposals applied and marked by GENEA notes" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Keep GENEA notes out of the evidence

**Files:**
- Modify: `src/genea/ingest/gedcom/mapper.py`
- Test: `tests/test_gedcom_mapper.py`

**Interfaces:**
- Produces: `GENEA_NOTE` regex in the mapper; person notes starting `GENEA v<N> ` or `GENEA CHECK v<N> ` are
  not indexed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gedcom_mapper.py` (add imports `from genea.ingest.gedcom.mapper import map_gedcom` and
`from genea.ingest.gedcom.parser import parse_gedcom` if the file does not already have them):

```python
def test_genea_notes_are_not_person_notes(tmp_path):
    path = tmp_path / "t.ged"
    path.write_text("0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME A /B/\n1 NOTE Mlynár\n"
                    "1 NOTE GENEA v1 2026-10-11 p-1 (claude): Add a note\n"
                    "1 NOTE GENEA CHECK v2 2026-10-11 f-1: x\n1 NOTE GENEALOGY club member\n0 TRLR\n",
                    encoding="utf-8")
    [person] = map_gedcom(parse_gedcom(path)).persons
    assert person.notes == "Mlynár\nGENEALOGY club member"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_gedcom_mapper.py::test_genea_notes_are_not_person_notes -v`
Expected: FAIL (the GENEA notes are included)

- [ ] **Step 3: Implement**

In `src/genea/ingest/gedcom/mapper.py` add below `FAMILY_EVENT_TAGS`:

```python
GENEA_NOTE = re.compile(r"^GENEA (CHECK )?v\d+ ")  # notes genea writes into iterations; never evidence
```

and change the `notes=` argument of `PersonRec(...)` to:

```python
                name_variants=variants,
                notes="\n".join(t for t in note_texts if t and not GENEA_NOTE.match(t)),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_gedcom_mapper.py tests/test_indexer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/genea/ingest/gedcom/mapper.py tests/test_gedcom_mapper.py
git commit -m "Do not index GENEA notes as person notes" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: MCP tools and instructions

**Files:**
- Modify: `src/genea/mcp_server.py`, `src/genea/agent/prompt.py`, `tests/test_mcp_server.py`, `tests/test_prompt.py`
- Test: `tests/test_mcp_analysis.py`

**Interfaces:**
- Consumes: `api.analyze`, `read_analysis`, `propose`, `withdraw`, `add_note`, `build_iteration`.
- Produces: MCP tools `analyze(scope, target_id, direction, depth)`, `read_analysis(name, severity, page)`,
  `propose(name, operation, payload, sources, rationale, finding_id)`, `withdraw(name, proposal_id)`,
  `add_analysis_note(name, text)`, `build_iteration(name)`. No `dismiss`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_analysis.py`:

```python
import asyncio
import json

import pytest

pytest.importorskip("mcp")

from fixtures.analysis_tree import event_line  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError as McpToolError  # noqa: E402

from genea.analysis import store  # noqa: E402
from genea.mcp_server import build_server  # noqa: E402
from genea.tool_runner import TRUNCATION_MARKER  # noqa: E402


def _call(server, tool, arguments):
    return asyncio.run(server.call_tool(tool, arguments)).content[0].text


def test_claude_analyzes_proposes_and_builds(actx, analysis_kb):
    server = build_server(actx)
    text = _call(server, "analyze", {"scope": "branch", "target_id": "tree.ged#@I3@", "direction": "ancestors"})
    assert TRUNCATION_MARKER not in text
    out = json.loads(text)
    name = out["name"]
    assert name == "tree-branch-I3-ancestors-4" and len(out["findings"]) <= 10
    line = event_line(actx, "@I3@", "BIRT")
    _call(server, "propose", {"name": name, "operation": "update_event",
                              "payload": {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                              "sources": ["[docs/zapis.txt]"], "rationale": "the register"})
    assert store.load(analysis_kb, name).proposals[0].author == "claude"
    _call(server, "add_analysis_note", {"name": name, "text": "Register checked."})
    built = json.loads(_call(server, "build_iteration", {"name": name}))
    assert (analysis_kb.root / built["iteration"]).is_file() and built["applied"] == ["p-1"]
    page = json.loads(_call(server, "read_analysis", {"name": name, "severity": "info"}))
    assert all(f["severity"] == "info" for f in page["findings"])
    assert json.loads(_call(server, "withdraw", {"name": name, "proposal_id": "p-1"}))["status"] == "withdrawn"


@pytest.mark.parametrize("name", ["..", "../x", "..\\x", "a/b", ""], ids=["dots", "up", "backslash", "slash", "empty"])
def test_names_cannot_leave_the_analyses_folder(actx, name):
    with pytest.raises(McpToolError, match="Invalid analysis name"):
        _call(build_server(actx), "read_analysis", {"name": name})


def test_bad_proposal_is_an_error_for_claude(actx):
    server = build_server(actx)
    name = json.loads(_call(server, "analyze", {"scope": "person", "target_id": "tree.ged#@I3@"}))["name"]
    with pytest.raises(McpToolError, match="Cite at least one source"):
        _call(server, "propose", {"name": name, "operation": "add_note",
                                  "payload": {"person_id": "tree.ged#@I3@", "text": "x"}, "sources": [],
                                  "rationale": "why"})
```

In `tests/test_mcp_server.py` replace the registration test with:

```python
ANALYSIS = {"analyze", "read_analysis", "propose", "withdraw", "add_analysis_note", "build_iteration"}


def test_registers_exactly_the_expected_tools(tools):
    names = {t.name for t in asyncio.run(build_server(tools).list_tools())}
    assert names == READ_ONLY | MCP_ONLY | ANALYSIS
    assert not names & {"write_note", "propose_change", "dismiss"}
```

In `tests/test_cli_mcp.py` (around line 106) extend the expected tool names of the stdio session:

```python
    assert {t.name for t in listed.tools} == {
        "find_person", "get_person", "relatives", "timeline", "search_documents",
        "read_document", "find_images", "find_conflicts", "index_status",
        "analyze", "read_analysis", "propose", "withdraw", "add_analysis_note", "build_iteration"}
```

In `tests/test_prompt.py` replace `test_mcp_instructions_mention_no_write_tools` with:

```python
def test_mcp_instructions_allow_only_analysis_writes():
    for word in ("propose_change", "write_note", "genea changes"):
        assert word not in MCP_INSTRUCTIONS
    assert ".genea/analyses/" in MCP_INSTRUCTIONS
    assert "Never claim that the family tree itself was changed" in MCP_INSTRUCTIONS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_analysis.py tests/test_mcp_server.py tests/test_prompt.py -v`
Expected: FAIL (unknown tool `analyze`, prompt text missing)

- [ ] **Step 3: Update the instructions**

In `src/genea/agent/prompt.py` replace `_MCP_RULE_4` with:

```python
_MCP_RULE_4 = """\
4. You cannot change the family tree, documents or photos. The only files you can write are analyses in
   .genea/analyses/: analyze finds conflicts and gaps, propose records a correction for the user to review
   (every proposal needs citations copied from tool results and a rationale), and build_iteration writes a
   copy of the tree with the proposals applied. To suggest anything else, describe it with its citations.
   Never claim that the family tree itself was changed.
"""
```

- [ ] **Step 4: Register the tools**

In `src/genea/mcp_server.py`: change the module docstring to
`"""MCP server: the query tools the built-in agent uses, plus analysis tools that write only under .genea/analyses/."""`,
change the `build_server` docstring's first sentence to `"""Register the tools.`, change
`from typing import Literal` to `from typing import Any, Literal`, add `from genea.analysis import api as analysis`,
and add before the `index_status` tool:

```python
    @server.tool(structured_output=False)
    async def analyze(scope: Literal["person", "family", "branch"], target_id: str,
                      direction: Literal["ancestors", "descendants", "both"] = "both", depth: int = 4) -> str:
        """Check a person, a family (id like 'tree.ged#@F3@') or a branch (ancestors/descendants of a person,
        depth 1-6) for conflicts and gaps. Writes a report under .genea/analyses/ and returns the analysis name,
        counts and the first findings; read_analysis pages through the rest. The tree is not changed."""
        return run(analysis.analyze, scope, target_id, direction, depth)

    @server.tool(structured_output=False)
    async def read_analysis(name: str, severity: Literal["error", "needs_review", "info"] | None = None,
                            page: int = 1) -> str:
        """One page of an analysis' findings and their proposals, optionally only one severity."""
        return run(analysis.read_analysis, name, severity, page)

    @server.tool(structured_output=False)
    async def propose(name: str,
                      operation: Literal["add_event", "update_event", "add_note", "add_source_citation",
                                         "link_child", "add_person"],
                      payload: dict[str, Any], sources: list[str], rationale: str,
                      finding_id: str | None = None) -> str:
        """Record a proposed correction in an analysis, for the user to review; the tree is not changed.
        payload: add_event {person_id, type, date?, place?}; update_event {person_id, line, date?, place?}
        (line from get_person); add_note {person_id, text}; add_source_citation {person_id, source, page?,
        event_type?}; link_child {family_id, child_id} (child_id may be 'proposal:<id>' of an add_person
        proposal); add_person {given, surname, sex?, birth_date?, birth_place?}. sources are citations copied
        from tool results, like '[docs/matrika.pdf p.3]'."""
        return run(analysis.propose, name, operation, payload, sources, rationale, finding_id, "claude")

    @server.tool(structured_output=False)
    async def withdraw(name: str, proposal_id: str) -> str:
        """Withdraw one of the proposals of an analysis."""
        return run(analysis.withdraw, name, proposal_id)

    @server.tool(structured_output=False)
    async def add_analysis_note(name: str, text: str) -> str:
        """Add a research note to an analysis report (kept apart from the evidence)."""
        return run(analysis.add_note, name, text)

    @server.tool(structured_output=False)
    async def build_iteration(name: str) -> str:
        """Write the next iteration of the tree under .genea/analyses/<name>/: a copy with every active proposal
        applied and marked by a GENEA note, plus a diff and a report. The real tree is not changed."""
        return run(analysis.build_iteration, name)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mcp_analysis.py tests/test_mcp_server.py tests/test_prompt.py tests/test_cli_mcp.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/genea/mcp_server.py src/genea/agent/prompt.py tests/test_mcp_analysis.py tests/test_mcp_server.py tests/test_prompt.py tests/test_cli_mcp.py
git commit -m "Serve analysis tools over MCP, writing only under .genea/analyses" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Documentation and full test run

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the commands**

In the `## Commands` table of `README.md`, add after the `genea conflicts [id]` row:

```markdown
| `genea analyze person\|family\|branch <id> [--direction D] [--depth N]` | Check part of the tree for conflicts and gaps; writes a report to `.genea/analyses/` |
| `genea analysis list / show / propose / withdraw / dismiss / build` | Review findings, record proposals and build a corrected copy of the tree |
```

and change the `genea mcp` row's description to
`Serve the knowledge base to Claude over MCP (read-only, except analyses; see "Use with Claude")`.

Add a section after `## How changes work`:

```markdown
## Analyses: proposing corrections to the tree

`genea analyze` (or the MCP tool `analyze`) checks a person, a family or a branch for conflicts (events after
death, a birth after the mother's death, siblings born too close together…) and gaps (no birth, no death for
someone born over 110 years ago, no parents, events without a source). Detectors only find problems; they
never guess a value.

Corrections are **proposals** with citations and a reason, recorded by Claude (`propose`) or by you
(`genea analysis propose`). `genea analysis build <name>` (MCP `build_iteration`) then writes
`.genea/analyses/<name>/vN.ged`: a full copy of the real tree with every active proposal applied, each marked
by a `NOTE` starting `GENEA vN`. Problems nobody has decided on get a `GENEA CHECK` note. Next to it are
`vN.diff` and `vN.md` (the report with each change's diff).

- The real `.ged` is never changed. Each iteration is rebuilt from it, so v2 does not contain v1's notes.
- If the tree changed since a proposal was made (a new Gramps export), the proposal is re-found by its event;
  if that is not possible it is marked stale and left out, never applied to the wrong line.
- `genea analysis dismiss <name> <finding>` marks a finding as not a problem (only you can do this).
- Only UTF-8 and ANSI (CP1250) trees can be written; export from Gramps as UTF-8.
- An iteration cannot be imported into Gramps (Gramps would duplicate everyone). Review the diff and enter the
  changes in Gramps; `GENEA` notes are never indexed as evidence.
```

- [ ] **Step 2: Run the whole test suite**

Run: `uv run pytest -q`
Expected: all tests pass (live tests skipped as usual).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document analyses and tree iterations" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
