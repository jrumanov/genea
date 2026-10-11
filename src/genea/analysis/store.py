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
