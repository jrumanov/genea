import json
from dataclasses import replace

import pytest
from fixtures.transcripts import NAME, sample_rows, write_transcript

from genea.index import summary_cache
from genea.index.db import open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import SUMMARY_PENDING, index_kb
from genea.ingest.summarize import FakeSummarizer, SummaryError, SummaryUnavailable
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


def _digest(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_row(kb, rel=REL):
    return _query(kb, "SELECT status, error_msg FROM files WHERE path = ?", (rel,))[0]


class Failing(FakeSummarizer):
    def summarize(self, segments):
        self.calls += 1
        raise SummaryError("model exploded")


class Down(FakeSummarizer):
    def summarize(self, segments):
        self.calls += 1
        raise SummaryUnavailable("Ollama is not reachable (refused). Is it running?")


def test_summary_chunks_are_indexed_and_cached(kb):
    path = write_transcript(kb.root)
    fake = FakeSummarizer(section_chars=3000)
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    summaries = _chunks(kb, kind="summary")
    assert len(summaries) == 1 + 3  # the overall summary and one per section (~6,000 characters / 3,000)
    overall = summaries[0]
    assert (overall["start_s"], overall["end_s"], overall["page"]) == (0.0, 899.0, None)  # to the last segment's end
    assert overall["text"].startswith("Zhrnutie (generované): ")
    assert all(s["end_s"] > s["start_s"] and "rodina" in s["text"] for s in summaries[1:])
    assert [c["chunk_no"] for c in _chunks(kb) + summaries] == list(range(len(_chunks(kb)) + len(summaries)))
    assert summary_cache.load(kb, _digest(path)) is not None
    assert _file_row(kb)["error_msg"] is None
    chunks = _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == chunks
    assert _query(kb, "SELECT COUNT(*) FROM chunks_vec")[0][0] == chunks


def test_summary_hit_points_to_the_transcript_time(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder(), summarizer=FakeSummarizer(section_chars=3000))
    context = open_tools(kb, FakeEmbedder())
    try:
        hits = [h for h in search_documents(context, "Súhrn rodine", k=20).hits if h.kind == "summary"]
    finally:
        context.conn.close()
    assert hits and all(h.file == REL for h in hits)
    assert all(h.source.cite().startswith(f"[{REL} @") for h in hits)


def test_an_unchanged_transcript_is_not_summarized_again(kb):
    write_transcript(kb.root)
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    report = index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 1 and report.ok == 0


def test_rebuild_reuses_the_cache(kb):
    write_transcript(kb.root)
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    index_kb(kb, FakeEmbedder(), rebuild=True, summarizer=fake)
    assert fake.calls == 1
    assert _chunks(kb, kind="summary")


def test_a_changed_transcript_is_summarized_again(kb):
    path = write_transcript(kb.root)
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    old = _digest(path)
    write_transcript(kb.root, rows=sample_rows()[:40])
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 2 and _digest(path) != old
    assert "Nahrávka o rodine" in _chunks(kb, kind="summary")[0]["text"]
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]


def test_a_failing_summarizer_keeps_the_transcript_searchable_and_is_retried(kb):
    write_transcript(kb.root)
    report = index_kb(kb, FakeEmbedder(), summarizer=Failing())
    row = _file_row(kb)
    assert row["status"] == "ok" and row["error_msg"] == f"{SUMMARY_PENDING}: SummaryError: model exploded"
    assert (REL, row["error_msg"]) in report.problems
    assert _chunks(kb) and not _chunks(kb, kind="summary")
    fake = FakeSummarizer()
    report = index_kb(kb, FakeEmbedder(), summarizer=fake)  # unchanged file, but pending: tried again
    assert fake.calls == 1 and report.ok == 1
    assert _file_row(kb)["error_msg"] is None and _chunks(kb, kind="summary")


def test_without_a_summarizer_the_transcript_is_marked_and_filled_in_later(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    assert _file_row(kb)["error_msg"] == f"{SUMMARY_PENDING}: summaries disabled"
    assert _chunks(kb) and not _chunks(kb, kind="summary")
    index_kb(kb, FakeEmbedder())  # still no summarizer: nothing to retry, the file is skipped
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 1 and _file_row(kb)["error_msg"] is None


def test_ollama_down_stops_after_the_first_transcript(kb):
    for number in (1, 2, 3):
        write_transcript(kb.root, f"2023_01_0{number}-rozhovor.txt")
    down = Down()
    report = index_kb(kb, FakeEmbedder(), summarizer=down)
    assert down.calls == 1  # not one timeout per recording
    assert report.errors == 0
    for number in (1, 2, 3):
        row = _file_row(kb, f"docs/2023_01_0{number}-rozhovor.txt")
        assert row["status"] == "ok" and "not reachable" in row["error_msg"]
        assert _chunks(kb, f"docs/2023_01_0{number}-rozhovor.txt")


def test_ordinary_text_is_never_summarized(kb):
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 0
    assert not _query(kb, "SELECT 1 FROM files WHERE error_msg LIKE 'summary pending%'")


JOSEF_ROW = (0.0, 5.0, "Josef Novák odišiel v roku 1700 do sveta.")  # a year that matches none of his events


def _links_by_kind(kb):
    return {r["kind"]: r["n"] for r in _query(
        kb, "SELECT c.kind, COUNT(*) AS n FROM links l JOIN chunks c ON l.source_kind = 'chunk' "
            "AND c.id = l.source_id JOIN files f ON f.id = c.file_id WHERE f.path = ? GROUP BY c.kind", (REL,))}


def test_summary_chunks_link_no_people(kb):
    write_transcript(kb.root, rows=[JOSEF_ROW, *sample_rows()[1:]])
    index_kb(kb, FakeEmbedder(), summarizer=FakeSummarizer())  # the fake summary repeats "Josef Novák"
    assert "Josef Novák" in _chunks(kb, kind="summary")[1]["text"]
    kinds = _links_by_kind(kb)
    assert kinds.get("text") and "summary" not in kinds  # only the transcript itself links people


def test_a_transcript_mismatch_cites_the_moment(kb):
    from genea.tools.conflicts import find_conflicts

    write_transcript(kb.root, rows=[JOSEF_ROW, *sample_rows()[1:]])
    index_kb(kb, FakeEmbedder(), summarizer=FakeSummarizer())
    context = open_tools(kb, FakeEmbedder())
    try:
        found = [c for c in find_conflicts(context).conflicts
                 if c.kind == "document_mismatch" and c.sources[0].file == REL]
    finally:
        context.conn.close()
    assert found  # from the transcript text, not from the summary
    assert all(c.sources[0].cite() == f"[{REL} @0:00]" for c in found)
