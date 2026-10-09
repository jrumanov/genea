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
    assert meta == {"schema_version": "2", "embed_model": "fake", "embed_dim": "8"}
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


def test_index_from_schema_1_needs_a_rebuild(tmp_path):
    path = tmp_path / "i.db"
    conn = connect(path, "fake", 8)
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaMismatch, match="--rebuild"):
        connect(path, "fake", 8)
