# Genealogy Knowledge-Base Agent ("genea") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `genea`, a local CLI agent that indexes a genealogy knowledge-base folder (GEDCOM, documents, image metadata), answers questions with citations, finds conflicts, writes notes, and proposes changes that the user reviews and applies.

**Architecture:** A Python package with clear layers. `ingest/` parses each data type into records. `index/` stores them in one SQLite file per knowledge base (relational tables, FTS5 and sqlite-vec). `tools/` holds pure query and curate functions with no LLM. `changes/` handles reviewed write-back. `agent/` wires the tools into a PydanticAI agent. `cli/` is a Typer front end. A knowledge base is just a root folder; all generated state lives in `<root>/.genea/`.

**Tech Stack:** Python 3.12, uv, PydanticAI 2.x, Ollama (Qwen3 + bge-m3), SQLite + FTS5 + sqlite-vec, pypdf, python-docx, ExifTool (subprocess), Typer + Rich, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-genealogy-agent-design.md`

## Global Constraints

- Python `>=3.12`; run everything through `uv` (`uv run pytest`, `uv run genea ...`).
- Licences: only permissive or GPL-compatible tools; **do not** add PyMuPDF (AGPL) or pyexiftool.
- ExifTool is called only through `genea.exiftool.run_exiftool` (subprocess + UTF-8 argument file).
- The agent never writes user data directly. Notes go to `.genea/notes/`; everything else goes through `changes/` and needs `genea changes apply`.
- XMP changes are written **only** to sidecars (`<stem>.xmp`, or an existing `<name>.<ext>.xmp`). Original images are never modified.
- In v1, GEDCOM changes are export-only. `gedcom_direct_write` defaults to `false`, and apply refuses GEDCOM changes either way.
- Every tool result carries `sources: list[SourceRef]`.
- Person ids are `"<ged_file>#<xref>"`, e.g. `tree.ged#@I12@`.
- The embedding model is always local (Ollama `bge-m3`, 1024 dims). Tests use `FakeEmbedder` (64 dims) and never touch the network, except tests marked `live`.
- Tests that need the ExifTool binary are marked `@pytest.mark.exiftool` and are skipped automatically when it is missing.
- Test files live flat in `tests/` with unique basenames. Fixtures live in `tests/fixtures/`.
- Commit after every task. End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Map

```
pyproject.toml, .python-version, .gitignore, .gitattributes, README.md
src/genea/
  __init__.py
  config.py            Settings, load_settings, resolve_kb_root, KBNotFound
  kb.py                KnowledgeBase, init_kb
  exiftool.py          run_exiftool, exiftool_version, read_metadata, ExifToolError
  doctor.py            Check, run_checks
  text.py              normalize
  ingest/__init__.py
  ingest/gedcom/__init__.py
  ingest/gedcom/ansel.py    decode_ansel
  ingest/gedcom/parser.py   GedLine, GedDocument, parse_gedcom, detect_encoding
  ingest/gedcom/dates.py    DateRange, parse_gedcom_date
  ingest/gedcom/mapper.py   PersonRec, FamilyRec, EventRec, GedcomData, map_gedcom, split_name
  ingest/docs.py       Page, DocExtract, Chunk, extract_document, chunk_pages, DOC_EXTS
  ingest/images.py     ImageRecord, sidecar_for, default_sidecar, map_metadata, extract_image, IMAGE_EXTS
  index/__init__.py
  index/db.py          connect, open_index, SchemaMismatch
  index/embed.py       Embedder, OllamaEmbedder, FakeEmbedder, EmbeddingError, make_embedder
  index/indexer.py     scan_files, file_hash, index_kb, reindex_file, rebuild_links, IndexReport
  tools/__init__.py
  tools/models.py      Pydantic result models (SourceRef, PersonSummary, ...)
  tools/base.py        ToolContext, ToolError, open_tools, person_id, parse_person_id, parse_family_id
  tools/people.py      find_person, get_person, relatives, timeline (+ helpers)
  tools/search.py      search_documents, read_document, find_images
  tools/conflicts.py   find_conflicts
  tools/curate.py      write_note, propose_change
  changes/__init__.py
  changes/store.py     Change, ChangeStore, ChangeError, StaleChange, validate_change, target_hash, XMP_FIELDS
  changes/apply.py     apply_change, undo_change, mark_manual, describe_change, xmp_args
  changes/export.py    export_gedcom_changes
  agent/__init__.py
  agent/prompt.py      SYSTEM_PROMPT
  agent/model.py       build_model, is_local
  agent/core.py        build_agent, ask, Answer, AgentFailed
  cli/__init__.py
  cli/app.py           app, changes_app (Typer objects + --kb callback)
  cli/common.py        console, open_kb, get_embedder, open_tool_context
  cli/main.py          entry point; imports command modules
  cli/kbcmds.py        init, doctor
  cli/indexcmds.py     index, status
  cli/querycmds.py     person, conflicts
  cli/changecmds.py    changes list/show/apply/reject/undo/export
  cli/agentcmds.py     ask, chat
tests/
  conftest.py
  fixtures/kb_sample/tree.ged
  fixtures/make_kb.py
  private/README.md    (drop real .ged files here for round-trip tests; git-ignored)
  test_*.py
  live/test_live_qa.py
```

---

### Task 1: Project scaffold, configuration, knowledge-base layout, `init` and `doctor`

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.gitattributes`, `tests/private/README.md`
- Create: `src/genea/__init__.py`, `src/genea/config.py`, `src/genea/kb.py`, `src/genea/exiftool.py`, `src/genea/doctor.py`
- Create: `src/genea/cli/__init__.py`, `src/genea/cli/app.py`, `src/genea/cli/common.py`, `src/genea/cli/main.py`, `src/genea/cli/kbcmds.py`
- Test: `tests/conftest.py`, `tests/test_config.py`, `tests/test_kb.py`, `tests/test_exiftool.py`, `tests/test_doctor.py`, `tests/test_cli_basic.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `genea.config.Settings` (frozen dataclass): `model: str="ollama:qwen3:14b"`, `embed_model: str="bge-m3"`, `embed_dim: int=1024`, `ollama_url: str="http://localhost:11434"`, `max_tool_calls: int=15`, `tool_output_chars: int=12000`, `privacy_notice: bool=True`, `gedcom_direct_write: bool=False`, `exclude: tuple[str,...]=()`, `exiftool: str="exiftool"`
  - `load_settings(kb_root: Path | None, overrides: dict | None = None) -> Settings`, `resolve_kb_root(kb: str | None) -> Path`, `global_dir() -> Path`, `KBNotFound(Exception)`
  - `genea.kb.KnowledgeBase(root: Path, settings: Settings)` with `.open(kb: str|None, overrides=None)`, properties `meta_dir, index_path, notes_dir, changes_dir, backups_dir, logs_dir, exports_dir`, methods `ensure_dirs()`, `rel(path) -> str`, `resolve_inside(rel_path: str) -> Path` (raises `ValueError`); `init_kb(root: Path) -> KnowledgeBase`
  - `genea.exiftool.run_exiftool(args: list[str], exe="exiftool", timeout=120.0) -> str`, `exiftool_version(exe="exiftool") -> str | None`, `read_metadata(paths: list[Path], exe="exiftool") -> list[dict]`, `ExifToolError(RuntimeError)`
  - `genea.doctor.Check(name, ok, detail, fix="")`, `run_checks(settings) -> list[Check]`, `ollama_models(url, timeout=5.0) -> list[str] | None`
  - `genea.cli.app.app`, `genea.cli.app.changes_app` (Typer); `genea.cli.common.console`, `open_kb(ctx) -> KnowledgeBase`

- [ ] **Step 1: Create the project files**

`pyproject.toml`:
```toml
[project]
name = "genea"
version = "0.1.0"
description = "Genealogy knowledge-base agent"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28",
    "pydantic>=2.11",
    "pydantic-ai-slim[openai]>=2.54",
    "pypdf>=6.0",
    "python-docx>=1.2",
    "rich>=15.0",
    "sqlite-vec>=0.1.9",
    "typer>=0.27",
]

[project.optional-dependencies]
cloud = ["pydantic-ai-slim[anthropic]>=2.54"]

[project.scripts]
genea = "genea.cli.main:app"

[dependency-groups]
dev = ["pillow>=12.0", "pytest>=8.4", "reportlab>=5.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/genea"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["tests"]
addopts = "-m 'not live'"
markers = [
    "exiftool: needs the ExifTool binary (skipped automatically when it is missing)",
    "live: talks to a real Ollama model (run with `uv run pytest -m live`)",
]
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
dist/
.genea/
tests/private/*
!tests/private/README.md
```

`.gitattributes` (keeps GEDCOM fixtures byte-exact for round-trip tests):
```
*.ged -text
```

`tests/private/README.md`:
```markdown
Put copies of your real `.ged` files here to run the lossless round-trip test on them
(`uv run pytest tests/test_gedcom_parser.py`). Everything in this folder except this
README is git-ignored.
```

`src/genea/__init__.py`:
```python
"""genea — genealogy knowledge-base agent."""

__version__ = "0.1.0"
```

`src/genea/cli/__init__.py`: empty file.

Run: `uv sync`
Expected: creates `.venv` and installs the dependencies without errors.

- [ ] **Step 2: Write failing tests for config, kb and exiftool**

`tests/conftest.py`:
```python
import pytest

from genea.exiftool import exiftool_version


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Point the global config dir at an empty temp folder for every test."""
    home = tmp_path_factory.mktemp("genea_home")
    monkeypatch.setenv("GENEA_HOME", str(home))
    monkeypatch.delenv("GENEA_KB", raising=False)
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")
    return home


def pytest_collection_modifyitems(config, items):
    if exiftool_version() is None:
        skip = pytest.mark.skip(reason="ExifTool binary not available")
        for item in items:
            if "exiftool" in item.keywords:
                item.add_marker(skip)
```

`tests/test_config.py`:
```python
import pytest

from genea.config import KBNotFound, Settings, load_settings, resolve_kb_root


def test_defaults():
    s = load_settings(None)
    assert s == Settings()
    assert s.model == "ollama:qwen3:14b"
    assert s.embed_dim == 1024


def test_precedence_global_then_kb_then_overrides(isolated_home, tmp_path):
    (isolated_home / "config.toml").write_text(
        'model = "ollama:qwen3:8b"\nmax_tool_calls = 9\n', encoding="utf-8"
    )
    kb = tmp_path / "kb"
    (kb / ".genea").mkdir(parents=True)
    (kb / ".genea" / "config.toml").write_text(
        'model = "anthropic:claude-sonnet-5-5"\nexclude = ["private/**"]\n', encoding="utf-8"
    )
    s = load_settings(kb, {"max_tool_calls": 3})
    assert s.model == "anthropic:claude-sonnet-5-5"
    assert s.exclude == ("private/**",)
    assert s.max_tool_calls == 3


def test_unknown_keys_are_ignored(isolated_home):
    (isolated_home / "config.toml").write_text('nonsense = 1\nembed_dim = 8\n', encoding="utf-8")
    assert load_settings(None).embed_dim == 8


def test_resolve_explicit_path(tmp_path):
    assert resolve_kb_root(str(tmp_path)) == tmp_path.resolve()


def test_resolve_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GENEA_KB", str(tmp_path))
    assert resolve_kb_root(None) == tmp_path.resolve()


def test_resolve_named_default(isolated_home, tmp_path):
    (isolated_home / "config.toml").write_text(
        f"default_kb = \"novak\"\n[kbs]\nnovak = '{tmp_path}'\n", encoding="utf-8"
    )
    assert resolve_kb_root(None) == tmp_path.resolve()
    assert resolve_kb_root("novak") == tmp_path.resolve()


def test_resolve_errors(tmp_path):
    with pytest.raises(KBNotFound, match="No knowledge base"):
        resolve_kb_root(None)
    with pytest.raises(KBNotFound, match="not found"):
        resolve_kb_root(str(tmp_path / "missing"))
```

`tests/test_kb.py`:
```python
import pytest

from genea.kb import KnowledgeBase, init_kb

SUBDIRS = ["notes", "changes/pending", "changes/applied", "changes/rejected",
           "changes/stale", "backups", "logs", "exports"]


def test_init_creates_layout(tmp_path):
    kb = init_kb(tmp_path)
    for sub in SUBDIRS:
        assert (tmp_path / ".genea" / sub).is_dir(), sub
    assert kb.meta_dir == tmp_path.resolve() / ".genea"
    assert (kb.meta_dir / "config.toml").is_file()


def test_init_keeps_existing_config(tmp_path):
    init_kb(tmp_path)
    cfg = tmp_path / ".genea" / "config.toml"
    cfg.write_text('model = "ollama:qwen3:8b"\n', encoding="utf-8")
    kb = init_kb(tmp_path)
    assert cfg.read_text(encoding="utf-8") == 'model = "ollama:qwen3:8b"\n'
    assert kb.settings.model == "ollama:qwen3:8b"


def test_resolve_inside_and_rel(tmp_path):
    kb = init_kb(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("x", encoding="utf-8")
    assert kb.resolve_inside("docs/a.txt") == (tmp_path / "docs" / "a.txt").resolve()
    assert kb.rel(tmp_path / "docs" / "a.txt") == "docs/a.txt"
    with pytest.raises(ValueError, match="outside"):
        kb.resolve_inside("../secret.txt")
    with pytest.raises(ValueError, match=".genea"):
        kb.resolve_inside(".genea/config.toml")


def test_open(tmp_path):
    kb = KnowledgeBase.open(str(tmp_path))
    assert kb.root == tmp_path.resolve()
    assert kb.notes_dir.is_dir()
```

`tests/test_exiftool.py`:
```python
import pytest

from genea.exiftool import ExifToolError, exiftool_version, read_metadata, run_exiftool


def test_missing_binary():
    assert exiftool_version("definitely-not-exiftool-xyz") is None
    with pytest.raises(ExifToolError):
        run_exiftool(["-ver"], "definitely-not-exiftool-xyz")


@pytest.mark.exiftool
def test_read_metadata_of_plain_jpeg(tmp_path):
    from PIL import Image

    path = tmp_path / "ž.jpg"
    Image.new("RGB", (8, 8), "red").save(path)
    [record] = read_metadata([path])
    assert record["SourceFile"].endswith("ž.jpg")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_kb.py tests/test_exiftool.py -v`
Expected: collection errors such as `ModuleNotFoundError: No module named 'genea.config'`.

- [ ] **Step 4: Implement config, kb and exiftool**

`src/genea/config.py`:
```python
"""Settings: built-in defaults < global ~/.genea/config.toml < <kb>/.genea/config.toml < overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    model: str = "ollama:qwen3:14b"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024
    ollama_url: str = "http://localhost:11434"
    max_tool_calls: int = 15
    tool_output_chars: int = 12000
    privacy_notice: bool = True
    gedcom_direct_write: bool = False
    exclude: tuple[str, ...] = ()
    exiftool: str = "exiftool"


class KBNotFound(Exception):
    """No usable knowledge-base root could be determined."""


def global_dir() -> Path:
    return Path(os.environ.get("GENEA_HOME") or Path.home() / ".genea")


def _load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _apply(settings: Settings, data: dict) -> Settings:
    known = {f.name for f in fields(Settings)}
    updates = {k: (tuple(v) if k == "exclude" else v) for k, v in data.items() if k in known}
    return replace(settings, **updates)


def load_settings(kb_root: Path | None, overrides: dict | None = None) -> Settings:
    settings = _apply(Settings(), _load_toml(global_dir() / "config.toml"))
    if kb_root is not None:
        settings = _apply(settings, _load_toml(Path(kb_root) / ".genea" / "config.toml"))
    return _apply(settings, overrides or {})


def resolve_kb_root(kb: str | None) -> Path:
    """--kb value > GENEA_KB > default_kb; names are looked up in [kbs] of the global config."""
    gcfg = _load_toml(global_dir() / "config.toml")
    value = kb or os.environ.get("GENEA_KB") or gcfg.get("default_kb")
    if not value:
        raise KBNotFound(
            "No knowledge base given: use --kb <folder>, set GENEA_KB, "
            "or set default_kb in ~/.genea/config.toml"
        )
    path = Path(gcfg.get("kbs", {}).get(value, value)).expanduser()
    if not path.is_dir():
        raise KBNotFound(f"Knowledge base folder not found: {path}")
    return path.resolve()
```

`src/genea/kb.py`:
```python
"""A knowledge base is a root folder; everything genea generates lives in <root>/.genea/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from genea.config import Settings, load_settings, resolve_kb_root

DEFAULT_KB_CONFIG = """\
# genea settings for this knowledge base. Everything is optional; values here
# override the global ~/.genea/config.toml.
# model = "ollama:qwen3:14b"          # or "anthropic:claude-sonnet-5-5"
# exclude = ["private/**", "*.tmp"]   # glob patterns relative to this folder
"""

SUBDIRS = ("notes", "changes/pending", "changes/applied", "changes/rejected",
           "changes/stale", "backups", "logs", "exports")


@dataclass(frozen=True)
class KnowledgeBase:
    root: Path
    settings: Settings

    @classmethod
    def open(cls, kb: str | None, overrides: dict | None = None) -> KnowledgeBase:
        root = resolve_kb_root(kb)
        instance = cls(root, load_settings(root, overrides))
        instance.ensure_dirs()
        return instance

    @property
    def meta_dir(self) -> Path:
        return self.root / ".genea"

    @property
    def index_path(self) -> Path:
        return self.meta_dir / "index.db"

    @property
    def notes_dir(self) -> Path:
        return self.meta_dir / "notes"

    @property
    def changes_dir(self) -> Path:
        return self.meta_dir / "changes"

    @property
    def backups_dir(self) -> Path:
        return self.meta_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.meta_dir / "logs"

    @property
    def exports_dir(self) -> Path:
        return self.meta_dir / "exports"

    def ensure_dirs(self) -> None:
        for sub in SUBDIRS:
            (self.meta_dir / sub).mkdir(parents=True, exist_ok=True)

    def rel(self, path: Path) -> str:
        """Path relative to the root, with forward slashes."""
        return Path(path).resolve().relative_to(self.root).as_posix()

    def resolve_inside(self, rel_path: str) -> Path:
        """Resolve a user/agent supplied path; refuse anything outside the root or inside .genea."""
        path = (self.root / rel_path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Path is outside the knowledge base: {rel_path}")
        if path.is_relative_to(self.meta_dir):
            raise ValueError(f"Path is inside .genea and not readable: {rel_path}")
        return path


def init_kb(root: Path) -> KnowledgeBase:
    root = Path(root).resolve()
    kb = KnowledgeBase(root, load_settings(root))
    kb.ensure_dirs()
    config = kb.meta_dir / "config.toml"
    if not config.exists():
        config.write_text(DEFAULT_KB_CONFIG, encoding="utf-8")
    return KnowledgeBase(root, load_settings(root))
```

`src/genea/exiftool.py`:
```python
"""Thin wrapper around the ExifTool binary (one process per call)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


class ExifToolError(RuntimeError):
    """ExifTool could not be started or reported an error."""


def run_exiftool(args: list[str], exe: str = "exiftool", timeout: float = 120.0) -> str:
    """Run ExifTool once and return stdout.

    Arguments are passed through a UTF-8 argument file (-@) so non-ASCII values and
    file names survive on Windows. An argument file holds one argument per line,
    so line breaks inside values are replaced by spaces.
    """
    clean = [a.replace("\r", " ").replace("\n", " ") for a in args]
    fd, argfile = tempfile.mkstemp(suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(clean) + "\n")
        try:
            proc = subprocess.run(
                [exe, "-charset", "filename=utf8", "-@", argfile],
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExifToolError(f"could not run {exe}: {exc}") from exc
    finally:
        os.unlink(argfile)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise ExifToolError(message or f"{exe} exited with code {proc.returncode}")
    return proc.stdout.decode("utf-8")


def exiftool_version(exe: str = "exiftool") -> str | None:
    try:
        return run_exiftool(["-ver"], exe, timeout=20).strip() or None
    except ExifToolError:
        return None


def read_metadata(paths: list[Path], exe: str = "exiftool") -> list[dict]:
    """Metadata of each file, keys like 'XMP-dc:Title' (-G1), numeric values unformatted (-n)."""
    if not paths:
        return []
    return json.loads(run_exiftool(["-json", "-G1", "-n", *[str(p) for p in paths]], exe))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_kb.py tests/test_exiftool.py -v`
Expected: all pass (`test_read_metadata_of_plain_jpeg` is SKIPPED if ExifTool is not installed).

- [ ] **Step 6: Write failing tests for doctor and the CLI**

`tests/test_doctor.py`:
```python
from genea import doctor
from genea.config import Settings


def _patch(monkeypatch, exif="13.33", models=("qwen3:14b", "bge-m3:latest")):
    monkeypatch.setattr(doctor, "exiftool_version", lambda exe: exif)
    monkeypatch.setattr(doctor, "ollama_models", lambda url: None if models is None else list(models))


def test_all_good(monkeypatch):
    _patch(monkeypatch)
    checks = doctor.run_checks(Settings())
    assert [c.name for c in checks] == ["sqlite-vec", "ExifTool", "Ollama", "Embedding model", "Chat model"]
    assert all(c.ok for c in checks), checks


def test_missing_chat_model_suggests_pull(monkeypatch):
    _patch(monkeypatch, models=("bge-m3",))
    chat = {c.name: c for c in doctor.run_checks(Settings())}["Chat model"]
    assert not chat.ok
    assert chat.fix == "ollama pull qwen3:14b"


def test_ollama_down_and_no_exiftool(monkeypatch):
    _patch(monkeypatch, exif=None, models=None)
    checks = {c.name: c for c in doctor.run_checks(Settings())}
    assert not checks["ExifTool"].ok
    assert not checks["Ollama"].ok
    assert "Embedding model" not in checks


def test_cloud_chat_model_is_not_checked_in_ollama(monkeypatch):
    _patch(monkeypatch, models=("bge-m3",))
    checks = {c.name: c for c in doctor.run_checks(Settings(model="anthropic:claude-sonnet-5-5"))}
    assert checks["Chat model"].ok
    assert "cloud" in checks["Chat model"].detail
```

`tests/test_cli_basic.py`:
```python
from typer.testing import CliRunner

from genea.cli.main import app
from genea.doctor import Check

runner = CliRunner()


def test_init_creates_meta_dir(tmp_path):
    result = runner.invoke(app, ["--kb", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".genea" / "config.toml").is_file()


def test_init_rejects_missing_folder(tmp_path):
    result = runner.invoke(app, ["--kb", str(tmp_path / "nope"), "init"])
    assert result.exit_code == 2


def test_doctor_exit_codes(monkeypatch):
    monkeypatch.setattr("genea.cli.kbcmds.run_checks",
                        lambda settings: [Check("ExifTool", False, "not found", "install it")])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "install it" in result.output

    monkeypatch.setattr("genea.cli.kbcmds.run_checks",
                        lambda settings: [Check("ExifTool", True, "13.33")])
    assert runner.invoke(app, ["doctor"]).exit_code == 0
```

- [ ] **Step 7: Run them to verify they fail**

Run: `uv run pytest tests/test_doctor.py tests/test_cli_basic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.doctor'`.

- [ ] **Step 8: Implement doctor and the CLI skeleton**

`src/genea/doctor.py`:
```python
"""Environment checks for `genea doctor`."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import httpx
import sqlite_vec

from genea.config import Settings
from genea.exiftool import exiftool_version


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def check_sqlite_vec() -> Check:
    try:
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        version = conn.execute("SELECT vec_version()").fetchone()[0]
        conn.close()
        return Check("sqlite-vec", True, version)
    except Exception as exc:  # noqa: BLE001 - any failure means the extension is unusable
        return Check("sqlite-vec", False, str(exc),
                     "Use a Python whose sqlite3 can load extensions (the uv-managed CPython can).")


def check_exiftool(exe: str) -> Check:
    version = exiftool_version(exe)
    if version:
        return Check("ExifTool", True, f"version {version}")
    return Check(
        "ExifTool", False, f"`{exe}` did not run",
        "Install ExifTool (`winget install OliverBetz.ExifTool`, or `choco install exiftool --force` "
        "to repair a Chocolatey install) or set exiftool = '<full path>' in config.toml.",
    )


def ollama_models(url: str, timeout: float = 5.0) -> list[str] | None:
    """Names of pulled Ollama models, or None if Ollama is unreachable."""
    try:
        response = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return [m["name"] for m in response.json().get("models", [])]


def _has_model(names: list[str], model: str) -> bool:
    return model in names or f"{model}:latest" in names


def run_checks(settings: Settings) -> list[Check]:
    checks = [check_sqlite_vec(), check_exiftool(settings.exiftool)]
    names = ollama_models(settings.ollama_url)
    if names is None:
        checks.append(Check("Ollama", False, f"not reachable at {settings.ollama_url}",
                            "Install Ollama from https://ollama.com and make sure it is running."))
        return checks
    checks.append(Check("Ollama", True, f"reachable at {settings.ollama_url}"))
    wanted = [("Embedding model", settings.embed_model)]
    if settings.model.startswith("ollama:"):
        wanted.append(("Chat model", settings.model.removeprefix("ollama:")))
    for label, model in wanted:
        if _has_model(names, model):
            checks.append(Check(label, True, model))
        else:
            checks.append(Check(label, False, f"{model} is not pulled", f"ollama pull {model}"))
    if not settings.model.startswith("ollama:"):
        checks.append(Check("Chat model", True,
                            f"{settings.model} (cloud; needs its API key in the environment)"))
    return checks
```

`src/genea/cli/app.py`:
```python
"""Typer application objects. Command modules register themselves on these."""

import typer

app = typer.Typer(no_args_is_help=True, help="Genealogy knowledge-base agent.")
changes_app = typer.Typer(no_args_is_help=True, help="Review and apply changes proposed by the agent.")
app.add_typer(changes_app, name="changes")


@app.callback()
def main(
    ctx: typer.Context,
    kb: str | None = typer.Option(
        None, "--kb", help="Knowledge-base folder, or a name from [kbs] in ~/.genea/config.toml."
    ),
) -> None:
    ctx.obj = {"kb": kb}
```

`src/genea/cli/common.py`:
```python
"""Helpers shared by CLI command modules."""

from __future__ import annotations

import typer
from rich.console import Console

from genea.config import KBNotFound
from genea.kb import KnowledgeBase

console = Console()


def open_kb(ctx: typer.Context) -> KnowledgeBase:
    try:
        return KnowledgeBase.open(ctx.obj["kb"])
    except KBNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
```

`src/genea/cli/kbcmds.py`:
```python
"""`genea init` and `genea doctor`."""

from __future__ import annotations

from pathlib import Path

import typer

from genea.cli.app import app
from genea.cli.common import console
from genea.config import KBNotFound, load_settings, resolve_kb_root
from genea.doctor import run_checks
from genea.kb import init_kb


@app.command()
def init(ctx: typer.Context) -> None:
    """Create .genea/ in the knowledge-base folder (--kb, default: current folder)."""
    root = Path(ctx.obj["kb"] or ".").expanduser()
    if not root.is_dir():
        console.print(f"[red]Folder not found: {root}[/red]")
        raise typer.Exit(2)
    kb = init_kb(root)
    console.print(f"Initialized [bold]{kb.meta_dir}[/bold]")


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check that ExifTool, Ollama and the configured models are available."""
    try:
        root = resolve_kb_root(ctx.obj["kb"])
    except KBNotFound:
        root = None
    failed = False
    for check in run_checks(load_settings(root)):
        mark = "[green]✓[/green]" if check.ok else "[red]✗[/red]"
        console.print(f"{mark} {check.name}: {check.detail}")
        if not check.ok:
            failed = True
            if check.fix:
                console.print(f"    fix: {check.fix}")
    raise typer.Exit(1 if failed else 0)
```

`src/genea/cli/main.py`:
```python
"""Console entry point: importing the command modules registers their commands."""

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from genea.cli import kbcmds  # noqa: E402,F401
from genea.cli.app import app  # noqa: E402

__all__ = ["app"]
```

- [ ] **Step 9: Run the full test suite**

Run: `uv run pytest -v`
Expected: all pass (ExifTool tests are skipped if the binary is missing).

Run: `uv run genea --help`
Expected: lists the `init`, `doctor` and `changes` commands.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: project scaffold, settings, knowledge-base layout, init and doctor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Lossless GEDCOM parser (encodings, ANSEL, round trip) and sample tree

**Files:**
- Create: `src/genea/ingest/__init__.py` (empty), `src/genea/ingest/gedcom/__init__.py` (empty), `src/genea/ingest/gedcom/ansel.py`, `src/genea/ingest/gedcom/parser.py`
- Create: `tests/fixtures/kb_sample/tree.ged`
- Test: `tests/test_ansel.py`, `tests/test_gedcom_parser.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `genea.ingest.gedcom.ansel.decode_ansel(data: bytes) -> str`
  - `genea.ingest.gedcom.parser.GedLine` (dataclass): `level: int, xref: str|None, tag: str, value: str|None, line_no: int, raw_bytes: bytes, children: list[GedLine]`, with methods `first(tag) -> GedLine|None`, `all(tag) -> list[GedLine]`, `text() -> str` (value + CONT/CONC)
  - `GedDocument`: `path, encoding: str, bom: bytes, lines: list[GedLine], records: list[GedLine], warnings: list[str]`, `serialize() -> bytes`
  - `parse_gedcom(path: Path) -> GedDocument`, `detect_encoding(data: bytes) -> tuple[str, bytes]`
  - Fixture `tests/fixtures/kb_sample/tree.ged`: 20 people, 6 families. It is used by every later task.

- [ ] **Step 1: Create the sample GEDCOM fixture**

`tests/fixtures/kb_sample/tree.ged` (UTF-8, LF line endings, exactly this content). Note that `1 CONC  obce.` has **two** spaces after `CONC`. I4 has a deliberate conflict: a residence in 1855 after death in 1849.

```
0 HEAD
1 SOUR GENEA_TEST
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Josef /Novák/
2 GIVN Josef
2 SURN Novák
1 NAME Joseph /Nowak/
1 SEX M
1 BIRT
2 DATE 12 MAR 1820
2 PLAC Brno
1 DEAT
2 DATE 1885
2 PLAC Brno
1 OCCU sedlák
1 NOTE @N1@
1 FAMS @F1@
0 @I2@ INDI
1 NAME Anna /Svobodová/
1 SEX F
1 BIRT
2 DATE ABT 1825
2 PLAC Tišnov
1 DEAT
2 DATE 3 JAN 1890
2 PLAC Brno
1 FAMS @F1@
0 @I3@ INDI
1 NAME Jan /Novák/
1 SEX M
1 BIRT
2 DATE 5 MAY 1846
2 PLAC Brno
2 SOUR @S1@
3 PAGE fol. 23
1 DEAT
2 DATE 1910
2 PLAC Brno
1 FAMC @F1@
1 FAMS @F2@
0 @I4@ INDI
1 NAME Marie /Nováková/
1 SEX F
1 BIRT
2 DATE 1848
2 PLAC Brno
1 DEAT
2 DATE 1849
2 PLAC Brno
1 RESI
2 DATE 1855
2 PLAC Brno
1 FAMC @F1@
0 @I5@ INDI
1 NAME František /Novák/
1 SEX M
1 BIRT
2 DATE BET 1850 AND 1852
2 PLAC Brno
1 FAMC @F1@
2 PEDI adopted
0 @I6@ INDI
1 NAME Terezie /Dvořáková/
1 SEX F
1 BIRT
2 DATE 1850
2 PLAC Tišnov
1 DEAT
2 DATE 1920
1 FAMS @F2@
0 @I7@ INDI
1 NAME Karel /Novák/
1 SEX M
1 BIRT
2 DATE 1871
2 PLAC Brno
1 FAMC @F2@
1 FAMS @F3@
0 @I8@ INDI
1 NAME Ludmila /Nováková/
1 SEX F
1 BIRT
2 DATE 1873
2 PLAC Brno
1 FAMC @F2@
1 FAMS @F4@
0 @I9@ INDI
1 NAME Václav /Novák/
1 SEX M
1 BIRT
2 DATE 1875
2 PLAC Brno
1 FAMC @F2@
0 @I10@ INDI
1 NAME Rosalie /Krejčí/
1 SEX F
1 BIRT
2 DATE 1874
2 PLAC Brno
1 FAMS @F3@
0 @I11@ INDI
1 NAME Jindřich /Horák/
1 SEX M
1 BIRT
2 DATE 1870
2 PLAC Brno
1 FAMS @F4@
0 @I12@ INDI
1 NAME Jan /Novák/
1 SEX M
1 BIRT
2 DATE 1896
2 PLAC Brno
1 FAMC @F3@
1 FAMS @F5@
0 @I13@ INDI
1 NAME Anna /Nováková/
1 SEX F
1 BIRT
2 DATE 1898
2 PLAC Brno
1 FAMC @F3@
0 @I14@ INDI
1 NAME Josef /Novák/
1 SEX M
1 BIRT
2 DATE 1900
2 PLAC Brno
1 FAMC @F3@
0 @I15@ INDI
1 NAME Emil /Horák/
1 SEX M
1 BIRT
2 DATE 1895
2 PLAC Brno
1 FAMC @F4@
1 FAMS @F6@
0 @I16@ INDI
1 NAME Marie /Dvořáková/
1 SEX F
1 BIRT
2 DATE 1900
2 PLAC Tišnov
1 FAMS @F5@
0 @I17@ INDI
1 NAME Jiří /Novák/
1 SEX M
1 BIRT
2 DATE 1934
2 PLAC Brno
1 FAMC @F5@
0 @I18@ INDI
1 NAME Eva /Nováková/
1 SEX F
1 BIRT
2 DATE 1936
2 PLAC Brno
1 FAMC @F5@
0 @I19@ INDI
1 NAME Petr /Horák/
1 SEX M
1 BIRT
2 DATE 1920
2 PLAC Brno
1 FAMC @F6@
0 @I20@ INDI
1 NAME Božena /Horáková/
1 SEX F
1 BIRT
2 DATE 1922
2 PLAC Brno
1 FAMC @F6@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1845
2 PLAC Brno
1 CHIL @I3@
1 CHIL @I4@
1 CHIL @I5@
0 @F2@ FAM
1 HUSB @I3@
1 WIFE @I6@
1 MARR
2 DATE 10 FEB 1870
2 PLAC Tišnov
1 CHIL @I7@
1 CHIL @I8@
1 CHIL @I9@
0 @F3@ FAM
1 HUSB @I7@
1 WIFE @I10@
1 MARR
2 DATE 1895
2 PLAC Brno
1 CHIL @I12@
1 CHIL @I13@
1 CHIL @I14@
0 @F4@ FAM
1 HUSB @I11@
1 WIFE @I8@
1 MARR
2 DATE 1894
2 PLAC Brno
1 CHIL @I15@
0 @F5@ FAM
1 HUSB @I12@
1 WIFE @I16@
1 MARR
2 DATE 12 JUN 1932
2 PLAC Brno
1 CHIL @I17@
1 CHIL @I18@
0 @F6@ FAM
1 HUSB @I15@
1 CHIL @I19@
1 CHIL @I20@
0 @N1@ NOTE Sedlák v Brně, uváděn v kronice
1 CONC  obce.
1 CONT Druhý řádek poznámky.
0 @S1@ SOUR
1 TITL Matrika narozených Brno 1840–1860
0 TRLR
```

- [ ] **Step 2: Write the failing tests**

`tests/test_ansel.py`:
```python
from genea.ingest.gedcom.ansel import decode_ansel


def test_ascii_passthrough():
    assert decode_ansel(b"0 HEAD") == "0 HEAD"


def test_combining_marks_precede_base_letter():
    assert decode_ansel(b"Nov\xe2ak") == "Novák"
    assert decode_ansel(b"Dvo\xe9rakov\xe2a") == "Dvořáková"
    assert decode_ansel(b"\xea") == "̊"  # dangling mark is kept


def test_special_characters():
    assert decode_ansel(b"\xa1od\xe2z") == "Łódź"
    assert decode_ansel(b"Stra\xcfe") == "Straße"


def test_unknown_byte_becomes_replacement_char():
    assert decode_ansel(b"a\x80b") == "a�b"
```

`tests/test_gedcom_parser.py`:
```python
from pathlib import Path

import pytest

from genea.ingest.gedcom.parser import detect_encoding, parse_gedcom

FIXTURE = Path(__file__).parent / "fixtures" / "kb_sample" / "tree.ged"
PRIVATE = sorted((Path(__file__).parent / "private").glob("*.ged"))


def _record(doc, xref):
    return next(r for r in doc.records if r.xref == xref)


def test_parses_fixture():
    doc = parse_gedcom(FIXTURE)
    assert doc.encoding == "utf-8"
    assert doc.warnings == []
    assert len([r for r in doc.records if r.tag == "INDI"]) == 20
    assert len([r for r in doc.records if r.tag == "FAM"]) == 6


def test_tree_structure_and_line_numbers():
    doc = parse_gedcom(FIXTURE)
    i1 = _record(doc, "@I1@")
    assert i1.line_no == 7
    birth = i1.first("BIRT")
    assert birth.first("DATE").value == "12 MAR 1820"
    assert birth.line_no == 13
    assert [n.value for n in i1.all("NAME")] == ["Josef /Novák/", "Joseph /Nowak/"]


def test_cont_and_conc():
    note = _record(parse_gedcom(FIXTURE), "@N1@")
    assert note.text() == "Sedlák v Brně, uváděn v kronice obce.\nDruhý řádek poznámky."


def test_round_trip_fixture():
    assert parse_gedcom(FIXTURE).serialize() == FIXTURE.read_bytes()


def test_round_trip_with_bom_crlf_and_bad_lines(tmp_path):
    data = (b"\xef\xbb\xbf0 HEAD\r\n1 CHAR UTF-8\r\ngarbage line\r\n\r\n"
            b"0 @I1@ INDI\r\n1 NAME Jan /Nov\xc3\xa1k/\r\n0 TRLR")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.serialize() == data
    assert doc.encoding == "utf-8"
    assert any(w.startswith("line 3:") for w in doc.warnings)
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Novák/"


def test_cp1250_declared_ansi(tmp_path):
    data = "0 HEAD\n1 CHAR ANSI\n0 @I1@ INDI\n1 NAME Jan /Dvořák/\n0 TRLR\n".encode("cp1250")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.encoding == "cp1250"
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Dvořák/"
    assert doc.serialize() == data


def test_invalid_utf8_falls_back_to_cp1250(tmp_path):
    data = "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Jan /Dvořák/\n0 TRLR\n".encode("cp1250")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.encoding == "cp1250"
    assert any("not valid UTF-8" in w for w in doc.warnings)
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Dvořák/"


def test_ansel_file(tmp_path):
    path = tmp_path / "x.ged"
    path.write_bytes(b"0 HEAD\n1 CHAR ANSEL\n0 @I1@ INDI\n1 NAME Jan /Nov\xe2ak/\n0 TRLR\n")
    doc = parse_gedcom(path)
    assert doc.encoding == "ansel"
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Novák/"


def test_utf16_with_bom(tmp_path):
    data = "﻿0 HEAD\r\n1 CHAR UNICODE\r\n0 @I1@ INDI\r\n1 NAME Jan /Novák/\r\n0 TRLR\r\n".encode("utf-16-le")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.encoding == "utf-16-le"
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Novák/"
    assert doc.serialize() == data


def test_level_jump_warns_but_keeps_line(tmp_path):
    path = tmp_path / "x.ged"
    path.write_bytes(b"0 HEAD\n0 @I1@ INDI\n2 DATE 1850\n0 TRLR\n")
    doc = parse_gedcom(path)
    assert any("level jumps" in w for w in doc.warnings)
    assert _record(doc, "@I1@").first("DATE").value == "1850"


def test_detect_encoding_defaults_to_cp1250():
    assert detect_encoding(b"0 HEAD\n0 TRLR\n") == ("cp1250", b"")


@pytest.mark.parametrize("path", PRIVATE, ids=lambda p: p.name)
def test_round_trip_private_files(path):
    """Drop real exports into tests/private/ to check they survive byte-for-byte."""
    assert parse_gedcom(path).serialize() == path.read_bytes()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ansel.py tests/test_gedcom_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.ingest'`.

- [ ] **Step 4: Implement the ANSEL decoder**

`src/genea/ingest/gedcom/ansel.py`:
```python
"""Decoder for ANSEL (ANSI Z39.47), the legacy GEDCOM 5.5 character set.

ANSEL writes combining diacritics *before* the base letter; Unicode puts them after.
"""

from __future__ import annotations

import unicodedata

_SPECIAL = {
    0xA1: "Ł", 0xA2: "Ø", 0xA3: "Đ", 0xA4: "Þ", 0xA5: "Æ", 0xA6: "Œ", 0xA7: "ʹ",
    0xA8: "·", 0xA9: "♭", 0xAA: "®", 0xAB: "±", 0xAC: "Ơ", 0xAD: "Ư", 0xAE: "ʼ",
    0xB0: "ʻ", 0xB1: "ł", 0xB2: "ø", 0xB3: "đ", 0xB4: "þ", 0xB5: "æ", 0xB6: "œ",
    0xB7: "ʺ", 0xB8: "ı", 0xB9: "£", 0xBA: "ð", 0xBC: "ơ", 0xBD: "ư", 0xC0: "°",
    0xC1: "ℓ", 0xC2: "℗", 0xC3: "©", 0xC4: "♯", 0xC5: "¿", 0xC6: "¡", 0xC7: "ß",
    0xC8: "€", 0xCF: "ß",
}

_COMBINING = {
    0xE0: "̉", 0xE1: "̀", 0xE2: "́", 0xE3: "̂", 0xE4: "̃",
    0xE5: "̄", 0xE6: "̆", 0xE7: "̇", 0xE8: "̈", 0xE9: "̌",
    0xEA: "̊", 0xEB: "︠", 0xEC: "︡", 0xED: "̕", 0xEE: "̋",
    0xEF: "̐", 0xF0: "̧", 0xF1: "̨", 0xF2: "̣", 0xF3: "̤",
    0xF4: "̥", 0xF5: "̳", 0xF6: "̲", 0xF7: "̦", 0xF8: "̜",
    0xF9: "̮", 0xFA: "︢", 0xFB: "︣", 0xFE: "̓",
}


def decode_ansel(data: bytes) -> str:
    out: list[str] = []
    pending: list[str] = []
    for byte in data:
        if byte in _COMBINING:
            pending.append(_COMBINING[byte])
            continue
        out.append(chr(byte) if byte < 0x80 else _SPECIAL.get(byte, "�"))
        out.extend(pending)
        pending.clear()
    out.extend(pending)
    return unicodedata.normalize("NFC", "".join(out))
```

- [ ] **Step 5: Implement the parser**

`src/genea/ingest/gedcom/parser.py`:
```python
"""Tolerant, lossless GEDCOM line-tree parser.

Every physical line keeps its original bytes (including the line terminator), so
`GedDocument.serialize()` reproduces the file byte-for-byte. Unknown tags are kept;
malformed lines are kept as `_INVALID` lines and reported as warnings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from genea.ingest.gedcom.ansel import decode_ansel

_LINE_RE = re.compile(r"^\s*(\d{1,2}) +(?:(@[^@\s][^@]*@) +)?([A-Za-z0-9_]+)(?: (.*))?$")
_SPLIT_BYTES_RE = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_SPLIT_TEXT_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_CHAR_RE = re.compile(rb"^\s*1 +CHAR +(\S+)", re.MULTILINE)
_BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))


@dataclass
class GedLine:
    level: int
    xref: str | None
    tag: str
    value: str | None
    line_no: int
    raw_bytes: bytes
    children: list[GedLine] = field(default_factory=list)

    def first(self, tag: str) -> GedLine | None:
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, tag: str) -> list[GedLine]:
        return [c for c in self.children if c.tag == tag]

    def text(self) -> str:
        """The value with CONT (new line) and CONC (append) continuation lines applied."""
        parts = [self.value or ""]
        for child in self.children:
            if child.tag == "CONT":
                parts.append("\n" + (child.value or ""))
            elif child.tag == "CONC":
                parts.append(child.value or "")
        return "".join(parts)


@dataclass
class GedDocument:
    path: Path
    encoding: str
    bom: bytes
    lines: list[GedLine]
    records: list[GedLine]
    warnings: list[str]

    def serialize(self) -> bytes:
        return self.bom + b"".join(line.raw_bytes for line in self.lines)


def detect_encoding(data: bytes) -> tuple[str, bytes]:
    """Return (encoding, bom). BOM > UTF-16 null-byte heuristic > `1 CHAR` header > cp1250."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding, bom
    head = data[:200]
    if b"\x00" in head:
        return ("utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"), b""
    match = _CHAR_RE.search(data[:4000])
    declared = match.group(1).decode("ascii", "replace").upper() if match else ""
    if declared in ("UTF-8", "UTF8", "ASCII"):
        return "utf-8", b""
    if declared == "ANSEL":
        return "ansel", b""
    return "cp1250", b""


def _split(body: bytes, encoding: str) -> list[bytes]:
    if encoding.startswith("utf-16"):
        text = body.decode(encoding)
        return [piece.encode(encoding) for piece in _SPLIT_TEXT_RE.findall(text)]
    return _SPLIT_BYTES_RE.findall(body)


def _decode(raw: bytes, encoding: str) -> str:
    if encoding == "ansel":
        return decode_ansel(raw)
    return raw.decode(encoding, errors="replace")


def parse_gedcom(path: Path) -> GedDocument:
    data = Path(path).read_bytes()
    encoding, bom = detect_encoding(data)
    body = data[len(bom):]
    warnings: list[str] = []
    if encoding == "utf-8":
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            warnings.append("file says UTF-8 but is not valid UTF-8; decoded as CP1250")
            encoding = "cp1250"

    lines: list[GedLine] = []
    records: list[GedLine] = []
    stack: list[GedLine] = []
    for line_no, raw in enumerate(_split(body, encoding), start=1):
        text = _decode(raw, encoding).rstrip("\r\n")
        if not text.strip():
            lines.append(GedLine(-1, None, "_BLANK", None, line_no, raw))
            continue
        match = _LINE_RE.match(text)
        if not match:
            warnings.append(f"line {line_no}: cannot parse {text[:60]!r}")
            lines.append(GedLine(-1, None, "_INVALID", text, line_no, raw))
            continue
        level = int(match.group(1))
        line = GedLine(level, match.group(2), match.group(3).upper(), match.group(4), line_no, raw)
        lines.append(line)
        while stack and stack[-1].level >= level:
            stack.pop()
        if level == 0:
            records.append(line)
        elif not stack:
            warnings.append(f"line {line_no}: level {level} line outside any record; ignored")
            continue
        else:
            if level > stack[-1].level + 1:
                warnings.append(f"line {line_no}: level jumps from {stack[-1].level} to {level}")
            stack[-1].children.append(line)
        stack.append(line)
    return GedDocument(Path(path), encoding, bom, lines, records, warnings)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ansel.py tests/test_gedcom_parser.py -v`
Expected: all pass. `test_round_trip_private_files` is reported as SKIPPED (empty parameter set) until you add files to `tests/private/`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: lossless GEDCOM parser with ANSEL/CP1250/UTF-16 support and sample tree

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: GEDCOM date parser

**Files:**
- Create: `src/genea/ingest/gedcom/dates.py`
- Test: `tests/test_gedcom_dates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `genea.ingest.gedcom.dates.DateRange` (frozen dataclass: `date_from: str|None`, `date_to: str|None`, `qualifier: str|None`; dates are ISO `YYYY-MM-DD`), `parse_gedcom_date(raw: str | None) -> DateRange`.

- [ ] **Step 1: Write the failing test**

`tests/test_gedcom_dates.py`:
```python
import pytest

from genea.ingest.gedcom.dates import DateRange, parse_gedcom_date

CASES = [
    ("12 MAR 1850", "1850-03-12", "1850-03-12", None),
    ("MAR 1850", "1850-03-01", "1850-03-31", None),
    ("FEB 1852", "1852-02-01", "1852-02-29", None),
    ("1850", "1850-01-01", "1850-12-31", None),
    ("abt 1850", "1845-01-01", "1855-12-31", "ABT"),
    ("EST 1850", "1845-01-01", "1855-12-31", "EST"),
    ("CAL 1850", "1845-01-01", "1855-12-31", "CAL"),
    ("ABT 29 FEB 1852", "1847-02-28", "1857-02-28", "ABT"),
    ("BEF 1850", "1750-01-01", "1850-12-31", "BEF"),
    ("AFT 1850", "1850-01-01", "1950-12-31", "AFT"),
    ("BET 1849 AND 1851", "1849-01-01", "1851-12-31", "BET"),
    ("FROM 1849 TO MAR 1851", "1849-01-01", "1851-03-31", "FROM"),
    ("FROM 1849", "1849-01-01", "1849-12-31", "FROM"),
    ("TO 1851", "1851-01-01", "1851-12-31", "TO"),
    ("11 FEB 1731/32", "1732-02-11", "1732-02-11", None),
    ("@#DJULIAN@ 1 JAN 1700", "1700-01-01", "1700-01-01", None),
    ("INT 1850 (the year of the flood)", "1850-01-01", "1850-12-31", "INT"),
    ("850", "0850-01-01", "0850-12-31", None),
]


@pytest.mark.parametrize("raw, start, end, qualifier", CASES)
def test_parse(raw, start, end, qualifier):
    assert parse_gedcom_date(raw) == DateRange(start, end, qualifier)


@pytest.mark.parametrize("raw", [None, "", "   ", "31 FEB 1850", "sometime", "@#DFRENCH R@ 1 VEND 11"])
def test_unparseable_gives_empty_range(raw):
    result = parse_gedcom_date(raw)
    assert result.date_from is None and result.date_to is None


def test_phrase():
    assert parse_gedcom_date("(after the war)") == DateRange(None, None, "PHRASE")


def test_qualifier_kept_when_inner_date_is_bad():
    assert parse_gedcom_date("ABT spring") == DateRange(None, None, "ABT")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_gedcom_dates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.ingest.gedcom.dates'`.

- [ ] **Step 3: Implement**

`src/genea/ingest/gedcom/dates.py`:
```python
"""GEDCOM 5.5.1 dates -> inclusive ISO date ranges.

ABT/CAL/EST widen by ±5 years, BEF reaches 100 years back, AFT 100 years forward.
Julian dates are treated as Gregorian (best effort); French/Hebrew calendars are not parsed.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_CAL_RE = re.compile(r"@#D([A-Z ]+)@\s*")
_SIMPLE_RE = re.compile(r"^(?:(\d{1,2})\s+)?(?:([A-Z]{3})\s+)?(\d{1,4})(?:/(\d{1,2}))?$")
_RANGE_RE = re.compile(r"^(BET|FROM)\s+(.+?)\s+(AND|TO)\s+(.+)$")


@dataclass(frozen=True)
class DateRange:
    date_from: str | None = None
    date_to: str | None = None
    qualifier: str | None = None


def _shift(d: date, years: int) -> date:
    year = min(max(d.year + years, 1), 9999)
    try:
        return d.replace(year=year)
    except ValueError:  # 29 Feb in a non-leap year
        return d.replace(year=year, day=28)


def _simple(text: str) -> tuple[date, date] | None:
    match = _SIMPLE_RE.match(text.strip())
    if not match:
        return None
    day, month, year_text, dual = match.groups()
    year = int(year_text)
    if dual:  # "1731/32" means 1732 in today's calendar
        year = int(year_text[: len(year_text) - len(dual)] + dual)
    if year < 1:
        return None
    if month is None:
        if day:
            return None
        return date(year, 1, 1), date(year, 12, 31)
    if month not in MONTHS:
        return None
    m = MONTHS[month]
    if day is None:
        return date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
    try:
        d = date(year, m, int(day))
    except ValueError:
        return None
    return d, d


def parse_gedcom_date(raw: str | None) -> DateRange:
    if not raw or not raw.strip():
        return DateRange()
    text = " ".join(raw.upper().split())
    if any(c.strip() not in ("GREGORIAN", "JULIAN") for c in _CAL_RE.findall(text)):
        return DateRange()
    text = _CAL_RE.sub("", text).strip()
    if text.startswith("("):
        return DateRange(qualifier="PHRASE")

    qualifier = None
    if text.startswith("INT "):
        qualifier = "INT"
        text = text[4:].split("(")[0].strip()

    match = _RANGE_RE.match(text)
    if match:
        start, end = _simple(match.group(2)), _simple(match.group(4))
        if start and end:
            return DateRange(start[0].isoformat(), end[1].isoformat(), match.group(1))
        return DateRange(qualifier=match.group(1))

    word, _, rest = text.partition(" ")
    if word in ("ABT", "CAL", "EST", "BEF", "AFT", "FROM", "TO") and rest:
        parsed = _simple(rest)
        if not parsed:
            return DateRange(qualifier=word)
        lo, hi = parsed
        if word in ("ABT", "CAL", "EST"):
            lo, hi = _shift(lo, -5), _shift(hi, 5)
        elif word == "BEF":
            lo = _shift(lo, -100)
        elif word == "AFT":
            hi = _shift(hi, 100)
        return DateRange(lo.isoformat(), hi.isoformat(), word)

    parsed = _simple(text)
    if parsed:
        return DateRange(parsed[0].isoformat(), parsed[1].isoformat(), qualifier)
    return DateRange(qualifier=qualifier)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_gedcom_dates.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: GEDCOM date parser producing ISO date ranges

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Name normalization and GEDCOM → records mapper

**Files:**
- Create: `src/genea/text.py`, `src/genea/ingest/gedcom/mapper.py`
- Test: `tests/test_text.py`, `tests/test_gedcom_mapper.py`

**Interfaces:**
- Consumes: `parse_gedcom`, `GedLine`, `GedDocument` (Task 2); `parse_gedcom_date`, `DateRange` (Task 3).
- Produces:
  - `genea.text.normalize(text: str) -> str`: lowercase, accents removed, punctuation → single spaces (`"Dvořáková"` → `"dvorakova"`).
  - `genea.ingest.gedcom.mapper`:
    - `PersonRec(xref, given, surname, sex, name_variants: list[str], notes: str, line_no)`
    - `EventRec(owner_xref, type, description, date_raw, date: DateRange, place, citations: list[dict], line_no)`
    - `FamilyRec(xref, husband_xref, wife_xref, children: list[tuple[str, str]], line_no)`, where children are `(child_xref, pedigree)`
    - `GedcomData(persons, families, events, warnings)`, `map_gedcom(doc) -> GedcomData`, `split_name(value) -> tuple[str, str]`
    - constants `INDIVIDUAL_EVENT_TAGS`, `FAMILY_EVENT_TAGS`

- [ ] **Step 1: Write the failing tests**

`tests/test_text.py`:
```python
from genea.text import normalize


def test_normalize():
    assert normalize("Dvořáková") == "dvorakova"
    assert normalize("  Łódź,  Nowak-Ślązak ") == "lodz nowak slazak"
    assert normalize("Straße") == "strasse"
    assert normalize("") == ""
```

`tests/test_gedcom_mapper.py`:
```python
from pathlib import Path

from genea.ingest.gedcom.mapper import map_gedcom, split_name
from genea.ingest.gedcom.parser import parse_gedcom

FIXTURE = Path(__file__).parent / "fixtures" / "kb_sample" / "tree.ged"
DATA = map_gedcom(parse_gedcom(FIXTURE))
PERSONS = {p.xref: p for p in DATA.persons}
FAMILIES = {f.xref: f for f in DATA.families}


def _events(owner, tag):
    return [e for e in DATA.events if e.owner_xref == owner and e.type == tag]


def test_counts():
    assert len(PERSONS) == 20
    assert len(FAMILIES) == 6
    assert DATA.warnings == []


def test_person_names_variants_notes():
    josef = PERSONS["@I1@"]
    assert (josef.given, josef.surname, josef.sex) == ("Josef", "Novák", "M")
    assert josef.name_variants == ["Josef Novák", "Joseph Nowak"]
    assert josef.notes == "Sedlák v Brně, uváděn v kronice obce.\nDruhý řádek poznámky."
    assert josef.line_no == 7


def test_event_with_citation():
    [birth] = _events("@I3@", "BIRT")
    assert birth.date_raw == "5 MAY 1846"
    assert (birth.date.date_from, birth.date.date_to) == ("1846-05-05", "1846-05-05")
    assert birth.place == "Brno"
    assert birth.citations == [
        {"source": "Matrika narozených Brno 1840–1860", "xref": "@S1@", "page": "fol. 23"}
    ]


def test_occupation_description():
    [occu] = _events("@I1@", "OCCU")
    assert occu.description == "sedlák"


def test_families_children_and_pedigree():
    f1 = FAMILIES["@F1@"]
    assert (f1.husband_xref, f1.wife_xref) == ("@I1@", "@I2@")
    assert f1.children == [("@I3@", "birth"), ("@I4@", "birth"), ("@I5@", "adopted")]
    assert FAMILIES["@F6@"].wife_xref is None


def test_family_event():
    [marr] = _events("@F2@", "MARR")
    assert marr.date.date_from == "1870-02-10"
    assert marr.place == "Tišnov"


def test_split_name():
    assert split_name("Jan /Novák/") == ("Jan", "Novák")
    assert split_name("Jan") == ("Jan", "")
    assert split_name("/Novák/") == ("", "Novák")
    assert split_name("") == ("", "")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_text.py tests/test_gedcom_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/genea/text.py`:
```python
"""Text normalization used for name matching and searching."""

from __future__ import annotations

import re
import unicodedata

_EXTRA = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "đ": "d",
                        "Đ": "D", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE"})


def normalize(text: str) -> str:
    """Lowercase, strip accents, turn punctuation into single spaces."""
    text = unicodedata.normalize("NFKD", text.translate(_EXTRA))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())
```

`src/genea/ingest/gedcom/mapper.py`:
```python
"""Map a parsed GEDCOM line tree to person, family and event records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from genea.ingest.gedcom.dates import DateRange, parse_gedcom_date
from genea.ingest.gedcom.parser import GedDocument, GedLine

INDIVIDUAL_EVENT_TAGS = frozenset({
    "BIRT", "CHR", "BAPM", "DEAT", "BURI", "CREM", "ADOP", "RESI", "OCCU", "EDUC", "EMIG",
    "IMMI", "NATU", "CENS", "PROB", "WILL", "GRAD", "RETI", "EVEN", "CONF", "FCOM", "ORDN", "RELI",
})
FAMILY_EVENT_TAGS = frozenset({"MARR", "DIV", "ENGA", "MARB", "MARC", "MARL", "ANUL", "EVEN", "CENS", "RESI"})


@dataclass
class PersonRec:
    xref: str
    given: str
    surname: str
    sex: str | None
    name_variants: list[str]
    notes: str
    line_no: int


@dataclass
class EventRec:
    owner_xref: str
    type: str
    description: str | None
    date_raw: str | None
    date: DateRange
    place: str | None
    citations: list[dict]
    line_no: int


@dataclass
class FamilyRec:
    xref: str
    husband_xref: str | None
    wife_xref: str | None
    children: list[tuple[str, str]]
    line_no: int


@dataclass
class GedcomData:
    persons: list[PersonRec] = field(default_factory=list)
    families: list[FamilyRec] = field(default_factory=list)
    events: list[EventRec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def split_name(value: str) -> tuple[str, str]:
    """'Jan /Novák/' -> ('Jan', 'Novák')."""
    match = re.match(r"^(.*?)/(.*?)/(.*)$", value or "")
    if not match:
        return (value or "").strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def _citations(line: GedLine, sources: dict[str, str]) -> list[dict]:
    result = []
    for sour in line.all("SOUR"):
        value = sour.value or ""
        if value.startswith("@"):
            entry = {"source": sources.get(value, value), "xref": value}
        else:
            entry = {"source": sour.text()}
        page = sour.first("PAGE")
        if page is not None and page.value:
            entry["page"] = page.text()
        result.append(entry)
    return result


def _event(owner: str, line: GedLine, sources: dict[str, str]) -> EventRec:
    date_line, place_line, type_line = line.first("DATE"), line.first("PLAC"), line.first("TYPE")
    description = line.text() if line.value else (type_line.value if type_line else None)
    raw = date_line.value if date_line else None
    return EventRec(
        owner_xref=owner,
        type=line.tag,
        description=description or None,
        date_raw=raw,
        date=parse_gedcom_date(raw),
        place=place_line.value if place_line else None,
        citations=_citations(line, sources),
        line_no=line.line_no,
    )


def _value(line: GedLine | None) -> str | None:
    return line.value if line is not None and line.value else None


def map_gedcom(doc: GedDocument) -> GedcomData:
    notes = {r.xref: r.text() for r in doc.records if r.tag == "NOTE" and r.xref}
    sources = {
        r.xref: (r.first("TITL").text() if r.first("TITL") else r.xref)
        for r in doc.records if r.tag == "SOUR" and r.xref
    }
    data = GedcomData(warnings=list(doc.warnings))
    pedigree: dict[tuple[str, str], str] = {}

    for record in doc.records:
        if record.tag == "INDI" and record.xref:
            names = record.all("NAME")
            given, surname = split_name(names[0].value or "") if names else ("", "")
            if names:
                given = _value(names[0].first("GIVN")) or given
                surname = _value(names[0].first("SURN")) or surname
            variants: list[str] = []
            for name in names:
                full = " ".join(" ".join(split_name(name.value or "")).split())
                if full and full not in variants:
                    variants.append(full)
            note_texts = [
                notes.get(n.value, "") if (n.value or "").startswith("@") else n.text()
                for n in record.all("NOTE")
            ]
            data.persons.append(PersonRec(
                xref=record.xref, given=given, surname=surname, sex=_value(record.first("SEX")),
                name_variants=variants, notes="\n".join(t for t in note_texts if t),
                line_no=record.line_no,
            ))
            for child in record.children:
                if child.tag in INDIVIDUAL_EVENT_TAGS:
                    data.events.append(_event(record.xref, child, sources))
                elif child.tag == "FAMC" and child.value:
                    pedi = _value(child.first("PEDI"))
                    if pedi:
                        pedigree[(child.value, record.xref)] = pedi.lower()
        elif record.tag == "FAM" and record.xref:
            data.families.append(FamilyRec(
                xref=record.xref,
                husband_xref=_value(record.first("HUSB")),
                wife_xref=_value(record.first("WIFE")),
                children=[(c.value, "birth") for c in record.all("CHIL") if c.value],
                line_no=record.line_no,
            ))
            for child in record.children:
                if child.tag in FAMILY_EVENT_TAGS:
                    data.events.append(_event(record.xref, child, sources))

    for family in data.families:
        family.children = [(c, pedigree.get((family.xref, c), "birth")) for c, _ in family.children]
    return data
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_text.py tests/test_gedcom_mapper.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: name normalization and GEDCOM record mapper

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Index database and embedders

**Files:**
- Create: `src/genea/index/__init__.py` (empty), `src/genea/index/db.py`, `src/genea/index/embed.py`
- Test: `tests/test_index_db.py`, `tests/test_embed.py`

**Interfaces:**
- Consumes: `KnowledgeBase` (Task 1), `Settings` (Task 1), `normalize` (Task 4).
- Produces:
  - `genea.index.db.connect(path: Path, embed_model: str, embed_dim: int) -> sqlite3.Connection` (row_factory=`sqlite3.Row`, foreign keys on, sqlite-vec loaded, schema created). Raises `SchemaMismatch` with "Run `genea index --rebuild`" when the stored schema version, embed model or dim differ.
  - `open_index(kb, embedder) -> sqlite3.Connection`, `SCHEMA_VERSION = "1"`
  - Tables: `meta, files, persons, families, family_children, events, chunks, chunks_fts (fts5), chunks_vec (vec0), images, links`. Column names are exactly as in the SQL below.
  - `genea.index.embed`: `Embedder` (Protocol with `model: str`, `dim: int`, `embed(texts) -> list[list[float]]`), `OllamaEmbedder(url, model, dim, timeout=120.0, client=None)`, `FakeEmbedder(dim=64)` (`model == "fake"`), `EmbeddingError`, `make_embedder(settings) -> OllamaEmbedder`

- [ ] **Step 1: Write the failing tests**

`tests/test_index_db.py`:
```python
import pytest
import sqlite_vec

from genea.index.db import SchemaMismatch, connect

TABLES = {"meta", "files", "persons", "families", "family_children", "events", "chunks",
          "chunks_fts", "chunks_vec", "images", "links"}


def test_connect_creates_schema(tmp_path):
    conn = connect(tmp_path / "i.db", "fake", 8)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert TABLES <= names
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    assert meta == {"schema_version": "1", "embed_model": "fake", "embed_dim": "8"}
    conn.close()


def test_reopen_and_mismatch(tmp_path):
    path = tmp_path / "i.db"
    connect(path, "fake", 8).close()
    connect(path, "fake", 8).close()
    with pytest.raises(SchemaMismatch, match="--rebuild"):
        connect(path, "fake", 16)
    with pytest.raises(SchemaMismatch, match="embed_model"):
        connect(path, "bge-m3", 8)


def test_vector_search(tmp_path):
    conn = connect(tmp_path / "i.db", "fake", 3)
    conn.execute("INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                 (1, sqlite_vec.serialize_float32([1.0, 0.0, 0.0])))
    conn.execute("INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                 (2, sqlite_vec.serialize_float32([0.0, 1.0, 0.0])))
    rows = conn.execute(
        "SELECT chunk_id FROM chunks_vec WHERE embedding MATCH ? AND k = 2 ORDER BY distance",
        (sqlite_vec.serialize_float32([0.9, 0.1, 0.0]),),
    ).fetchall()
    assert [r["chunk_id"] for r in rows] == [1, 2]


def test_fts_ignores_diacritics(tmp_path):
    conn = connect(tmp_path / "i.db", "fake", 3)
    conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (5, 'Terezie Dvořáková 1851')")
    rows = conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ('"dvorakova"',)).fetchall()
    assert [r[0] for r in rows] == [5]
```

`tests/test_embed.py`:
```python
import math

import httpx
import pytest

from genea.index.embed import EmbeddingError, FakeEmbedder, OllamaEmbedder


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_fake_embedder_is_deterministic_and_normalized():
    e = FakeEmbedder(dim=32)
    a, b, c = e.embed(["Jan Novák Brno", "jan novak brno", "zcela jiny text o kocce"])
    assert a == b
    assert math.isclose(_dot(a, a), 1.0, rel_tol=1e-9)
    assert _dot(a, b) > _dot(a, c)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_embedder_success():
    def handler(request):
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    e = OllamaEmbedder("http://ollama:11434", "bge-m3", 2, client=_client(handler))
    assert e.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert e.embed([]) == []


def test_ollama_embedder_http_error():
    e = OllamaEmbedder("http://ollama:11434", "bge-m3", 2,
                       client=_client(lambda r: httpx.Response(404, json={"error": "model not found"})))
    with pytest.raises(EmbeddingError, match="genea doctor"):
        e.embed(["a"])


def test_ollama_embedder_wrong_dimension():
    e = OllamaEmbedder("http://ollama:11434", "bge-m3", 3,
                       client=_client(lambda r: httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})))
    with pytest.raises(EmbeddingError, match="3 dimensions"):
        e.embed(["a"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_index_db.py tests/test_embed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.index'`.

- [ ] **Step 3: Implement the database module**

`src/genea/index/db.py`:
```python
"""SQLite index: relational tables + FTS5 + sqlite-vec, one file per knowledge base."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import sqlite_vec

if TYPE_CHECKING:
    from genea.index.embed import Embedder
    from genea.kb import KnowledgeBase

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mtime REAL NOT NULL,
    indexed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    error_msg TEXT
);
CREATE TABLE persons (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ged_file TEXT NOT NULL,
    xref TEXT NOT NULL,
    given TEXT,
    surname TEXT,
    sex TEXT,
    name_norm TEXT NOT NULL,
    names_norm TEXT NOT NULL,
    name_variants TEXT NOT NULL,
    notes TEXT,
    line_no INTEGER NOT NULL,
    UNIQUE (ged_file, xref)
);
CREATE INDEX persons_name_norm ON persons (name_norm);
CREATE TABLE families (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ged_file TEXT NOT NULL,
    xref TEXT NOT NULL,
    husband_xref TEXT,
    wife_xref TEXT,
    line_no INTEGER NOT NULL,
    UNIQUE (ged_file, xref)
);
CREATE TABLE family_children (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ged_file TEXT NOT NULL,
    family_xref TEXT NOT NULL,
    child_xref TEXT NOT NULL,
    pedigree TEXT NOT NULL
);
CREATE INDEX family_children_child ON family_children (ged_file, child_xref);
CREATE INDEX family_children_family ON family_children (ged_file, family_xref);
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ged_file TEXT NOT NULL,
    owner_xref TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    date_raw TEXT,
    date_from TEXT,
    date_to TEXT,
    date_qualifier TEXT,
    place TEXT,
    source_citations TEXT NOT NULL,
    line_no INTEGER NOT NULL
);
CREATE INDEX events_owner ON events (ged_file, owner_xref);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page INTEGER,
    chunk_no INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sidecar_path TEXT,
    title TEXT,
    description TEXT,
    date_taken TEXT,
    keywords TEXT NOT NULL,
    persons_shown TEXT NOT NULL,
    place TEXT,
    gps_lat REAL,
    gps_lon REAL,
    search_norm TEXT NOT NULL
);
CREATE TABLE links (
    id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    person_ged_file TEXT NOT NULL,
    person_xref TEXT NOT NULL,
    confidence REAL NOT NULL
);
CREATE INDEX links_person ON links (person_ged_file, person_xref);
CREATE INDEX links_source ON links (source_kind, source_id);
"""


class SchemaMismatch(RuntimeError):
    """The index was built with another schema or embedding model."""


def connect(path: Path, embed_model: str, embed_dim: int) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    expected = {"schema_version": SCHEMA_VERSION, "embed_model": embed_model, "embed_dim": str(embed_dim)}
    has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone()
    if has_meta is None:
        conn.executescript(_SCHEMA)
        conn.execute(
            f"CREATE VIRTUAL TABLE chunks_vec USING vec0("
            f"chunk_id INTEGER PRIMARY KEY, embedding float[{int(embed_dim)}])"
        )
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", expected.items())
        conn.commit()
        return conn
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    for key, value in expected.items():
        if stored.get(key) != value:
            conn.close()
            raise SchemaMismatch(
                f"The index was built with {key}={stored.get(key)!r} but the current setting is "
                f"{value!r}. Run `genea index --rebuild`."
            )
    return conn


def open_index(kb: KnowledgeBase, embedder: Embedder) -> sqlite3.Connection:
    return connect(kb.index_path, embedder.model, embedder.dim)
```

- [ ] **Step 4: Implement the embedders**

`src/genea/index/embed.py`:
```python
"""Text embedders: Ollama for real use, a deterministic fake for tests."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import httpx

from genea.config import Settings
from genea.text import normalize


class EmbeddingError(RuntimeError):
    """Embedding failed (Ollama down, model missing, unexpected output)."""


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, url: str, model: str, dim: int, timeout: float = 120.0,
                 client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self.client = client or httpx.Client()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self.client.post(f"{self.url}/api/embed",
                                        json={"model": self.model, "input": texts},
                                        timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"Ollama embedding failed ({exc}). Is Ollama running and `{self.model}` pulled? "
                "Run `genea doctor`."
            ) from exc
        vectors = response.json().get("embeddings", [])
        if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
            raise EmbeddingError(
                f"{self.model} returned unexpected embeddings; expected {self.dim} dimensions. "
                "Check embed_model/embed_dim in config.toml."
            )
        return vectors


class FakeEmbedder:
    """Hashed bag-of-words vectors: deterministic, offline, good enough for tests."""

    model = "fake"

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in normalize(text).split():
                vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            result.append([x / norm for x in vector])
        return result


def make_embedder(settings: Settings) -> OllamaEmbedder:
    return OllamaEmbedder(settings.ollama_url, settings.embed_model, settings.embed_dim)
```

- [ ] **Step 5: Run them to verify they pass**

Run: `uv run pytest tests/test_index_db.py tests/test_embed.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: SQLite index schema with FTS5 and sqlite-vec, Ollama and fake embedders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Document extraction, chunking and the sample knowledge-base builder

**Files:**
- Create: `src/genea/ingest/docs.py`
- Create: `tests/fixtures/__init__.py` (empty), `tests/fixtures/make_kb.py`
- Modify: `tests/conftest.py` (add the `sample_root`, `kb` and `kb_with_images` fixtures)
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: `KnowledgeBase`, `init_kb` (Task 1); fixture `tree.ged` (Task 2).
- Produces:
  - `genea.ingest.docs`: `DOC_EXTS`, `Page(number: int|None, text: str)`, `DocExtract(pages: list[Page], needs_ocr: bool=False)`, `Chunk(page: int|None, chunk_no: int, text: str)`, `extract_document(path) -> DocExtract` (raises `ValueError` for unsupported types), `chunk_pages(pages, size=3200, overlap=400) -> list[Chunk]`
  - `tests/fixtures/make_kb.py`: `build_sample_kb(root: Path) -> Path` writes `tree.ged`, `docs/{kronika.txt, poznamky.md, dopis.docx, matrika.pdf, sken.pdf}` and `photos/{svatba_1932.jpg + svatba_1932.xmp, anna_1935.jpg (EXIF), dum.jpg}`
  - pytest fixtures: `sample_root` (built sample folder), `kb` (KnowledgeBase with `exclude=("photos/**",)`), `kb_with_images` (no exclude)

- [ ] **Step 1: Write the sample knowledge-base builder**

`tests/fixtures/make_kb.py`:
```python
"""Build the sample knowledge base used across the test suite."""

from __future__ import annotations

import shutil
from pathlib import Path

import docx
from PIL import Image, ExifTags
from reportlab.pdfgen import canvas

FIXTURES = Path(__file__).parent

KRONIKA = (
    "Kronika obce Brno.\n\n"
    "Josef Novák, sedlák v Brně, se roku 1845 oženil s Annou Svobodovou. "
    "Jejich syn Jan Novák se narodil roku 1846.\n"
)
POZNAMKY = "# Poznámky\n\nTerezie Dvořáková se narodila roku 1851 v Tišnově.\n"
DOPIS = "Milý Karle Nováku, blahopřeji ke svatbě v roce 1895. Karel Novák a Rosalie Krejčí."

XMP = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'>
 <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
  <rdf:Description rdf:about=''
    xmlns:dc='http://purl.org/dc/elements/1.1/'
    xmlns:Iptc4xmpExt='http://iptc.org/std/Iptc4xmpExt/2008-02-29/'
    xmlns:photoshop='http://ns.adobe.com/photoshop/1.0/'>
   <dc:title><rdf:Alt><rdf:li xml:lang='x-default'>Svatba Jana a Marie</rdf:li></rdf:Alt></dc:title>
   <dc:description><rdf:Alt><rdf:li xml:lang='x-default'>Svatba Jana Nováka a Marie Dvořákové, Brno 1932</rdf:li></rdf:Alt></dc:description>
   <dc:subject><rdf:Bag><rdf:li>svatba</rdf:li><rdf:li>Brno</rdf:li></rdf:Bag></dc:subject>
   <Iptc4xmpExt:PersonInImage><rdf:Bag><rdf:li>Jan Novák</rdf:li><rdf:li>Marie Dvořáková</rdf:li></rdf:Bag></Iptc4xmpExt:PersonInImage>
   <photoshop:City>Brno</photoshop:City>
   <photoshop:DateCreated>1932-06-12</photoshop:DateCreated>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>
"""


def _pdf(path: Path, pages: list[list[str]]) -> None:
    pdf = canvas.Canvas(str(path))
    for lines in pages:
        y = 720
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()


def build_sample_kb(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "kb_sample" / "tree.ged", root / "tree.ged")

    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "kronika.txt").write_text(KRONIKA, encoding="utf-8")
    (docs / "poznamky.md").write_text(POZNAMKY, encoding="utf-8")
    letter = docx.Document()
    letter.add_paragraph(DOPIS)
    letter.save(str(docs / "dopis.docx"))
    # Built-in PDF fonts cannot show Czech háčeks, so the PDF text is plain ASCII.
    _pdf(docs / "matrika.pdf", [["Matrika Brno 1846: Jan Novak, syn Josefa Novaka."],
                                ["1875 pokrten Vaclav Novak v Brne."]])
    scan = canvas.Canvas(str(docs / "sken.pdf"))  # an "image only" page: no text layer
    scan.rect(50, 50, 200, 200, fill=1)
    scan.showPage()
    scan.save()

    photos = root / "photos"
    photos.mkdir(exist_ok=True)
    Image.new("RGB", (16, 16), "white").save(photos / "svatba_1932.jpg")
    (photos / "svatba_1932.xmp").write_text(XMP, encoding="utf-8")
    image = Image.new("RGB", (16, 16), "gray")
    exif = image.getexif()
    exif[ExifTags.Base.ImageDescription] = "Anna Novakova 1935"
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = "1935:05:01 12:00:00"
    image.save(photos / "anna_1935.jpg", exif=exif)
    Image.new("RGB", (16, 16), "blue").save(photos / "dum.jpg")
    return root
```

`tests/fixtures/__init__.py`: empty file.

Append to `tests/conftest.py`:
```python
from dataclasses import replace

from fixtures.make_kb import build_sample_kb

from genea.kb import KnowledgeBase, init_kb


@pytest.fixture
def sample_root(tmp_path):
    return build_sample_kb(tmp_path / "kb")


@pytest.fixture
def kb(sample_root):
    """Sample knowledge base with photos excluded, so no ExifTool is needed."""
    base = init_kb(sample_root)
    return KnowledgeBase(base.root, replace(base.settings, exclude=("photos/**",)))


@pytest.fixture
def kb_with_images(sample_root):
    return init_kb(sample_root)
```
(Move the new imports to the top of `conftest.py` next to the existing ones.)

- [ ] **Step 2: Write the failing test**

`tests/test_docs.py`:
```python
import pytest

from genea.ingest.docs import Chunk, Page, chunk_pages, extract_document


def test_text_markdown_docx(sample_root):
    txt = extract_document(sample_root / "docs" / "kronika.txt")
    assert txt.pages[0].number is None
    assert "Josef Novák" in txt.pages[0].text
    assert "Terezie Dvořáková" in extract_document(sample_root / "docs" / "poznamky.md").pages[0].text
    assert "Karel Novák" in extract_document(sample_root / "docs" / "dopis.docx").pages[0].text


def test_pdf_pages(sample_root):
    pdf = extract_document(sample_root / "docs" / "matrika.pdf")
    assert [p.number for p in pdf.pages] == [1, 2]
    assert "Vaclav Novak" in pdf.pages[1].text
    assert not pdf.needs_ocr


def test_scanned_pdf_needs_ocr(sample_root):
    scan = extract_document(sample_root / "docs" / "sken.pdf")
    assert scan.needs_ocr
    assert scan.pages == []


def test_cp1250_text_fallback(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes("Jan Dvořák".encode("cp1250"))
    assert extract_document(path).pages[0].text == "Jan Dvořák"


def test_unsupported_type(tmp_path):
    path = tmp_path / "x.rtf"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        extract_document(path)


def test_chunking_long_page():
    text = " ".join(f"slovo{i}" for i in range(3000))
    chunks = chunk_pages([Page(7, text)])
    assert len(chunks) > 1
    assert all(len(c.text) <= 3200 for c in chunks)
    assert [c.chunk_no for c in chunks] == list(range(len(chunks)))
    assert all(c.page == 7 for c in chunks)
    last_word = chunks[0].text.split()[-1]
    assert last_word in chunks[1].text  # overlap


def test_chunking_short_pages_continue_numbering():
    assert chunk_pages([Page(1, " short "), Page(2, "second")]) == [
        Chunk(1, 0, "short"), Chunk(2, 1, "second")]
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_docs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.ingest.docs'`.

- [ ] **Step 4: Implement**

`src/genea/ingest/docs.py`:
```python
"""Text extraction (txt/md/docx/pdf) and chunking."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import docx
from pypdf import PdfReader

DOC_EXTS = frozenset({".txt", ".md", ".docx", ".pdf"})


@dataclass
class Page:
    number: int | None
    text: str


@dataclass
class DocExtract:
    pages: list[Page] = field(default_factory=list)
    needs_ocr: bool = False


@dataclass
class Chunk:
    page: int | None
    chunk_no: int
    text: str


def _read_text_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1250", errors="replace")


def _single(text: str) -> DocExtract:
    return DocExtract([Page(None, text)] if text.strip() else [])


def extract_document(path: Path) -> DocExtract:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return _single(_read_text_file(path))
    if suffix == ".docx":
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return _single("\n".join(p for p in parts if p.strip()))
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(Page(number, text))
        return DocExtract(pages, needs_ocr=bool(reader.pages) and not pages)
    raise ValueError(f"Unsupported document type: {path.suffix}")


def chunk_pages(pages: list[Page], size: int = 3200, overlap: int = 400) -> list[Chunk]:
    """Split pages into ~size-character chunks (~800 tokens) that overlap by `overlap` characters."""
    chunks: list[Chunk] = []
    for page in pages:
        text = page.text.strip()
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                cut = text.rfind(" ", start + size - 400, end)
                if cut > start:
                    end = cut
            piece = text[start:end].strip()
            if piece:
                chunks.append(Chunk(page.number, len(chunks), piece))
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return chunks
```

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_docs.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: document text extraction, chunking and sample knowledge-base fixture

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Image metadata extraction

**Files:**
- Create: `src/genea/ingest/images.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Consumes: `read_metadata` (Task 1); `sample_root` fixture (Task 6).
- Produces: `genea.ingest.images`:
  - `IMAGE_EXTS`
  - `ImageRecord(sidecar: Path|None, title, description, date_taken, keywords: list[str], persons_shown: list[str], place, gps_lat, gps_lon)`
  - `sidecar_for(image) -> Path|None`, `default_sidecar(image) -> Path`
  - `normalize_exif_date(value) -> str|None`
  - `map_metadata(md: dict, sidecar: Path|None) -> ImageRecord`
  - `extract_image(path, exe="exiftool") -> ImageRecord`

- [ ] **Step 1: Write the failing test**

`tests/test_images.py`:
```python
import pytest

from genea.ingest.images import (default_sidecar, extract_image, map_metadata,
                                 normalize_exif_date, sidecar_for)


def test_map_metadata_prefers_xmp_and_merges_keywords():
    md = {
        "XMP-dc:Title": "Svatba", "IPTC:ObjectName": "old title",
        "XMP-dc:Description": "Wedding", "ExifIFD:DateTimeOriginal": "1932:06:12 10:00:00",
        "XMP-dc:Subject": ["svatba", "Brno"], "IPTC:Keywords": "svatba",
        "XMP-iptcExt:PersonInImage": ["Jan Novák", "Marie Dvořáková"],
        "XMP-photoshop:City": "Brno", "Composite:GPSLatitude": 49.19, "Composite:GPSLongitude": 16.6,
    }
    rec = map_metadata(md, None)
    assert rec.title == "Svatba"
    assert rec.description == "Wedding"
    assert rec.date_taken == "1932-06-12"
    assert rec.keywords == ["svatba", "Brno"]
    assert rec.persons_shown == ["Jan Novák", "Marie Dvořáková"]
    assert rec.place == "Brno"
    assert (rec.gps_lat, rec.gps_lon) == (49.19, 16.6)


def test_map_metadata_single_values_and_numbers():
    rec = map_metadata({"XMP-dc:Title": 1932, "IPTC:Keywords": "rodina",
                        "IFD0:ImageDescription": "Anna"}, None)
    assert rec.title == "1932"
    assert rec.keywords == ["rodina"]
    assert rec.description == "Anna"
    assert rec.date_taken is None


@pytest.mark.parametrize("value, expected", [
    ("1932:06:12 10:00:00", "1932-06-12"), ("1932-06-12", "1932-06-12"),
    ("1932:06:00 00:00:00", "1932-06"), ("1932:00:00", "1932"), ("1932", "1932"),
    ("unknown", None), (None, None),
])
def test_normalize_exif_date(value, expected):
    assert normalize_exif_date(value) == expected


def test_sidecar_conventions(tmp_path):
    image = tmp_path / "a.jpg"
    image.write_bytes(b"x")
    assert sidecar_for(image) is None
    assert default_sidecar(image) == tmp_path / "a.xmp"
    (tmp_path / "a.jpg.xmp").write_text("x", encoding="utf-8")
    assert sidecar_for(image) == tmp_path / "a.jpg.xmp"
    (tmp_path / "a.xmp").write_text("x", encoding="utf-8")
    assert sidecar_for(image) == tmp_path / "a.xmp"


@pytest.mark.exiftool
def test_extract_fixture_images(sample_root):
    photos = sample_root / "photos"
    wedding = extract_image(photos / "svatba_1932.jpg")
    assert wedding.sidecar == photos / "svatba_1932.xmp"
    assert wedding.title == "Svatba Jana a Marie"
    assert wedding.persons_shown == ["Jan Novák", "Marie Dvořáková"]
    assert wedding.place == "Brno"
    assert wedding.date_taken == "1932-06-12"
    anna = extract_image(photos / "anna_1935.jpg")
    assert anna.date_taken == "1935-05-01"
    assert anna.description == "Anna Novakova 1935"
    house = extract_image(photos / "dum.jpg")
    assert (house.title, house.keywords, house.persons_shown) == (None, [], [])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_images.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.ingest.images'`.

- [ ] **Step 3: Implement**

`src/genea/ingest/images.py`:
```python
"""Image metadata (EXIF/IPTC/XMP + sidecar) via ExifTool."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from genea.exiftool import read_metadata

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".tif", ".tiff", ".png", ".heic"})


@dataclass
class ImageRecord:
    sidecar: Path | None
    title: str | None = None
    description: str | None = None
    date_taken: str | None = None
    keywords: list[str] = field(default_factory=list)
    persons_shown: list[str] = field(default_factory=list)
    place: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None


def sidecar_for(image: Path) -> Path | None:
    """Existing sidecar: <stem>.xmp (Lightroom) first, then <name>.<ext>.xmp (darktable)."""
    for candidate in (image.with_suffix(".xmp"), image.with_name(image.name + ".xmp")):
        if candidate.is_file():
            return candidate
    return None


def default_sidecar(image: Path) -> Path:
    return sidecar_for(image) or image.with_suffix(".xmp")


def normalize_exif_date(value) -> str | None:
    """'1932:06:12 10:00:00' -> '1932-06-12'; zero month/day parts are dropped."""
    if value is None:
        return None
    match = re.match(r"^(\d{4})(?:[:\-](\d{2}))?(?:[:\-](\d{2}))?", str(value).strip())
    if not match:
        return None
    year, month, day = match.groups()
    if not month or month == "00":
        return year
    if not day or day == "00":
        return f"{year}-{month}"
    return f"{year}-{month}-{day}"


def _first(md: dict, *keys: str):
    for key in keys:
        value = md.get(key)
        if value not in (None, "", []):
            return value
    return None


def _as_list(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in items if str(v).strip()]


def _text(value) -> str | None:
    return None if value is None else str(value)


def map_metadata(md: dict, sidecar: Path | None) -> ImageRecord:
    keywords = list(dict.fromkeys(_as_list(md.get("XMP-dc:Subject")) + _as_list(md.get("IPTC:Keywords"))))
    place_parts = [
        _first(md, "XMP-iptcCore:Location", "IPTC:Sub-location"),
        _first(md, "XMP-photoshop:City", "IPTC:City"),
        _first(md, "XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName"),
    ]
    lat = _first(md, "Composite:GPSLatitude", "XMP-exif:GPSLatitude")
    lon = _first(md, "Composite:GPSLongitude", "XMP-exif:GPSLongitude")
    return ImageRecord(
        sidecar=sidecar,
        title=_text(_first(md, "XMP-dc:Title", "IPTC:ObjectName")),
        description=_text(_first(md, "XMP-dc:Description", "IPTC:Caption-Abstract", "IFD0:ImageDescription")),
        date_taken=normalize_exif_date(_first(md, "ExifIFD:DateTimeOriginal", "XMP-exif:DateTimeOriginal",
                                              "XMP-photoshop:DateCreated", "IPTC:DateCreated")),
        keywords=keywords,
        persons_shown=_as_list(md.get("XMP-iptcExt:PersonInImage")),
        place=", ".join(str(p) for p in place_parts if p) or None,
        gps_lat=float(lat) if lat is not None else None,
        gps_lon=float(lon) if lon is not None else None,
    )


def extract_image(path: Path, exe: str = "exiftool") -> ImageRecord:
    """Read the image and its sidecar; sidecar values win on conflicts."""
    sidecar = sidecar_for(path)
    merged: dict = {}
    for record in read_metadata([path] + ([sidecar] if sidecar else []), exe):
        merged.update(record)
    return map_metadata(merged, sidecar)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_images.py -v`
Expected: all pass (`test_extract_fixture_images` is SKIPPED without ExifTool).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: image metadata extraction with XMP sidecar support

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Incremental indexer, person links, and the `index` / `status` commands

**Files:**
- Create: `src/genea/index/indexer.py`, `src/genea/cli/indexcmds.py`
- Modify: `src/genea/cli/common.py` (add `get_embedder`), `src/genea/cli/main.py` (import `indexcmds`)
- Test: `tests/test_indexer.py`, `tests/test_cli_index.py`

**Interfaces:**
- Consumes: Tasks 1–7 (`KnowledgeBase`, `parse_gedcom`, `map_gedcom`, `extract_document`, `chunk_pages`, `extract_image`, `sidecar_for`, `open_index`, `Embedder`, `normalize`).
- Produces:
  - `genea.index.indexer`: `IndexReport(ok, needs_ocr, errors, skipped, removed, problems: list[tuple[str, str]])`, `KIND_BY_EXT`, `scan_files(kb) -> list[tuple[Path, str]]`, `file_hash(path, kind) -> str`, `index_kb(kb, embedder, rebuild=False, on_file=None) -> IndexReport`, `reindex_file(kb, embedder, path) -> None`, `rebuild_links(conn) -> None`, `name_forms(given, surname, variants) -> list[tuple[str, float]]`, `remove_file(conn, file_id)`
  - `genea.cli.common.get_embedder(kb) -> Embedder` (tests monkeypatch this)
  - CLI: `genea index [--rebuild]`, `genea status`

- [ ] **Step 1: Write the failing tests**

`tests/test_indexer.py`:
```python
import pytest

from genea.index.db import SchemaMismatch, open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb, name_forms, scan_files


def _query(kb, sql, params=()):
    conn = open_index(kb, FakeEmbedder())
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_full_index(kb):
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.needs_ocr, report.errors) == (5, 1, 0)
    statuses = {r["path"]: r["status"] for r in _query(kb, "SELECT path, status FROM files")}
    assert statuses["docs/sken.pdf"] == "needs_ocr"
    assert statuses["tree.ged"] == "ok"
    assert _query(kb, "SELECT COUNT(*) FROM persons")[0][0] == 20
    chunks = _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]
    assert chunks >= 5
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == chunks
    assert _query(kb, "SELECT COUNT(*) FROM chunks_vec")[0][0] == chunks


def test_second_run_skips_unchanged(kb):
    index_kb(kb, FakeEmbedder())
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.skipped) == (0, 6)


def test_changed_and_removed_files(kb):
    index_kb(kb, FakeEmbedder())
    (kb.root / "docs" / "kronika.txt").write_text("Nový text o tom, co dělal Josef Novák.", encoding="utf-8")
    (kb.root / "docs" / "poznamky.md").unlink()
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.removed, report.skipped) == (1, 1, 4)
    paths = {r["path"] for r in _query(kb, "SELECT path FROM files")}
    assert "docs/poznamky.md" not in paths
    assert _query(kb, "SELECT COUNT(*) FROM chunks WHERE text LIKE '%1845%'")[0][0] == 0
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]


def test_bad_file_is_recorded_and_others_continue(kb):
    (kb.root / "docs" / "broken.docx").write_bytes(b"not a zip file")
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.errors) == (5, 1)
    [row] = _query(kb, "SELECT status, error_msg FROM files WHERE path = 'docs/broken.docx'")
    assert row["status"] == "error"
    assert row["error_msg"]


def test_links_from_chronicle(kb):
    index_kb(kb, FakeEmbedder())
    rows = _query(kb, """
        SELECT l.person_xref, l.confidence FROM links l
        JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id
        JOIN files f ON f.id = c.file_id
        WHERE f.path = 'docs/kronika.txt'""")
    found = {(r["person_xref"], r["confidence"]) for r in rows}
    assert {("@I1@", 1.0), ("@I14@", 1.0), ("@I3@", 1.0), ("@I12@", 1.0)} <= found


def test_dimension_change_needs_rebuild(kb):
    index_kb(kb, FakeEmbedder(64))
    with pytest.raises(SchemaMismatch):
        index_kb(kb, FakeEmbedder(32))
    assert index_kb(kb, FakeEmbedder(32), rebuild=True).ok == 5


def test_scan_skips_hidden_and_excluded(kb):
    (kb.root / ".git").mkdir()
    (kb.root / ".git" / "notes.txt").write_text("x", encoding="utf-8")
    rels = [kb.rel(p) for p, _ in scan_files(kb)]
    assert "tree.ged" in rels
    assert not any(r.startswith((".git/", ".genea/", "photos/")) for r in rels)


def test_name_forms():
    forms = dict(name_forms("Jan Josef", "Novák", ["Jan Josef Novák", "Johann Nowak"]))
    assert forms["jan josef novak"] == 1.0
    assert forms["novak jan"] == 1.0
    assert forms["jan novak"] == 1.0
    assert forms["j novak"] == 0.6
    assert forms["johann nowak"] == 1.0


@pytest.mark.exiftool
def test_images_are_indexed_and_linked(kb_with_images):
    report = index_kb(kb_with_images, FakeEmbedder())
    assert report.ok == 8
    assert _query(kb_with_images, "SELECT COUNT(*) FROM images")[0][0] == 3
    linked = {r[0] for r in _query(kb_with_images,
                                   "SELECT person_xref FROM links WHERE source_kind = 'image'")}
    assert "@I16@" in linked
```

`tests/test_cli_index.py`:
```python
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder

runner = CliRunner()


def _prepare(sample_root, monkeypatch):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())


def test_status_before_index(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert result.exit_code == 1
    assert "genea index" in result.output


def test_index_then_status(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    assert result.exit_code == 0, result.output
    assert "ok 5" in result.output
    assert "needs OCR 1" in result.output
    result = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert result.exit_code == 0, result.output
    assert "docs/sken.pdf" in result.output
    assert "20 people" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_indexer.py tests/test_cli_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.index.indexer'`.

- [ ] **Step 3: Implement the indexer**

`src/genea/index/indexer.py`:
```python
"""Incremental indexing of a knowledge base into .genea/index.db."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sqlite_vec

from genea.index.db import open_index
from genea.index.embed import Embedder
from genea.ingest.docs import DOC_EXTS, chunk_pages, extract_document
from genea.ingest.gedcom.mapper import map_gedcom
from genea.ingest.gedcom.parser import parse_gedcom
from genea.ingest.images import IMAGE_EXTS, extract_image, sidecar_for
from genea.kb import KnowledgeBase
from genea.text import normalize

KIND_BY_EXT = {".ged": "ged", **{e: "doc" for e in DOC_EXTS}, **{e: "image" for e in IMAGE_EXTS}}
EMBED_BATCH = 32


@dataclass
class IndexReport:
    ok: int = 0
    needs_ocr: int = 0
    errors: int = 0
    skipped: int = 0
    removed: int = 0
    problems: list[tuple[str, str]] = field(default_factory=list)


def scan_files(kb: KnowledgeBase) -> list[tuple[Path, str]]:
    found = []
    for path in sorted(kb.root.rglob("*")):
        if not path.is_file():
            continue
        rel = kb.rel(path)
        if any(part.startswith(".") for part in rel.split("/")[:-1]):
            continue  # .genea, .git, other hidden folders
        if any(fnmatch.fnmatch(rel, pattern) for pattern in kb.settings.exclude):
            continue
        kind = KIND_BY_EXT.get(path.suffix.lower())
        if kind:
            found.append((path, kind))
    return found


def file_hash(path: Path, kind: str) -> str:
    """sha256 of the file; for images it also covers the XMP sidecar."""
    digest = hashlib.sha256(path.read_bytes())
    if kind == "image":
        sidecar = sidecar_for(path)
        if sidecar is not None:
            digest.update(b"\0sidecar\0")
            digest.update(sidecar.read_bytes())
    return digest.hexdigest()


def _ingest_ged(conn, kb, embedder, path, file_id) -> tuple[str, str | None]:
    data = map_gedcom(parse_gedcom(path))
    ged = kb.rel(path)
    conn.executemany(
        "INSERT OR IGNORE INTO persons (file_id, ged_file, xref, given, surname, sex, name_norm, "
        "names_norm, name_variants, notes, line_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, ged, p.xref, p.given, p.surname, p.sex, normalize(f"{p.given} {p.surname}"),
          " | ".join(normalize(v) for v in p.name_variants) or normalize(f"{p.given} {p.surname}"),
          json.dumps(p.name_variants, ensure_ascii=False), p.notes, p.line_no) for p in data.persons],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO families (file_id, ged_file, xref, husband_xref, wife_xref, line_no) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(file_id, ged, f.xref, f.husband_xref, f.wife_xref, f.line_no) for f in data.families],
    )
    conn.executemany(
        "INSERT INTO family_children (file_id, ged_file, family_xref, child_xref, pedigree) "
        "VALUES (?, ?, ?, ?, ?)",
        [(file_id, ged, f.xref, child, pedigree) for f in data.families for child, pedigree in f.children],
    )
    conn.executemany(
        "INSERT INTO events (file_id, ged_file, owner_xref, type, description, date_raw, date_from, "
        "date_to, date_qualifier, place, source_citations, line_no) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, ged, e.owner_xref, e.type, e.description, e.date_raw, e.date.date_from,
          e.date.date_to, e.date.qualifier, e.place, json.dumps(e.citations, ensure_ascii=False),
          e.line_no) for e in data.events],
    )
    if data.warnings:
        return "ok", f"{len(data.warnings)} warning(s); first: {data.warnings[0]}"
    return "ok", None


def _ingest_doc(conn, kb, embedder, path, file_id) -> tuple[str, str | None]:
    extract = extract_document(path)
    if extract.needs_ocr:
        return "needs_ocr", "no text layer; OCR is needed"
    chunks = chunk_pages(extract.pages)
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        vectors += embedder.embed([c.text for c in chunks[i:i + EMBED_BATCH]])
    for chunk, vector in zip(chunks, vectors):
        chunk_id = conn.execute(
            "INSERT INTO chunks (file_id, page, chunk_no, text) VALUES (?, ?, ?, ?)",
            (file_id, chunk.page, chunk.chunk_no, chunk.text),
        ).lastrowid
        conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (chunk_id, chunk.text))
        conn.execute("INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                     (chunk_id, sqlite_vec.serialize_float32(vector)))
    return "ok", None


def _ingest_image(conn, kb, embedder, path, file_id) -> tuple[str, str | None]:
    rec = extract_image(path, kb.settings.exiftool)
    search = normalize(" ".join([rec.title or "", rec.description or "", rec.place or "",
                                 *rec.keywords, *rec.persons_shown]))
    conn.execute(
        "INSERT INTO images (file_id, sidecar_path, title, description, date_taken, keywords, "
        "persons_shown, place, gps_lat, gps_lon, search_norm) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (file_id, kb.rel(rec.sidecar) if rec.sidecar else None, rec.title, rec.description,
         rec.date_taken, json.dumps(rec.keywords, ensure_ascii=False),
         json.dumps(rec.persons_shown, ensure_ascii=False), rec.place, rec.gps_lat, rec.gps_lon, search),
    )
    return "ok", None


INGESTERS = {"ged": _ingest_ged, "doc": _ingest_doc, "image": _ingest_image}


def remove_file(conn: sqlite3.Connection, file_id: int) -> None:
    chunk_ids = [(r[0],) for r in conn.execute("SELECT id FROM chunks WHERE file_id = ?", (file_id,))]
    conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", chunk_ids)
    conn.executemany("DELETE FROM chunks_vec WHERE chunk_id = ?", chunk_ids)
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))  # cascades to the other tables


def _index_one(conn, kb, embedder, path, kind, digest, old_id, report: IndexReport) -> None:
    rel = kb.rel(path)
    now = datetime.now().isoformat(timespec="seconds")
    mtime = path.stat().st_mtime
    try:
        with conn:
            if old_id is not None:
                remove_file(conn, old_id)
            file_id = conn.execute(
                "INSERT INTO files (path, kind, sha256, mtime, indexed_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'ok')", (rel, kind, digest, mtime, now),
            ).lastrowid
            status, message = INGESTERS[kind](conn, kb, embedder, path, file_id)
            conn.execute("UPDATE files SET status = ?, error_msg = ? WHERE id = ?", (status, message, file_id))
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop indexing
        message = f"{type(exc).__name__}: {exc}"
        with conn:
            if old_id is not None:
                remove_file(conn, old_id)
            conn.execute(
                "INSERT INTO files (path, kind, sha256, mtime, indexed_at, status, error_msg) "
                "VALUES (?, ?, ?, ?, ?, 'error', ?)", (rel, kind, digest, mtime, now, message),
            )
        report.errors += 1
        report.problems.append((rel, message))
        return
    if status == "needs_ocr":
        report.needs_ocr += 1
        report.problems.append((rel, message))
    else:
        report.ok += 1
        if message:
            report.problems.append((rel, message))


def name_forms(given: str | None, surname: str | None, variants: list[str]) -> list[tuple[str, float]]:
    """Normalized name spellings to look for in text, with a confidence for each."""
    forms: dict[str, float] = {}
    g, s = normalize(given or ""), normalize(surname or "")
    if g and s:
        first = g.split()[0]
        for form in (f"{g} {s}", f"{s} {g}", f"{first} {s}", f"{s} {first}"):
            forms[form] = 1.0
        forms.setdefault(f"{first[0]} {s}", 0.6)
    for variant in variants:
        norm = normalize(variant)
        if len(norm.split()) >= 2:
            forms[norm] = 1.0
    return list(forms.items())


def rebuild_links(conn: sqlite3.Connection) -> None:
    """Recompute person mentions in document chunks and image metadata."""
    conn.execute("DELETE FROM links")
    people = [
        (r["ged_file"], r["xref"], name_forms(r["given"], r["surname"], json.loads(r["name_variants"])))
        for r in conn.execute("SELECT ged_file, xref, given, surname, name_variants FROM persons")
    ]
    sources = [("chunk", r["id"], normalize(r["text"])) for r in conn.execute("SELECT id, text FROM chunks")]
    sources += [("image", r["id"], r["search_norm"]) for r in conn.execute("SELECT id, search_norm FROM images")]
    rows = []
    for kind, source_id, text in sources:
        padded = f" {text} "
        for ged, xref, forms in people:
            best = max((conf for form, conf in forms if f" {form} " in padded), default=0.0)
            if best:
                rows.append((kind, source_id, ged, xref, best))
    conn.executemany(
        "INSERT INTO links (source_kind, source_id, person_ged_file, person_xref, confidence) "
        "VALUES (?, ?, ?, ?, ?)", rows,
    )


def index_kb(kb: KnowledgeBase, embedder: Embedder, rebuild: bool = False,
             on_file: Callable[[str], None] | None = None) -> IndexReport:
    if rebuild and kb.index_path.exists():
        kb.index_path.unlink()
    report = IndexReport()
    conn = open_index(kb, embedder)
    try:
        known = {r["path"]: r for r in conn.execute("SELECT id, path, sha256, status FROM files")}
        seen: set[str] = set()
        for path, kind in scan_files(kb):
            rel = kb.rel(path)
            seen.add(rel)
            digest = file_hash(path, kind)
            old = known.get(rel)
            if old is not None and old["sha256"] == digest and old["status"] != "error":
                report.skipped += 1
                continue
            if on_file:
                on_file(rel)
            _index_one(conn, kb, embedder, path, kind, digest, old["id"] if old else None, report)
        for rel, row in known.items():
            if rel not in seen:
                with conn:
                    remove_file(conn, row["id"])
                report.removed += 1
        with conn:
            rebuild_links(conn)
    finally:
        conn.close()
    return report


def reindex_file(kb: KnowledgeBase, embedder: Embedder, path: Path) -> None:
    """Re-index one file (used after applying a change)."""
    kind = KIND_BY_EXT[path.suffix.lower()]
    conn = open_index(kb, embedder)
    try:
        row = conn.execute("SELECT id FROM files WHERE path = ?", (kb.rel(path),)).fetchone()
        _index_one(conn, kb, embedder, path, kind, file_hash(path, kind),
                   row["id"] if row else None, IndexReport())
        with conn:
            rebuild_links(conn)
    finally:
        conn.close()
```

- [ ] **Step 4: Add the CLI commands**

Add to `src/genea/cli/common.py`:
```python
from genea.index.embed import Embedder, make_embedder


def get_embedder(kb: KnowledgeBase) -> Embedder:
    return make_embedder(kb.settings)
```
(Put the import at the top with the other imports.)

`src/genea/cli/indexcmds.py`:
```python
"""`genea index` and `genea status`."""

from __future__ import annotations

import sqlite3

import typer
from rich.markup import escape
from rich.table import Table

from genea.cli import common
from genea.cli.app import app
from genea.index.db import SchemaMismatch
from genea.index.indexer import index_kb


@app.command()
def index(
    ctx: typer.Context,
    rebuild: bool = typer.Option(False, "--rebuild", help="Delete the index and build it from scratch."),
) -> None:
    """Index new and changed files of the knowledge base."""
    kb = common.open_kb(ctx)
    try:
        report = index_kb(kb, common.get_embedder(kb), rebuild=rebuild,
                          on_file=lambda rel: common.console.print(f"[dim]indexing {escape(rel)}[/dim]"))
    except SchemaMismatch as exc:
        common.console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    common.console.print(
        f"ok {report.ok} · needs OCR {report.needs_ocr} · errors {report.errors} · "
        f"unchanged {report.skipped} · removed {report.removed}"
    )
    for rel, message in report.problems:
        common.console.print(f"  [yellow]{escape(rel)}[/yellow]: {escape(message)}")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show what is indexed and which files had problems."""
    kb = common.open_kb(ctx)
    if not kb.index_path.exists():
        common.console.print("[yellow]Not indexed yet. Run `genea index`.[/yellow]")
        raise typer.Exit(1)
    conn = sqlite3.connect(kb.index_path)
    conn.row_factory = sqlite3.Row
    try:
        counts = conn.execute(
            "SELECT kind, status, COUNT(*) AS n FROM files GROUP BY kind, status ORDER BY kind, status"
        ).fetchall()
        problems = conn.execute(
            "SELECT path, status, error_msg FROM files WHERE status != 'ok' OR error_msg IS NOT NULL ORDER BY path"
        ).fetchall()
        people = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    finally:
        conn.close()
    table = Table("kind", "status", "files")
    for row in counts:
        table.add_row(row["kind"], row["status"], str(row["n"]))
    common.console.print(table)
    common.console.print(f"{people} people in the family-tree index")
    for row in problems:
        common.console.print(f"[yellow]{escape(row['path'])}[/yellow] ({row['status']}): "
                             f"{escape(row['error_msg'] or '')}")
```

In `src/genea/cli/main.py`, change the import line to:
```python
from genea.cli import indexcmds, kbcmds  # noqa: E402,F401
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_indexer.py tests/test_cli_index.py -v`
Expected: all pass (`test_images_are_indexed_and_linked` is SKIPPED without ExifTool).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: incremental indexer with person links, index and status commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Tool foundations and family-tree tools (`find_person`, `get_person`, `relatives`, `timeline`) and the `person` command

**Files:**
- Create: `src/genea/tools/__init__.py` (empty), `src/genea/tools/models.py`, `src/genea/tools/base.py`, `src/genea/tools/people.py`, `src/genea/cli/querycmds.py`
- Modify: `src/genea/cli/common.py` (add `open_tool_context`), `src/genea/cli/main.py` (import `querycmds`), `tests/conftest.py` (add the `tools` and `tools_with_images` fixtures)
- Test: `tests/test_people.py`, `tests/test_cli_query.py`

**Interfaces:**
- Consumes: `index_kb`, `open_index`, `FakeEmbedder`, `Embedder` (Tasks 5, 8); `normalize` (Task 4); `kb` / `kb_with_images` fixtures (Task 6).
- Produces:
  - `genea.tools.models`: `SourceRef(file, line=None, page=None, chunk=None)` with `cite() -> str`; `ToolResult(sources)`; `PersonSummary(id, name, sex, birth, death, source)`; `PersonMatches(query, candidates)`; `EventOut(type, description, date, date_from, date_to, place, citations, source)`; `PersonDetail(id, name, given, surname, sex, name_variants, notes, events, parents, spouses, children, families_as_spouse, families_as_child)`; `RelativeOut(generation, person)`; `RelativesResult(person, relation, relatives)`; `TimelineEntry(date, date_from, role, person_id, person_name, type, place, source)`; `Timeline(person, entries)`; `DocHit(file, page, chunk, text, score, source)`; `SearchResult(query, hits, note)`; `DocumentText(file, page, text, truncated)`; `ImageOut(file, sidecar, title, description, date_taken, keywords, persons_shown, place, gps_lat, gps_lon, source)`; `ImageResult(images)`; `Conflict(kind, severity, person_id, message, sources)`; `ConflictResult(conflicts)`; `NoteResult(path)`; `ChangeResult(id, status, created, message)`. Every `*Result`/`Person*`/`Timeline`/`DocumentText` model inherits `sources` from `ToolResult`, except `PersonSummary`, `EventOut`, `RelativeOut`, `TimelineEntry`, `DocHit`, `ImageOut` and `Conflict`, which carry their own `source`/`sources`.
  - `genea.tools.base`: `ToolContext(kb, conn, embedder)`, `ToolError`, `open_tools(kb, embedder) -> ToolContext`, `person_id(ged_file, xref) -> str`, `parse_person_id(ctx, raw) -> (ged, xref)`, `parse_family_id(ctx, raw) -> (ged, xref)`
  - `genea.tools.people`: `find_person(ctx, name, born_around=None, place=None, limit=10) -> PersonMatches`, `get_person(ctx, pid) -> PersonDetail`, `relatives(ctx, pid, relation, depth=1) -> RelativesResult`, `timeline(ctx, pid) -> Timeline`; helpers reused later: `event_rows`, `first_event`, `summary`, `families_as_spouse`, `families_as_child`, `parents_of`, `children_of`, `spouses_of`, `siblings_of`, `BIRTH_TYPES`, `DEATH_TYPES`, `RELATIONS`
  - `genea.cli.common.open_tool_context(kb) -> ToolContext`; CLI `genea person <query|id>`
  - pytest fixtures `tools`, `tools_with_images` (indexed sample KB + open `ToolContext`)

- [ ] **Step 1: Add the test fixtures**

Append to `tests/conftest.py` (imports at the top):
```python
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.tools.base import open_tools


@pytest.fixture
def tools(kb):
    index_kb(kb, FakeEmbedder())
    ctx = open_tools(kb, FakeEmbedder())
    yield ctx
    ctx.conn.close()


@pytest.fixture
def tools_with_images(kb_with_images):
    index_kb(kb_with_images, FakeEmbedder())
    ctx = open_tools(kb_with_images, FakeEmbedder())
    yield ctx
    ctx.conn.close()
```

- [ ] **Step 2: Write the failing tests**

`tests/test_people.py`:
```python
import pytest

from genea.tools.base import ToolError
from genea.tools.people import find_person, get_person, relatives, timeline

T = "tree.ged#"


def test_find_person_by_name(tools):
    ids = {c.id for c in find_person(tools, "Jan Novák").candidates}
    assert ids == {T + "@I3@", T + "@I12@"}


def test_find_person_ranks_by_birth_year(tools):
    assert find_person(tools, "jan novak", born_around=1900).candidates[0].id == T + "@I12@"
    assert find_person(tools, "jan novak", born_around=1850).candidates[0].id == T + "@I3@"


def test_find_person_by_variant(tools):
    result = find_person(tools, "Joseph Nowak")
    assert [c.id for c in result.candidates] == [T + "@I1@"]
    assert result.sources[0].line == 7


def test_find_person_place_boost(tools):
    assert find_person(tools, "Marie", place="Tišnov").candidates[0].id == T + "@I16@"


def test_find_person_empty_name(tools):
    with pytest.raises(ToolError):
        find_person(tools, "  ")


def test_get_person(tools):
    d = get_person(tools, T + "@I3@")
    assert d.name == "Jan Novák"
    assert [p.id for p in d.parents] == [T + "@I1@", T + "@I2@"]
    assert [p.id for p in d.spouses] == [T + "@I6@"]
    assert [p.id for p in d.children] == [T + "@I7@", T + "@I8@", T + "@I9@"]
    assert d.families_as_spouse == [T + "@F2@"]
    assert d.families_as_child == [T + "@F1@"]
    birth = next(e for e in d.events if e.type == "BIRT")
    assert birth.citations[0]["page"] == "fol. 23"
    assert birth.source.file == "tree.ged" and birth.source.line is not None
    assert any(e.type == "MARR" and e.place == "Tišnov" for e in d.events)


def test_get_person_short_ids(tools):
    assert get_person(tools, "I3").id == T + "@I3@"
    assert get_person(tools, "@I3@").id == T + "@I3@"


def test_get_person_unknown(tools):
    with pytest.raises(ToolError, match="find_person"):
        get_person(tools, T + "@I999@")


def test_ancestors(tools):
    result = relatives(tools, T + "@I17@", "ancestors", depth=3)
    by_generation = {}
    for rel in result.relatives:
        by_generation.setdefault(rel.generation, set()).add(rel.person.id)
    assert by_generation == {1: {T + "@I12@", T + "@I16@"},
                             2: {T + "@I7@", T + "@I10@"},
                             3: {T + "@I3@", T + "@I6@"}}


def test_simple_relations(tools):
    assert [r.person.id for r in relatives(tools, T + "@I7@", "siblings").relatives] == [T + "@I8@", T + "@I9@"]
    assert [r.person.id for r in relatives(tools, T + "@I8@", "spouses").relatives] == [T + "@I11@"]
    descendants = {r.person.id for r in relatives(tools, T + "@I1@", "descendants", depth=2).relatives}
    assert descendants == {T + x for x in ("@I3@", "@I4@", "@I5@", "@I7@", "@I8@", "@I9@")}


def test_bad_relation(tools):
    with pytest.raises(ToolError, match="relation"):
        relatives(tools, T + "@I7@", "cousins")


def test_timeline(tools):
    t = timeline(tools, T + "@I3@")
    seen = {(e.role, e.type, e.person_id) for e in t.entries}
    assert ("self", "BIRT", T + "@I3@") in seen
    assert ("self", "MARR", T + "@I3@") in seen
    assert ("parent", "MARR", T + "@I1@") in seen
    assert ("child", "BIRT", T + "@I7@") in seen
    dated = [e.date_from for e in t.entries if e.date_from]
    assert dated == sorted(dated)
```

`tests/test_cli_query.py`:
```python
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder

runner = CliRunner()


def _indexed(sample_root, monkeypatch):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0


def test_person_search_and_detail(sample_root, monkeypatch):
    _indexed(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "person", "Jan Novák"])
    assert result.exit_code == 0, result.output
    assert "tree.ged#@I3@" in result.output and "tree.ged#@I12@" in result.output
    result = runner.invoke(app, ["--kb", str(sample_root), "person", "tree.ged#@I3@"])
    assert result.exit_code == 0, result.output
    assert "Josef Novák" in result.output
    assert "Tišnov" in result.output


def test_person_unknown_id(sample_root, monkeypatch):
    _indexed(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "person", "tree.ged#@I999@"])
    assert result.exit_code == 1
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_people.py tests/test_cli_query.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.tools'`.

- [ ] **Step 4: Implement the models and base**

`src/genea/tools/models.py`:
```python
"""Typed tool results. Every result carries the sources it was built from."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    file: str
    line: int | None = None
    page: int | None = None
    chunk: int | None = None

    def cite(self) -> str:
        if self.line is not None:
            return f"[{self.file}:L{self.line}]"
        if self.page is not None:
            return f"[{self.file} p.{self.page}]"
        return f"[{self.file}]"


class ToolResult(BaseModel):
    sources: list[SourceRef] = Field(default_factory=list)


class PersonSummary(BaseModel):
    id: str
    name: str
    sex: str | None = None
    birth: str | None = None
    death: str | None = None
    source: SourceRef


class PersonMatches(ToolResult):
    query: str
    candidates: list[PersonSummary]


class EventOut(BaseModel):
    type: str
    description: str | None = None
    date: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    place: str | None = None
    citations: list[dict] = Field(default_factory=list)
    source: SourceRef


class PersonDetail(ToolResult):
    id: str
    name: str
    given: str | None = None
    surname: str | None = None
    sex: str | None = None
    name_variants: list[str] = Field(default_factory=list)
    notes: str | None = None
    events: list[EventOut] = Field(default_factory=list)
    parents: list[PersonSummary] = Field(default_factory=list)
    spouses: list[PersonSummary] = Field(default_factory=list)
    children: list[PersonSummary] = Field(default_factory=list)
    families_as_spouse: list[str] = Field(default_factory=list)
    families_as_child: list[str] = Field(default_factory=list)


class RelativeOut(BaseModel):
    generation: int
    person: PersonSummary


class RelativesResult(ToolResult):
    person: PersonSummary
    relation: str
    relatives: list[RelativeOut]


class TimelineEntry(BaseModel):
    date: str | None = None
    date_from: str | None = None
    role: str
    person_id: str
    person_name: str
    type: str
    place: str | None = None
    source: SourceRef


class Timeline(ToolResult):
    person: PersonSummary
    entries: list[TimelineEntry]


class DocHit(BaseModel):
    file: str
    page: int | None = None
    chunk: int
    text: str
    score: float
    source: SourceRef


class SearchResult(ToolResult):
    query: str
    hits: list[DocHit]
    note: str | None = None


class DocumentText(ToolResult):
    file: str
    page: int | None = None
    text: str
    truncated: bool


class ImageOut(BaseModel):
    file: str
    sidecar: str | None = None
    title: str | None = None
    description: str | None = None
    date_taken: str | None = None
    keywords: list[str] = Field(default_factory=list)
    persons_shown: list[str] = Field(default_factory=list)
    place: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    source: SourceRef


class ImageResult(ToolResult):
    images: list[ImageOut]


class Conflict(BaseModel):
    kind: str
    severity: Literal["error", "needs_review"]
    person_id: str
    message: str
    sources: list[SourceRef] = Field(default_factory=list)


class ConflictResult(ToolResult):
    conflicts: list[Conflict]


class NoteResult(ToolResult):
    path: str


class ChangeResult(ToolResult):
    id: str
    status: str
    created: bool
    message: str
```

`src/genea/tools/base.py`:
```python
"""Shared plumbing for tools: context, errors, id parsing."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from genea.index.db import open_index
from genea.index.embed import Embedder
from genea.kb import KnowledgeBase


@dataclass
class ToolContext:
    kb: KnowledgeBase
    conn: sqlite3.Connection
    embedder: Embedder


class ToolError(Exception):
    """Bad tool input. The message is shown to the model (or user) so it can correct itself."""


def open_tools(kb: KnowledgeBase, embedder: Embedder) -> ToolContext:
    return ToolContext(kb, open_index(kb, embedder), embedder)


def person_id(ged_file: str, xref: str) -> str:
    return f"{ged_file}#{xref}"


def _parse(ctx: ToolContext, raw: str, table: str, what: str, hint: str) -> tuple[str, str]:
    raw = (raw or "").strip()
    if "#" in raw:
        ged, xref = raw.split("#", 1)
    else:
        geds = [r[0] for r in ctx.conn.execute(f"SELECT DISTINCT ged_file FROM {table}")]
        if len(geds) != 1:
            raise ToolError(f"Ambiguous {what} id {raw!r}: use '<file>#<xref>' "
                            f"(GEDCOM files: {', '.join(geds) or 'none'})")
        ged, xref = geds[0], raw
    xref = "@" + xref.strip().strip("@") + "@"
    found = ctx.conn.execute(f"SELECT 1 FROM {table} WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    if found is None:
        raise ToolError(f"No {what} with id {ged}#{xref}. {hint}")
    return ged, xref


def parse_person_id(ctx: ToolContext, raw: str) -> tuple[str, str]:
    return _parse(ctx, raw, "persons", "person", "Use find_person to look up ids.")


def parse_family_id(ctx: ToolContext, raw: str) -> tuple[str, str]:
    return _parse(ctx, raw, "families", "family",
                  "get_person lists a person's family ids (families_as_spouse / families_as_child).")
```

- [ ] **Step 5: Implement the family-tree tools**

`src/genea/tools/people.py`:
```python
"""Family-tree queries: find people, person details, relatives and timelines."""

from __future__ import annotations

import json
import sqlite3

from genea.text import normalize
from genea.tools.base import ToolContext, ToolError, parse_person_id, person_id
from genea.tools.models import (EventOut, PersonDetail, PersonMatches, PersonSummary,
                                RelativeOut, RelativesResult, SourceRef, Timeline, TimelineEntry)

BIRTH_TYPES = ("BIRT", "CHR", "BAPM")
DEATH_TYPES = ("DEAT", "BURI", "CREM")
RELATIONS = ("parents", "children", "siblings", "spouses", "ancestors", "descendants")


def event_rows(conn: sqlite3.Connection, ged: str, owner: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE ged_file = ? AND owner_xref = ? "
        "ORDER BY COALESCE(date_from, '9999'), line_no", (ged, owner),
    ).fetchall()


def first_event(events, types) -> sqlite3.Row | None:
    for event_type in types:
        for event in events:
            if event["type"] == event_type:
                return event
    return None


def _describe(event) -> str | None:
    if event is None:
        return None
    parts = [p for p in (event["date_raw"], event["place"]) if p]
    return ", ".join(parts) or event["type"]


def summary(conn: sqlite3.Connection, ged: str, xref: str, events=None) -> PersonSummary:
    row = conn.execute("SELECT * FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    if row is None:
        return PersonSummary(id=person_id(ged, xref), name=xref, source=SourceRef(file=ged))
    events = event_rows(conn, ged, xref) if events is None else events
    name = " ".join(f"{row['given'] or ''} {row['surname'] or ''}".split()) or xref
    return PersonSummary(
        id=person_id(ged, xref), name=name, sex=row["sex"],
        birth=_describe(first_event(events, BIRTH_TYPES)),
        death=_describe(first_event(events, DEATH_TYPES)),
        source=SourceRef(file=ged, line=row["line_no"]),
    )


def event_out(ged: str, event) -> EventOut:
    return EventOut(
        type=event["type"], description=event["description"], date=event["date_raw"],
        date_from=event["date_from"], date_to=event["date_to"], place=event["place"],
        citations=json.loads(event["source_citations"]),
        source=SourceRef(file=ged, line=event["line_no"]),
    )


def families_as_spouse(conn, ged, xref) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM families WHERE ged_file = ? AND (husband_xref = ? OR wife_xref = ?) ORDER BY line_no",
        (ged, xref, xref),
    ).fetchall()


def families_as_child(conn, ged, xref) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.* FROM family_children fc JOIN families f "
        "ON f.ged_file = fc.ged_file AND f.xref = fc.family_xref "
        "WHERE fc.ged_file = ? AND fc.child_xref = ? ORDER BY f.line_no", (ged, xref),
    ).fetchall()


def _children_in(conn, ged, family_xref) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT child_xref FROM family_children WHERE ged_file = ? AND family_xref = ? ORDER BY rowid",
        (ged, family_xref))]


def _unique(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def parents_of(conn, ged, xref) -> list[str]:
    return _unique(x for f in families_as_child(conn, ged, xref) for x in (f["husband_xref"], f["wife_xref"]))


def children_of(conn, ged, xref) -> list[str]:
    return _unique(c for f in families_as_spouse(conn, ged, xref) for c in _children_in(conn, ged, f["xref"]))


def spouses_of(conn, ged, xref) -> list[str]:
    return _unique(f["wife_xref"] if f["husband_xref"] == xref else f["husband_xref"]
                   for f in families_as_spouse(conn, ged, xref))


def siblings_of(conn, ged, xref) -> list[str]:
    return _unique(c for f in families_as_child(conn, ged, xref)
                   for c in _children_in(conn, ged, f["xref"]) if c != xref)


def find_person(ctx: ToolContext, name: str, born_around: int | None = None,
                place: str | None = None, limit: int = 10) -> PersonMatches:
    tokens = normalize(name).split()
    if not tokens:
        raise ToolError("Give a name to search for.")
    rows = ctx.conn.execute(
        "SELECT ged_file, xref FROM persons WHERE " + " AND ".join("names_norm LIKE ?" for _ in tokens),
        [f"%{t}%" for t in tokens],
    ).fetchall()
    place_norm = normalize(place) if place else ""
    scored = []
    for row in rows:
        events = event_rows(ctx.conn, row["ged_file"], row["xref"])
        score = 0.0
        if born_around is not None:
            birth = first_event(events, BIRTH_TYPES)
            if birth is not None and birth["date_from"]:
                middle = (int(birth["date_from"][:4]) + int(birth["date_to"][:4])) / 2
                score -= abs(middle - born_around)
            else:
                score -= 100
        if place_norm and any(place_norm in normalize(e["place"] or "") for e in events):
            score += 10
        scored.append((score, summary(ctx.conn, row["ged_file"], row["xref"], events)))
    scored.sort(key=lambda item: (-item[0], item[1].name, item[1].id))
    candidates = [s for _, s in scored[: max(1, min(limit, 50))]]
    return PersonMatches(query=name, candidates=candidates, sources=[c.source for c in candidates])


def get_person(ctx: ToolContext, pid: str) -> PersonDetail:
    ged, xref = parse_person_id(ctx, pid)
    conn = ctx.conn
    row = conn.execute("SELECT * FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    spouse_families = families_as_spouse(conn, ged, xref)
    events = list(event_rows(conn, ged, xref))
    for family in spouse_families:
        events += event_rows(conn, ged, family["xref"])
    events.sort(key=lambda e: (e["date_from"] or "9999", e["line_no"]))
    me = summary(conn, ged, xref)
    event_models = [event_out(ged, e) for e in events]
    return PersonDetail(
        id=me.id, name=me.name, given=row["given"], surname=row["surname"], sex=row["sex"],
        name_variants=json.loads(row["name_variants"]), notes=row["notes"] or None,
        events=event_models,
        parents=[summary(conn, ged, x) for x in parents_of(conn, ged, xref)],
        spouses=[summary(conn, ged, x) for x in spouses_of(conn, ged, xref)],
        children=[summary(conn, ged, x) for x in children_of(conn, ged, xref)],
        families_as_spouse=[person_id(ged, f["xref"]) for f in spouse_families],
        families_as_child=[person_id(ged, f["xref"]) for f in families_as_child(conn, ged, xref)],
        sources=[me.source] + [e.source for e in event_models],
    )


def relatives(ctx: ToolContext, pid: str, relation: str, depth: int = 1) -> RelativesResult:
    if relation not in RELATIONS:
        raise ToolError(f"Unknown relation {relation!r}; use one of {', '.join(RELATIONS)}.")
    ged, xref = parse_person_id(ctx, pid)
    conn = ctx.conn
    depth = max(1, min(depth, 6))
    found: list[RelativeOut] = []
    direct = {"parents": (parents_of, 1), "children": (children_of, -1),
              "siblings": (siblings_of, 0), "spouses": (spouses_of, 0)}
    if relation in direct:
        step, generation = direct[relation]
        found = [RelativeOut(generation=generation, person=summary(conn, ged, x)) for x in step(conn, ged, xref)]
    else:
        step, sign = (parents_of, 1) if relation == "ancestors" else (children_of, -1)
        seen, frontier = {xref}, [xref]
        for generation in range(1, depth + 1):
            next_frontier = []
            for current in frontier:
                for other in step(conn, ged, current):
                    if other not in seen:
                        seen.add(other)
                        next_frontier.append(other)
                        found.append(RelativeOut(generation=sign * generation, person=summary(conn, ged, other)))
            frontier = next_frontier
    return RelativesResult(person=summary(conn, ged, xref), relation=relation, relatives=found,
                           sources=[r.person.source for r in found])


def timeline(ctx: ToolContext, pid: str) -> Timeline:
    ged, xref = parse_person_id(ctx, pid)
    conn = ctx.conn
    names: dict[str, str] = {}
    entries: list[TimelineEntry] = []
    life = set(BIRTH_TYPES) | set(DEATH_TYPES)

    def name_of(who: str) -> str:
        if who not in names:
            names[who] = summary(conn, ged, who).name
        return names[who]

    def add(owner: str, role: str, who: str, types: set[str] | None = None) -> None:
        for event in event_rows(conn, ged, owner):
            if types is None or event["type"] in types:
                entries.append(TimelineEntry(
                    date=event["date_raw"], date_from=event["date_from"], role=role,
                    person_id=person_id(ged, who), person_name=name_of(who), type=event["type"],
                    place=event["place"], source=SourceRef(file=ged, line=event["line_no"]),
                ))

    add(xref, "self", xref)
    for family in families_as_spouse(conn, ged, xref):
        add(family["xref"], "self", xref, {"MARR", "DIV", "ENGA"})
    for family in families_as_child(conn, ged, xref):
        for parent in (family["husband_xref"], family["wife_xref"]):
            if parent:
                add(parent, "parent", parent, life)
        who = family["husband_xref"] or family["wife_xref"]
        if who:
            add(family["xref"], "parent", who, {"MARR"})
    for spouse in spouses_of(conn, ged, xref):
        add(spouse, "spouse", spouse, life)
    for child in children_of(conn, ged, xref):
        add(child, "child", child, life)
        for family in families_as_spouse(conn, ged, child):
            add(family["xref"], "child", child, {"MARR"})
    entries.sort(key=lambda e: (e.date_from or "9999", e.source.line or 0))
    return Timeline(person=summary(conn, ged, xref), entries=entries, sources=[e.source for e in entries])
```

- [ ] **Step 6: Implement the `person` command**

Add to `src/genea/cli/common.py`:
```python
from rich.markup import escape

from genea.index.db import SchemaMismatch
from genea.tools.base import ToolContext, open_tools


def open_tool_context(kb: KnowledgeBase) -> ToolContext:
    if not kb.index_path.exists():
        console.print("[red]This knowledge base is not indexed yet. Run `genea index` first.[/red]")
        raise typer.Exit(1)
    try:
        return open_tools(kb, get_embedder(kb))
    except SchemaMismatch as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
```
(Imports go at the top of the file.)

`src/genea/cli/querycmds.py`:
```python
"""`genea person` (and, from Task 11, `genea conflicts`): direct tool access without an LLM."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from genea.cli import common
from genea.cli.app import app
from genea.tools.base import ToolError
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
```

In `src/genea/cli/main.py`, change the import line to:
```python
from genea.cli import indexcmds, kbcmds, querycmds  # noqa: E402,F401
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_people.py tests/test_cli_query.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: family-tree tools (find, details, relatives, timeline) and person command

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Search tools (`search_documents`, `read_document`, `find_images`)

**Files:**
- Create: `src/genea/tools/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `ToolContext`, `ToolError`, `parse_person_id` (Task 9); models `SearchResult, DocHit, DocumentText, ImageResult, ImageOut, SourceRef` (Task 9); `EmbeddingError` (Task 5); `extract_document`, `DOC_EXTS` (Task 6); `normalize` (Task 4).
- Produces: `genea.tools.search.search_documents(ctx, query, k=8) -> SearchResult`, `read_document(ctx, path, page=None) -> DocumentText`, `find_images(ctx, person=None, place=None, year_from=None, year_to=None, text=None, limit=20) -> ImageResult`

- [ ] **Step 1: Write the failing test**

`tests/test_search.py`:
```python
from dataclasses import replace

import pytest

from genea.index.embed import EmbeddingError
from genea.kb import KnowledgeBase
from genea.tools.base import ToolError
from genea.tools.search import find_images, read_document, search_documents


class BrokenEmbedder:
    model = "fake"
    dim = 64

    def embed(self, texts):
        raise EmbeddingError("Ollama is down")


def test_search_finds_relevant_chunk(tools):
    result = search_documents(tools, "Terezie Dvořáková Tišnov")
    assert result.hits[0].file == "docs/poznamky.md"
    assert result.sources[0].file == "docs/poznamky.md"
    assert result.note is None


def test_search_reports_pdf_page(tools):
    hit = next(h for h in search_documents(tools, "Vaclav").hits if h.file == "docs/matrika.pdf")
    assert hit.page == 2
    assert hit.source.cite() == "[docs/matrika.pdf p.2]"


def test_search_falls_back_to_full_text(tools):
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), "Dvořáková")
    assert result.hits
    assert "unavailable" in result.note


def test_search_empty_query(tools):
    with pytest.raises(ToolError):
        search_documents(tools, "   ")


def test_read_document(tools):
    page = read_document(tools, "docs/matrika.pdf", page=2)
    assert "Vaclav Novak" in page.text
    assert not page.truncated
    assert page.sources[0].cite() == "[docs/matrika.pdf p.2]"
    assert "Josef Novák" in read_document(tools, "docs/kronika.txt").text


def test_read_document_truncates(tools):
    small = KnowledgeBase(tools.kb.root, replace(tools.kb.settings, tool_output_chars=20))
    doc = read_document(replace(tools, kb=small), "docs/kronika.txt")
    assert doc.truncated
    assert doc.text.endswith("[truncated]")


@pytest.mark.parametrize("bad", ["../outside.txt", ".genea/config.toml", "tree.ged", "docs/missing.txt"])
def test_read_document_refuses(tools, bad):
    with pytest.raises(ToolError):
        read_document(tools, bad)


def test_read_document_missing_page(tools):
    with pytest.raises(ToolError, match="Page 9"):
        read_document(tools, "docs/matrika.pdf", page=9)


def test_find_images_without_indexed_images(tools):
    assert find_images(tools, text="svatba").images == []


@pytest.mark.exiftool
def test_find_images(tools_with_images):
    ctx = tools_with_images
    assert [i.file for i in find_images(ctx, person="tree.ged#@I16@").images] == ["photos/svatba_1932.jpg"]
    assert [i.file for i in find_images(ctx, year_from=1930, year_to=1933).images] == ["photos/svatba_1932.jpg"]
    assert [i.file for i in find_images(ctx, text="Anna").images] == ["photos/anna_1935.jpg"]
    assert [i.file for i in find_images(ctx, place="Brno").images] == ["photos/svatba_1932.jpg"]
    wedding = find_images(ctx, person="Jan Novák").images[0]
    assert wedding.persons_shown == ["Jan Novák", "Marie Dvořáková"]
    assert wedding.source.cite() == "[photos/svatba_1932.jpg]"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.tools.search'`.

- [ ] **Step 3: Implement**

`src/genea/tools/search.py`:
```python
"""Document and image search tools."""

from __future__ import annotations

import json
import re

import sqlite_vec

from genea.index.embed import EmbeddingError
from genea.ingest.docs import DOC_EXTS, extract_document
from genea.text import normalize
from genea.tools.base import ToolContext, ToolError, parse_person_id
from genea.tools.models import DocHit, DocumentText, ImageOut, ImageResult, SearchResult, SourceRef

RRF_K = 60
CANDIDATES = 30


def _fts_query(text: str) -> str | None:
    tokens = re.findall(r"\w+", text)
    return " OR ".join(f'"{t}"' for t in tokens) or None


def search_documents(ctx: ToolContext, query: str, k: int = 8) -> SearchResult:
    """Hybrid search: FTS5 (BM25) + vector KNN, merged with reciprocal rank fusion."""
    if not query.strip():
        raise ToolError("Give a search query.")
    k = max(1, min(k, 20))
    scores: dict[int, float] = {}
    fts = _fts_query(query)
    if fts:
        rows = ctx.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
            (fts, CANDIDATES)).fetchall()
        for rank, row in enumerate(rows, start=1):
            scores[row[0]] = scores.get(row[0], 0.0) + 1 / (RRF_K + rank)
    note = None
    try:
        vector = ctx.embedder.embed([query])[0]
        rows = ctx.conn.execute(
            "SELECT chunk_id FROM chunks_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(vector), CANDIDATES)).fetchall()
        for rank, row in enumerate(rows, start=1):
            scores[row[0]] = scores.get(row[0], 0.0) + 1 / (RRF_K + rank)
    except EmbeddingError as exc:
        note = f"semantic search unavailable, used full-text search only: {exc}"
    hits = []
    for chunk_id, score in sorted(scores.items(), key=lambda item: -item[1])[:k]:
        row = ctx.conn.execute(
            "SELECT c.page, c.chunk_no, c.text, f.path FROM chunks c JOIN files f ON f.id = c.file_id "
            "WHERE c.id = ?", (chunk_id,)).fetchone()
        source = SourceRef(file=row["path"], page=row["page"], chunk=row["chunk_no"])
        hits.append(DocHit(file=row["path"], page=row["page"], chunk=row["chunk_no"],
                           text=row["text"][:1500], score=round(score, 4), source=source))
    return SearchResult(query=query, hits=hits, note=note, sources=[h.source for h in hits])


def read_document(ctx: ToolContext, path: str, page: int | None = None) -> DocumentText:
    try:
        full_path = ctx.kb.resolve_inside(path)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if not full_path.is_file() or full_path.suffix.lower() not in DOC_EXTS:
        raise ToolError(f"Not a readable document: {path} (use search_documents to find documents)")
    extract = extract_document(full_path)
    pages = extract.pages if page is None else [p for p in extract.pages if p.number == page]
    if page is not None and not pages:
        raise ToolError(f"Page {page} was not found or has no text in {path}")
    text = "\n\n".join((f"[page {p.number}]\n" if p.number else "") + p.text for p in pages)
    limit = ctx.kb.settings.tool_output_chars
    truncated = len(text) > limit
    if truncated:
        text = text[:limit] + "\n…[truncated]"
    rel = ctx.kb.rel(full_path)
    return DocumentText(file=rel, page=page, text=text, truncated=truncated,
                        sources=[SourceRef(file=rel, page=page)])


def find_images(ctx: ToolContext, person: str | None = None, place: str | None = None,
                year_from: int | None = None, year_to: int | None = None,
                text: str | None = None, limit: int = 20) -> ImageResult:
    where: list[str] = []
    params: list = []
    if person:
        if "#" in person or person.strip().startswith("@"):
            ged, xref = parse_person_id(ctx, person)
            where.append("i.id IN (SELECT source_id FROM links WHERE source_kind = 'image' "
                         "AND person_ged_file = ? AND person_xref = ?)")
            params += [ged, xref]
        else:
            text = f"{person} {text or ''}"
    for value in (place, text):
        for token in normalize(value or "").split():
            where.append("i.search_norm LIKE ?")
            params.append(f"%{token}%")
    if year_from is not None:
        where.append("CAST(substr(i.date_taken, 1, 4) AS INTEGER) >= ?")
        params.append(year_from)
    if year_to is not None:
        where.append("CAST(substr(i.date_taken, 1, 4) AS INTEGER) <= ?")
        params.append(year_to)
    sql = ("SELECT i.*, f.path FROM images i JOIN files f ON f.id = i.file_id"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY i.date_taken, f.path LIMIT ?")
    rows = ctx.conn.execute(sql, [*params, max(1, min(limit, 100))]).fetchall()
    images = [ImageOut(
        file=r["path"], sidecar=r["sidecar_path"], title=r["title"], description=r["description"],
        date_taken=r["date_taken"], keywords=json.loads(r["keywords"]),
        persons_shown=json.loads(r["persons_shown"]), place=r["place"],
        gps_lat=r["gps_lat"], gps_lon=r["gps_lon"], source=SourceRef(file=r["path"]),
    ) for r in rows]
    return ImageResult(images=images, sources=[i.source for i in images])
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_search.py -v`
Expected: all pass (`test_find_images` is SKIPPED without ExifTool).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: hybrid document search, document reader and image search tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Conflict checks and the `conflicts` command

**Files:**
- Create: `src/genea/tools/conflicts.py`
- Modify: `src/genea/cli/querycmds.py` (add `conflicts`)
- Test: `tests/test_conflicts.py`; Modify: `tests/test_cli_query.py`

**Interfaces:**
- Consumes: `event_rows`, `first_event`, `summary`, `families_as_child`, `families_as_spouse`, `BIRTH_TYPES` (Task 9); `parse_person_id`, `person_id` (Task 9); `Conflict`, `ConflictResult`, `SourceRef` (Task 9); `links` table (Task 8).
- Produces: `genea.tools.conflicts.find_conflicts(ctx, pid=None, limit=100) -> ConflictResult`. Conflict kinds: `event_after_death`, `baptism_before_birth`, `parent_too_young`, `mother_too_old`, `married_too_young` (severity `error`) and `document_mismatch` (severity `needs_review`). CLI `genea conflicts [PERSON_ID]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_conflicts.py`:
```python
import pytest

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import init_kb
from genea.tools.base import open_tools
from genea.tools.conflicts import find_conflicts

T = "tree.ged#"

SYNTHETIC = """0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Otec /Mladý/
1 SEX M
1 BIRT
2 DATE 1900
1 FAMS @F1@
0 @I2@ INDI
1 NAME Matka /Stará/
1 SEX F
1 BIRT
2 DATE 1800
1 FAMS @F1@
0 @I3@ INDI
1 NAME Dítě /Mladé/
1 BIRT
2 DATE 1905
1 BAPM
2 DATE 1890
1 FAMC @F1@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1908
1 CHIL @I3@
0 TRLR
"""


@pytest.fixture
def synthetic(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / "tree.ged").write_text(SYNTHETIC, encoding="utf-8")
    kb = init_kb(root)
    index_kb(kb, FakeEmbedder())
    ctx = open_tools(kb, FakeEmbedder())
    yield ctx
    ctx.conn.close()


def test_rule_checks(synthetic):
    found = {(c.kind, c.person_id) for c in find_conflicts(synthetic).conflicts}
    assert found == {
        ("parent_too_young", T + "@I3@"),
        ("mother_too_old", T + "@I3@"),
        ("baptism_before_birth", T + "@I3@"),
        ("married_too_young", T + "@I1@"),
    }


def test_sample_has_exactly_one_hard_error(tools):
    errors = [c for c in find_conflicts(tools).conflicts if c.severity == "error"]
    assert [(c.kind, c.person_id) for c in errors] == [("event_after_death", T + "@I4@")]
    assert "1855" in errors[0].message
    assert all(s.file == "tree.ged" and s.line for s in errors[0].sources)


def test_document_mismatch_needs_review(tools):
    [conflict] = [c for c in find_conflicts(tools, T + "@I6@").conflicts if c.kind == "document_mismatch"]
    assert conflict.severity == "needs_review"
    assert conflict.sources[0].file == "docs/poznamky.md"
    assert "1851" in conflict.message


def test_limit(tools):
    assert len(find_conflicts(tools, limit=1).conflicts) == 1
```

Append to `tests/test_cli_query.py`:
```python
def test_conflicts_command(sample_root, monkeypatch):
    _indexed(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "conflicts"])
    assert result.exit_code == 0, result.output
    assert "event_after_death" in result.output
    assert "tree.ged#@I4@" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_conflicts.py tests/test_cli_query.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.tools.conflicts'` (and `No such command 'conflicts'`).

- [ ] **Step 3: Implement**

`src/genea/tools/conflicts.py`:
```python
"""Rule-based consistency checks over the family tree and the documents linked to it.

Date ranges are compared conservatively: a rule fires only if it is violated for every
date inside the ranges (e.g. 'ABT 1850' never triggers on a borderline case).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from genea.tools.base import ToolContext, parse_person_id, person_id
from genea.tools.models import Conflict, ConflictResult, SourceRef
from genea.tools.people import (BIRTH_TYPES, event_rows, families_as_child, families_as_spouse,
                                first_event, summary)

YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
ALLOWED_AFTER_DEATH = {"DEAT", "BURI", "CREM", "PROB"}


def _d(value: str) -> date:
    return date.fromisoformat(value)


def _years(start: str, end: str) -> float:
    return (_d(end) - _d(start)).days / 365.25


def find_conflicts(ctx: ToolContext, pid: str | None = None, limit: int = 100) -> ConflictResult:
    if pid:
        people = [parse_person_id(ctx, pid)]
    else:
        people = [(r["ged_file"], r["xref"]) for r in
                  ctx.conn.execute("SELECT ged_file, xref FROM persons ORDER BY id")]
    found: list[Conflict] = []
    for ged, xref in people:
        found += _check_person(ctx.conn, ged, xref)
        if len(found) >= limit:
            break
    found = found[:max(1, limit)]
    sources = list({s.cite(): s for c in found for s in c.sources}.values())
    return ConflictResult(conflicts=found, sources=sources)


def _check_person(conn: sqlite3.Connection, ged: str, xref: str) -> list[Conflict]:
    pid = person_id(ged, xref)
    events = event_rows(conn, ged, xref)
    birth = first_event(events, ("BIRT",))
    born = first_event(events, BIRTH_TYPES)
    death = first_event(events, ("DEAT",))
    out: list[Conflict] = []

    def ref(*rows) -> list[SourceRef]:
        return [SourceRef(file=ged, line=r["line_no"]) for r in rows if r is not None]

    def add(kind: str, message: str, sources: list[SourceRef]) -> None:
        out.append(Conflict(kind=kind, severity="error", person_id=pid, message=message, sources=sources))

    if death is not None and death["date_to"]:
        for e in events:
            if e["type"] not in ALLOWED_AFTER_DEATH and e["date_from"] and e["date_from"] > death["date_to"]:
                add("event_after_death",
                    f"{e['type']} ({e['date_raw']}) is dated after death ({death['date_raw']})", ref(e, death))

    if birth is not None and birth["date_from"]:
        for e in events:
            if e["type"] in ("BAPM", "CHR") and e["date_to"] and (_d(birth["date_from"]) - _d(e["date_to"])).days > 365:
                add("baptism_before_birth",
                    f"{e['type']} ({e['date_raw']}) is more than a year before birth ({birth['date_raw']})",
                    ref(e, birth))

    if born is not None and born["date_from"]:
        for family in families_as_child(conn, ged, xref):
            for role, parent in (("father", family["husband_xref"]), ("mother", family["wife_xref"])):
                if not parent:
                    continue
                parent_birth = first_event(event_rows(conn, ged, parent), BIRTH_TYPES)
                if parent_birth is None or not parent_birth["date_from"]:
                    continue
                name = summary(conn, ged, parent).name
                if _years(parent_birth["date_from"], born["date_to"]) < 12:
                    add("parent_too_young", f"The {role} {name} was under 12 at this person's birth",
                        ref(born, parent_birth))
                if role == "mother" and _years(parent_birth["date_to"], born["date_from"]) > 55:
                    add("mother_too_old", f"The mother {name} was over 55 at this person's birth",
                        ref(born, parent_birth))
        for family in families_as_spouse(conn, ged, xref):
            for e in event_rows(conn, ged, family["xref"]):
                if e["type"] == "MARR" and e["date_to"] and _years(born["date_from"], e["date_to"]) < 12:
                    add("married_too_young", f"Married ({e['date_raw']}) before the age of 12", ref(e, born))

    out += _document_mismatches(conn, ged, xref, pid, events, born)
    return out


def _document_mismatches(conn, ged, xref, pid, events, born) -> list[Conflict]:
    """Flag linked passages whose years match none of the person's GEDCOM events."""
    rows = list(events)
    for family in families_as_spouse(conn, ged, xref):
        rows += event_rows(conn, ged, family["xref"])
    ranges = [(int(e["date_from"][:4]), int(e["date_to"][:4])) for e in rows if e["date_from"] and e["date_to"]]
    if not ranges:
        return []
    passages = conn.execute(
        "SELECT c.text, c.page, c.chunk_no, f.path FROM links l "
        "JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id "
        "JOIN files f ON f.id = c.file_id "
        "WHERE l.person_ged_file = ? AND l.person_xref = ? AND l.confidence >= 1.0 "
        "ORDER BY f.path, c.chunk_no", (ged, xref)).fetchall()
    out = []
    for passage in passages:
        years = sorted({int(y) for y in YEAR_RE.findall(passage["text"])})
        if years and not any(lo <= y <= hi for y in years for lo, hi in ranges):
            sources = [SourceRef(file=passage["path"], page=passage["page"], chunk=passage["chunk_no"])]
            if born is not None:
                sources.append(SourceRef(file=ged, line=born["line_no"]))
            out.append(Conflict(
                kind="document_mismatch", severity="needs_review", person_id=pid,
                message=(f"{passage['path']} mentions this person with year(s) "
                         f"{', '.join(map(str, years))}, which match none of the GEDCOM events. "
                         "Check whether it is the same person and which date is right."),
                sources=sources,
            ))
    return out
```

Add to `src/genea/cli/querycmds.py`:
```python
from genea.tools.conflicts import find_conflicts


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
```
(Put the import at the top of the module.)

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_conflicts.py tests/test_cli_query.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: rule-based conflict checks and conflicts command

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Change store, research notes and the curator tools (`write_note`, `propose_change`)

**Files:**
- Create: `src/genea/changes/__init__.py` (empty), `src/genea/changes/store.py`, `src/genea/tools/curate.py`
- Test: `tests/test_curate.py`

**Interfaces:**
- Consumes: `KnowledgeBase` (Task 1); `IMAGE_EXTS`, `sidecar_for` (Task 7); `SourceRef`, `NoteResult`, `ChangeResult`, `ToolContext`, `ToolError` (Task 9); `normalize` (Task 4).
- Produces:
  - `genea.changes.store`: `STATUSES = ("pending", "applied", "rejected", "stale")`, `XMP_FIELDS`, `GEDCOM_REQUIRED`, `ChangeError`, `StaleChange(ChangeError)`
  - `Change` (pydantic): `id, created, target: dict[str,str], operation, payload: dict, rationale, sources: list[SourceRef], base_hash, status, reason, backup, applied_at`
  - `target_hash(kb, target) -> str`, `validate_change(kb, target_kind, target_path, operation, payload) -> dict` (the normalized target)
  - `ChangeStore(kb)` with `propose(target_kind, target_path, operation, payload, rationale, sources) -> tuple[Change, bool]`, `get(id)`, `list(status=None)`, `move(change, status, **updates) -> Change`, `reject(id, reason) -> Change`, `log(action, change, **extra)`
  - `genea.tools.curate.write_note(ctx, title, markdown, related_persons=None) -> NoteResult`, `propose_change(ctx, target_kind, target_path, operation, payload, rationale, sources=None) -> ChangeResult`

- [ ] **Step 1: Write the failing test**

`tests/test_curate.py`:
```python
import hashlib
import json

import pytest

from genea.changes.store import ChangeError, ChangeStore
from genea.tools.base import ToolError
from genea.tools.curate import propose_change, write_note
from genea.tools.models import SourceRef

WEDDING = "photos/svatba_1932.jpg"


def _propose(tools, payload=None, rationale="GEDCOM marriage F5, Brno 1932"):
    return propose_change(tools, "image_xmp", WEDDING, "set_fields",
                          payload or {"XMP-dc:Description": "Svatba Jana Nováka a Marie Dvořákové"},
                          rationale, [SourceRef(file="tree.ged", line=7)])


def test_write_note(tools):
    result = write_note(tools, "Rodina Novákových", "## Zjištění\nJosef byl sedlák.", ["tree.ged#@I1@"])
    assert result.path.startswith(".genea/notes/")
    assert result.path.endswith("-rodina-novakovych.md")
    text = (tools.kb.root / result.path).read_text(encoding="utf-8")
    assert 'title: "Rodina Novákových"' in text
    assert 'related_persons: ["tree.ged#@I1@"]' in text
    assert text.rstrip().endswith("Josef byl sedlák.")
    again = write_note(tools, "Rodina Novákových", "Druhá poznámka.")
    assert again.path.endswith("-rodina-novakovych-2.md")


def test_write_note_requires_content(tools):
    with pytest.raises(ToolError):
        write_note(tools, "Title", "   ")


def test_propose_image_change(tools):
    result = _propose(tools)
    assert result.created and result.status == "pending" and result.id.startswith("chg-")
    stored = json.loads((tools.kb.changes_dir / "pending" / f"{result.id}.json").read_text(encoding="utf-8"))
    assert stored["target"] == {"kind": "image_xmp", "path": WEDDING}
    sidecar = tools.kb.root / "photos" / "svatba_1932.xmp"
    assert stored["base_hash"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert stored["sources"] == [{"file": "tree.ged", "line": 7, "page": None, "chunk": None}]


def test_duplicate_is_not_proposed_twice(tools):
    first = _propose(tools)
    second = _propose(tools)
    assert (second.id, second.created) == (first.id, False)
    assert len(ChangeStore(tools.kb).list("pending")) == 1


def test_rejected_change_is_not_reproposed(tools):
    first = _propose(tools)
    ChangeStore(tools.kb).reject(first.id, "wrong people")
    again = _propose(tools)
    assert (again.id, again.created, again.status) == (first.id, False, "rejected")


def test_ids_increment(tools):
    a = _propose(tools, {"XMP-dc:Title": "A"})
    b = _propose(tools, {"XMP-dc:Title": "B"})
    assert int(b.id[-4:]) == int(a.id[-4:]) + 1


@pytest.mark.parametrize("kind, path, operation, payload, message", [
    ("image_xmp", WEDDING, "set_fields", {"EXIF:Make": "x"}, "not allowed"),
    ("image_xmp", WEDDING, "set_fields", {}, "at least one"),
    ("image_xmp", WEDDING, "set_fields", {"XMP-dc:Title": 5}, "string"),
    ("image_xmp", WEDDING, "add_keywords", {"keywords": []}, "keywords"),
    ("image_xmp", WEDDING, "rotate", {}, "Unknown operation"),
    ("image_xmp", "docs/kronika.txt", "set_fields", {"XMP-dc:Title": "x"}, "not an image"),
    ("image_xmp", "../x.jpg", "set_fields", {"XMP-dc:Title": "x"}, "outside"),
    ("image_xmp", "photos/missing.jpg", "set_fields", {"XMP-dc:Title": "x"}, "does not exist"),
    ("gedcom", "tree.ged", "add_event", {"person_id": "tree.ged#@I1@"}, "missing"),
    ("gedcom", "tree.ged", "delete_person", {"person_id": "x"}, "Unknown operation"),
    ("gedcom", "docs/kronika.txt", "add_note", {"person_id": "x", "text": "y"}, "not a GEDCOM"),
    ("video", "tree.ged", "add_note", {}, "target_kind"),
])
def test_invalid_proposals(tools, kind, path, operation, payload, message):
    with pytest.raises(ToolError, match=message):
        propose_change(tools, kind, path, operation, payload, "because")


def test_rationale_required(tools):
    with pytest.raises(ToolError, match="rationale"):
        _propose(tools, rationale=" ")


def test_reject_moves_file_and_logs(tools):
    change = _propose(tools)
    store = ChangeStore(tools.kb)
    rejected = store.reject(change.id, "not needed")
    assert rejected.status == "rejected" and rejected.reason == "not needed"
    assert not (tools.kb.changes_dir / "pending" / f"{change.id}.json").exists()
    assert (tools.kb.changes_dir / "rejected" / f"{change.id}.json").exists()
    log = [json.loads(line) for line in (tools.kb.changes_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [entry["action"] for entry in log] == ["propose", "reject"]
    with pytest.raises(ChangeError):
        store.reject(change.id, "again")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_curate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.changes'`.

- [ ] **Step 3: Implement the change store**

`src/genea/changes/store.py`:
```python
"""Change sets: edits proposed by the agent, stored as JSON until the user acts on them.

.genea/changes/<status>/<id>.json, status in pending | applied | rejected | stale,
plus an append-only audit log in .genea/changes/log.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from genea.ingest.images import IMAGE_EXTS, sidecar_for
from genea.kb import KnowledgeBase
from genea.tools.models import SourceRef

STATUSES = ("pending", "applied", "rejected", "stale")
XMP_FIELDS = frozenset({
    "XMP-dc:Title", "XMP-dc:Description", "XMP-dc:Subject", "XMP-iptcExt:PersonInImage",
    "XMP-photoshop:City", "XMP-photoshop:Country", "XMP-photoshop:DateCreated", "XMP-iptcCore:Location",
})
IMAGE_OPERATIONS = ("set_fields", "add_keywords", "remove_keywords")
GEDCOM_REQUIRED = {
    "add_event": ("person_id", "type"),
    "update_event": ("person_id", "line"),
    "add_note": ("person_id", "text"),
    "add_source_citation": ("person_id", "source"),
    "link_child": ("family_id", "child_id"),
    "add_person": ("given", "surname"),
}


class ChangeError(Exception):
    """A change is invalid or cannot be applied in its current state."""


class StaleChange(ChangeError):
    """The target file changed after the change was proposed."""


class Change(BaseModel):
    id: str
    created: str
    target: dict[str, str]
    operation: str
    payload: dict[str, Any]
    rationale: str
    sources: list[SourceRef] = Field(default_factory=list)
    base_hash: str | None = None
    status: str = "pending"
    reason: str | None = None
    backup: str | None = None
    applied_at: str | None = None


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def target_hash(kb: KnowledgeBase, target: dict[str, str]) -> str:
    """Hash of what a change will modify: the sidecar if it exists (else the image), or the .ged file."""
    path = kb.resolve_inside(target["path"])
    if target["kind"] == "image_xmp":
        path = sidecar_for(path) or path
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_text_list(value) -> bool:
    return isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value))


def validate_change(kb: KnowledgeBase, target_kind: str, target_path: str,
                    operation: str, payload: dict) -> dict[str, str]:
    if target_kind not in ("image_xmp", "gedcom"):
        raise ChangeError("target_kind must be 'image_xmp' or 'gedcom'")
    try:
        path = kb.resolve_inside(target_path)
    except ValueError as exc:
        raise ChangeError(str(exc)) from exc
    if not path.is_file():
        raise ChangeError(f"Target file does not exist: {target_path}")
    rel = kb.rel(path)
    if target_kind == "image_xmp":
        if path.suffix.lower() not in IMAGE_EXTS:
            raise ChangeError(f"{rel} is not an image")
        if operation == "set_fields":
            if not payload:
                raise ChangeError("set_fields needs at least one field")
            bad = sorted(set(payload) - XMP_FIELDS)
            if bad:
                raise ChangeError(f"Fields not allowed: {bad}. Allowed: {sorted(XMP_FIELDS)}")
            for key, value in payload.items():
                if not _is_text_list(value):
                    raise ChangeError(f"{key} must be a string or a list of strings")
        elif operation in ("add_keywords", "remove_keywords"):
            keywords = payload.get("keywords")
            if not isinstance(keywords, list) or not keywords or not all(
                    isinstance(k, str) and k.strip() for k in keywords):
                raise ChangeError("payload must be {'keywords': ['...', ...]}")
        else:
            raise ChangeError(f"Unknown operation {operation!r} for image_xmp; use {', '.join(IMAGE_OPERATIONS)}")
    else:
        if path.suffix.lower() != ".ged":
            raise ChangeError(f"{rel} is not a GEDCOM file")
        required = GEDCOM_REQUIRED.get(operation)
        if required is None:
            raise ChangeError(f"Unknown operation {operation!r} for gedcom; use {', '.join(GEDCOM_REQUIRED)}")
        missing = [k for k in required if payload.get(k) in (None, "")]
        if missing:
            raise ChangeError(f"payload for {operation} is missing {missing}")
    return {"kind": target_kind, "path": rel}


class ChangeStore:
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.dir = kb.changes_dir
        kb.ensure_dirs()

    def _path(self, status: str, change_id: str):
        return self.dir / status / f"{change_id}.json"

    def _write(self, change: Change) -> None:
        path = self._path(change.status, change.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(change.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def list(self, status: str | None = None) -> list[Change]:
        changes = []
        for st in ([status] if status else STATUSES):
            for path in sorted((self.dir / st).glob("*.json")):
                changes.append(Change.model_validate_json(path.read_text(encoding="utf-8")))
        return sorted(changes, key=lambda c: c.id)

    def get(self, change_id: str) -> Change:
        for status in STATUSES:
            path = self._path(status, change_id)
            if path.exists():
                return Change.model_validate_json(path.read_text(encoding="utf-8"))
        raise ChangeError(f"No change with id {change_id}")

    def _next_id(self) -> str:
        prefix = f"chg-{datetime.now():%Y%m%d}-"
        taken = [int(c.id[len(prefix):]) for c in self.list() if c.id.startswith(prefix)]
        return f"{prefix}{max(taken, default=0) + 1:04d}"

    def propose(self, target_kind: str, target_path: str, operation: str, payload: dict,
                rationale: str, sources: list[SourceRef]) -> tuple[Change, bool]:
        if not rationale or not rationale.strip():
            raise ChangeError("Explain the change in rationale")
        target = validate_change(self.kb, target_kind, target_path, operation, payload)
        for existing in self.list("pending") + self.list("rejected"):
            if (existing.target, existing.operation, existing.payload) == (target, operation, payload):
                return existing, False
        change = Change(id=self._next_id(), created=now(), target=target, operation=operation,
                        payload=payload, rationale=rationale.strip(), sources=sources,
                        base_hash=target_hash(self.kb, target))
        self._write(change)
        self.log("propose", change)
        return change, True

    def move(self, change: Change, status: str, **updates) -> Change:
        old_path = self._path(change.status, change.id)
        moved = change.model_copy(update={"status": status, **updates})
        self._write(moved)
        if old_path != self._path(status, change.id):
            old_path.unlink(missing_ok=True)
        return moved

    def reject(self, change_id: str, reason: str) -> Change:
        change = self.get(change_id)
        if change.status != "pending":
            raise ChangeError(f"{change.id} is {change.status}; only pending changes can be rejected")
        rejected = self.move(change, "rejected", reason=reason or "rejected")
        self.log("reject", rejected, reason=reason)
        return rejected

    def log(self, action: str, change: Change, **extra) -> None:
        entry = {"ts": now(), "action": action, "id": change.id, "status": change.status, **extra}
        with (self.dir / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Implement the curator tools**

`src/genea/tools/curate.py`:
```python
"""Curator tools: research notes (written directly) and change proposals (reviewed by the user)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from genea.changes.store import ChangeError, ChangeStore
from genea.text import normalize
from genea.tools.base import ToolContext, ToolError
from genea.tools.models import ChangeResult, NoteResult, SourceRef


def write_note(ctx: ToolContext, title: str, markdown: str,
               related_persons: list[str] | None = None) -> NoteResult:
    if not title.strip() or not markdown.strip():
        raise ToolError("A note needs a title and some Markdown content.")
    slug = "-".join(normalize(title).split())[:60] or "note"
    stem = f"{date.today().isoformat()}-{slug}"
    path = ctx.kb.notes_dir / f"{stem}.md"
    n = 2
    while path.exists():
        path = ctx.kb.notes_dir / f"{stem}-{n}.md"
        n += 1
    front = ["---", f"title: {json.dumps(title.strip(), ensure_ascii=False)}",
             f"created: {datetime.now().isoformat(timespec='seconds')}",
             f"related_persons: {json.dumps(related_persons or [], ensure_ascii=False)}", "---", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(front) + markdown.strip() + "\n", encoding="utf-8")
    return NoteResult(path=ctx.kb.rel(path))


def propose_change(ctx: ToolContext, target_kind: str, target_path: str, operation: str,
                   payload: dict[str, Any], rationale: str,
                   sources: list[SourceRef] | None = None) -> ChangeResult:
    try:
        change, created = ChangeStore(ctx.kb).propose(target_kind, target_path, operation, payload,
                                                      rationale, sources or [])
    except ChangeError as exc:
        raise ToolError(str(exc)) from exc
    if created:
        message = (f"Proposed as {change.id}. Nothing has changed yet: the user reviews it with "
                   f"`genea changes show {change.id}` and applies it with `genea changes apply {change.id}`.")
    else:
        message = f"An identical change already exists ({change.id}, {change.status}); not proposed again."
    return ChangeResult(id=change.id, status=change.status, created=created, message=message,
                        sources=change.sources)
```

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_curate.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: change store with validation and de-duplication, notes and proposal tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Applying, undoing and exporting changes, and the `changes` commands

**Files:**
- Create: `src/genea/changes/apply.py`, `src/genea/changes/export.py`, `src/genea/cli/changecmds.py`
- Modify: `src/genea/cli/main.py` (import `changecmds`)
- Test: `tests/test_changes_apply.py`, `tests/test_export.py`, `tests/test_cli_changes.py`

**Interfaces:**
- Consumes: `Change`, `ChangeStore`, `ChangeError`, `StaleChange`, `target_hash`, `now` (Task 12); `run_exiftool`, `read_metadata`, `ExifToolError` (Task 1); `default_sidecar`, `sidecar_for` (Task 7); `reindex_file` (Task 8); `Embedder` (Task 5); `propose_change` (Task 12, used in tests).
- Produces:
  - `genea.changes.apply`: `ABSENT = "<absent>"`, `xmp_args(change, sidecar) -> list[str]`, `apply_change(kb, store, change_id, embedder) -> Change`, `undo_change(kb, store, change_id, embedder) -> Change`, `mark_manual(kb, store, change_id) -> Change`, `describe_change(kb, change) -> list[tuple[str, str, str]]` as `(field, before, after)`
  - `genea.changes.export.export_gedcom_changes(kb, store) -> tuple[Path, Path] | None` returning `(checklist.md, changes.ged)`
  - CLI: `genea changes list [--status]`, `show <id>`, `apply <id> | --all | <id> --manual`, `reject <id> [--reason]`, `undo <id>`, `export`

- [ ] **Step 1: Write the failing tests**

`tests/test_changes_apply.py`:
```python
import json
from pathlib import Path

import pytest

from genea.changes.apply import (ABSENT, apply_change, describe_change, mark_manual,
                                 undo_change, xmp_args)
from genea.changes.store import Change, ChangeError, ChangeStore, StaleChange
from genea.exiftool import read_metadata
from genea.index.embed import FakeEmbedder
from genea.tools.curate import propose_change


def _image_change(ctx, path="photos/svatba_1932.jpg", operation="set_fields", payload=None):
    return propose_change(ctx, "image_xmp", path, operation,
                          payload or {"XMP-dc:Description": "Nový popis"}, "test").id


def _ged_change(ctx):
    return propose_change(ctx, "gedcom", "tree.ged", "add_event",
                          {"person_id": "tree.ged#@I9@", "type": "BAPM", "date": "1875"}, "matrika p.2").id


def test_xmp_args():
    change = Change(id="c", created="", target={"kind": "image_xmp", "path": "p.jpg"}, operation="set_fields",
                    payload={"XMP-dc:Title": "T", "XMP-iptcExt:PersonInImage": ["A", "B"], "XMP-photoshop:City": []},
                    rationale="r")
    assert xmp_args(change, Path("p.xmp")) == [
        "-overwrite_original", "-XMP-dc:Title=T", "-XMP-iptcExt:PersonInImage=A",
        "-XMP-iptcExt:PersonInImage=B", "-XMP-photoshop:City=", "p.xmp"]
    add = change.model_copy(update={"operation": "add_keywords", "payload": {"keywords": ["x"]}})
    assert xmp_args(add, Path("p.xmp")) == ["-overwrite_original", "-XMP-dc:Subject-=x", "-XMP-dc:Subject+=x", "p.xmp"]
    remove = change.model_copy(update={"operation": "remove_keywords", "payload": {"keywords": ["x"]}})
    assert xmp_args(remove, Path("p.xmp")) == ["-overwrite_original", "-XMP-dc:Subject-=x", "p.xmp"]


def test_stale_change_is_not_applied(tools):
    change_id = _image_change(tools)
    sidecar = tools.kb.root / "photos" / "svatba_1932.xmp"
    sidecar.write_text(sidecar.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    store = ChangeStore(tools.kb)
    with pytest.raises(StaleChange):
        apply_change(tools.kb, store, change_id, FakeEmbedder())
    assert store.get(change_id).status == "stale"


def test_gedcom_changes_are_export_only(tools):
    change_id = _ged_change(tools)
    store = ChangeStore(tools.kb)
    with pytest.raises(ChangeError, match="export"):
        apply_change(tools.kb, store, change_id, FakeEmbedder())
    assert store.get(change_id).status == "pending"
    done = mark_manual(tools.kb, store, change_id)
    assert (done.status, done.reason) == ("applied", "applied manually")
    with pytest.raises(ChangeError):
        undo_change(tools.kb, store, change_id, FakeEmbedder())


def test_manual_is_only_for_gedcom(tools):
    with pytest.raises(ChangeError, match="GEDCOM"):
        mark_manual(tools.kb, ChangeStore(tools.kb), _image_change(tools))


def test_only_pending_changes_apply(tools):
    change_id = _image_change(tools)
    store = ChangeStore(tools.kb)
    store.reject(change_id, "no")
    with pytest.raises(ChangeError, match="rejected"):
        apply_change(tools.kb, store, change_id, FakeEmbedder())


def test_describe_gedcom_change(tools):
    store = ChangeStore(tools.kb)
    rows = describe_change(tools.kb, store.get(_ged_change(tools)))
    assert ("type", "", "BAPM") in rows


@pytest.mark.exiftool
def test_apply_and_undo_with_existing_sidecar(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "svatba_1932.xmp"
    original = sidecar.read_bytes()
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx)
    [(field, before, after)] = describe_change(ctx.kb, store.get(change_id))
    assert (field, after) == ("XMP-dc:Description", "Nový popis")
    assert before.startswith("Svatba Jana")

    applied = apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert applied.status == "applied"
    assert applied.backup.startswith(".genea/backups/")
    assert read_metadata([sidecar])[0]["XMP-dc:Description"] == "Nový popis"
    row = ctx.conn.execute("SELECT i.description FROM images i JOIN files f ON f.id = i.file_id "
                           "WHERE f.path = 'photos/svatba_1932.jpg'").fetchone()
    assert row["description"] == "Nový popis"

    undone = undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert (undone.status, undone.reason) == ("rejected", "undone")
    assert sidecar.read_bytes() == original
    log = [json.loads(l)["action"] for l in (ctx.kb.changes_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log == ["propose", "apply", "undo"]


@pytest.mark.exiftool
def test_apply_creates_sidecar_and_undo_removes_it(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "dum.xmp"
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx, "photos/dum.jpg", "add_keywords", {"keywords": ["dům"]})
    applied = apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert applied.backup == ABSENT
    assert read_metadata([sidecar])[0]["XMP-dc:Subject"] in ("dům", ["dům"])
    undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert not sidecar.exists()
```

`tests/test_export.py`:
```python
from genea.changes.export import export_gedcom_changes
from genea.changes.store import ChangeStore
from genea.ingest.gedcom.parser import parse_gedcom
from genea.tools.curate import propose_change
from genea.tools.models import SourceRef


def test_export_nothing_pending(tools):
    assert export_gedcom_changes(tools.kb, ChangeStore(tools.kb)) is None


def test_export(tools):
    event = propose_change(tools, "gedcom", "tree.ged", "add_event",
                           {"person_id": "tree.ged#@I9@", "type": "BAPM", "date": "1875", "place": "Brno"},
                           "The register says he was baptized in 1875",
                           [SourceRef(file="docs/matrika.pdf", page=2)])
    propose_change(tools, "gedcom", "tree.ged", "add_person",
                   {"given": "Karel", "surname": "Novák", "sex": "M", "birth_date": "1897"}, "Mentioned in a letter")
    propose_change(tools, "gedcom", "tree.ged", "add_note",
                   {"person_id": "tree.ged#@I1@", "text": "Line one\nLine two"}, "Chronicle")
    propose_change(tools, "gedcom", "tree.ged", "link_child",
                   {"family_id": "tree.ged#@F6@", "child_id": "tree.ged#@I14@"}, "Test link")
    propose_change(tools, "image_xmp", "photos/dum.jpg", "add_keywords", {"keywords": ["dům"]}, "Not exported")

    md_path, ged_path = export_gedcom_changes(tools.kb, ChangeStore(tools.kb))
    md = md_path.read_text(encoding="utf-8")
    assert event.id in md
    assert "- [ ] Add BAPM to tree.ged#@I9@: date 1875, place Brno" in md
    assert "[docs/matrika.pdf p.2]" in md
    assert "genea changes apply <id> --manual" in md
    assert "dům" not in md

    lines = ged_path.read_text(encoding="utf-8").splitlines()
    assert (lines[0], lines[-1]) == ("0 HEAD", "0 TRLR")
    for expected in ("0 @I9@ INDI", "1 BAPM", "2 DATE 1875", "2 PLAC Brno", "0 @GENEA2@ INDI",
                     "1 NAME Karel /Novák/", "1 NOTE Line one", "2 CONT Line two",
                     "0 @F6@ FAM", "1 CHIL @I14@"):
        assert expected in lines
    assert parse_gedcom(ged_path).warnings == []
```

`tests/test_cli_changes.py`:
```python
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.kb import KnowledgeBase
from genea.tools.base import open_tools
from genea.tools.curate import propose_change

runner = CliRunner()


def _setup(sample_root, monkeypatch):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0
    tools = open_tools(KnowledgeBase.open(str(sample_root)), FakeEmbedder())
    image = propose_change(tools, "image_xmp", "photos/svatba_1932.jpg", "set_fields",
                           {"XMP-dc:Title": "Svatba 1932"}, "better title")
    ged = propose_change(tools, "gedcom", "tree.ged", "add_event",
                         {"person_id": "tree.ged#@I9@", "type": "BAPM", "date": "1875"}, "matrika says so")
    tools.conn.close()
    return image.id, ged.id


def _run(sample_root, *args):
    return runner.invoke(app, ["--kb", str(sample_root), "changes", *args])


def test_changes_workflow(sample_root, monkeypatch):
    image_id, ged_id = _setup(sample_root, monkeypatch)
    listing = _run(sample_root, "list")
    assert listing.exit_code == 0 and image_id in listing.output and ged_id in listing.output

    shown = _run(sample_root, "show", ged_id)
    assert shown.exit_code == 0 and "BAPM" in shown.output and "matrika says so" in shown.output

    exported = _run(sample_root, "export")
    assert exported.exit_code == 0 and "checklist" in exported.output

    refused = _run(sample_root, "apply", ged_id)
    assert refused.exit_code == 1 and "export" in refused.output
    assert _run(sample_root, "apply", ged_id, "--manual").exit_code == 0

    assert _run(sample_root, "reject", image_id, "--reason", "not needed").exit_code == 0
    everything = _run(sample_root, "list", "--status", "all")
    assert "applied" in everything.output and "rejected" in everything.output

    assert _run(sample_root, "undo", image_id).exit_code == 1  # rejected, not applied
    assert _run(sample_root, "show", "chg-nope").exit_code == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_changes_apply.py tests/test_export.py tests/test_cli_changes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.changes.apply'`.

- [ ] **Step 3: Implement apply and undo**

`src/genea/changes/apply.py`:
```python
"""Apply, undo and describe changes. Only XMP sidecars are ever written."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from genea.changes.store import Change, ChangeError, ChangeStore, StaleChange, now, target_hash
from genea.exiftool import ExifToolError, read_metadata, run_exiftool
from genea.index.embed import Embedder
from genea.index.indexer import reindex_file
from genea.ingest.images import default_sidecar, sidecar_for
from genea.kb import KnowledgeBase

ABSENT = "<absent>"  # backup marker: there was no sidecar before the change
KEYWORD_FIELD = "XMP-dc:Subject"


def xmp_args(change: Change, sidecar: Path) -> list[str]:
    args = ["-overwrite_original"]
    if change.operation == "set_fields":
        for key, value in change.payload.items():
            values = value if isinstance(value, list) else [value]
            if not values:
                args.append(f"-{key}=")
            args += [f"-{key}={v}" for v in values]
    elif change.operation == "add_keywords":
        for keyword in change.payload["keywords"]:
            args += [f"-{KEYWORD_FIELD}-={keyword}", f"-{KEYWORD_FIELD}+={keyword}"]  # no duplicates
    elif change.operation == "remove_keywords":
        args += [f"-{KEYWORD_FIELD}-={k}" for k in change.payload["keywords"]]
    else:
        raise ChangeError(f"Unknown image operation {change.operation!r}")
    return [*args, str(sidecar)]


def _require(store: ChangeStore, change_id: str, status: str) -> Change:
    change = store.get(change_id)
    if change.status != status:
        raise ChangeError(f"{change.id} is {change.status}; this needs a {status} change")
    return change


def apply_change(kb: KnowledgeBase, store: ChangeStore, change_id: str, embedder: Embedder) -> Change:
    change = _require(store, change_id, "pending")
    if change.target["kind"] == "gedcom":
        raise ChangeError(
            "GEDCOM changes are not written to the .ged file in this version. Run `genea changes export`, "
            "make the change in your genealogy program, then run `genea changes apply <id> --manual`."
        )
    image = kb.resolve_inside(change.target["path"])
    if target_hash(kb, change.target) != change.base_hash:
        stale = store.move(change, "stale", reason="the file changed after the change was proposed")
        store.log("stale", stale)
        raise StaleChange(f"{change.id}: {change.target['path']} changed after the proposal; "
                          "ask the agent to propose it again.")
    sidecar = default_sidecar(image)
    if sidecar.exists():
        backup = kb.backups_dir / f"{datetime.now():%Y%m%dT%H%M%S%f}" / kb.rel(sidecar)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sidecar, backup)
        backup_ref = kb.rel(backup)
    else:
        backup_ref = ABSENT
    exe = kb.settings.exiftool
    try:
        if backup_ref == ABSENT:
            try:  # start the new sidecar from the XMP already embedded in the image
                run_exiftool(["-tagsfromfile", str(image), "-xmp:all", str(sidecar)], exe)
            except ExifToolError:
                pass  # the image has no XMP; the next call creates the sidecar from scratch
        run_exiftool(xmp_args(change, sidecar), exe)
    except Exception:
        if backup_ref == ABSENT:
            sidecar.unlink(missing_ok=True)
        else:
            shutil.copy2(kb.root / backup_ref, sidecar)
        raise
    applied = store.move(change, "applied", backup=backup_ref, applied_at=now())
    store.log("apply", applied, sidecar=kb.rel(sidecar))
    reindex_file(kb, embedder, image)
    return applied


def undo_change(kb: KnowledgeBase, store: ChangeStore, change_id: str, embedder: Embedder) -> Change:
    change = _require(store, change_id, "applied")
    if change.target["kind"] != "image_xmp":
        raise ChangeError("Only image changes written by genea can be undone; "
                          "undo GEDCOM edits in your genealogy program.")
    image = kb.resolve_inside(change.target["path"])
    sidecar = default_sidecar(image)
    if change.backup == ABSENT:
        sidecar.unlink(missing_ok=True)
    else:
        shutil.copy2(kb.root / change.backup, sidecar)
    undone = store.move(change, "rejected", reason="undone")
    store.log("undo", undone)
    reindex_file(kb, embedder, image)
    return undone


def mark_manual(kb: KnowledgeBase, store: ChangeStore, change_id: str) -> Change:
    change = _require(store, change_id, "pending")
    if change.target["kind"] != "gedcom":
        raise ChangeError("--manual is only for GEDCOM changes; image changes are applied by genea")
    done = store.move(change, "applied", reason="applied manually", applied_at=now())
    store.log("apply_manual", done)
    return done


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _current_metadata(kb: KnowledgeBase, change: Change) -> dict | None:
    image = kb.resolve_inside(change.target["path"])
    sidecar = sidecar_for(image)
    try:
        merged: dict = {}
        for record in read_metadata([image] + ([sidecar] if sidecar else []), kb.settings.exiftool):
            merged.update(record)
        return merged
    except ExifToolError:
        return None


def describe_change(kb: KnowledgeBase, change: Change) -> list[tuple[str, str, str]]:
    """(field, before, after) rows; 'before' is '?' when ExifTool is unavailable."""
    if change.target["kind"] != "image_xmp":
        return [(key, "", _fmt(value)) for key, value in change.payload.items()]
    current = _current_metadata(kb, change)
    if change.operation == "set_fields":
        return [(key, "?" if current is None else _fmt(current.get(key)), _fmt(value))
                for key, value in change.payload.items()]
    keywords = change.payload["keywords"]
    adding = change.operation == "add_keywords"
    if current is None:
        return [(KEYWORD_FIELD, "?", ("+ " if adding else "- ") + ", ".join(keywords))]
    raw = current.get(KEYWORD_FIELD)
    before = [str(k) for k in (raw if isinstance(raw, list) else [raw] if raw else [])]
    after = list(dict.fromkeys(before + keywords)) if adding else [k for k in before if k not in keywords]
    return [(KEYWORD_FIELD, _fmt(before), _fmt(after))]
```

- [ ] **Step 4: Implement the GEDCOM export**

`src/genea/changes/export.py`:
```python
"""Export pending GEDCOM changes as a Markdown checklist and a GEDCOM fragment."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from genea.changes.store import Change, ChangeStore
from genea.kb import KnowledgeBase


def _xref(ref: str) -> str:
    return ref.split("#", 1)[-1]  # "tree.ged#@I9@" -> "@I9@"


def _instruction(change: Change) -> str:
    p, op = change.payload, change.operation
    if op == "add_event":
        return f"Add {p['type']} to {p['person_id']}: date {p.get('date') or '—'}, place {p.get('place') or '—'}"
    if op == "update_event":
        updates = ", ".join(f"{k} → {v}" for k, v in p.items() if k not in ("person_id", "line"))
        return f"Update the event on line {p['line']} of {p['person_id']}: {updates}"
    if op == "add_note":
        return f"Add a note to {p['person_id']}: {p['text']}"
    if op == "add_source_citation":
        page = f", page {p['page']}" if p.get("page") else ""
        where = f" ({p['event_type']})" if p.get("event_type") else ""
        return f"Cite source '{p['source']}'{page} on {p['person_id']}{where}"
    if op == "link_child":
        return f"Add {p['child_id']} as a child of family {p['family_id']}"
    details = ", ".join(f"{k} {p[k]}" for k in ("sex", "birth_date", "birth_place") if p.get(k))
    return f"Add a new person {p['given']} {p['surname']}" + (f" ({details})" if details else "")


def _text_lines(level: int, tag: str, text: str) -> list[str]:
    first, *rest = (text or "").split("\n")
    return [f"{level} {tag} {first}".rstrip()] + [f"{level + 1} CONT {line}".rstrip() for line in rest]


def _ged_lines(change: Change, n: int) -> list[str]:
    p, op = change.payload, change.operation
    if op == "add_person":
        lines = [f"0 @GENEA{n}@ INDI", f"1 NAME {p['given']} /{p['surname']}/"]
        if p.get("sex"):
            lines.append(f"1 SEX {p['sex']}")
        if p.get("birth_date") or p.get("birth_place"):
            lines.append("1 BIRT")
            if p.get("birth_date"):
                lines.append(f"2 DATE {p['birth_date']}")
            if p.get("birth_place"):
                lines.append(f"2 PLAC {p['birth_place']}")
        return lines
    if op == "link_child":
        return [f"0 {_xref(p['family_id'])} FAM", f"1 CHIL {_xref(p['child_id'])}"]
    lines = [f"0 {_xref(p['person_id'])} INDI"]
    if op == "add_event":
        lines.append(f"1 {p['type']}")
        if p.get("date"):
            lines.append(f"2 DATE {p['date']}")
        if p.get("place"):
            lines.append(f"2 PLAC {p['place']}")
    elif op == "update_event":
        lines += _text_lines(1, "NOTE", f"genea {change.id}: {_instruction(change)}")
    elif op == "add_note":
        lines += _text_lines(1, "NOTE", p["text"])
    elif op == "add_source_citation":
        level = 1
        if p.get("event_type"):
            lines.append(f"1 {p['event_type']}")
            level = 2
        lines.append(f"{level} SOUR {p['source']}")
        if p.get("page"):
            lines.append(f"{level + 1} PAGE {p['page']}")
    return lines


def export_gedcom_changes(kb: KnowledgeBase, store: ChangeStore) -> tuple[Path, Path] | None:
    changes = [c for c in store.list("pending") if c.target["kind"] == "gedcom"]
    if not changes:
        return None
    day = date.today().isoformat()
    kb.exports_dir.mkdir(parents=True, exist_ok=True)
    md_path = kb.exports_dir / f"{day}-checklist.md"
    ged_path = kb.exports_dir / f"{day}-changes.ged"
    md = ["# GEDCOM changes to make", "",
          f"{len(changes)} pending change(s), exported {datetime.now():%Y-%m-%d %H:%M}.",
          "Make each change in your genealogy program, then run `genea changes apply <id> --manual`.", ""]
    for change in changes:
        cites = ", ".join(s.cite() for s in change.sources) or "none given"
        md += [f"## {change.id} — {change.operation} in {change.target['path']}", "",
               f"- [ ] {_instruction(change)}", f"- Why: {change.rationale}", f"- Sources: {cites}", ""]
    ged = ["0 HEAD", "1 SOUR GENEA", "1 GEDC", "2 VERS 5.5.1", "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8",
           "1 NOTE Changes proposed by genea. Records that reuse existing ids must be merged by hand."]
    for n, change in enumerate(changes, start=1):
        ged += _ged_lines(change, n)
    ged.append("0 TRLR")
    md_path.write_text("\n".join(md), encoding="utf-8")
    ged_path.write_text("\n".join(ged) + "\n", encoding="utf-8")
    return md_path, ged_path
```

Note: in `test_export`, the `add_person` change is the second GEDCOM change, so its xref is `@GENEA2@`.

- [ ] **Step 5: Implement the CLI**

`src/genea/cli/changecmds.py`:
```python
"""`genea changes ...`: review, apply, reject, undo and export proposed changes."""

from __future__ import annotations

from typing import NoReturn

import typer
from rich.markup import escape

from genea.changes.apply import apply_change, describe_change, mark_manual, undo_change
from genea.changes.export import export_gedcom_changes
from genea.changes.store import STATUSES, ChangeError, ChangeStore
from genea.cli import common
from genea.cli.app import changes_app
from genea.exiftool import ExifToolError


def _fail(exc: Exception) -> NoReturn:
    common.console.print(f"[red]{escape(str(exc))}[/red]")
    raise typer.Exit(1)


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
```

In `src/genea/cli/main.py`, change the import line to:
```python
from genea.cli import changecmds, indexcmds, kbcmds, querycmds  # noqa: E402,F401
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_changes_apply.py tests/test_export.py tests/test_cli_changes.py -v`
Expected: all pass (the two ExifTool tests are SKIPPED without ExifTool).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: apply/undo XMP sidecar changes with backups, GEDCOM export, changes commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The agent (PydanticAI), `ask` and `chat`

**Files:**
- Create: `src/genea/agent/__init__.py` (empty), `src/genea/agent/prompt.py`, `src/genea/agent/model.py`, `src/genea/agent/core.py`, `src/genea/cli/agentcmds.py`
- Modify: `src/genea/cli/main.py` (import `agentcmds`)
- Test: `tests/test_agent.py`, `tests/test_cli_agent.py`

**Interfaces:**
- Consumes: all tool functions (Tasks 9–12), `ToolContext`, `ToolError`, `SourceRef`, `ChangeStore` (tests), `Settings`.
- Produces:
  - `genea.agent.model.build_model(settings) -> Model | str` (an `OpenAIChatModel` through `OllamaProvider` for `ollama:<name>`; any other string is passed to PydanticAI unchanged, e.g. `anthropic:claude-sonnet-5-5`), `is_local(settings) -> bool`
  - `genea.agent.core.build_agent(model) -> Agent[ToolContext, str]`, `ask(agent, deps, question, history=None) -> Answer`, `Answer(text, tool_calls: list[tuple[str, str]], messages)`, `AgentFailed`
  - CLI `genea ask "<question>" [--show-tools]`, `genea chat [--show-tools]`

- [ ] **Step 1: Write the failing tests**

`tests/test_agent.py`:
```python
import json
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel

from genea.agent.core import AgentFailed, ask, build_agent
from genea.agent.model import build_model, is_local
from genea.changes.store import ChangeStore
from genea.config import Settings
from genea.kb import KnowledgeBase


def _parts(messages):
    return [p for m in messages for p in m.parts]


def script(*responses):
    """A fake model that returns the next scripted response on each request."""
    def fn(messages, info):
        n = sum(1 for m in messages if isinstance(m, ModelResponse))
        response = responses[n]
        return response(messages) if callable(response) else response
    return FunctionModel(fn)


def _with_settings(tools, **changes):
    return replace(tools, kb=KnowledgeBase(tools.kb.root, replace(tools.kb.settings, **changes)))


def test_answer_uses_tool_and_cites(tools):
    def final(messages):
        result = json.loads([p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1].content)
        top = result["candidates"][0]
        return ModelResponse(parts=[TextPart(f"{top['name']} {top['id']} [tree.ged:L{top['source']['line']}]")])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("find_person", {"name": "Jan Novák", "born_around": 1900})]), final))
    answer = ask(agent, tools, "Kdo je Jan Novák narozený kolem roku 1900?")
    assert answer.text.startswith("Jan Novák tree.ged#@I12@ [tree.ged:L")
    assert [name for name, _ in answer.tool_calls] == ["find_person"]


def test_tool_error_is_sent_back_as_retry(tools):
    def final(messages):
        retry = [p for p in _parts(messages) if isinstance(p, RetryPromptPart)][-1]
        return ModelResponse(parts=[TextPart(f"retry: {retry.content}")])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("get_person", {"person_id": "tree.ged#@I999@"})]), final))
    assert "find_person" in ask(agent, tools, "q").text


def test_propose_change_through_agent(tools):
    call = ToolCallPart("propose_change", {
        "target_kind": "image_xmp", "target_path": "photos/svatba_1932.jpg", "operation": "add_keywords",
        "payload": {"keywords": ["Jan Novák"]}, "rationale": "He is in the photo",
        "sources": [{"file": "tree.ged", "line": 7}],
    })
    agent = build_agent(script(ModelResponse(parts=[call]), ModelResponse(parts=[TextPart("Proposed.")])))
    ask(agent, tools, "Tag the wedding photo")
    [change] = ChangeStore(tools.kb).list("pending")
    assert change.payload == {"keywords": ["Jan Novák"]}
    assert change.sources[0].line == 7


def test_runaway_tool_loop_fails_cleanly(tools):
    looping = FunctionModel(lambda messages, info: ModelResponse(
        parts=[ToolCallPart("find_person", {"name": "Novák"})]))
    with pytest.raises(AgentFailed, match="tool_calls_limit"):
        ask(build_agent(looping), _with_settings(tools, max_tool_calls=2), "q")
    assert list(tools.kb.logs_dir.glob("*-failure.json"))


def test_history_is_passed_on(tools):
    seen = []

    def fn(messages, info):
        seen.append(len(messages))
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent(FunctionModel(fn))
    first = ask(agent, tools, "a")
    ask(agent, tools, "b", first.messages)
    assert seen[1] > seen[0]


def test_tool_output_is_truncated(tools):
    def final(messages):
        return ModelResponse(parts=[TextPart([p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1].content)])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("get_person", {"person_id": "tree.ged#@I3@"})]), final))
    text = ask(agent, _with_settings(tools, tool_output_chars=50), "q").text
    assert text.endswith("[truncated: narrow the query]")
    assert len(text) < 120


def test_build_model():
    model = build_model(Settings(model="ollama:qwen3:8b", ollama_url="http://gpu:11434"))
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "qwen3:8b"
    assert build_model(Settings(model="anthropic:claude-sonnet-5-5")) == "anthropic:claude-sonnet-5-5"
    assert is_local(Settings()) and not is_local(Settings(model="anthropic:claude-sonnet-5-5"))
    with pytest.raises(ValueError):
        build_model(Settings(model="ollama:"))
```

`tests/test_cli_agent.py`:
```python
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder

runner = CliRunner()


def _setup(sample_root, monkeypatch, extra_config=""):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n' + extra_config, encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    monkeypatch.setattr("genea.cli.agentcmds.build_model", lambda settings: FunctionModel(
        lambda messages, info: ModelResponse(parts=[TextPart("Josef Novák [tree.ged:L7]")])))
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0


def test_ask(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 0, result.output
    assert "Josef Novák" in result.output
    assert "will be sent" not in result.output


def test_privacy_notice_for_cloud_models(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch, 'model = "anthropic:claude-sonnet-5-5"\n')
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 0, result.output
    assert "will be sent to anthropic" in result.output


def test_chat(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "chat"], input="Ahoj\nexit\n")
    assert result.exit_code == 0, result.output
    assert "Josef Novák" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_agent.py tests/test_cli_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.agent'`.

- [ ] **Step 3: Implement the prompt and model selection**

`src/genea/agent/prompt.py`:
```python
SYSTEM_PROMPT = """\
You are genea, a careful genealogy research assistant for ONE family-history knowledge base
(a GEDCOM family tree, text documents and photo metadata). You can only see it through your tools.

Rules:
1. Every genealogical fact you state must have a citation copied from tool results, written like
   [tree.ged:L1042] (GEDCOM line), [docs/matrika.pdf p.3] (document page) or [photos/x.jpg].
   If the tools return nothing relevant, say so. Never use outside knowledge about these people.
2. People are identified by ids like 'tree.ged#@I12@'. Always call find_person first. If several
   candidates fit and the question does not say which one is meant, list them (name, life dates, id)
   and ask the user to choose.
3. Keep evidence and reasoning apart: when you draw conclusions, use the headings
   "Documented:" and "Inferred:".
4. You cannot change any file yourself. To suggest a correction, call propose_change, then give the
   user the change id and tell them to review it with `genea changes show <id>`. Never claim that a
   change was made. Use write_note only when the user asks you to save findings.
5. Answer in the language of the user's question. Be concise.
"""
```

`src/genea/agent/model.py`:
```python
"""Choose the chat model from settings: local Ollama by default, any PydanticAI model string otherwise."""

from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider

from genea.config import Settings


def is_local(settings: Settings) -> bool:
    return settings.model.startswith("ollama:")


def build_model(settings: Settings):
    provider, _, name = settings.model.partition(":")
    if provider == "ollama":
        if not name:
            raise ValueError("model must look like 'ollama:<model name>', e.g. 'ollama:qwen3:14b'")
        return OpenAIChatModel(name, provider=OllamaProvider(base_url=settings.ollama_url.rstrip("/") + "/v1"))
    # e.g. "anthropic:claude-sonnet-5-5" (needs `uv sync --extra cloud` and ANTHROPIC_API_KEY)
    return settings.model
```

- [ ] **Step 4: Implement the agent core**

`src/genea/agent/core.py`:
```python
"""The PydanticAI agent: tools wired to the model, plus a safe `ask` wrapper."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ToolCallPart
from pydantic_ai.usage import UsageLimits

from genea.agent.prompt import SYSTEM_PROMPT
from genea.kb import KnowledgeBase
from genea.tools import conflicts, curate, people, search
from genea.tools.base import ToolContext, ToolError
from genea.tools.models import SourceRef


class AgentFailed(RuntimeError):
    """The agent run failed (model unreachable, limits exceeded, invalid output...)."""


@dataclass
class Answer:
    text: str
    tool_calls: list[tuple[str, str]]
    messages: list[ModelMessage]


def _render(ctx: RunContext[ToolContext], result: BaseModel) -> str:
    text = result.model_dump_json(exclude_none=True)
    limit = ctx.deps.kb.settings.tool_output_chars
    if len(text) > limit:
        return text[:limit] + " …[truncated: narrow the query]"
    return text


def _call(ctx: RunContext[ToolContext], fn, *args, **kwargs) -> str:
    try:
        return _render(ctx, fn(ctx.deps, *args, **kwargs))
    except ToolError as exc:
        raise ModelRetry(str(exc)) from exc


def build_agent(model) -> Agent[ToolContext, str]:
    agent = Agent(model, deps_type=ToolContext, instructions=SYSTEM_PROMPT, retries=2, defer_model_check=True)

    @agent.tool
    def find_person(ctx: RunContext[ToolContext], name: str, born_around: int | None = None,
                    place: str | None = None) -> str:
        """Find people in the family tree by name (accents optional). Returns candidate ids such as
        'tree.ged#@I12@' with birth and death. Call this before any other person tool."""
        return _call(ctx, people.find_person, name, born_around, place)

    @agent.tool
    def get_person(ctx: RunContext[ToolContext], person_id: str) -> str:
        """Full record of one person: names, events with GEDCOM citations, parents, spouses,
        children and family ids."""
        return _call(ctx, people.get_person, person_id)

    @agent.tool
    def relatives(ctx: RunContext[ToolContext], person_id: str,
                  relation: Literal["parents", "children", "siblings", "spouses", "ancestors", "descendants"],
                  depth: int = 1) -> str:
        """Relatives of a person. depth (1-6) is used for ancestors and descendants."""
        return _call(ctx, people.relatives, person_id, relation, depth)

    @agent.tool
    def timeline(ctx: RunContext[ToolContext], person_id: str) -> str:
        """A person's life events in order, with births, marriages and deaths of parents, spouses and children."""
        return _call(ctx, people.timeline, person_id)

    @agent.tool
    def search_documents(ctx: RunContext[ToolContext], query: str, k: int = 8) -> str:
        """Search the text documents (letters, chronicles, registers, notes). Returns passages with file and page."""
        return _call(ctx, search.search_documents, query, k)

    @agent.tool
    def read_document(ctx: RunContext[ToolContext], path: str, page: int | None = None) -> str:
        """Read the full text of a document (or one PDF page) found by search_documents."""
        return _call(ctx, search.read_document, path, page)

    @agent.tool
    def find_images(ctx: RunContext[ToolContext], person: str | None = None, place: str | None = None,
                    year_from: int | None = None, year_to: int | None = None, text: str | None = None) -> str:
        """Find photos by person (an id from find_person, or a name), place, year range or caption text."""
        return _call(ctx, search.find_images, person, place, year_from, year_to, text)

    @agent.tool
    def find_conflicts(ctx: RunContext[ToolContext], person_id: str | None = None) -> str:
        """Check the tree for impossible or suspicious data (events after death, parents too young or
        too old, documents that disagree). Without person_id it checks everyone."""
        return _call(ctx, conflicts.find_conflicts, person_id)

    @agent.tool
    def write_note(ctx: RunContext[ToolContext], title: str, markdown: str,
                   related_persons: list[str] | None = None) -> str:
        """Save a research note (Markdown) in the knowledge base's notes folder. Use it only when the
        user asks to keep findings."""
        return _call(ctx, curate.write_note, title, markdown, related_persons)

    @agent.tool
    def propose_change(ctx: RunContext[ToolContext], target_kind: Literal["image_xmp", "gedcom"],
                       target_path: str, operation: str, payload: dict[str, Any], rationale: str,
                       sources: list[SourceRef] | None = None) -> str:
        """Propose a change for the user to review; nothing changes until they apply it.
        image_xmp (target_path = the photo): operation set_fields with payload {field: value or [values]}
        using XMP-dc:Title, XMP-dc:Description, XMP-dc:Subject, XMP-iptcExt:PersonInImage,
        XMP-photoshop:City, XMP-photoshop:Country, XMP-photoshop:DateCreated, XMP-iptcCore:Location;
        or add_keywords / remove_keywords with payload {"keywords": [...]}.
        gedcom (target_path = the .ged file): add_event {person_id, type, date?, place?},
        update_event {person_id, line, date?, place?}, add_note {person_id, text},
        add_source_citation {person_id, source, page?, event_type?}, link_child {family_id, child_id},
        add_person {given, surname, sex?, birth_date?, birth_place?}.
        Always give a rationale and the sources that support the change."""
        return _call(ctx, curate.propose_change, target_kind, target_path, operation, payload,
                     rationale, sources)

    return agent


def _save_failure(kb: KnowledgeBase, question: str, exc: Exception) -> str:
    path = kb.logs_dir / f"{datetime.now():%Y%m%dT%H%M%S%f}-failure.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"question": question, "error": f"{type(exc).__name__}: {exc}",
                                "traceback": traceback.format_exc()}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return kb.rel(path)


def ask(agent: Agent[ToolContext, str], deps: ToolContext, question: str,
        history: list[ModelMessage] | None = None) -> Answer:
    settings = deps.kb.settings
    limits = UsageLimits(request_limit=settings.max_tool_calls + 5, tool_calls_limit=settings.max_tool_calls)
    try:
        result = agent.run_sync(question, deps=deps, message_history=history, usage_limits=limits)
    except Exception as exc:  # noqa: BLE001 - every failure is reported the same way
        log = _save_failure(deps.kb, question, exc)
        raise AgentFailed(f"{type(exc).__name__}: {exc} (details in {log})") from exc
    calls = [(p.tool_name, p.args_as_json_str())
             for m in result.new_messages() for p in m.parts if isinstance(p, ToolCallPart)]
    return Answer(result.output, calls, result.all_messages())
```

- [ ] **Step 5: Implement the CLI commands**

`src/genea/cli/agentcmds.py`:
```python
"""`genea ask` and `genea chat`."""

from __future__ import annotations

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
                answer = ask_agent(agent, tools, question, history)
            except AgentFailed as exc:
                common.console.print(f"[red]{escape(str(exc))}[/red]")
                continue
            history = answer.messages
            _print(answer, show_tools)
    finally:
        tools.conn.close()
```

In `src/genea/cli/main.py`, change the import line to:
```python
from genea.cli import agentcmds, changecmds, indexcmds, kbcmds, querycmds  # noqa: E402,F401
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent.py tests/test_cli_agent.py -v`
Expected: all pass.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -v`
Expected: everything passes; only `exiftool` tests are skipped (when ExifTool is missing).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: PydanticAI agent with genealogy tools, ask and chat commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Live evaluation with a real model, README and final verification

**Files:**
- Create: `tests/live/test_live_qa.py`, `README.md`
- Test: the live suite (opt-in) and the full suite

**Interfaces:**
- Consumes: everything above.
- Produces: `uv run pytest -m live` (15 Q/A pairs against the sample knowledge base), with the model chosen by the `GENEA_LIVE_MODEL` env var or the global config; user documentation.

- [ ] **Step 1: Write the live evaluation**

`tests/live/test_live_qa.py`:
```python
"""End-to-end questions against a real local model.

Run:  uv run pytest -m live            (uses the model from ~/.genea/config.toml or the default)
      GENEA_LIVE_MODEL=ollama:qwen3:8b uv run pytest -m live   (compare models)
Needs Ollama running with the chat model and bge-m3 pulled.
"""

import os
from dataclasses import replace

import pytest
from fixtures.make_kb import build_sample_kb

from genea.agent.core import ask, build_agent
from genea.agent.model import build_model
from genea.doctor import ollama_models
from genea.index.embed import make_embedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase, init_kb
from genea.text import normalize
from genea.tools.base import open_tools

pytestmark = pytest.mark.live

QUESTIONS = [
    ("Kdo byli rodiče Jana Nováka narozeného roku 1846?", ["Josef", "Anna"]),
    ("Who were the parents of Jan Novák born in 1846?", ["Josef", "Anna"]),
    ("Kdy a kde se oženil Jan Novák narozený 1846?", ["1870", "Tišnov"]),
    ("Jak se jmenovala manželka Karla Nováka?", ["Rosalie"]),
    ("Jmenuj děti Jana Nováka (1846) a Terezie Dvořákové.", ["Karel", "Ludmila", "Václav"]),
    ("Who were the grandparents of Jiří Novák born 1934?", ["Karel", "Rosalie"]),
    ("Which children did Josef Novák born 1820 have?", ["Jan", "Marie", "František"]),
    ("Kdo byli sourozenci Karla Nováka narozeného 1871?", ["Ludmila", "Václav"]),
    ("Find inconsistencies in the data about Marie Nováková born 1848.", ["1855"]),
    ("Co o Terezii Dvořákové říkají dokumenty?", ["1851"]),
    ("Which document mentions Václav Novák and on which page?", ["matrika", "2"]),
    ("What was the occupation of Josef Novák born 1820?", ["sedl"]),
    ("Koho si vzala Ludmila Nováková?", ["Jindřich"]),
    ("When did Anna Svobodová die?", ["1890"]),
    ("Is there a letter about Karel Novák's wedding?", ["dopis", "1895"]),
]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    root = build_sample_kb(tmp_path_factory.mktemp("live") / "kb")
    base = init_kb(root)
    settings = replace(base.settings, exclude=("photos/**",),
                       model=os.environ.get("GENEA_LIVE_MODEL", base.settings.model))
    if ollama_models(settings.ollama_url) is None:
        pytest.skip("Ollama is not running")
    kb = KnowledgeBase(base.root, settings)
    embedder = make_embedder(settings)
    index_kb(kb, embedder)
    tools = open_tools(kb, embedder)
    yield tools, build_agent(build_model(settings))
    tools.conn.close()


@pytest.mark.parametrize("question, expected", QUESTIONS)
def test_live_question(live, question, expected):
    tools, agent = live
    answer = ask(agent, tools, question)
    text = normalize(answer.text)
    for word in expected:
        assert normalize(word) in text, answer.text
    assert "[" in answer.text, f"no citation in: {answer.text}"
```

Run: `uv run pytest -v`
Expected: the live tests are deselected (`-m 'not live'` in addopts) and everything else passes.

Run (only if Ollama is running and `ollama pull qwen3:14b` / `ollama pull bge-m3` are done): `uv run pytest -m live -v`
Expected: most questions pass. Record the pass rate in the README's "Model choice" section. A few failures on small models are acceptable; they are a signal for the prompt or the model choice, not a bug.

- [ ] **Step 2: Write the README**

`README.md`:
````markdown
# genea: a genealogy knowledge-base agent

A local, open-source assistant for a genealogy research folder: GEDCOM family trees, documents
(txt, md, docx, pdf) and photo metadata (EXIF/IPTC/XMP). It answers questions with citations,
finds inconsistencies, writes research notes, and **proposes** corrections that you review and
apply. Point it at any folder; each folder is its own knowledge base.

## Install

1. [uv](https://docs.astral.sh/uv/): `winget install astral-sh.uv`
2. [Ollama](https://ollama.com), then pull a chat model and the embedding model:
   `ollama pull qwen3:14b` (use `qwen3:8b` with less than ~12 GB of GPU memory) and `ollama pull bge-m3`
3. [ExifTool](https://exiftool.org): `winget install OliverBetz.ExifTool`
   (if a Chocolatey install reports "Cannot find file at ...exiftool.exe", run `choco install exiftool --force`)
4. In this repository: `uv sync`, then check everything with `uv run genea doctor`

Optional cloud models: `uv sync --extra cloud`, set `ANTHROPIC_API_KEY`, and use
`model = "anthropic:claude-sonnet-5-5"`. If installing the `anthropic` package fails on Windows with
missing-module errors, enable long paths (`git config --system core.longpaths true` and the
`LongPathsEnabled` registry setting).

## Quick start

```powershell
uv run genea --kb D:\Genealogy\Novak init      # creates D:\Genealogy\Novak\.genea\
uv run genea --kb D:\Genealogy\Novak index     # re-run any time; only changed files are processed
uv run genea --kb D:\Genealogy\Novak ask "Kdo byli prarodiče Jiřího Nováka?"
uv run genea --kb D:\Genealogy\Novak chat --show-tools
```

To skip `--kb`, name your knowledge bases in `~/.genea/config.toml`:

```toml
default_kb = "novak"
[kbs]
novak = 'D:\Genealogy\Novak'
svoboda = 'E:\Rodokmen\Svoboda'
```

Then `uv run genea ask "..."` or `uv run genea --kb svoboda ask "..."`. The `GENEA_KB` environment
variable works too.

## Commands

| Command | What it does |
|---|---|
| `genea doctor` | Check ExifTool, Ollama and the models |
| `genea init` / `index [--rebuild]` / `status` | Set up, index, and inspect a knowledge base |
| `genea ask "..."` / `chat` | Talk to the agent (`--show-tools` shows its tool calls) |
| `genea person <name or id>` | Look a person up without the LLM |
| `genea conflicts [id]` | Rule-based consistency checks without the LLM |
| `genea changes list / show / apply / reject / undo / export` | Review what the agent proposed |

## How changes work

The agent never edits your files. It writes research notes to `.genea/notes/` and **proposes**
changes to `.genea/changes/pending/`. Review them with `genea changes show <id>`.

- **Photo metadata** (title, description, keywords, people, place, date): `genea changes apply <id>`
  writes an XMP **sidecar** (`photo.xmp`) next to the photo. The photo itself is never touched,
  a backup is kept, and `genea changes undo <id>` restores it.
- **Family tree**: `genea changes export` writes a checklist and a GEDCOM fragment to
  `.genea/exports/`. Make the edits in your genealogy program, then mark each change with
  `genea changes apply <id> --manual`. Writing `.ged` files directly is planned for a later version.

## Configuration (`~/.genea/config.toml` or `<kb>/.genea/config.toml`)

| Key | Default | Meaning |
|---|---|---|
| `model` | `ollama:qwen3:14b` | Chat model (`ollama:<name>` or e.g. `anthropic:claude-sonnet-5-5`) |
| `embed_model` / `embed_dim` | `bge-m3` / `1024` | Local embedding model (changing it needs `index --rebuild`) |
| `ollama_url` | `http://localhost:11434` | Ollama server |
| `exclude` | `[]` | Glob patterns to skip, e.g. `["private/**"]` |
| `max_tool_calls` | `15` | Tool calls allowed per question |
| `tool_output_chars` | `12000` | Truncation limit for each tool result |
| `privacy_notice` | `true` | Warn when a cloud model will receive your data |
| `exiftool` | `exiftool` | Path to the ExifTool binary |

## Privacy

With an `ollama:` model, nothing leaves your computer. With a cloud model, your questions and the
tool results (names, dates, document passages) are sent to that provider; genea warns you once per
session.

## Limitations (v1)

No OCR (scanned PDFs are listed as `needs_ocr` by `genea status`), no image-content analysis, and
no direct GEDCOM writing. Name matching does not handle Czech declension ("Annou Svobodovou"),
so full-text and semantic search do that work.

## Development

```powershell
uv run pytest                 # unit and integration tests (no network)
uv run pytest -m live         # end-to-end questions against a real Ollama model
```

Copy real `.ged` files into `tests/private/` (git-ignored) to check they survive a parse/serialize
round trip byte for byte.
````

- [ ] **Step 3: Final verification**

Run: `uv run pytest -v`
Expected: all tests pass; only the `exiftool` tests are skipped when ExifTool is missing; the live tests are deselected.

Run: `uv run genea --help` and `uv run genea changes --help`
Expected: `init, doctor, index, status, person, conflicts, ask, chat, changes` are listed, and the `changes` subcommands `list, show, apply, reject, undo, export` are shown.

Run: `uv run genea doctor`
Expected: a clear ✓/✗ report with a fix for each ✗ (on a machine without Ollama or ExifTool it exits with code 1 and prints the install hints).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: README and opt-in live evaluation against a real model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|---|---|
| Pick a knowledge base by `--kb` / `GENEA_KB` / named `default_kb`; self-contained `.genea/` | 1 |
| Config precedence (CLI > KB > global > defaults) | 1 |
| `genea doctor` checks for ExifTool, Ollama and models | 1 |
| Lossless GEDCOM parsing, encodings (UTF-8/BOM, ANSEL, CP1250, UTF-16), warnings with line numbers | 2 |
| GEDCOM date ranges (ABT/EST/CAL/BEF/AFT/BET/FROM/TO, dual, Julian) | 3 |
| Persons, families, events, citations, notes, name variants, pedigree | 4 |
| SQLite + FTS5 + sqlite-vec schema, schema/embedding version check | 5 |
| txt/md/docx/pdf extraction, `needs_ocr`, ~800-token chunks with overlap | 6 |
| Image metadata + sidecar conventions | 7 |
| Incremental re-indexing by hash, per-file errors, `--rebuild`, links with confidence | 8 |
| Tools `find_person`, `get_person`, `relatives`, `timeline` | 9 |
| Tools `search_documents` (RRF hybrid), `read_document` (path guard, truncation), `find_images` | 10 |
| `find_conflicts` rules + `needs_review` document mismatches | 11 |
| `write_note`, `propose_change`, validation, XMP field whitelist, de-duplication, audit log | 12 |
| apply (base_hash → stale, backup, sidecar only, reindex), undo, reject, export, `--manual` | 13 |
| Agent with citation rules, `ModelRetry` on tool errors, tool-call limits, truncation, privacy notice, failure logs | 14 |
| `TestModel`/`FunctionModel` agent tests; optional `pytest -m live` with ~15 Q/A pairs | 14, 15 |
| v1 acceptance criteria (doctor, index, grandparents question, caption change apply/undo) | 15 (+ manual run on the real knowledge base) |

