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
        try:
            if not path.is_file():
                continue  # also files that vanished since listing
            rel = kb.rel(path)
        except (OSError, ValueError):
            continue  # unreadable, or a symlink/junction leading outside the root
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


def _record_error(conn, rel, kind, digest, mtime, old_id, exc: Exception, report: IndexReport) -> None:
    """Store a failed file as status 'error' (sha256 '' when it could not even be read)."""
    message = f"{type(exc).__name__}: {exc}"
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        if old_id is not None:
            remove_file(conn, old_id)
        conn.execute(
            "INSERT INTO files (path, kind, sha256, mtime, indexed_at, status, error_msg) "
            "VALUES (?, ?, ?, ?, ?, 'error', ?)", (rel, kind, digest, mtime, now, message),
        )
    report.errors += 1
    report.problems.append((rel, message))


def _index_one(conn, kb, embedder, path, kind, digest, old_id, report: IndexReport) -> None:
    rel = kb.rel(path)
    now = datetime.now().isoformat(timespec="seconds")
    mtime = 0.0
    try:
        mtime = path.stat().st_mtime
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
        _record_error(conn, rel, kind, digest, mtime, old_id, exc, report)
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
            old = known.get(rel)
            try:
                digest = file_hash(path, kind)
            except Exception as exc:  # noqa: BLE001 - locked, online-only or vanished file
                _record_error(conn, rel, kind, "", 0.0, old["id"] if old else None, exc, report)
                continue
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
