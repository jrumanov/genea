import json

from fixtures.transcripts import NAME, sample_rows, write_transcript
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index import summary_cache, summary_work
from genea.index.db import open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import file_hash, index_kb
from genea.ingest.summarize import FakeSummarizer

REL = f"docs/{NAME}"


def _request(kb, digest):
    return json.loads((kb.summary_work_dir / f"{digest}.request.json").read_text(encoding="utf-8"))


def _answer(request, overall="Celkovo o rodine."):
    return {"overall": overall,
            "sections": [{"summary": f"Časť {i}.", "topics": ["rodina"], "people": ["babka Fábiková", " "],
                          "places": "Prískupice", "years": [1954]}
                         for i, _ in enumerate(request["sections"])]}


def _write_result(kb, digest, data):
    path = kb.summary_work_dir / f"{digest}.result.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_pending_writes_one_request_per_unsummarized_transcript(kb):
    path = write_transcript(kb.root)
    requests = summary_work.write_requests(kb, section_chars=3000)
    digest = file_hash(path, "doc")
    assert [(r.path, r.digest) for r in requests] == [(REL, digest)]
    data = _request(kb, digest)
    assert data["path"] == REL and data["digest"] == digest
    assert len(data["sections"]) == 3  # ~6,000 characters / 3,000, split as the Ollama summarizer does
    first, last = data["sections"][0], data["sections"][-1]
    assert first["start_s"] == 0.0 and last["end_s"] == 899.0
    text = (kb.summary_work_dir / f"{digest}.sections.md").read_text(encoding="utf-8")
    assert text.startswith(f"# {REL}") and "## Section 3 of 3 (" in text
    assert "Prískupíc" in text and max(len(line) for line in text.splitlines()) <= 100
    assert (kb.summary_work_dir / "INSTRUCTIONS.md").is_file()


def test_pending_skips_cached_transcripts_and_ordinary_text(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder(), summarizer=FakeSummarizer())  # kb also holds docs/kronika.txt
    assert summary_work.write_requests(kb) == []
    assert list(kb.summary_work_dir.glob("*.request.json")) == []


def test_pending_removes_requests_that_are_no_longer_needed(kb):
    path = write_transcript(kb.root)
    summary_work.write_requests(kb)
    old = file_hash(path, "doc")
    write_transcript(kb.root, rows=sample_rows()[:40])  # changed: a new digest
    summary_work.write_requests(kb)
    assert not (kb.summary_work_dir / f"{old}.request.json").exists()
    assert (kb.summary_work_dir / f"{file_hash(path, 'doc')}.request.json").exists()


def test_import_stores_a_valid_result(kb):
    path = write_transcript(kb.root)
    summary_work.write_requests(kb, section_chars=3000)
    digest = file_hash(path, "doc")
    request = _request(kb, digest)
    _write_result(kb, digest, _answer(request))
    report = summary_work.import_results(kb, model="claude-haiku-4-5")
    assert report.imported == [REL] and report.failed == []
    summary = summary_cache.load(kb, digest)
    assert summary.overall == "Celkovo o rodine."
    assert [(s.start_s, s.end_s) for s in summary.sections] == [(s["start_s"], s["end_s"]) for s in request["sections"]]
    first = summary.sections[0]
    assert first.people == ["babka Fábiková"] and first.places == [] and first.years == ["1954"]
    stored = json.loads(summary_cache.cache_path(kb, digest).read_text(encoding="utf-8"))
    assert stored["model"] == "claude-haiku-4-5"
    assert list(kb.summary_work_dir.glob(f"{digest}.*")) == []  # the work files are gone


def test_a_single_section_needs_no_overall(kb):
    path = write_transcript(kb.root, rows=sample_rows()[:5])
    summary_work.write_requests(kb)
    digest = file_hash(path, "doc")
    answer = _answer(_request(kb, digest))
    del answer["overall"]
    _write_result(kb, digest, answer)
    assert summary_work.import_results(kb).imported == [REL]
    assert summary_cache.load(kb, digest).overall == "Časť 0."


def test_import_rejects_bad_results_and_keeps_them(kb):
    path = write_transcript(kb.root)
    summary_work.write_requests(kb, section_chars=3000)
    digest = file_hash(path, "doc")
    request = _request(kb, digest)
    for bad, reason in [(dict(_answer(request), sections=_answer(request)["sections"][:2]), "3 sections"),
                        (dict(_answer(request), overall=" "), "'overall'"),
                        ("{not json", "JSON")]:
        result = kb.summary_work_dir / f"{digest}.result.json"
        result.write_text(bad if isinstance(bad, str) else json.dumps(bad), encoding="utf-8")
        report = summary_work.import_results(kb)
        assert report.imported == [] and len(report.failed) == 1
        assert report.failed[0][0] == REL and reason in report.failed[0][1]
        assert result.exists() and summary_cache.load(kb, digest) is None


def test_import_ignores_a_result_without_a_request(kb):
    kb.summary_work_dir.mkdir()
    _write_result(kb, "0" * 64, {"overall": "x", "sections": []})
    report = summary_work.import_results(kb)
    assert report.imported == [] and report.failed[0][0] == f"{'0' * 64}.result.json"


def test_an_imported_summary_is_indexed_without_a_summarizer(kb):
    path = write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())  # no summarizer: marked pending
    summary_work.write_requests(kb)
    digest = file_hash(path, "doc")
    _write_result(kb, digest, _answer(_request(kb, digest)))
    summary_work.import_results(kb)
    report = index_kb(kb, FakeEmbedder())  # still no summarizer, but the summary is now cached
    assert report.ok == 1
    conn = open_index(kb, FakeEmbedder())
    try:
        row = conn.execute("SELECT error_msg FROM files WHERE path = ?", (REL,)).fetchone()
        summaries = conn.execute("SELECT COUNT(*) FROM chunks WHERE kind = 'summary'").fetchone()[0]
    finally:
        conn.close()
    assert row[0] is None and summaries > 0
    assert index_kb(kb, FakeEmbedder()).ok == 0  # and not again


def test_pending_without_a_cached_summary_is_still_skipped_without_a_summarizer(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    assert index_kb(kb, FakeEmbedder()).ok == 0


def test_cli_pending_then_import(kb):
    path = write_transcript(kb.root)
    runner = CliRunner()
    result = runner.invoke(app, ["--kb", str(kb.root), "summaries", "pending"])
    assert result.exit_code == 0, result.output
    assert "1 transcript" in result.output and str(kb.summary_work_dir) in result.output.replace("\n", "")
    digest = file_hash(path, "doc")
    _write_result(kb, digest, _answer(_request(kb, digest)))
    result = runner.invoke(app, ["--kb", str(kb.root), "summaries", "import", "--model", "claude-haiku-4-5"])
    assert result.exit_code == 0, result.output
    assert "imported 1" in result.output and "genea index" in result.output


def test_cli_import_fails_on_a_bad_result(kb):
    path = write_transcript(kb.root)
    runner = CliRunner()
    runner.invoke(app, ["--kb", str(kb.root), "summaries", "pending"])
    _write_result(kb, file_hash(path, "doc"), {"sections": []})
    result = runner.invoke(app, ["--kb", str(kb.root), "summaries", "import"])
    assert result.exit_code == 1 and REL in result.output
