# DNA Analyzer (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `genea dna setup|import|analyze|status` turn a local MyHeritage raw DNA file into a per-person store with
mtDNA and Y haplogroups, and three read-only tools (`dna_summary`, `haplogroups`, `snp_lookup`) serve the results to
the local agent and the MCP server with a per-call cap and a per-session budget.

**Architecture:** A new package `genea.dna`: `store` owns `<kb>/.genea/dna/<slug>/` (meta.json, haplogroups.json,
genotypes.sqlite), `parse` + `qc` read the CSV through `snps`, `runtime` finds or installs Java and Haplogrep under
`~/.genea/dna-tools/`, `haplo` builds tool inputs and parses tool outputs, `api` holds the CLI operations, `limits` +
`tools` are the read-only tool layer. Tools take only a `KnowledgeBase` (no index), run through a new
`run_kb_tool`, and bypass `IndexGate` on MCP.

**Tech Stack:** Python 3.12, pydantic, typer, SQLite, `snps` 2.12 (BSD-3), `yhaplo` (23andMe, GitHub, free for
non-commercial individual use), Haplogrep 3.3.2 (MIT, Java), Temurin JRE 21 (portable fallback), MCP Python SDK,
pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-10-14-dna-analyzer-design.md`

## Global Constraints

- Nothing is hardcoded to a KB path: per-KB data lives only under `kb.dna_dir` (`<kb root>/.genea/dna`); shared tools live under `config.global_dir() / "dna-tools"`.
- Input is a MyHeritage raw CSV, header `RSID,CHROMOSOME,POSITION,RESULT`, build 37 only; any other build is refused, never lifted over.
- `snps` is called with `deduplicate_XY_chrom=False, deduplicate_MT_chrom=False` so heterozygous/heteroplasmic X/Y/MT calls are kept as reported.
- Call rate below 95% is a warning, not an error.
- `snp_lookup`: at most **20** rsIDs per call (rejected, not truncated); per-session budget default **200**, setting `dna_session_rsids`.
- No tool returns whole chromosomes, the file, or the path to the raw CSV; `meta.json` stores only the file name.
- MCP and the agent never import, analyse, start Java or write; computation is CLI-only.
- Citations: `[dna/<slug>/meta.json]`, `[dna/<slug>/haplogroups.json]`, `[dna/<slug>/genotypes.sqlite]` (relative to `.genea`).
- All writes are atomic (temp file + `os.replace`, or staging folder + rename); a failed run leaves no half-written file.
- Haplogrep 3.3.2: `https://github.com/genepi/haplogrep3/releases/download/v3.3.2/haplogrep3-3.3.2-windows.tar.gz`, sha256 `97a808461cdf8ce21d0d00c9a29f8500b29345fae8d22ec99c69d6853033c5f4`, tree `phylotree-fu-rcrs@1.3`, run as `java -jar haplogrep3.exe classify --in x.vcf --tree phylotree-fu-rcrs@1.3 --chip --out y.txt`.
- Haplogrep needs Java 11+. Portable JRE: `jdk-21.0.12.1+1`, `https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip`, sha256 `d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636`. System Java, `PATH` and `JAVA_HOME` are never modified.
- `yhaplo` pinned to commit `f72f88f2818aebd049c29d2ca8fc2f30c3003b17`, run as `python -c "from yhaplo.cli.yhaplo import main; main()" -i <x>.genos.txt -o <dir>`, result `<dir>/haplogroups.<x>.txt`.
- The `dna` extra is optional: `genea doctor` shows DNA checks only when `snps` is installed.
- Tests never use the real DNA file; fixtures are synthetic.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Heterozygous Y or heteroplasmic MT calls (`AG`) — must be skipped and counted, never collapsed to one base and fed to a classifier (tests in Task 4).
2. A file whose MT positions are not rCRS coordinates — mtDNA is skipped with a clear reason and Y still runs, instead of a confident wrong haplogroup (test in Task 4).
3. Re-import with `--replace` after an analysis — the old `haplogroups.json` must disappear with the old genotypes, never survive next to new data (test in Task 1).
4. The tree's sex disagrees with the DNA's sex — the import warns that the file may be linked to the wrong person (test in Task 2).
5. `snp_lookup` with duplicates, mixed case or one comma-separated string — normalised, charged once per distinct rsID, and invalid ids rejected before any budget is spent (tests in Task 5).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/genea/dna/__init__.py` | package marker |
| `src/genea/dna/store.py` | models (`QC`, `PersonMeta`, `HaploCall`, `Haplogroups`), `DnaError`, slugs, GEDCOM id normalisation, atomic writes, per-person folder, genotype SQLite, `resolve_person`, `citation_exists` |
| `src/genea/dna/parse.py` | header check, `read_myheritage` via `snps` |
| `src/genea/dna/qc.py` | `compute_qc` |
| `src/genea/dna/runtime.py` | tool locations, Java detection, verified downloads, `setup` |
| `src/genea/dna/haplo.py` | `haploid`, `mt_vcf`, `y_genos`, output parsers, `call_mtdna`, `call_y` |
| `src/genea/dna/api.py` | `import_file`, `analyze` (CLI operations) |
| `src/genea/dna/limits.py` | `MAX_RSIDS_PER_CALL`, `RsidBudget` |
| `src/genea/dna/tools.py` | `dna_summary`, `haplogroups`, `snp_lookup` + result models |
| `src/genea/cli/dnacmds.py` | `genea dna setup|import|analyze|status` |
| Modify `pyproject.toml` | `dna` extra, `snps` in dev group, hatch direct references |
| Modify `src/genea/config.py` | `dna_session_rsids` |
| Modify `src/genea/kb.py` | `dna_dir` |
| Modify `src/genea/tool_runner.py` | `render_limited`, `error_limited`, `run_kb_tool` |
| Modify `src/genea/analysis/api.py` | `propose` accepts DNA citations |
| Modify `src/genea/doctor.py` | `dna_checks` |
| Modify `src/genea/mcp_server.py`, `src/genea/agent/core.py`, `src/genea/agent/prompt.py` | DNA tools and rule |
| Modify `src/genea/cli/app.py`, `src/genea/cli/main.py` | register `dna` commands |
| Modify `README.md` | DNA section |
| `tests/fixtures/dna.py` | synthetic rows, CSV writer, `add_person` |
| Tests | `test_dna_store.py`, `test_dna_parse.py`, `test_dna_runtime.py`, `test_dna_haplo.py`, `test_dna_tools.py`, `test_cli_dna.py`, `test_mcp_dna.py`, `tests/live/test_live_dna.py`; edits to `test_mcp_server.py`, `test_prompt.py`, `test_doctor.py`, `test_analysis_proposals.py`, `test_kb.py` |

---

### Task 1: Store, settings and the `dna` extra

**Files:**
- Modify: `pyproject.toml`, `src/genea/config.py`, `src/genea/kb.py`
- Create: `src/genea/dna/__init__.py`, `src/genea/dna/store.py`, `tests/fixtures/dna.py`
- Test: `tests/test_dna_store.py`, `tests/test_kb.py`

**Interfaces:**
- Consumes: `KnowledgeBase`, `ToolError` (`genea.tools.base`).
- Produces:
  - `Row = tuple[str, str, int, str | None]` (rsid, chrom, pos, genotype; `None` = no call)
  - `class DnaError(ToolError)`
  - models `QC(total, called, call_rate, by_chrom, warnings)`, `PersonMeta(slug, gedcom_id, tree_name, tree_sex, source_name, build, sex, qc, imported_at)`, `HaploCall(line, haplogroup, detail, quality, markers_used, markers_skipped, skipped_reason, tool)`, `Haplogroups(analyzed_at, calls)`
  - `now() -> str`, `normalize_gedcom_id(raw) -> str`, `slug_for(gedcom_id) -> str`, `check_slug(slug)`, `person_dir(kb, slug) -> Path`
  - `save_person(kb, meta, rows, replace=False) -> Path`, `load_meta(kb, slug)`, `list_people(kb)`, `resolve_person(kb, person) -> PersonMeta`
  - `save_haplogroups(kb, slug, result)`, `load_haplogroups(kb, slug) -> Haplogroups | None`
  - `genotypes(kb, slug, rsids) -> dict[str, Row]`, `chrom_rows(kb, slug, chroms) -> list[Row]`, `citation_exists(kb, file) -> bool`
  - `KnowledgeBase.dna_dir`, `Settings.dna_session_rsids: int = 200`
  - fixtures `Y_R1B`, `rows_for(sex, seed=1)`, `write_csv(path, sex="male", build=37, rows=None)`, `add_person(kb, slug=..., gedcom_id=..., sex="male", rows=None)`

- [ ] **Step 1: Dependencies and settings**

In `pyproject.toml` add to `[project.optional-dependencies]`:

```toml
dna = ["snps>=2.12", "yhaplo @ git+https://github.com/23andMe/yhaplo@f72f88f2818aebd049c29d2ca8fc2f30c3003b17"]
```

add `"snps>=2.12"` to the `dev` list in `[dependency-groups]`, and add (hatchling refuses direct references otherwise):

```toml
[tool.hatch.metadata]
allow-direct-references = true
```

In `src/genea/config.py` add to `Settings` after `transcribe_structure_model`:

```python
    dna_session_rsids: int = 200  # rsIDs one agent or MCP session may look up with snp_lookup (privacy budget)
```

Run: `uv sync` — Expected: installs `snps`, `pandas`, `numpy` into the dev environment.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_kb.py`:

```python
def test_dna_dir_is_under_meta_dir_and_created_on_demand(tmp_path):
    kb = init_kb(tmp_path)
    assert kb.dna_dir == kb.meta_dir / "dna"
    assert not kb.dna_dir.exists()
```

Create `tests/fixtures/dna.py`:

```python
"""Synthetic MyHeritage raw DNA data for the tests: invented genotypes, never a real file."""

from __future__ import annotations

import random
from pathlib import Path

from genea.dna import store
from genea.kb import KnowledgeBase

# (GRCh37 position, allele) from yhaplo's ISOGG 2016 table: derived alleles of M168, M89, M9, M45, M207, M173,
# M343, M269, L23, L51, P311, P312 and ancestral alleles of M170, M253, M198, M458. yhaplo calls this R-P312.
Y_R1B = [(14813991, "T"), (21917313, "T"), (21730257, "G"), (21867787, "A"), (15581983, "G"), (15026424, "C"),
         (2887824, "A"), (22739367, "C"), (6753511, "A"), (8502236, "A"), (18248698, "G"), (22157311, "A"),
         (14847792, "A"), (15022707, "C"), (15030752, "C"), (24366464, "A")]
MT_POSITIONS = (73, 263, 750, 1438, 2706, 4769, 7028, 8860, 15326, 16519)


def rows_for(sex: str, seed: int = 1) -> list[store.Row]:
    """300 autosomal SNPs (every 50th a no-call, so the call rate stays above 95%), 200 X SNPs (a third heterozygous for a woman), the R1b Y SNPs for a man,
    ten homoplasmic MT calls and one heteroplasmic MT call at 16000."""
    r = random.Random(seed)
    rows: list[store.Row] = [(f"rs{1000 + i}", "1", 10_000 + i * 100,
                              None if i % 50 == 0 else r.choice(["AA", "AG", "GG"])) for i in range(300)]
    for i in range(200):
        het = sex == "female" and i % 3 == 0
        rows.append((f"rs{5000 + i}", "X", 3_000_000 + i * 100,
                     r.choice(["AG", "CT"]) if het else r.choice(["AA", "GG"])))
    if sex == "male":
        rows += [(f"rs{8000 + i}", "Y", pos, base * 2) for i, (pos, base) in enumerate(Y_R1B)]
    rows += [(f"i{900 + i}", "MT", pos, "AA") for i, pos in enumerate(MT_POSITIONS)]
    rows.append(("i999", "MT", 16000, "AG"))
    return rows


def write_csv(path: Path, sex: str = "male", build: int = 37, rows: list[store.Row] | None = None) -> Path:
    rows = rows_for(sex) if rows is None else rows
    lines = ["# MyHeritage DNA raw data.",
             "# This file was generated for the genea tests.",
             "# The genotype is reported on the forward (+) strand with respect to the human reference "
             f"build {build}.",
             "RSID,CHROMOSOME,POSITION,RESULT"]
    lines += [f'"{rsid}","{chrom}","{pos}","{gt or "--"}"' for rsid, chrom, pos, gt in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def add_person(kb: KnowledgeBase, slug: str = "tree.ged-I12", gedcom_id: str = "tree.ged#@I12@",
               sex: str = "male", rows: list[store.Row] | None = None) -> store.PersonMeta:
    rows = rows_for(sex) if rows is None else rows
    called = sum(1 for r in rows if r[3])
    meta = store.PersonMeta(slug=slug, gedcom_id=gedcom_id, source_name="MyHeritage_raw_dna_data.csv", build=37,
                            sex=sex, qc=store.QC(total=len(rows), called=called, call_rate=called / len(rows),
                                                 by_chrom={}),
                            imported_at="2026-10-15T10:00:00")
    store.save_person(kb, meta, rows)
    return meta
```

Create `tests/test_dna_store.py`:

```python
import pytest
from fixtures.dna import add_person

from genea.dna import store
from genea.dna.store import DnaError, HaploCall, Haplogroups
from genea.kb import init_kb


@pytest.fixture
def kb(tmp_path):
    return init_kb(tmp_path / "kb")


def test_slug_and_gedcom_id():
    assert store.normalize_gedcom_id(" tree.ged#I12 ") == "tree.ged#@I12@"
    assert store.slug_for("tree.ged#@I12@") == "tree.ged-I12"
    with pytest.raises(DnaError, match="tree.ged#@I12@"):
        store.normalize_gedcom_id("I12")
    for bad in ("..", ".", "a/b", ""):
        with pytest.raises(DnaError):
            store.check_slug(bad)


def test_save_and_resolve_by_slug_or_gedcom_id(kb):
    add_person(kb)
    assert store.resolve_person(kb, "tree.ged-I12").gedcom_id == "tree.ged#@I12@"
    assert store.resolve_person(kb, "tree.ged#I12").slug == "tree.ged-I12"
    with pytest.raises(DnaError, match="tree.ged-I12"):
        store.resolve_person(kb, "nobody")
    assert (kb.dna_dir / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert [m.slug for m in store.list_people(kb)] == ["tree.ged-I12"]


def test_genotype_lookup_and_rows(kb):
    add_person(kb)
    found = store.genotypes(kb, "tree.ged-I12", ["rs8000", "rs1000", "rs404"])
    assert found["rs8000"] == ("rs8000", "Y", 14813991, "TT")
    assert "rs404" not in found
    mt = store.chrom_rows(kb, "tree.ged-I12", ("MT",))
    assert [r[2] for r in mt] == sorted(r[2] for r in mt) and len(mt) == 11


def test_existing_person_needs_replace(kb):
    add_person(kb)
    with pytest.raises(DnaError, match="--replace"):
        add_person(kb)


def test_replace_drops_old_analysis(kb):
    add_person(kb)
    store.save_haplogroups(kb, "tree.ged-I12", Haplogroups(analyzed_at="x", calls=[HaploCall(line="Y")]))
    meta = store.load_meta(kb, "tree.ged-I12")
    store.save_person(kb, meta, [("rs1", "1", 5, "AA")], replace=True)
    assert store.load_haplogroups(kb, "tree.ged-I12") is None
    assert store.genotypes(kb, "tree.ged-I12", ["rs8000"]) == {}
    assert not any(p.name.startswith(".tree.ged-I12") for p in kb.dna_dir.iterdir())


def test_failed_write_leaves_no_person(kb, monkeypatch):
    def boom(path, rows):
        raise OSError("disk full")
    monkeypatch.setattr(store, "_write_genotypes", boom)
    with pytest.raises(OSError):
        add_person(kb)
    assert store.list_people(kb) == []
    assert not (kb.dna_dir / "tree.ged-I12").exists()


def test_citation_exists(kb):
    add_person(kb)
    assert store.citation_exists(kb, "dna/tree.ged-I12/meta.json")
    assert store.citation_exists(kb, "dna/tree.ged-I12/genotypes.sqlite")
    assert not store.citation_exists(kb, "dna/tree.ged-I12/haplogroups.json")  # not analysed yet
    assert not store.citation_exists(kb, "dna/../config.toml")
    assert not store.citation_exists(kb, "dna/../meta.json")


def test_two_kbs_do_not_share_people(tmp_path):
    a, b = init_kb(tmp_path / "a"), init_kb(tmp_path / "b")
    add_person(a)
    assert store.list_people(b) == []
    with pytest.raises(DnaError):
        store.resolve_person(b, "tree.ged-I12")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dna_store.py tests/test_kb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'genea.dna'` and `AttributeError: ... 'dna_dir'`.

- [ ] **Step 4: Implement**

In `src/genea/kb.py` add after `analyses_dir` (not in `SUBDIRS`: only created when DNA is imported):

```python
    @property
    def dna_dir(self) -> Path:
        """Per-person DNA data (`genea dna`); created on the first import."""
        return self.meta_dir / "dna"
```

Create `src/genea/dna/__init__.py` with the single line `"""DNA analysis of a MyHeritage raw file: storage, haplogroups and read-only tools."""`.

Create `src/genea/dna/store.py`:

```python
"""Per-person DNA data under <kb>/.genea/dna/<slug>/: meta.json, haplogroups.json and genotypes.sqlite."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from genea.kb import KnowledgeBase
from genea.tools.base import ToolError

Row = tuple[str, str, int, str | None]  # rsid, chromosome, position, genotype (None: no call)

_SLUG = re.compile(r"^[\w.-]+$")
_CITE_FILE = re.compile(r"^dna/(?P<slug>[\w.-]+)/(?P<name>meta\.json|haplogroups\.json|genotypes\.sqlite)$")


class DnaError(ToolError):
    """A DNA problem the user or the model can act on; the message says what to do."""


class QC(BaseModel):
    total: int
    called: int
    call_rate: float
    by_chrom: dict[str, int]
    warnings: list[str] = Field(default_factory=list)


class PersonMeta(BaseModel):
    slug: str
    gedcom_id: str
    tree_name: str | None = None
    tree_sex: str | None = None
    source_name: str  # the file name only, never its path
    build: int
    sex: Literal["male", "female", "unknown"]
    qc: QC
    imported_at: str


class HaploCall(BaseModel):
    line: Literal["Y", "mtDNA"]
    haplogroup: str | None = None
    detail: str | None = None
    quality: float | None = None
    markers_used: int = 0
    markers_skipped: int = 0
    skipped_reason: str | None = None
    tool: str | None = None


class Haplogroups(BaseModel):
    analyzed_at: str
    calls: list[HaploCall]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_gedcom_id(raw: str) -> str:
    """'tree.ged#I12' or 'tree.ged#@I12@' -> 'tree.ged#@I12@'."""
    text = (raw or "").strip()
    ged, _, xref = text.partition("#")
    xref = xref.strip().strip("@")
    if not ged.strip() or not xref:
        raise DnaError(f"Use a tree id like 'tree.ged#@I12@' (find_person shows them), not {raw!r}.")
    return f"{ged.strip()}#@{xref}@"


def check_slug(slug: str) -> None:
    if not _SLUG.match(slug or "") or slug in (".", ".."):
        raise DnaError(f"Invalid DNA person name {slug!r}: use letters, digits, '.', '_' or '-'.")


def slug_for(gedcom_id: str) -> str:
    """'tree.ged#@I12@' -> 'tree.ged-I12'."""
    slug = re.sub(r"[^\w.-]+", "-", gedcom_id).strip("-")
    check_slug(slug)
    return slug


def person_dir(kb: KnowledgeBase, slug: str) -> Path:
    check_slug(slug)
    return kb.dna_dir / slug


def _write_json(path: Path, model: BaseModel) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_genotypes(path: Path, rows: Iterable[Row]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE genotypes (rsid TEXT PRIMARY KEY, chrom TEXT NOT NULL, pos INTEGER NOT NULL, "
                     "genotype TEXT) WITHOUT ROWID")
        conn.execute("CREATE INDEX genotypes_chrom ON genotypes (chrom, pos)")
        conn.executemany("INSERT OR IGNORE INTO genotypes VALUES (?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _open_genotypes(kb: KnowledgeBase, slug: str) -> sqlite3.Connection:
    path = person_dir(kb, slug) / "genotypes.sqlite"
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def save_person(kb: KnowledgeBase, meta: PersonMeta, rows: Iterable[Row], replace: bool = False) -> Path:
    """Write into a staging folder, then swap it in. A re-import replaces everything, old analyses included."""
    final = person_dir(kb, meta.slug)
    if final.exists() and not replace:
        raise DnaError(f"{meta.slug} is already imported; add --replace to import it again.")
    kb.dna_dir.mkdir(parents=True, exist_ok=True)
    ignore = kb.dna_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    staging, old = kb.dna_dir / f".{meta.slug}.importing", kb.dna_dir / f".{meta.slug}.old"
    for leftover in (staging, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    staging.mkdir()
    try:
        _write_genotypes(staging / "genotypes.sqlite", rows)
        _write_json(staging / "meta.json", meta)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if final.exists():
        final.rename(old)
    staging.rename(final)
    shutil.rmtree(old, ignore_errors=True)
    return final


def load_meta(kb: KnowledgeBase, slug: str) -> PersonMeta:
    path = person_dir(kb, slug) / "meta.json"
    if not path.is_file():
        raise DnaError(f"No DNA imported as {slug}.")
    return PersonMeta.model_validate_json(path.read_text(encoding="utf-8"))


def list_people(kb: KnowledgeBase) -> list[PersonMeta]:
    if not kb.dna_dir.is_dir():
        return []
    return [load_meta(kb, d.name) for d in sorted(kb.dna_dir.iterdir())
            if d.is_dir() and not d.name.startswith(".") and (d / "meta.json").is_file()]


def resolve_person(kb: KnowledgeBase, person: str) -> PersonMeta:
    """A DNA person name ('tree.ged-I12') or the tree id it was imported for ('tree.ged#@I12@')."""
    wanted = {(person or "").strip()}
    if "#" in (person or ""):
        try:
            wanted.add(normalize_gedcom_id(person))
        except DnaError:
            pass
    people = list_people(kb)
    for meta in people:
        if wanted & {meta.slug, meta.gedcom_id}:
            return meta
    known = ", ".join(f"{m.slug} ({m.gedcom_id})" for m in people) or "none"
    raise DnaError(f"No DNA for {person!r}. Imported: {known}. "
                   "The user imports a file with `genea dna import <csv> --person <tree id>`.")


def save_haplogroups(kb: KnowledgeBase, slug: str, result: Haplogroups) -> None:
    _write_json(person_dir(kb, slug) / "haplogroups.json", result)


def load_haplogroups(kb: KnowledgeBase, slug: str) -> Haplogroups | None:
    path = person_dir(kb, slug) / "haplogroups.json"
    if not path.is_file():
        return None
    return Haplogroups.model_validate_json(path.read_text(encoding="utf-8"))


def genotypes(kb: KnowledgeBase, slug: str, rsids: list[str]) -> dict[str, Row]:
    if not rsids:
        return {}
    conn = _open_genotypes(kb, slug)
    try:
        marks = ",".join("?" * len(rsids))
        rows = conn.execute(f"SELECT rsid, chrom, pos, genotype FROM genotypes WHERE rsid IN ({marks})", rsids)
        return {row[0]: tuple(row) for row in rows}
    finally:
        conn.close()


def chrom_rows(kb: KnowledgeBase, slug: str, chroms: tuple[str, ...]) -> list[Row]:
    conn = _open_genotypes(kb, slug)
    try:
        marks = ",".join("?" * len(chroms))
        return [tuple(r) for r in conn.execute(
            f"SELECT rsid, chrom, pos, genotype FROM genotypes WHERE chrom IN ({marks}) ORDER BY chrom, pos", chroms)]
    finally:
        conn.close()


def citation_exists(kb: KnowledgeBase, file: str) -> bool:
    """True for 'dna/<slug>/meta.json' (or haplogroups.json, genotypes.sqlite) when that file exists."""
    match = _CITE_FILE.match(file or "")
    if not match or match["slug"] in (".", ".."):
        return False
    return (kb.dna_dir / match["slug"] / match["name"]).is_file()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dna_store.py tests/test_kb.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/genea/config.py src/genea/kb.py src/genea/dna tests/fixtures/dna.py tests/test_dna_store.py tests/test_kb.py
git commit -m "DNA: per-person store under .genea/dna and the dna extra

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Parse, QC and import

**Files:**
- Create: `src/genea/dna/parse.py`, `src/genea/dna/qc.py`, `src/genea/dna/api.py`
- Test: `tests/test_dna_parse.py`

**Interfaces:**
- Consumes: Task 1 `store.*`, fixtures `write_csv`, `rows_for`.
- Produces:
  - `parse.HEADER`, `parse.RawDna(rows, build, sex)`, `parse.check_header(path)`, `parse.read_myheritage(path) -> RawDna` (rsids lower-cased)
  - `qc.CALL_RATE_WARNING = 0.95`, `qc.compute_qc(rows) -> QC`
  - `api.ImportResult(meta, warnings)`, `api.import_file(kb, csv_path, gedcom_id, slug=None, replace=False) -> ImportResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dna_parse.py`:

```python
import sqlite3

import pytest
from fixtures.dna import rows_for, write_csv

from genea.dna import api, store
from genea.dna.parse import read_myheritage
from genea.dna.qc import compute_qc
from genea.dna.store import DnaError
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import init_kb

pytest.importorskip("snps")


@pytest.mark.parametrize("sex", ["male", "female"])
def test_reads_myheritage_and_infers_sex(tmp_path, sex):
    raw = read_myheritage(write_csv(tmp_path / "raw.csv", sex))
    assert raw.build == 37 and raw.sex == sex
    by_rsid = {r[0]: r for r in raw.rows}
    assert by_rsid["i999"] == ("i999", "MT", 16000, "AG")  # heteroplasmy kept, not collapsed
    assert len(by_rsid["rs5000"][3]) == 2  # X stays diploid as reported
    assert any(r[3] is None for r in raw.rows)  # "--" is a no-call
    assert ("rs8000" in by_rsid) == (sex == "male")


def test_refuses_other_files(tmp_path):
    other = tmp_path / "other.csv"
    other.write_text("# 23andMe\nrsid\tchromosome\tposition\tgenotype\nrs1\t1\t5\tAA\n", encoding="utf-8")
    with pytest.raises(DnaError, match="line 2"):
        read_myheritage(other)
    with pytest.raises(DnaError, match="build 37"):
        read_myheritage(write_csv(tmp_path / "b38.csv", build=38))
    with pytest.raises(DnaError, match="not found"):
        read_myheritage(tmp_path / "missing.csv")


def test_qc_counts_and_warns_on_low_call_rate():
    qc = compute_qc(rows_for("male"))
    assert qc.total == len(rows_for("male"))
    assert qc.by_chrom["Y"] == 16 and qc.by_chrom["MT"] == 11
    assert list(qc.by_chrom) == ["1", "MT", "X", "Y"]  # autosomes by number, then the rest by name
    low = compute_qc([("rs1", "1", 1, None), ("rs2", "1", 2, "AA")])
    assert low.call_rate == 0.5 and "Low call rate" in low.warnings[0]


def test_import_links_the_tree_person(analysis_kb, tmp_path):
    result = api.import_file(analysis_kb, write_csv(tmp_path / "MyHeritage_raw_dna_data.csv"), "tree.ged#I1")
    meta = result.meta
    assert (meta.slug, meta.gedcom_id, meta.tree_name, meta.tree_sex) == ("tree.ged-I1", "tree.ged#@I1@",
                                                                          "Karol Weiss", "M")
    assert meta.source_name == "MyHeritage_raw_dna_data.csv" and str(tmp_path) not in meta.model_dump_json()
    assert result.warnings == []
    assert store.genotypes(analysis_kb, "tree.ged-I1", ["rs8000"])["rs8000"][3] == "TT"


def test_import_warns_when_tree_sex_disagrees(analysis_kb, tmp_path):
    result = api.import_file(analysis_kb, write_csv(tmp_path / "f.csv", "female"), "tree.ged#@I1@")
    assert any("looks female" in w for w in result.warnings)


def test_import_refuses_unknown_tree_person(analysis_kb, tmp_path):
    with pytest.raises(DnaError, match="I999"):
        api.import_file(analysis_kb, write_csv(tmp_path / "m.csv"), "tree.ged#@I999@")


def test_import_without_index_warns(tmp_path):
    kb = init_kb(tmp_path / "kb")
    result = api.import_file(kb, write_csv(tmp_path / "m.csv"), "tree.ged#@I12@", slug="dad")
    assert result.meta.slug == "dad" and result.meta.tree_name is None
    assert any("not indexed" in w for w in result.warnings)


def test_reimport_needs_replace_before_parsing(analysis_kb, tmp_path, monkeypatch):
    csv = write_csv(tmp_path / "m.csv")
    api.import_file(analysis_kb, csv, "tree.ged#@I1@")
    monkeypatch.setattr(api, "read_myheritage", lambda path: pytest.fail("parsed before the --replace check"))
    with pytest.raises(DnaError, match="--replace"):
        api.import_file(analysis_kb, csv, "tree.ged#@I1@")


def test_dna_data_is_never_indexed(analysis_kb, tmp_path):
    api.import_file(analysis_kb, write_csv(tmp_path / "m.csv"), "tree.ged#@I1@")
    index_kb(analysis_kb, FakeEmbedder())
    conn = sqlite3.connect(analysis_kb.index_path)
    paths = [r[0] for r in conn.execute("SELECT path FROM files")]
    conn.close()
    assert paths and not [p for p in paths if p.startswith(".genea")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dna_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'genea.dna.parse'`.

- [ ] **Step 3: Implement**

Create `src/genea/dna/parse.py`:

```python
"""Read a MyHeritage raw DNA CSV (through snps) into rows; refuse anything else."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from genea.dna.store import DnaError, Row

HEADER = "RSID,CHROMOSOME,POSITION,RESULT"


@dataclass(frozen=True)
class RawDna:
    rows: list[Row]
    build: int
    sex: str  # "male", "female" or "unknown"


def check_header(path: Path) -> None:
    """The first line that is not a comment must be the MyHeritage header."""
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        for number, line in enumerate(f, 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.replace('"', "").replace(" ", "").upper() == HEADER:
                return
            raise DnaError(f"Not a MyHeritage raw DNA file: line {number} is {text[:80]!r}, "
                           f"expected the header {HEADER}.")
    raise DnaError(f"Not a MyHeritage raw DNA file: no {HEADER} header line.")


def read_myheritage(path: Path) -> RawDna:
    path = Path(path)
    if not path.is_file():
        raise DnaError(f"File not found: {path}")
    check_header(path)
    try:
        from snps import SNPs
    except ImportError as exc:
        raise DnaError("DNA import needs the optional extra: uv sync --extra dna") from exc
    # Keep X, Y and MT as reported: collapsing would hide heterozygous Y and heteroplasmic MT calls.
    data = SNPs(str(path), deduplicate_XY_chrom=False, deduplicate_MT_chrom=False)
    if data.source != "MyHeritage":
        raise DnaError(f"Not a MyHeritage raw DNA file (snps detected {data.source or 'nothing'}).")
    if data.build != 37:
        raise DnaError(f"The file uses genome build {data.build or 'unknown'}; genea needs build 37 (GRCh37) "
                       "and does not convert coordinates.")
    frame = data.snps
    rows: list[Row] = [(str(rsid).lower(), str(chrom), int(pos), g if isinstance(g, str) and g else None)
                       for rsid, chrom, pos, g in zip(frame.index, frame["chrom"], frame["pos"], frame["genotype"],
                                                      strict=True)]
    if not rows:
        raise DnaError("The file contains no SNPs.")
    return RawDna(rows, 37, {"Male": "male", "Female": "female"}.get(data.sex, "unknown"))
```

Create `src/genea/dna/qc.py`:

```python
"""Call rate and per-chromosome counts of an imported file."""

from __future__ import annotations

from collections import Counter

from genea.dna.store import QC, DnaError, Row

CALL_RATE_WARNING = 0.95


def _chrom_order(item: tuple[str, int]) -> tuple[int, int | str]:
    chrom = item[0]
    return (0, int(chrom)) if chrom.isdigit() else (1, chrom)


def compute_qc(rows: list[Row]) -> QC:
    if not rows:
        raise DnaError("The file contains no SNPs.")
    by_chrom = Counter(r[1] for r in rows if r[3])
    called = sum(by_chrom.values())
    rate = called / len(rows)
    warnings = []
    if rate < CALL_RATE_WARNING:
        warnings.append(f"Low call rate {rate:.1%} (below {CALL_RATE_WARNING:.0%}); results may be unreliable.")
    return QC(total=len(rows), called=called, call_rate=round(rate, 4),
              by_chrom=dict(sorted(by_chrom.items(), key=_chrom_order)), warnings=warnings)
```

Create `src/genea/dna/api.py`:

```python
"""DNA operations for the CLI: import a raw file and analyse haplogroups. Tools never call these."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from genea.dna.parse import read_myheritage
from genea.dna.qc import compute_qc
from genea.dna.store import (DnaError, PersonMeta, check_slug, normalize_gedcom_id, now, person_dir, save_person,
                             slug_for)
from genea.kb import KnowledgeBase


@dataclass
class ImportResult:
    meta: PersonMeta
    warnings: list[str]


def _tree_person(kb: KnowledgeBase, gedcom_id: str) -> tuple[str | None, str | None] | None:
    """(name, sex) of the person in the index; None when the knowledge base is not indexed."""
    if not kb.index_path.exists():
        return None
    ged, xref = gedcom_id.split("#", 1)
    conn = sqlite3.connect(f"{kb.index_path.as_uri()}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT given, surname, sex FROM persons WHERE ged_file = ? AND xref = ?",
                           (ged, xref)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise DnaError(f"No person {gedcom_id} in the indexed tree; check the id with find_person.")
    return " ".join(p for p in row[:2] if p) or None, row[2]


def import_file(kb: KnowledgeBase, csv_path: Path, gedcom_id: str, slug: str | None = None,
                replace: bool = False) -> ImportResult:
    gedcom_id = normalize_gedcom_id(gedcom_id)
    slug = slug or slug_for(gedcom_id)
    check_slug(slug)
    if person_dir(kb, slug).exists() and not replace:  # before parsing: a real file takes a while to read
        raise DnaError(f"{slug} is already imported; add --replace to import it again.")
    tree = _tree_person(kb, gedcom_id)
    raw = read_myheritage(Path(csv_path))
    qc = compute_qc(raw.rows)
    warnings = list(qc.warnings)
    name = tree_sex = None
    if tree is None:
        warnings.append("The knowledge base is not indexed, so the tree id was not checked; run `genea index` "
                        "and import again with --replace to link the name.")
    else:
        name, tree_sex = tree
        dna_sex = {"male": "M", "female": "F"}.get(raw.sex)
        if dna_sex and tree_sex in ("M", "F") and tree_sex != dna_sex:
            warnings.append(f"The tree records {name or gedcom_id} as {tree_sex}, but the DNA looks {raw.sex}; "
                            "is the file linked to the right person?")
    meta = PersonMeta(slug=slug, gedcom_id=gedcom_id, tree_name=name, tree_sex=tree_sex,
                      source_name=Path(csv_path).name, build=raw.build, sex=raw.sex, qc=qc, imported_at=now())
    save_person(kb, meta, raw.rows, replace)
    return ImportResult(meta, warnings)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dna_parse.py tests/test_dna_store.py -v`
Expected: PASS. If `test_reads_myheritage_and_infers_sex[female]` reports `unknown`, print `SNPs(...).sex` for the fixture and raise the share of heterozygous X rows in `rows_for` (snps needs X heterozygosity above 3%); do not weaken the assertion.

- [ ] **Step 5: Commit**

```bash
git add src/genea/dna/parse.py src/genea/dna/qc.py src/genea/dna/api.py tests/test_dna_parse.py
git commit -m "DNA: read MyHeritage raw files, QC and import linked to the tree

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Tool runtime — Java, Haplogrep and `setup`

**Files:**
- Create: `src/genea/dna/runtime.py`
- Test: `tests/test_dna_runtime.py`

**Interfaces:**
- Consumes: `config.global_dir`, `store.DnaError`.
- Produces: constants `HAPLOGREP_VERSION`, `HAPLOGREP_TREE`, `HAPLOGREP_URL`, `HAPLOGREP_SHA256`, `JRE_RELEASE`, `JRE_URL`, `JRE_SHA256`, `JAVA_MIN`; `tools_dir()`, `haplogrep_dir()`, `haplogrep_exe()`, `rcrs_fasta()`, `portable_java()`; `java_major(java, run) -> int | None`; `system_java() -> str | None`; `JavaChoice(path, kind, detail)`; `find_java(run=subprocess.run) -> JavaChoice`; `yhaplo_version() -> str | None`; `download_verified(url, sha256, dest, fetch_fn)`; `install_jre(fetch_fn)`; `install_haplogrep(java, fetch_fn, run)`; `setup(fetch_fn=fetch, run=subprocess.run, platform=sys.platform) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dna_runtime.py`:

```python
import hashlib
import io
import subprocess
import tarfile
import zipfile

import pytest

from genea.dna import runtime
from genea.dna.store import DnaError


def _done(cmd, out="", err="", code=0):
    return subprocess.CompletedProcess(cmd, code, out, err)


@pytest.mark.parametrize(("text", "major"), [('java version "1.8.0_503"', 8),
                                              ('openjdk version "21.0.12.1" 2026-08-18 LTS', 21),
                                              ('openjdk version "11" 2018-09-25', 11), ("garbage", None)])
def test_java_major(text, major):
    assert runtime.java_major("java", lambda cmd, **kw: _done(cmd, err=text)) == major


def test_java_major_missing_binary():
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert runtime.java_major("nope", run) is None


def test_find_java_prefers_new_system_java(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/jdk17/bin/java.exe")
    choice = runtime.find_java(lambda cmd, **kw: _done(cmd, err='openjdk version "17.0.1"'))
    assert (choice.path, choice.kind) == ("C:/jdk17/bin/java.exe", "system")


def test_find_java_falls_back_to_portable(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/java8/bin/java.exe")
    portable = runtime.portable_java()
    portable.parent.mkdir(parents=True)
    portable.write_bytes(b"")

    def run(cmd, **kw):
        return _done(cmd, err='java version "1.8.0_503"' if "java8" in cmd[0] else 'openjdk version "21.0.12.1"')
    choice = runtime.find_java(run)
    assert (choice.path, choice.kind) == (str(portable), "portable")


def test_find_java_reports_old_java(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/java8/bin/java.exe")
    choice = runtime.find_java(lambda cmd, **kw: _done(cmd, err='java version "1.8.0_503"'))
    assert choice.path is None and "Java 8" in choice.detail and "11+" in choice.detail


def _fake_archives(monkeypatch):
    """A Haplogrep tar.gz and a JRE zip with the layout of the real ones; their checksums replace the pinned ones."""
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
        for name, data in (("haplogrep3.exe", b"jar"), ("haplogrep3.yaml", b"port: 7000\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as z:
        z.writestr(f"{runtime.JRE_RELEASE}-jre/bin/java.exe", b"exe")
    blobs = {runtime.HAPLOGREP_URL: tar_bytes.getvalue(), runtime.JRE_URL: zip_bytes.getvalue()}
    monkeypatch.setattr(runtime, "HAPLOGREP_SHA256", hashlib.sha256(blobs[runtime.HAPLOGREP_URL]).hexdigest())
    monkeypatch.setattr(runtime, "JRE_SHA256", hashlib.sha256(blobs[runtime.JRE_URL]).hexdigest())
    fetched = []

    def fetch(url, dest):
        fetched.append(url)
        dest.write_bytes(blobs[url])
    return fetch, fetched


def _tool_run(cmd, **kw):
    """java -version answers 21 for the portable JRE and 8 otherwise; java -jar downloads the trees."""
    if cmd[1] == "-version":
        return _done(cmd, err='openjdk version "21.0.12.1"' if "dna-tools" in cmd[0] else 'java version "1.8.0"')
    runtime.rcrs_fasta().parent.mkdir(parents=True, exist_ok=True)
    runtime.rcrs_fasta().write_text(">rCRS\nGATC\n", encoding="ascii")
    return _done(cmd, out="Tree phylotree-fu-rcrs@1.3 installed.")


def test_setup_installs_portable_jre_and_haplogrep(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/java8/bin/java.exe")
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: "2.1.19")
    fetch, fetched = _fake_archives(monkeypatch)
    messages = runtime.setup(fetch, _tool_run, platform="win32")
    assert fetched == [runtime.JRE_URL, runtime.HAPLOGREP_URL]
    assert runtime.portable_java().is_file() and runtime.haplogrep_exe().is_file() and runtime.rcrs_fasta().is_file()
    assert any("portable" in m for m in messages) and any("yhaplo 2.1.19" in m for m in messages)
    assert not list(runtime.tools_dir().glob("*.part")) and not list(runtime.tools_dir().glob("*.zip"))
    assert runtime.setup(fetch, _tool_run, platform="win32")  # second run downloads nothing
    assert fetched == [runtime.JRE_URL, runtime.HAPLOGREP_URL]


def test_checksum_mismatch_installs_nothing(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: None)
    fetch, _ = _fake_archives(monkeypatch)
    monkeypatch.setattr(runtime, "JRE_SHA256", "0" * 64)
    with pytest.raises(DnaError, match="Checksum mismatch"):
        runtime.setup(fetch, _tool_run, platform="win32")
    assert not (runtime.tools_dir() / "jre").exists()
    assert not list(runtime.tools_dir().glob("jre*"))


def test_setup_needs_windows():
    with pytest.raises(DnaError, match="Java 11"):
        runtime.setup(platform="linux")


def test_tools_dir_follows_genea_home(isolated_home):
    assert runtime.tools_dir() == isolated_home / "dna-tools"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dna_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'genea.dna.runtime'`.

- [ ] **Step 3: Implement**

Create `src/genea/dna/runtime.py`:

```python
"""The DNA tools under ~/.genea/dna-tools (shared by every knowledge base) and `genea dna setup`.

Haplogrep 3 needs Java 11+. A suitable system Java is used as is; otherwise setup unpacks a portable
Temurin JRE next to Haplogrep. The system Java, PATH and JAVA_HOME are never changed.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from genea.config import global_dir
from genea.dna.store import DnaError

HAPLOGREP_VERSION = "3.3.2"
HAPLOGREP_TREE = "phylotree-fu-rcrs@1.3"
HAPLOGREP_URL = "https://github.com/genepi/haplogrep3/releases/download/v3.3.2/haplogrep3-3.3.2-windows.tar.gz"
HAPLOGREP_SHA256 = "97a808461cdf8ce21d0d00c9a29f8500b29345fae8d22ec99c69d6853033c5f4"
JRE_RELEASE = "jdk-21.0.12.1+1"
JRE_URL = ("https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/"
           "OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip")
JRE_SHA256 = "d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636"
JAVA_MIN = 11

Fetch = Callable[[str, Path], None]
Run = Callable[..., subprocess.CompletedProcess]

_VERSION = re.compile(r'version "(\d+)(?:\.(\d+))?')


def tools_dir() -> Path:
    return global_dir() / "dna-tools"


def haplogrep_dir() -> Path:
    return tools_dir() / "haplogrep3"


def haplogrep_exe() -> Path:
    return haplogrep_dir() / "haplogrep3.exe"


def rcrs_fasta() -> Path:
    """The rCRS reference that comes with Haplogrep's tree; downloaded on Haplogrep's first start."""
    return haplogrep_dir() / "trees" / "phylotree-fu-rcrs" / "1.3" / "rcrs.fasta"


def portable_java() -> Path:
    return tools_dir() / "jre" / f"{JRE_RELEASE}-jre" / "bin" / "java.exe"


def java_major(java: str, run: Run = subprocess.run) -> int | None:
    """8 for 'java version "1.8.0_503"', 21 for 'openjdk version "21.0.12.1"'; None if it does not run."""
    try:
        done = run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search((done.stderr or "") + (done.stdout or ""))
    if not match:
        return None
    first = int(match[1])
    return int(match[2]) if first == 1 and match[2] else first


def system_java() -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home:
        for name in ("java.exe", "java"):
            candidate = Path(home) / "bin" / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("java")


@dataclass(frozen=True)
class JavaChoice:
    path: str | None
    kind: Literal["system", "portable", "none"]
    detail: str


def find_java(run: Run = subprocess.run) -> JavaChoice:
    found = []
    system = system_java()
    if system:
        major = java_major(system, run)
        if major is not None and major >= JAVA_MIN:
            return JavaChoice(system, "system", f"system Java {major} ({system})")
        found.append(f"Java {major} at {system}" if major else f"a Java that does not run at {system}")
    portable = portable_java()
    if portable.is_file():
        major = java_major(str(portable), run)
        if major is not None and major >= JAVA_MIN:
            return JavaChoice(str(portable), "portable", f"portable Java {major} ({portable})")
        found.append(f"a portable Java that does not run at {portable}")
    suffix = f" (found {'; '.join(found)})" if found else ""
    return JavaChoice(None, "none", f"no Java {JAVA_MIN}+ for Haplogrep{suffix}")


def yhaplo_version() -> str | None:
    try:
        return importlib.metadata.version("yhaplo")
    except importlib.metadata.PackageNotFoundError:
        return None


def fetch(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with dest.open("wb") as f:
            for chunk in response.iter_bytes(1 << 20):
                f.write(chunk)


def download_verified(url: str, sha256: str, dest: Path, fetch_fn: Fetch = fetch) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        fetch_fn(url, part)
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
    except (httpx.HTTPError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise DnaError(f"Download failed: {url} ({exc})") from exc
    if digest != sha256:
        part.unlink(missing_ok=True)
        raise DnaError(f"Checksum mismatch for {url}: expected {sha256}, got {digest}. Nothing was installed.")
    os.replace(part, dest)


def _unpack(archive: Path, final: Path, extract: Callable[[Path], None]) -> None:
    staging = final.with_name(final.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        extract(staging)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise DnaError(f"Could not unpack {archive.name}: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
    shutil.rmtree(final, ignore_errors=True)
    staging.rename(final)


def install_jre(fetch_fn: Fetch = fetch) -> None:
    archive = tools_dir() / "jre.zip"
    download_verified(JRE_URL, JRE_SHA256, archive, fetch_fn)

    def extract(target: Path) -> None:
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    _unpack(archive, tools_dir() / "jre", extract)
    if not portable_java().is_file():
        raise DnaError(f"The JRE archive did not contain {portable_java()}.")


def install_haplogrep(java: str, fetch_fn: Fetch = fetch, run: Run = subprocess.run) -> None:
    if not haplogrep_exe().is_file():
        archive = tools_dir() / "haplogrep3.tar.gz"
        download_verified(HAPLOGREP_URL, HAPLOGREP_SHA256, archive, fetch_fn)

        def extract(target: Path) -> None:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(target, filter="data")
        _unpack(archive, haplogrep_dir(), extract)
        if not haplogrep_exe().is_file():
            raise DnaError(f"{haplogrep_exe()} is missing after unpacking; an antivirus may have removed it.")
    if not rcrs_fasta().is_file():
        # Haplogrep downloads its phylotrees (about 60 MB) next to itself on its first start.
        done = run([java, "-jar", str(haplogrep_exe())], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=1800, cwd=haplogrep_dir())
        if not rcrs_fasta().is_file():
            tail = ((done.stderr or "") + (done.stdout or "")).strip()[-500:]
            raise DnaError(f"Haplogrep did not download its trees: {tail or f'exit code {done.returncode}'}")


def setup(fetch_fn: Fetch = fetch, run: Run = subprocess.run, platform: str = sys.platform) -> list[str]:
    if platform != "win32":
        raise DnaError(f"`genea dna setup` installs the Windows builds only. Elsewhere install Java {JAVA_MIN}+ "
                       f"and unpack Haplogrep {HAPLOGREP_VERSION} into {haplogrep_dir()}.")
    messages = []
    java = find_java(run)
    if java.path is None:
        messages.append(f"{java.detail}; downloading a portable Java ({JRE_RELEASE}, about 50 MB) into "
                        f"{tools_dir()}. Your system Java is not changed.")
        install_jre(fetch_fn)
        java = find_java(run)
        if java.path is None:
            raise DnaError(f"The portable Java does not run: {java.detail}")
    messages.append(f"Java: {java.detail}")
    install_haplogrep(java.path, fetch_fn, run)
    messages.append(f"Haplogrep {HAPLOGREP_VERSION} with {HAPLOGREP_TREE}: {haplogrep_dir()}")
    version = yhaplo_version()
    messages.append(f"yhaplo {version}" if version else "yhaplo is missing: run `uv sync --extra dna`.")
    return messages
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dna_runtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/genea/dna/runtime.py tests/test_dna_runtime.py
git commit -m "DNA: find Java, install Haplogrep and a portable JRE with pinned checksums

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Haplogroups — inputs, tool calls and `analyze`

**Files:**
- Create: `src/genea/dna/haplo.py`
- Modify: `src/genea/dna/api.py` (add `analyze`)
- Test: `tests/test_dna_haplo.py`

**Interfaces:**
- Consumes: `runtime.find_java`, `runtime.haplogrep_exe`, `runtime.haplogrep_dir`, `runtime.rcrs_fasta`, `runtime.yhaplo_version`, `runtime.HAPLOGREP_*`; `store.*`; fixtures `add_person`, `rows_for`, `Y_R1B`, `MT_POSITIONS`.
- Produces: `haplo.haploid(genotype) -> str | None`, `haplo.read_fasta(path) -> str`, `haplo.mt_vcf(rows, rcrs) -> tuple[str, int, int, int]` (text, used, skipped, agreeing), `haplo.y_genos(rows) -> tuple[str, int, int]`, `haplo.parse_haplogrep(text) -> tuple[str, float]`, `haplo.parse_yhaplo(text) -> tuple[str, str]`, `haplo.call_mtdna(rows, work, run=subprocess.run) -> HaploCall`, `haplo.call_y(sex, rows, work, run=subprocess.run) -> HaploCall`, `haplo.RCRS_MIN_AGREEMENT = 0.75`; `api.analyze(kb, person, run=subprocess.run) -> Haplogroups`.

A missing tool skips only its own line (with the fix in `skipped_reason`); a tool that runs and fails raises `DnaError` and nothing is written.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dna_haplo.py`:

```python
import subprocess
from pathlib import Path

import pytest
from fixtures.dna import MT_POSITIONS, Y_R1B, add_person, rows_for

from genea.dna import api, haplo, runtime, store
from genea.dna.store import DnaError
from genea.kb import init_kb

RCRS = "ACGT" * 4143  # 16572 bases; position p has RCRS[p - 1]


def mt(pos, base):
    return (f"i{pos}", "MT", pos, base * 2)


def test_haploid():
    assert [haplo.haploid(g) for g in ["AA", "A", "AG", None, "--", "II", "NN"]] == \
        ["A", "A", None, None, None, None, None]


def test_mt_vcf_marks_variants_and_skips_heteroplasmy():
    rows = [mt(1, "A"), mt(2, "T"), ("i3", "MT", 3, "GT"), ("i4", "MT", 4, None), mt(1, "A"), mt(20000, "A")]
    text, used, skipped, agree = haplo.mt_vcf(rows, RCRS)
    assert (used, skipped, agree) == (2, 4, 1)
    body = text.splitlines()[2:]
    assert body == ["MT\t1\t.\tA\t.\t.\tPASS\t.\tGT\t0", "MT\t2\t.\tC\tT\t.\tPASS\t.\tGT\t1"]


def test_y_genos_is_sample_major_text():
    text, used, skipped = haplo.y_genos([("a", "Y", 20, "CC"), ("b", "Y", 10, "A"), ("c", "Y", 30, "AG")])
    assert text == "ID\t10\t20\nSAMPLE\tA\tC\n" and (used, skipped) == (2, 1)
    assert haplo.y_genos([("c", "Y", 30, "AG")]) == ("", 0, 1)


def test_parsers():
    hg = '"SampleID"\t"Haplogroup"\t"Rank"\t"Quality"\t"Range"\n"SAMPLE"\t"J1b1a"\t"1"\t"0.9312"\t"70 93"\n'
    assert haplo.parse_haplogrep(hg) == ("J1b1a", 0.9312)
    assert haplo.parse_yhaplo("SAMPLE       R-P312          R-P312          R1b1a2a1a2\n") == ("R-P312", "R1b1a2a1a2")
    with pytest.raises(DnaError):
        haplo.parse_yhaplo("")


@pytest.fixture
def tools_installed(monkeypatch, tmp_path):
    """Pretend Java, Haplogrep (with an rCRS of RCRS) and yhaplo are installed."""
    exe = runtime.haplogrep_exe()
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"jar")
    runtime.rcrs_fasta().parent.mkdir(parents=True)
    runtime.rcrs_fasta().write_text(">rCRS\n" + RCRS + "\n", encoding="ascii")
    monkeypatch.setattr(runtime, "find_java", lambda run=None: runtime.JavaChoice("java", "system", "system Java 21"))
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: "2.1.19")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "classify" in cmd:
            vcf = Path(cmd[cmd.index("--in") + 1]).read_text(encoding="ascii")
            assert "--chip" in cmd and runtime.HAPLOGREP_TREE in cmd and "\tGT\t" in vcf
            Path(cmd[cmd.index("--out") + 1]).write_text(
                '"SampleID"\t"Haplogroup"\t"Rank"\t"Quality"\n"SAMPLE"\t"H1"\t"1"\t"0.85"\n', encoding="utf-8")
        else:
            genos = Path(cmd[cmd.index("-i") + 1])
            assert genos.name == "y.genos.txt"
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir()
            (out / "haplogroups.y.txt").write_text("SAMPLE R-P312 R-P312 R1b1a2a1a2\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run, calls


def _matching_mt_rows():
    return [mt(p, RCRS[p - 1]) for p in MT_POSITIONS] + [("i999", "MT", 16000, "AG")]


def test_analyze_male_writes_both_lines(tmp_path, tools_installed):
    run, calls = tools_installed
    kb = init_kb(tmp_path / "kb")
    rows = [r for r in rows_for("male") if r[1] != "MT"] + _matching_mt_rows()
    add_person(kb, rows=rows)
    result = api.analyze(kb, "tree.ged-I12", run)
    mtdna, y = result.calls
    assert (mtdna.line, mtdna.haplogroup, mtdna.quality, mtdna.markers_used, mtdna.markers_skipped) == \
        ("mtDNA", "H1", 0.85, 10, 1)
    assert (y.line, y.haplogroup, y.detail, y.markers_used) == ("Y", "R-P312", "R1b1a2a1a2", len(Y_R1B))
    assert "Haplogrep 3.3.2" in mtdna.tool and "yhaplo 2.1.19" in y.tool
    assert store.load_haplogroups(kb, "tree.ged-I12") == result
    assert len(calls) == 2


def test_female_skips_y_without_running_yhaplo(tmp_path, tools_installed):
    run, calls = tools_installed
    kb = init_kb(tmp_path / "kb")
    rows = [r for r in rows_for("female") if r[1] != "MT"] + _matching_mt_rows()
    add_person(kb, sex="female", rows=rows)
    mtdna, y = api.analyze(kb, "tree.ged-I12", run).calls
    assert mtdna.haplogroup == "H1"
    assert y.haplogroup is None and "female" in y.skipped_reason
    assert all("classify" in c for c in calls)


def test_mt_not_in_rcrs_coordinates_is_skipped_but_y_runs(tmp_path, tools_installed):
    run, _ = tools_installed
    kb = init_kb(tmp_path / "kb")
    shifted = [mt(p, RCRS[p]) for p in MT_POSITIONS]  # every base belongs to the next position
    add_person(kb, rows=[("rs8000", "Y", Y_R1B[0][0], "TT")] + shifted)
    mtdna, y = api.analyze(kb, "tree.ged-I12", run).calls
    assert mtdna.haplogroup is None and "rCRS" in mtdna.skipped_reason
    assert y.haplogroup == "R-P312"


def test_missing_java_skips_mtdna_with_the_fix(tmp_path, tools_installed, monkeypatch):
    run, _ = tools_installed
    monkeypatch.setattr(runtime, "find_java", lambda run=None: runtime.JavaChoice(None, "none", "no Java 11+"))
    kb = init_kb(tmp_path / "kb")
    add_person(kb)
    mtdna, y = api.analyze(kb, "tree.ged-I12", run).calls
    assert "genea dna setup" in mtdna.skipped_reason and y.haplogroup == "R-P312"


def test_missing_yhaplo_skips_y_with_the_fix(tmp_path, tools_installed, monkeypatch):
    run, _ = tools_installed
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: None)
    kb = init_kb(tmp_path / "kb")
    add_person(kb)
    y = api.analyze(kb, "tree.ged-I12", run).calls[1]
    assert "uv sync --extra dna" in y.skipped_reason and y.markers_used == len(Y_R1B)


def test_failing_tool_writes_nothing(tmp_path, tools_installed):
    kb = init_kb(tmp_path / "kb")
    add_person(kb, rows=_matching_mt_rows())

    def broken(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "Exception in thread main")
    with pytest.raises(DnaError, match="Haplogrep failed: Exception in thread main"):
        api.analyze(kb, "tree.ged-I12", broken)
    assert store.load_haplogroups(kb, "tree.ged-I12") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dna_haplo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'genea.dna.haplo'`.

- [ ] **Step 3: Implement**

Create `src/genea/dna/haplo.py`:

```python
"""mtDNA (Haplogrep 3) and Y (yhaplo) haplogroups from a person's MT and Y rows.

Only clean haploid calls are used: no-calls, heterozygous Y and heteroplasmic MT calls are skipped and counted.
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from pathlib import Path

from genea.dna import runtime
from genea.dna.store import DnaError, HaploCall, Row

RCRS_MIN_AGREEMENT = 0.75  # real mtDNA matches rCRS at most chip positions; shifted coordinates match ~25%
YHAPLO_TREE = "ISOGG 2016.01.04"


def haploid(genotype: str | None) -> str | None:
    """'AA' or 'A' -> 'A'; a no-call, 'AG' or anything but one of A/C/G/T -> None."""
    if not genotype or len(set(genotype)) != 1 or genotype[0] not in "ACGT":
        return None
    return genotype[0]


def read_fasta(path: Path) -> str:
    lines = path.read_text(encoding="ascii").splitlines()
    return "".join(line.strip() for line in lines if not line.startswith(">")).upper()


def mt_vcf(rows: list[Row], rcrs: str) -> tuple[str, int, int, int]:
    """A single-sample chip VCF against rCRS: (text, positions used, rows skipped, positions equal to rCRS)."""
    lines = ["##fileformat=VCFv4.2", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE"]
    used = skipped = agree = 0
    seen: set[int] = set()
    for _rsid, _chrom, pos, genotype in sorted(rows, key=lambda r: r[2]):
        base = haploid(genotype)
        ref = rcrs[pos - 1] if 1 <= pos <= len(rcrs) else "N"
        if base is None or ref not in ("A", "C", "G", "T") or pos in seen:
            skipped += 1
            continue
        seen.add(pos)
        used += 1
        if base == ref:
            agree += 1
            lines.append(f"MT\t{pos}\t.\t{ref}\t.\t.\tPASS\t.\tGT\t0")
        else:
            lines.append(f"MT\t{pos}\t.\t{ref}\t{base}\t.\tPASS\t.\tGT\t1")
    return "\n".join(lines) + "\n", used, skipped, agree


def y_genos(rows: list[Row]) -> tuple[str, int, int]:
    """yhaplo's sample-major text (row 0: GRCh37 positions): (text, positions used, rows skipped)."""
    positions, alleles, skipped, seen = [], [], 0, set()
    for _rsid, _chrom, pos, genotype in sorted(rows, key=lambda r: r[2]):
        base = haploid(genotype)
        if base is None or pos in seen:
            skipped += 1
            continue
        seen.add(pos)
        positions.append(str(pos))
        alleles.append(base)
    if not positions:
        return "", 0, skipped
    return "ID\t" + "\t".join(positions) + "\nSAMPLE\t" + "\t".join(alleles) + "\n", len(positions), skipped


def parse_haplogrep(text: str) -> tuple[str, float]:
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    if not rows or not rows[0].get("Haplogroup"):
        raise DnaError("Haplogrep wrote no haplogroup.")
    return rows[0]["Haplogroup"], float(rows[0]["Quality"])


def parse_yhaplo(text: str) -> tuple[str, str]:
    """yhaplo's line 'SAMPLE  R-P312  R-P312  R1b1a2a1a2' -> ('R-P312', 'R1b1a2a1a2')."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            return parts[2], parts[3]
    raise DnaError("yhaplo wrote no haplogroup.")


def _tail(done: subprocess.CompletedProcess) -> str:
    text = ((done.stderr or "") + (done.stdout or "")).strip()
    return text[-500:] or f"exit code {done.returncode}"


def call_mtdna(rows: list[Row], work: Path, run=subprocess.run) -> HaploCall:
    tool = f"Haplogrep {runtime.HAPLOGREP_VERSION} ({runtime.HAPLOGREP_TREE})"
    java = runtime.find_java()
    if java.path is None:
        return HaploCall(line="mtDNA", skipped_reason=f"{java.detail}; run `genea dna setup`.", tool=tool)
    if not (runtime.haplogrep_exe().is_file() and runtime.rcrs_fasta().is_file()):
        return HaploCall(line="mtDNA", skipped_reason="Haplogrep is not installed; run `genea dna setup`.", tool=tool)
    text, used, skipped, agree = mt_vcf(rows, read_fasta(runtime.rcrs_fasta()))
    if used == 0:
        return HaploCall(line="mtDNA", markers_skipped=skipped, skipped_reason="The file has no usable mtDNA calls.",
                         tool=tool)
    if agree / used < RCRS_MIN_AGREEMENT:
        return HaploCall(line="mtDNA", markers_used=used, markers_skipped=skipped, tool=tool,
                         skipped_reason=f"Only {agree / used:.0%} of the mtDNA calls match the rCRS reference, so "
                                        "the file's mtDNA positions do not look like rCRS coordinates; no "
                                        "haplogroup was called.")
    vcf, out = work / "mt.vcf", work / "mt.txt"
    vcf.write_text(text, encoding="ascii")
    cmd = [java.path, "-jar", str(runtime.haplogrep_exe()), "classify", "--in", str(vcf),
           "--tree", runtime.HAPLOGREP_TREE, "--chip", "--out", str(out)]
    done = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
               cwd=runtime.haplogrep_dir())
    if done.returncode != 0 or not out.is_file():
        raise DnaError(f"Haplogrep failed: {_tail(done)}")
    haplogroup, quality = parse_haplogrep(out.read_text(encoding="utf-8"))
    return HaploCall(line="mtDNA", haplogroup=haplogroup, quality=quality, markers_used=used,
                     markers_skipped=skipped, tool=tool)


def call_y(sex: str, rows: list[Row], work: Path, run=subprocess.run) -> HaploCall:
    version = runtime.yhaplo_version()
    tool = f"yhaplo {version} ({YHAPLO_TREE})" if version else f"yhaplo ({YHAPLO_TREE})"
    if sex == "female":
        return HaploCall(line="Y", skipped_reason="The DNA looks female: there is no Y chromosome to classify.",
                         tool=tool)
    text, used, skipped = y_genos(rows)
    if used == 0:
        return HaploCall(line="Y", markers_skipped=skipped, skipped_reason="The file has no usable Y calls.",
                         tool=tool)
    if version is None:
        return HaploCall(line="Y", markers_used=used, markers_skipped=skipped, tool=tool,
                         skipped_reason="yhaplo is not installed; run `uv sync --extra dna`.")
    genos, out_dir = work / "y.genos.txt", work / "yhaplo"
    genos.write_text(text, encoding="ascii")
    cmd = [sys.executable, "-c", "from yhaplo.cli.yhaplo import main; main()", "-i", str(genos), "-o", str(out_dir)]
    done = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    result = out_dir / "haplogroups.y.txt"
    if done.returncode != 0 or not result.is_file():
        raise DnaError(f"yhaplo failed: {_tail(done)}")
    haplogroup, isogg = parse_yhaplo(result.read_text(encoding="utf-8"))
    return HaploCall(line="Y", haplogroup=haplogroup, detail=isogg, markers_used=used, markers_skipped=skipped,
                     tool=tool)
```

Append to `src/genea/dna/api.py` (add `import subprocess`, `import tempfile` and `from genea.dna import haplo`, and `Haplogroups, chrom_rows, resolve_person, save_haplogroups` to the store import):

```python
def analyze(kb: KnowledgeBase, person: str, run=subprocess.run) -> Haplogroups:
    """Call the mtDNA and Y haplogroups and save them; a tool that fails leaves the old result untouched."""
    meta = resolve_person(kb, person)
    rows = chrom_rows(kb, meta.slug, ("MT", "Y"))
    with tempfile.TemporaryDirectory(prefix="genea-dna-") as tmp:
        work = Path(tmp)
        calls = [haplo.call_mtdna([r for r in rows if r[1] == "MT"], work, run),
                 haplo.call_y(meta.sex, [r for r in rows if r[1] == "Y"], work, run)]
    result = Haplogroups(analyzed_at=now(), calls=calls)
    save_haplogroups(kb, meta.slug, result)
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dna_haplo.py tests/test_dna_parse.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/genea/dna/haplo.py src/genea/dna/api.py tests/test_dna_haplo.py
git commit -m "DNA: mtDNA and Y haplogroups through Haplogrep and yhaplo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Read-only tool layer, privacy limits and DNA citations

**Files:**
- Create: `src/genea/dna/limits.py`, `src/genea/dna/tools.py`
- Modify: `src/genea/tool_runner.py`, `src/genea/analysis/api.py:211-213`
- Test: `tests/test_dna_tools.py`, `tests/test_analysis_proposals.py`

**Interfaces:**
- Consumes: Task 1 `store.*`, `SourceRef`, `ToolResult`, `ToolError`.
- Produces:
  - `limits.MAX_RSIDS_PER_CALL = 20`, `limits.RsidBudget(limit)` with `.left`, `.take(n)`
  - `tools.CAVEAT`, `tools.normalize_rsids(rsids) -> list[str]`, `tools.dna_summary(kb, person) -> DnaSummary`, `tools.haplogroups(kb, person) -> HaplogroupResult`, `tools.snp_lookup(kb, budget, person, rsids) -> SnpLookupResult`
  - `tool_runner.render_limited(result, limit)`, `tool_runner.error_limited(message, limit)`, `tool_runner.run_kb_tool(kb, fn, *args, **kwargs) -> ToolOutput`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dna_tools.py`:

```python
import json

import pytest
from fixtures.dna import add_person

from genea.dna import store, tools
from genea.dna.limits import RsidBudget
from genea.dna.store import HaploCall, Haplogroups
from genea.kb import init_kb
from genea.tool_runner import run_kb_tool
from genea.tools.base import ToolError


@pytest.fixture
def kb(tmp_path):
    kb = init_kb(tmp_path / "kb")
    add_person(kb)
    return kb


def test_summary_before_analysis_names_the_command(kb):
    out = json.loads(run_kb_tool(kb, tools.dna_summary, "tree.ged#@I12@").text)
    assert out["person"] == "tree.ged-I12" and out["sex"] == "male"
    assert "genea dna analyze tree.ged-I12" in out["haplogroups"]
    assert out["sources"] == [{"file": "dna/tree.ged-I12/meta.json"}]
    assert "MyHeritage_raw_dna_data.csv" not in json.dumps(out)


def test_haplogroups_with_caveat_and_citation(kb):
    with pytest.raises(ToolError, match="genea dna analyze"):
        tools.haplogroups(kb, "tree.ged-I12")
    store.save_haplogroups(kb, "tree.ged-I12", Haplogroups(analyzed_at="2026-10-15T11:00:00", calls=[
        HaploCall(line="Y", haplogroup="R-P312", detail="R1b1a2a1a2", markers_used=16, tool="yhaplo")]))
    result = tools.haplogroups(kb, "tree.ged-I12")
    assert result.calls[0].haplogroup == "R-P312" and "paternal" in result.caveat
    assert result.sources[0].cite() == "[dna/tree.ged-I12/haplogroups.json]"


def test_snp_lookup_answers_found_missing_and_no_call(kb):
    budget = RsidBudget(200)
    result = tools.snp_lookup(kb, budget, "tree.ged-I12", ["RS8000", "rs404", "rs1003"])
    by_id = {r.rsid: r for r in result.results}
    assert by_id["rs8000"].genotype == "TT" and by_id["rs8000"].chrom == "Y"
    assert "not on this chip" in by_id["rs404"].note
    assert result.lookups_left == 197
    assert result.sources[0].cite() == "[dna/tree.ged-I12/genotypes.sqlite]"


def test_rsids_are_normalised_and_charged_once():
    assert tools.normalize_rsids(["rs1, RS2;rs1", "rs2"]) == ["rs1", "rs2"]
    assert tools.normalize_rsids("i3000002") == ["i3000002"]


def test_invalid_rsids_cost_nothing(kb):
    budget = RsidBudget(200)
    with pytest.raises(ToolError, match="Not rsIDs: 4988235"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", ["rs1", "4988235"])
    with pytest.raises(ToolError, match="1-20"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", [])
    assert budget.left == 200


def test_more_than_20_is_rejected_not_truncated(kb):
    budget = RsidBudget(200)
    with pytest.raises(ToolError, match="At most 20 rsIDs per call \\(21 given\\)"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{i}" for i in range(21)])
    assert budget.left == 200


def test_session_budget_stops_paging(kb):
    budget = RsidBudget(30)
    tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{1000 + i}" for i in range(20)])
    with pytest.raises(ToolError, match="Privacy limit.*30.*10 are left.*dna_session_rsids"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{1100 + i}" for i in range(20)])
    tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{1100 + i}" for i in range(10)])
    assert budget.left == 0


def test_run_kb_tool_turns_errors_into_text(kb):
    out = run_kb_tool(kb, tools.dna_summary, "nobody")
    assert out.is_error and "No DNA for 'nobody'" in json.loads(out.text)["error"]
```

Append to `tests/test_analysis_proposals.py`:

```python
def test_dna_results_can_be_cited(actx, analysis_kb, name):
    from fixtures.dna import add_person

    from genea.dna import store as dna_store
    from genea.dna.store import HaploCall, Haplogroups

    add_person(analysis_kb, slug="tree.ged-I1", gedcom_id="tree.ged#@I1@")
    cite = "[dna/tree.ged-I1/haplogroups.json]"
    with pytest.raises(ToolError, match="not an indexed file"):
        api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I1@", "text": "Y: R-P312"}, [cite], "yhaplo")
    dna_store.save_haplogroups(analysis_kb, "tree.ged-I1",
                               Haplogroups(analyzed_at="x", calls=[HaploCall(line="Y", haplogroup="R-P312")]))
    result = api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I1@", "text": "Y: R-P312"}, [cite],
                         "yhaplo called R-P312")
    assert result.status == "active"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dna_tools.py tests/test_analysis_proposals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'genea.dna.limits'` and the DNA citation rejected as "not an indexed file".

- [ ] **Step 3: Implement**

In `src/genea/tool_runner.py` add `from genea.kb import KnowledgeBase` and replace `render` and `error_text` with:

```python
def render_limited(result: BaseModel, limit: int) -> str:
    text = result.model_dump_json(exclude_none=True)
    if len(text) <= limit:
        return text
    return shrink(result.model_dump(mode="json", exclude_none=True), limit) + TRUNCATION_MARKER


def render(ctx: ToolContext, result: BaseModel) -> str:
    return render_limited(result, ctx.kb.settings.tool_output_chars)


def error_limited(message: str, limit: int) -> str:
    """A failure is a normal tool result, so repeated failures never exhaust a per-tool retry budget."""
    if len(message) > limit:
        message = message[:limit] + "…"
    return dump({"error": message})


def error_text(ctx: ToolContext, message: str) -> str:
    return error_limited(message, ctx.kb.settings.tool_output_chars)
```

and append:

```python
def run_kb_tool(kb: KnowledgeBase, fn, *args, **kwargs) -> ToolOutput:
    """run_tool for tools that need only the knowledge base, not the index (the DNA tools)."""
    limit = kb.settings.tool_output_chars
    try:
        return ToolOutput(render_limited(fn(kb, *args, **kwargs), limit), False)
    except ToolError as exc:
        return ToolOutput(error_limited(str(exc), limit), True)
    except Exception as exc:  # noqa: BLE001 - one bad file must not crash the agent loop or server
        return ToolOutput(error_limited(f"{fn.__name__} failed: {type(exc).__name__}: {exc}", limit), True)
```

Create `src/genea/dna/limits.py`:

```python
"""How many SNPs a model may read: a per-call cap and a budget per agent or MCP session."""

from __future__ import annotations

from genea.tools.base import ToolError

MAX_RSIDS_PER_CALL = 20


class RsidBudget:
    """rsIDs one session may look up. The per-call cap alone would let a model page the genotype out 20 at a time."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self.used = 0

    @property
    def left(self) -> int:
        return self.limit - self.used

    def take(self, n: int) -> None:
        if n > self.left:
            raise ToolError(f"Privacy limit: this session may look up {self.limit} rsIDs in total and {self.left} "
                            "are left. The limit is deliberate; do not split or repeat requests to get around it. "
                            "The user can raise dna_session_rsids in the knowledge base's config.toml.")
        self.used += n
```

Create `src/genea/dna/tools.py`:

```python
"""Read-only DNA tools shared by the local agent and the MCP server. Nothing here computes or writes."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from genea.dna.limits import MAX_RSIDS_PER_CALL, RsidBudget
from genea.dna.store import HaploCall, genotypes, load_haplogroups, resolve_person
from genea.kb import KnowledgeBase
from genea.tools.base import ToolError
from genea.tools.models import SourceRef, ToolResult

CAVEAT = ("A Y haplogroup describes only the direct paternal line and an mtDNA haplogroup only the direct maternal "
          "line, not the whole person. Calls from a genotyping chip have limited resolution (see markers_used). "
          "A haplogroup supports, but never proves, a specific link in the tree.")
_RSID = re.compile(r"^(rs|i)\d+$")


class DnaSummary(ToolResult):
    person: str
    gedcom_id: str
    tree_name: str | None = None
    sex: str
    build: int
    snps: int
    called: int
    call_rate: float
    warnings: list[str] = Field(default_factory=list)
    haplogroups: str


class HaplogroupResult(ToolResult):
    person: str
    gedcom_id: str
    analyzed_at: str
    calls: list[HaploCall]
    caveat: str


class SnpGenotype(BaseModel):
    rsid: str
    chrom: str | None = None
    pos: int | None = None
    genotype: str | None = None
    note: str | None = None


class SnpLookupResult(ToolResult):
    person: str
    results: list[SnpGenotype]
    lookups_left: int


def _source(slug: str, name: str) -> SourceRef:
    return SourceRef(file=f"dna/{slug}/{name}")


def dna_summary(kb: KnowledgeBase, person: str) -> DnaSummary:
    meta = resolve_person(kb, person)
    analysed = load_haplogroups(kb, meta.slug)
    status = (f"analysed {analysed.analyzed_at}" if analysed
              else f"not analysed yet: the user runs `genea dna analyze {meta.slug}`")
    return DnaSummary(person=meta.slug, gedcom_id=meta.gedcom_id, tree_name=meta.tree_name, sex=meta.sex,
                      build=meta.build, snps=meta.qc.total, called=meta.qc.called, call_rate=meta.qc.call_rate,
                      warnings=meta.qc.warnings, haplogroups=status, sources=[_source(meta.slug, "meta.json")])


def haplogroups(kb: KnowledgeBase, person: str) -> HaplogroupResult:
    meta = resolve_person(kb, person)
    result = load_haplogroups(kb, meta.slug)
    if result is None:
        raise ToolError(f"No haplogroups for {meta.slug} yet: the user runs `genea dna analyze {meta.slug}`.")
    return HaplogroupResult(person=meta.slug, gedcom_id=meta.gedcom_id, analyzed_at=result.analyzed_at,
                            calls=result.calls, caveat=CAVEAT, sources=[_source(meta.slug, "haplogroups.json")])


def normalize_rsids(rsids: list[str] | str) -> list[str]:
    """Lower-case, split 'rs1, rs2', drop duplicates; reject anything that is not an rsID before it costs budget."""
    items = [rsids] if isinstance(rsids, str) else list(rsids or [])
    wanted = list(dict.fromkeys(part.lower() for item in items for part in re.split(r"[\s,;]+", str(item)) if part))
    bad = [r for r in wanted if not _RSID.match(r)]
    if bad:
        raise ToolError(f"Not rsIDs: {', '.join(bad[:5])}. Use ids like rs4988235.")
    if not wanted:
        raise ToolError(f"Give 1-{MAX_RSIDS_PER_CALL} rsIDs, like rs4988235.")
    if len(wanted) > MAX_RSIDS_PER_CALL:
        raise ToolError(f"At most {MAX_RSIDS_PER_CALL} rsIDs per call ({len(wanted)} given). "
                        "This is a deliberate privacy limit.")
    return wanted


def snp_lookup(kb: KnowledgeBase, budget: RsidBudget, person: str, rsids: list[str] | str) -> SnpLookupResult:
    meta = resolve_person(kb, person)
    wanted = normalize_rsids(rsids)
    budget.take(len(wanted))
    found = genotypes(kb, meta.slug, wanted)
    results = []
    for rsid in wanted:
        row = found.get(rsid)
        if row is None:
            results.append(SnpGenotype(rsid=rsid, note="not on this chip (not tested), which is not the same as "
                                                       "no variant"))
        elif row[3] is None:
            results.append(SnpGenotype(rsid=rsid, chrom=row[1], pos=row[2], note="tested, but no call"))
        else:
            results.append(SnpGenotype(rsid=rsid, chrom=row[1], pos=row[2], genotype=row[3]))
    return SnpLookupResult(person=meta.slug, results=results, lookups_left=budget.left,
                           sources=[_source(meta.slug, "genotypes.sqlite")])
```

In `src/genea/analysis/api.py`, add `from genea.dna.store import citation_exists` and change the citation check in `propose` to:

```python
    for ref in refs:
        indexed = ctx.conn.execute("SELECT 1 FROM files WHERE path = ?", (ref.file,)).fetchone() is not None
        if not indexed and not citation_exists(ctx.kb, ref.file):
            raise ToolError(f"{ref.file} is not an indexed file of this knowledge base; cite what the tools returned.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dna_tools.py tests/test_analysis_proposals.py tests/test_tool_runner.py -v`
Expected: PASS. Then run `uv run pytest` — Expected: everything passes.

- [ ] **Step 5: Commit**

```bash
git add src/genea/dna/limits.py src/genea/dna/tools.py src/genea/tool_runner.py src/genea/analysis/api.py tests/test_dna_tools.py tests/test_analysis_proposals.py
git commit -m "DNA: read-only tools with a per-call cap, a session budget and citations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: CLI (`genea dna ...`) and `doctor`

**Files:**
- Create: `src/genea/cli/dnacmds.py`
- Modify: `src/genea/cli/app.py`, `src/genea/cli/main.py`, `src/genea/doctor.py`
- Test: `tests/test_cli_dna.py`, `tests/test_doctor.py`

**Interfaces:**
- Consumes: `api.import_file`, `api.analyze`, `runtime.setup`, `runtime.find_java`, `runtime.haplogrep_exe`, `runtime.rcrs_fasta`, `runtime.yhaplo_version`, `store.list_people`, `store.load_haplogroups`, `store.DnaError`, `common.open_kb`, `common.console`.
- Produces: `dna_app` (typer) with `setup`, `import`, `analyze`, `status`; `doctor.dna_checks() -> list[Check]`.

Exit codes: 0 success; 1 a `DnaError`, or `analyze` skipped a line because a tool is missing (its reason contains `genea dna setup` or `uv sync`); 2 no knowledge base.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_dna.py`:

```python
import pytest
from fixtures.dna import write_csv
from typer.testing import CliRunner

from genea.cli.main import app
from genea.dna import api, runtime
from genea.dna.store import DnaError, HaploCall, Haplogroups

runner = CliRunner()
pytest.importorskip("snps")


def flat(result) -> str:
    """Rich wraps at 80 columns; compare the text with the line breaks removed."""
    return " ".join(result.output.split())


def test_import_status_and_analyze(analysis_kb, tmp_path, monkeypatch):
    kb = ["--kb", str(analysis_kb.root)]
    csv = write_csv(tmp_path / "MyHeritage_raw_dna_data.csv")
    result = runner.invoke(app, [*kb, "dna", "import", str(csv), "--person", "tree.ged#@I1@"])
    assert result.exit_code == 0, result.output
    assert "Person: Karol Weiss (tree.ged#@I1@)" in flat(result) and "DNA name: tree.ged-I1" in flat(result)

    again = runner.invoke(app, [*kb, "dna", "import", str(csv), "--person", "tree.ged#@I1@"])
    assert again.exit_code == 1 and "--replace" in flat(again)

    status = runner.invoke(app, [*kb, "dna", "status"])
    assert status.exit_code == 0 and "not analysed yet: genea dna analyze tree.ged-I1" in flat(status)

    monkeypatch.setattr(api, "analyze", lambda kb_, person: Haplogroups(analyzed_at="x", calls=[
        HaploCall(line="mtDNA", haplogroup="H1", quality=0.9, markers_used=300),
        HaploCall(line="Y", haplogroup="R-P312", detail="R1b1a2a1a2", markers_used=1500)]))
    done = runner.invoke(app, [*kb, "dna", "analyze", "tree.ged-I1"])
    assert done.exit_code == 0, done.output
    assert "mtDNA: H1 from 300 markers" in flat(done) and "Y: R-P312 (R1b1a2a1a2)" in flat(done)
    assert "None" not in done.output


def test_analyze_exits_1_when_a_tool_is_missing(analysis_kb, monkeypatch):
    monkeypatch.setattr(api, "analyze", lambda kb_, person: Haplogroups(analyzed_at="x", calls=[
        HaploCall(line="mtDNA", skipped_reason="no Java 11+ for Haplogrep; run `genea dna setup`."),
        HaploCall(line="Y", haplogroup="R-P312", markers_used=10)]))
    result = runner.invoke(app, ["--kb", str(analysis_kb.root), "dna", "analyze", "x"])
    assert result.exit_code == 1 and "genea dna setup" in flat(result)


def test_analyze_unknown_person(analysis_kb):
    result = runner.invoke(app, ["--kb", str(analysis_kb.root), "dna", "analyze", "nobody"])
    assert result.exit_code == 1 and "No DNA for 'nobody'" in flat(result)


def test_setup_reports_messages_and_errors(monkeypatch):
    monkeypatch.setattr(runtime, "setup", lambda: ["Java: system Java 21", "yhaplo 2.1.19"])
    ok = runner.invoke(app, ["dna", "setup"])
    assert ok.exit_code == 0 and "yhaplo 2.1.19" in flat(ok)

    def fail():
        raise DnaError("Checksum mismatch for x")
    monkeypatch.setattr(runtime, "setup", fail)
    bad = runner.invoke(app, ["dna", "setup"])
    assert bad.exit_code == 1 and "Checksum mismatch" in flat(bad)
```

In `tests/test_doctor.py`, change `_patch` so the existing tests stay independent of the `dna` extra:

```python
def _patch(monkeypatch, exif="13.33", models=("qwen3:14b", "bge-m3:latest")):
    monkeypatch.setattr(doctor, "exiftool_version", lambda exe: exif)
    monkeypatch.setattr(doctor, "ollama_models", lambda url: None if models is None else list(models))
    monkeypatch.setattr(doctor, "dna_checks", lambda: [])
```

and append:

```python
def test_dna_checks_point_to_setup(monkeypatch):
    from genea.dna import runtime

    monkeypatch.setattr(runtime, "find_java", lambda run=None: runtime.JavaChoice(None, "none", "no Java 11+"))
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: None)
    checks = {c.name: c for c in doctor.dna_checks()}
    assert not checks["Java (DNA)"].ok and "genea dna setup" in checks["Java (DNA)"].fix
    assert not checks["Haplogrep"].ok and checks["Haplogrep"].fix == "genea dna setup"
    assert checks["yhaplo"].fix == "uv sync --extra dna"


def test_dna_checks_hidden_without_the_extra(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    assert doctor.dna_checks() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_dna.py tests/test_doctor.py -v`
Expected: FAIL — `No such command 'dna'` and `AttributeError: ... 'dna_checks'`.

- [ ] **Step 3: Implement**

In `src/genea/cli/app.py` add after the `analysis_app` lines:

```python
dna_app = typer.Typer(no_args_is_help=True,
                      help="Analyse a MyHeritage raw DNA file: mtDNA and Y haplogroups, SNP lookups for the agent.")
app.add_typer(dna_app, name="dna")
```

In `src/genea/cli/main.py` add `dnacmds,` to the imported command modules (alphabetical, after `changecmds`).

Create `src/genea/cli/dnacmds.py`:

```python
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
```

In `src/genea/doctor.py` add `import importlib.util` and:

```python
def dna_checks() -> list[Check]:
    """Shown only when the dna extra is installed, so doctor stays green for people who do not use DNA."""
    if importlib.util.find_spec("snps") is None:
        return []
    from genea.dna import runtime

    java = runtime.find_java()
    checks = [Check("Java (DNA)", java.path is not None, java.detail,
                    "" if java.path else "genea dna setup (downloads a portable Java into ~/.genea/dna-tools; "
                                         "your system Java is not changed)")]
    installed = runtime.haplogrep_exe().is_file() and runtime.rcrs_fasta().is_file()
    checks.append(Check("Haplogrep", installed,
                        f"{runtime.HAPLOGREP_VERSION} ({runtime.HAPLOGREP_TREE})" if installed else "not installed",
                        "" if installed else "genea dna setup"))
    version = runtime.yhaplo_version()
    checks.append(Check("yhaplo", version is not None, version or "not installed",
                        "" if version else "uv sync --extra dna"))
    return checks
```

and in `run_checks` change the first line to:

```python
    checks = [check_sqlite_vec(), check_exiftool(settings.exiftool), *dna_checks()]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_dna.py tests/test_doctor.py tests/test_cli_basic.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/genea/cli/dnacmds.py src/genea/cli/app.py src/genea/cli/main.py src/genea/doctor.py tests/test_cli_dna.py tests/test_doctor.py
git commit -m "DNA: genea dna setup/import/analyze/status and doctor checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: MCP server, local agent and prompt rule

**Files:**
- Modify: `src/genea/mcp_server.py`, `src/genea/agent/core.py`, `src/genea/agent/prompt.py`
- Test: `tests/test_mcp_dna.py`, `tests/test_mcp_server.py`, `tests/test_prompt.py`

**Interfaces:**
- Consumes: `dna.tools.dna_summary`, `dna.tools.haplogroups`, `dna.tools.snp_lookup`, `dna.limits.RsidBudget`, `tool_runner.run_kb_tool`, `Settings.dna_session_rsids`.
- Produces: MCP tools and agent tools `dna_summary(person)`, `haplogroups(person)`, `snp_lookup(person, rsids)` with identical docstrings; `prompt._DNA_RULE`.

The three docstrings, used verbatim in both files:

```python
DNA_SUMMARY_DOC = """What DNA data exists for a person (a DNA person name, or the tree id it was imported for, like
'tree.ged#@I12@'): inferred sex, SNP count, call rate, warnings and whether haplogroups were analysed."""
HAPLOGROUPS_DOC = """The Y (direct paternal line) and mtDNA (direct maternal line) haplogroups computed from a person's
raw DNA file, with the markers used and the tools that called them. Supporting evidence, never proof of a tree link."""
SNP_LOOKUP_DOC = """Genotypes of 1-20 SNPs by rsID (like rs4988235) from a person's raw DNA file. Limited on purpose:
at most 20 rsIDs per call and a fixed budget per session. Look up only what the user asks about."""
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_dna.py`:

```python
import asyncio
import json
from dataclasses import replace

import pytest

pytest.importorskip("mcp")

from fixtures.dna import add_person  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError as McpToolError  # noqa: E402

from genea.dna import store  # noqa: E402
from genea.dna.store import HaploCall, Haplogroups  # noqa: E402
from genea.kb import KnowledgeBase, init_kb  # noqa: E402
from genea.mcp_server import IndexGate, build_server  # noqa: E402


def _never_opened():
    raise AssertionError("DNA tools must not open the index")


@pytest.fixture
def server(tmp_path):
    """A knowledge base that is not indexed: DNA tools still answer."""
    base = init_kb(tmp_path / "kb")
    kb = KnowledgeBase(base.root, replace(base.settings, dna_session_rsids=25))
    add_person(kb)
    store.save_haplogroups(kb, "tree.ged-I12", Haplogroups(analyzed_at="x", calls=[
        HaploCall(line="Y", haplogroup="R-P312", markers_used=16)]))
    return build_server(IndexGate(kb, _never_opened))


def _call(server, tool, arguments):
    return asyncio.run(server.call_tool(tool, arguments))


def test_dna_tools_work_without_an_index(server):
    summary = json.loads(_call(server, "dna_summary", {"person": "tree.ged#@I12@"}).content[0].text)
    assert summary["person"] == "tree.ged-I12"
    groups = json.loads(_call(server, "haplogroups", {"person": "tree.ged-I12"}).content[0].text)
    assert groups["calls"][0]["haplogroup"] == "R-P312"


def test_snp_lookup_budget_lasts_the_whole_server_session(server):
    first = json.loads(_call(server, "snp_lookup", {"person": "tree.ged-I12",
                                                    "rsids": [f"rs{1000 + i}" for i in range(20)]}).content[0].text)
    assert first["lookups_left"] == 5
    with pytest.raises(McpToolError, match="Privacy limit"):
        _call(server, "snp_lookup", {"person": "tree.ged-I12", "rsids": [f"rs{1100 + i}" for i in range(6)]})


def test_cap_reaches_the_client_as_an_error(server):
    with pytest.raises(McpToolError, match="At most 20"):
        _call(server, "snp_lookup", {"person": "tree.ged-I12", "rsids": [f"rs{i}" for i in range(21)]})
```

In `tests/test_mcp_server.py` add `DNA = {"dna_summary", "haplogroups", "snp_lookup"}`, change the registration assertion to `assert names == READ_ONLY | MCP_ONLY | ANALYSIS | DNA`, and in `test_descriptions_match_the_agent_tools` change the loop to `for name in READ_ONLY | DNA:`.

Append to `tests/test_prompt.py`:

```python
def test_dna_rule_in_both():
    for prompt in (SYSTEM_PROMPT, MCP_INSTRUCTIONS):
        assert "direct paternal line" in prompt and "never as proof" in prompt
        assert "[dna/tree.ged-I12/haplogroups.json]" in prompt and "reconstruct the genotype" in prompt
    assert "\n7. dna_summary" in SYSTEM_PROMPT
    assert "\n8. dna_summary" in MCP_INSTRUCTIONS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_dna.py tests/test_mcp_server.py tests/test_prompt.py -v`
Expected: FAIL — unknown tool `dna_summary`, tool set mismatch, missing DNA rule.

- [ ] **Step 3: Implement**

In `src/genea/agent/prompt.py` add before `SYSTEM_PROMPT`:

```python
_DNA_RULE = """\
{n}. dna_summary, haplogroups and snp_lookup read results computed from a family member's raw DNA file. A Y
   haplogroup describes only the direct paternal line and an mtDNA haplogroup only the direct maternal line, never
   the whole person, and chip data has limited resolution. Treat a haplogroup as supporting evidence, never as proof
   of a specific link in the tree; cite it like [dna/tree.ged-I12/haplogroups.json]. snp_lookup is limited on
   purpose: look up only what the user asks about and never try to reconstruct the genotype.
"""
```

and change the two constants to:

```python
SYSTEM_PROMPT = _HEAD + _AGENT_RULE_4 + _TAIL + _SUMMARY_RULE.format(n=6) + _DNA_RULE.format(n=7)
MCP_INSTRUCTIONS = _HEAD + _MCP_RULE_4 + _TAIL + _MCP_RULE_6 + _SUMMARY_RULE.format(n=7) + _DNA_RULE.format(n=8)
```

In `src/genea/mcp_server.py` add imports `from genea.dna import tools as dna_tools`, `from genea.dna.limits import RsidBudget` and change `from genea.tool_runner import run_tool` to `from genea.tool_runner import run_kb_tool, run_tool`. Inside `build_server`, after the `run` helper, add:

```python
    budget = RsidBudget(gate.kb.settings.dna_session_rsids)  # one budget for the whole MCP session

    def run_dna(fn, *args) -> str:
        """DNA tools need no index: they answer before and while `genea index` runs."""
        out = run_kb_tool(gate.kb, fn, *args)
        if out.is_error:
            raise McpToolError(out.text)
        return out.text
```

and before `index_status`, register:

```python
    @server.tool(structured_output=False)
    async def dna_summary(person: str) -> str:
        """What DNA data exists for a person (a DNA person name, or the tree id it was imported for, like
        'tree.ged#@I12@'): inferred sex, SNP count, call rate, warnings and whether haplogroups were analysed."""
        return run_dna(dna_tools.dna_summary, person)

    @server.tool(structured_output=False)
    async def haplogroups(person: str) -> str:
        """The Y (direct paternal line) and mtDNA (direct maternal line) haplogroups computed from a person's
        raw DNA file, with the markers used and the tools that called them. Supporting evidence, never proof of a tree link."""
        return run_dna(dna_tools.haplogroups, person)

    @server.tool(structured_output=False)
    async def snp_lookup(person: str, rsids: list[str]) -> str:
        """Genotypes of 1-20 SNPs by rsID (like rs4988235) from a person's raw DNA file. Limited on purpose:
        at most 20 rsIDs per call and a fixed budget per session. Look up only what the user asks about."""
        return run_dna(dna_tools.snp_lookup, budget, person, rsids)
```

In `src/genea/agent/core.py` add imports `from genea.dna import tools as dna_tools`, `from genea.dna.limits import RsidBudget` and change the runner import to `from genea.tool_runner import run_kb_tool, run_tool`. Inside `build_agent`, before `return agent`, add:

```python
    budget: RsidBudget | None = None  # one budget per agent, i.e. per `genea chat` session

    def _dna(ctx: RunContext[ToolContext], fn, *args) -> str:
        return run_kb_tool(ctx.deps.kb, fn, *args).text

    @agent.tool
    async def dna_summary(ctx: RunContext[ToolContext], person: str) -> str:
        """What DNA data exists for a person (a DNA person name, or the tree id it was imported for, like
        'tree.ged#@I12@'): inferred sex, SNP count, call rate, warnings and whether haplogroups were analysed."""
        return _dna(ctx, dna_tools.dna_summary, person)

    @agent.tool
    async def haplogroups(ctx: RunContext[ToolContext], person: str) -> str:
        """The Y (direct paternal line) and mtDNA (direct maternal line) haplogroups computed from a person's
        raw DNA file, with the markers used and the tools that called them. Supporting evidence, never proof of a tree link."""
        return _dna(ctx, dna_tools.haplogroups, person)

    @agent.tool
    async def snp_lookup(ctx: RunContext[ToolContext], person: str, rsids: list[str]) -> str:
        """Genotypes of 1-20 SNPs by rsID (like rs4988235) from a person's raw DNA file. Limited on purpose:
        at most 20 rsIDs per call and a fixed budget per session. Look up only what the user asks about."""
        nonlocal budget
        if budget is None:
            budget = RsidBudget(ctx.deps.kb.settings.dna_session_rsids)
        return _dna(ctx, dna_tools.snp_lookup, budget, person, rsids)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mcp_dna.py tests/test_mcp_server.py tests/test_prompt.py tests/test_agent.py -v`
Expected: PASS. Then `uv run pytest` — Expected: everything passes.

- [ ] **Step 5: Commit**

```bash
git add src/genea/mcp_server.py src/genea/agent/core.py src/genea/agent/prompt.py tests/test_mcp_dna.py tests/test_mcp_server.py tests/test_prompt.py
git commit -m "DNA: serve dna_summary, haplogroups and snp_lookup to the agent and over MCP

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Live tests, README and manual check

**Files:**
- Create: `tests/live/test_live_dna.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `haplo.call_mtdna`, `haplo.call_y`, `haplo.read_fasta`, `runtime.*`, fixtures `Y_R1B`.
- Produces: nothing new.

- [ ] **Step 1: Write the live tests**

Create `tests/live/test_live_dna.py`:

```python
"""Real Haplogrep and yhaplo runs on public or synthetic data (never the user's file).

Run:  uv sync --extra dna && uv run genea dna setup && uv run pytest -m live tests/live/test_live_dna.py
"""

import pytest
from fixtures.dna import Y_R1B

from genea.dna import haplo, runtime

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def real_tools_dir(monkeypatch):
    """conftest points GENEA_HOME at an empty folder; the live tests need the tools `genea dna setup` installed."""
    monkeypatch.delenv("GENEA_HOME", raising=False)


def test_mtdna_reproduces_haplogreps_own_example(tmp_path):
    if runtime.find_java().path is None or not runtime.rcrs_fasta().is_file():
        pytest.skip("run `genea dna setup` first")
    example = runtime.haplogrep_dir() / "data" / "examples" / "example-microarray.vcf"
    rows, column = [], None
    for line in example.read_text(encoding="utf-8").splitlines():
        if line.startswith("##"):
            continue
        cols = line.split("\t")
        if line.startswith("#"):
            column = cols.index("NA12282")
            continue
        if cols[column] != ".":
            base = cols[4] if cols[column] == "1" else cols[3]
            rows.append((f"i{cols[1]}", "MT", int(cols[1]), base * 2))
    call = haplo.call_mtdna(rows, tmp_path)
    assert call.haplogroup == "J1b1a", call


def test_y_r1b(tmp_path):
    if runtime.yhaplo_version() is None:
        pytest.skip("uv sync --extra dna")
    rows = [(f"rs{i}", "Y", pos, base * 2) for i, (pos, base) in enumerate(Y_R1B)]
    call = haplo.call_y("male", rows, tmp_path)
    assert (call.haplogroup, call.detail) == ("R-P312", "R1b1a2a1a2"), call
```

- [ ] **Step 2: Run them**

Run: `uv sync --extra dna --extra mcp && uv run genea dna setup && uv run pytest -m live tests/live/test_live_dna.py -v`
Expected: `genea dna setup` reports the portable Java (this machine has Java 1.8) and Haplogrep 3.3.2; both live tests PASS. If `haplogrep3.exe` is missing after unpacking, check Windows Defender's protection history before retrying.

- [ ] **Step 3: README**

Add a `## DNA (MyHeritage raw data)` section to `README.md` after the analysis section:

```markdown
## DNA (MyHeritage raw data)

genea can read a MyHeritage raw DNA download (`MyHeritage_raw_dna_data.csv`, build 37) for one person of the tree
and call the mtDNA (maternal line) and Y (paternal line) haplogroups with free tools.

    uv sync --extra dna
    genea dna setup                                   # Haplogrep 3 (+ a portable Java 21 if yours is older than 11)
    genea dna import MyHeritage_raw_dna_data.csv --person "tree.ged#@I12@"
    genea dna analyze tree.ged-I12
    genea dna status

- Data stays in `<knowledge base>/.genea/dna/<person>/`; it is never indexed or searchable. The raw CSV is not copied.
- The tools go to `~/.genea/dna-tools/` and are shared by all knowledge bases; your system Java is not changed.
- The agent and the MCP server get read-only tools: `dna_summary`, `haplogroups` and `snp_lookup`. `snp_lookup`
  answers at most 20 rsIDs per call and 200 per session (`dna_session_rsids` in `config.toml`). Over MCP, results
  are sent to Claude.
- Licenses: Haplogrep 3 is MIT; yhaplo (23andMe) is free for non-commercial use by individuals.
```

- [ ] **Step 4: Full test run**

Run: `uv run pytest`
Expected: all non-live tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/live/test_live_dna.py README.md
git commit -m "DNA: live tests against Haplogrep's example and an R1b sample, README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Manual check with the real file (the user runs it; report results, do not paste genotypes)**

1. `genea --kb "D:\Foto DB" dna import <path>\MyHeritage_raw_dna_data.csv --person "<tree id of the donor>"` — note the SNP count, call rate, DNA sex and any warning.
2. `genea --kb "D:\Foto DB" dna analyze <slug>` — note the mtDNA and Y haplogroups and their marker counts; these answer the spec's "how many markers does the chip cover" question.
3. `genea --kb "D:\Foto DB" dna status`.
4. In Claude Code (genea MCP): "What are the haplogroups of <donor>?" — the answer cites `[dna/<slug>/haplogroups.json]` and states the paternal/maternal-line caveat.
5. Ask for 25 rsIDs in one go — the tool refuses with the 20-per-call message.
```
