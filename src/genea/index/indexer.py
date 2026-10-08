"""Incremental indexing of a knowledge base into .genea/index.db."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sqlite_vec

from genea.exiftool import ExifToolError
from genea.index.db import open_index, set_index_state
from genea.index.embed import Embedder
from genea.index.progress import IndexDbLocked, IndexInUse, IndexLock, IndexProgress, clear_progress, now_iso, write_progress
from genea.ingest.docs import DOC_EXTS, chunk_pages, extract_document
from genea.ingest.gedcom.mapper import map_gedcom
from genea.ingest.gedcom.parser import parse_gedcom
from genea.ingest.images import IMAGE_EXTS, ImageRecord, extract_image, extract_images, sidecar_for
from genea.kb import KnowledgeBase
from genea.text import normalize

KIND_BY_EXT = {".ged": "ged", **{e: "doc" for e in DOC_EXTS}, **{e: "image" for e in IMAGE_EXTS}}
EMBED_BATCH = 32
LOCK_WAIT = 2.0  # seconds: rides out a `genea status` / `genea mcp` probe that holds the lock briefly
PROGRESS_INTERVAL = 1.0  # seconds between progress file writes within a phase


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


def source_mtime(path: Path, kind: str) -> float:
    """Modification time of the file; for images the newer of the image and its XMP sidecar."""
    mtime = path.stat().st_mtime
    if kind == "image":
        sidecar = sidecar_for(path)
        if sidecar is not None:
            mtime = max(mtime, sidecar.stat().st_mtime)
    return mtime


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


def _ingest_image(conn, kb, embedder, path, file_id, record: ImageRecord | None = None) -> tuple[str, str | None]:
    rec = record or extract_image(path, kb.settings.exiftool)
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


def _index_one(conn, kb, embedder, path, kind, digest, old_id, report: IndexReport,
               record: ImageRecord | None = None) -> None:
    """`record` is the image's metadata when it was already read (in a batch)."""
    rel = kb.rel(path)
    now = datetime.now().isoformat(timespec="seconds")
    mtime = 0.0
    try:
        mtime = source_mtime(path, kind)
        with conn:
            if old_id is not None:
                remove_file(conn, old_id)
            file_id = conn.execute(
                "INSERT INTO files (path, kind, sha256, mtime, indexed_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'ok')", (rel, kind, digest, mtime, now),
            ).lastrowid
            extra = {"record": record} if kind == "image" else {}
            status, message = INGESTERS[kind](conn, kb, embedder, path, file_id, **extra)
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
    # form -> [(person number, ged file, xref, confidence)]. Normalized text has single spaces, so a form
    # is in a text exactly when its words are consecutive words of the text: look the text's word
    # sequences up here instead of searching every form of every person in every text.
    by_form: dict[str, list[tuple[int, str, str, float]]] = {}
    for number, r in enumerate(conn.execute("SELECT ged_file, xref, given, surname, name_variants FROM persons")):
        for form, conf in name_forms(r["given"], r["surname"], json.loads(r["name_variants"])):
            by_form.setdefault(form, []).append((number, r["ged_file"], r["xref"], conf))
    form_lengths = sorted({len(form.split()) for form in by_form})
    sources = [("chunk", r["id"], normalize(r["text"])) for r in conn.execute("SELECT id, text FROM chunks")]
    sources += [("image", r["id"], r["search_norm"]) for r in conn.execute("SELECT id, search_norm FROM images")]
    rows = []
    for kind, source_id, text in sources:
        words = text.split()
        best: dict[int, tuple[str, str, float]] = {}
        for length in form_lengths:
            for start in range(len(words) - length + 1):
                for number, ged, xref, conf in by_form.get(" ".join(words[start:start + length]), ()):
                    if conf > best.get(number, ("", "", 0.0))[2]:
                        best[number] = (ged, xref, conf)
        rows += [(kind, source_id, *best[number]) for number in sorted(best)]
    conn.executemany(
        "INSERT INTO links (source_kind, source_id, person_ged_file, person_xref, confidence) "
        "VALUES (?, ?, ?, ?, ?)", rows,
    )


IMAGE_BATCH = 50  # images per ExifTool run


class _ImageBatches:
    """Image metadata read IMAGE_BATCH images per ExifTool run instead of one run per image. The images
    must be asked for in the order given. If a run fails, its images get no record (None), so each is
    read on its own and a bad file fails alone."""

    def __init__(self, kb: KnowledgeBase, images: list[Path]) -> None:
        self.kb, self.images = kb, images
        self.records: dict[Path, ImageRecord | None] = {}

    def get(self, image: Path) -> ImageRecord | None:
        if image not in self.records:
            batch = self.images[self.images.index(image):][:IMAGE_BATCH]
            try:
                self.records.update(extract_images(batch, self.kb.settings.exiftool))
            except ExifToolError:
                self.records.update(dict.fromkeys(batch))
        return self.records.pop(image)


class _Progress:
    """Reports the run to `on_progress` on every step and to the progress file at most once a second."""

    def __init__(self, kb: KnowledgeBase, callback: Callable[[IndexProgress], None] | None) -> None:
        self.kb = kb
        self.callback = callback
        now = now_iso()
        self.state = IndexProgress(pid=os.getpid(), started_at=now, phase="scanning",
                                   phase_started_at=now, updated_at=now)
        self._written = 0.0
        self._emit(force=True)

    def phase(self, name: str, total: int = 0) -> None:
        now = now_iso()
        self.state.phase, self.state.phase_started_at, self.state.updated_at = name, now, now
        self.state.done, self.state.total, self.state.current = 0, total, None
        self._emit(force=True)

    def update(self, done: int, current: str | None = None) -> None:
        self.state.done, self.state.current = done, current
        self._emit()

    def _emit(self, force: bool = False) -> None:
        if force or time.monotonic() - self._written >= PROGRESS_INTERVAL:
            self.state.updated_at = now_iso()
            write_progress(self.kb, self.state)
            self._written = time.monotonic()
        if self.callback:
            self.callback(self.state)


def index_kb(kb: KnowledgeBase, embedder: Embedder, rebuild: bool = False,
             on_progress: Callable[[IndexProgress], None] | None = None) -> IndexReport:
    """Raises IndexBusy when another run holds the lock, IndexInUse when a rebuild cannot delete the index,
    and IndexDbLocked when a program that ignores the lock keeps the database locked."""
    with IndexLock(kb, wait=LOCK_WAIT):
        try:
            return _index_locked(kb, embedder, rebuild, _Progress(kb, on_progress))
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                raise IndexDbLocked() from exc
            raise
        finally:
            clear_progress(kb)


def _index_locked(kb: KnowledgeBase, embedder: Embedder, rebuild: bool, progress: _Progress) -> IndexReport:
    if rebuild and kb.index_path.exists():
        try:
            kb.index_path.unlink()
        except PermissionError as exc:  # Windows: still open, typically by `genea mcp`
            raise IndexInUse() from exc
    report = IndexReport()
    conn = open_index(kb, embedder)
    try:
        set_index_state(conn, "running")  # stays 'running' if this run dies: seen as interrupted
        known = {r["path"]: r for r in conn.execute("SELECT id, path, sha256, mtime, status FROM files")}
        seen: set[str] = set()
        pending = []  # (path, kind, rel, digest, old row) of the files that need indexing
        for path, kind in scan_files(kb):
            rel = kb.rel(path)
            seen.add(rel)
            progress.update(len(seen))
            old = known.get(rel)
            try:
                mtime = source_mtime(path, kind)
                if old is not None and old["status"] != "error" and old["mtime"] == mtime:
                    report.skipped += 1  # same modification time: trust it, do not read the file
                    continue
                digest = file_hash(path, kind)
            except Exception as exc:  # noqa: BLE001 - locked, online-only or vanished file
                _record_error(conn, rel, kind, "", 0.0, old["id"] if old else None, exc, report)
                continue
            if old is not None and old["sha256"] == digest and old["status"] != "error":
                with conn:  # touched but identical: remember the new time so it is not hashed again
                    conn.execute("UPDATE files SET mtime = ? WHERE id = ?", (mtime, old["id"]))
                report.skipped += 1
                continue
            pending.append((path, kind, rel, digest, old))
        progress.phase("indexing", total=len(pending))
        records = _ImageBatches(kb, [path for path, kind, *_ in pending if kind == "image"])
        for done, (path, kind, rel, digest, old) in enumerate(pending):
            progress.update(done, rel)
            _index_one(conn, kb, embedder, path, kind, digest, old["id"] if old else None, report,
                       record=records.get(path) if kind == "image" else None)
        for rel, row in known.items():
            if rel not in seen:
                with conn:
                    remove_file(conn, row["id"])
                report.removed += 1
        progress.phase("linking")
        with conn:
            rebuild_links(conn)
        set_index_state(conn, "complete")
    finally:
        conn.close()
    return report


def reindex_file(kb: KnowledgeBase, embedder: Embedder, path: Path) -> None:
    """Re-index one file (used after applying a change)."""
    kind = KIND_BY_EXT[path.suffix.lower()]
    with IndexLock(kb, wait=LOCK_WAIT):
        conn = open_index(kb, embedder)
        try:
            row = conn.execute("SELECT id FROM files WHERE path = ?", (kb.rel(path),)).fetchone()
            _index_one(conn, kb, embedder, path, kind, file_hash(path, kind),
                       row["id"] if row else None, IndexReport())
            with conn:
                rebuild_links(conn)
        finally:
            conn.close()
