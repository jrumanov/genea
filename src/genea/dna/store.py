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
