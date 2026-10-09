import pytest
from fixtures.transcripts import NAME, write_transcript

from genea.index.db import open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb

REL = f"docs/{NAME}"


def _query(kb, sql, params=()):
    conn = open_index(kb, FakeEmbedder())
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _chunks(kb, rel=REL, kind="text"):
    return _query(kb, "SELECT c.* FROM chunks c JOIN files f ON f.id = c.file_id "
                      "WHERE f.path = ? AND c.kind = ? ORDER BY c.chunk_no", (rel, kind))


def test_transcript_chunks_carry_times_and_clean_text(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    chunks = _chunks(kb)
    assert len(chunks) >= 2
    assert all(c["start_s"] is not None and c["end_s"] > c["start_s"] and c["page"] is None for c in chunks)
    assert [c["start_s"] for c in chunks] == sorted(c["start_s"] for c in chunks)
    assert all("	" not in c["text"] and "fabik" not in c["text"] for c in chunks)
    last = max(chunks, key=lambda c: c["start_s"])
    assert last["start_s"] <= 840 <= last["end_s"]
    assert "Prískupíc" in last["text"]


def test_file_name_context_is_searchable_but_not_stored(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    ids = {c["id"] for c in _chunks(kb)}
    hits = {r[0] for r in _query(kb, "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ('"fabik"',))}
    assert hits == ids  # every chunk of this recording, and nothing else
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]


def test_ordinary_text_has_no_times(kb):
    index_kb(kb, FakeEmbedder())
    rows = _chunks(kb, "docs/kronika.txt")
    assert rows and all(r["start_s"] is None and r["end_s"] is None for r in rows)
