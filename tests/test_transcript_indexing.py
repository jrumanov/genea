import json
from dataclasses import replace

import pytest
from fixtures.transcripts import NAME, sample_rows, write_transcript

from genea.index.db import open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase
from genea.tool_runner import TRUNCATION_MARKER, run_tool
from genea.tools.base import ToolError, open_tools
from genea.tools.models import SourceRef
from genea.tools.search import read_document, search_documents

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
    assert all("\t" not in c["text"] and "fabik" not in c["text"] for c in chunks)
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


@pytest.fixture
def ctx(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    context = open_tools(kb, FakeEmbedder())
    yield context
    context.conn.close()


@pytest.mark.parametrize(("seconds", "cited"), [(0, "[a.txt @0:00]"), (840, "[a.txt @14:00]"),
                                                (3725, "[a.txt @1:02:05]")])
def test_cite_a_moment(seconds, cited):
    assert SourceRef(file="a.txt", start_s=seconds).cite() == cited  # 0 is a moment too, not "no time"


def test_search_hit_points_to_the_moment(ctx):
    hit = search_documents(ctx, "Prískupíc babke nasťahovali").hits[0]
    assert hit.file == REL and hit.kind == "text"
    assert hit.start_s <= 840 <= hit.end_s
    assert hit.source.cite() == f"[{REL} @{int(hit.start_s) // 60}:{int(hit.start_s) % 60:02d}]"


def test_read_transcript_from_the_start(ctx):
    doc = read_document(ctx, REL)
    assert doc.text.startswith("[0:00] Veta číslo 0")
    assert "[14:00] V roku 1954" in doc.text
    assert not doc.truncated and doc.next_start_s is None
    assert doc.sources[0].cite() == f"[{REL} @0:00]"


def test_read_transcript_from_a_moment(ctx):
    doc = read_document(ctx, REL, start_s=840)
    assert doc.text.startswith("[14:00] V roku 1954")
    assert doc.start_s == 840 and doc.sources[0].cite() == f"[{REL} @14:00]"


def test_read_transcript_window_continues_where_it_stopped(ctx):
    small = KnowledgeBase(ctx.kb.root, replace(ctx.kb.settings, tool_output_chars=1200))
    first = read_document(replace(ctx, kb=small), REL)
    # budget 1200 - 600 = 600; each line costs 108 characters of JSON, so five 15 s segments fit
    assert first.truncated and first.next_start_s == 75.0
    second = read_document(replace(ctx, kb=small), REL, start_s=first.next_start_s)
    assert second.text.startswith("[1:15]")


def test_read_transcript_refuses_wrong_arguments(ctx):
    with pytest.raises(ToolError, match="no pages"):
        read_document(ctx, REL, page=1)
    with pytest.raises(ToolError, match="between 0 and 899"):
        read_document(ctx, REL, start_s=900)
    with pytest.raises(ToolError, match="not a transcript"):
        read_document(ctx, "docs/kronika.txt", start_s=10)


def test_a_long_transcript_window_is_not_shrunk_by_the_tool_runner(kb):
    write_transcript(kb.root, "big.txt", sample_rows(300))
    index_kb(kb, FakeEmbedder())
    context = open_tools(kb, FakeEmbedder())
    try:
        out = run_tool(context, read_document, "docs/big.txt")
    finally:
        context.conn.close()
    body = json.loads(out.text)
    assert TRUNCATION_MARKER not in out.text and len(out.text) <= kb.settings.tool_output_chars
    assert len(body["text"]) > 9000
    assert body["truncated"] is True and body["next_start_s"] > 0


@pytest.mark.parametrize("content", ["Slovo o rodine. " * 2000,  # 32,000 characters of plain text
                                     'Povedal "áno" a odišiel.\n' * 1500],  # quotes and newlines cost double in JSON
                         ids=["plain", "quotes-and-newlines"])  # long ids overflow Windows' env var limit
def test_a_long_ordinary_document_is_not_shrunk_by_the_tool_runner(kb, content):
    (kb.root / "docs" / "long.txt").write_text(content, encoding="utf-8")
    index_kb(kb, FakeEmbedder())
    context = open_tools(kb, FakeEmbedder())
    try:
        out = run_tool(context, read_document, "docs/long.txt")
    finally:
        context.conn.close()
    body = json.loads(out.text)  # valid JSON with no runner marker: the runner did not have to shrink it
    assert TRUNCATION_MARKER not in out.text and len(out.text) <= kb.settings.tool_output_chars
    assert len(body["text"]) > 5000 and body["text"].endswith("…[truncated]") and body["truncated"] is True
