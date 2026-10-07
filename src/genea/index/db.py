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
