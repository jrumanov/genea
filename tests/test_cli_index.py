import sqlite3

from fixtures.transcripts import write_transcript
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.ingest.summarize import FakeSummarizer
from genea.index.progress import IndexLock, IndexProgress, now_iso, write_progress
from genea.kb import KnowledgeBase

runner = CliRunner()


def _prepare(sample_root, monkeypatch):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())


def test_status_before_index(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert result.exit_code == 1
    assert "genea index" in result.output


def test_index_then_status(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    assert result.exit_code == 0, result.output
    assert "ok 5" in result.output
    assert "needs OCR 1" in result.output
    result = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert result.exit_code == 0, result.output
    assert "docs/sken.pdf" in result.output
    assert "20 people" in result.output


def test_status_while_first_index_runs(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    kb = KnowledgeBase.open(str(sample_root))
    write_progress(kb, IndexProgress(pid=1, started_at=now_iso(), phase="indexing", phase_started_at=now_iso(),
                                     updated_at=now_iso(), done=2, total=6, current="docs/kronika.txt"))
    with IndexLock(kb):
        result = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert result.exit_code == 0, result.output
    assert "Indexing in progress" in result.output
    assert "2 / 6 files" in result.output


def test_status_after_interrupted_run(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0
    kb = KnowledgeBase.open(str(sample_root))
    conn = sqlite3.connect(kb.index_path)
    conn.execute("UPDATE meta SET value = 'running' WHERE key = 'index_state'")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert result.exit_code == 0, result.output
    assert "did not finish" in result.output
    assert "20 people" in result.output


def test_index_refuses_a_second_run(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    monkeypatch.setattr("genea.index.indexer.LOCK_WAIT", 0.0)
    with IndexLock(KnowledgeBase.open(str(sample_root))):
        result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    assert result.exit_code == 1
    assert "already running" in result.output


def test_index_explains_a_locked_database(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0
    monkeypatch.setattr("genea.index.db.BUSY_TIMEOUT", 0.1)
    other = sqlite3.connect(KnowledgeBase.open(str(sample_root)).index_path)
    other.execute("BEGIN EXCLUSIVE")
    try:
        result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    finally:
        other.close()
    assert result.exit_code == 1
    assert "another program" in result.output
    assert "Traceback" not in result.output


def test_index_summarizes_transcripts_unless_told_not_to(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    write_transcript(sample_root)
    fake = FakeSummarizer()
    monkeypatch.setattr("genea.cli.common.get_summarizer", lambda kb: fake)

    result = runner.invoke(app, ["--kb", str(sample_root), "index", "--no-summaries"])
    assert result.exit_code == 0, result.output
    assert fake.calls == 0
    status = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert "disabled" in status.output  # single words: rich may wrap a longer phrase across lines

    result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    assert result.exit_code == 0, result.output
    assert fake.calls == 1
    assert "pending" not in runner.invoke(app, ["--kb", str(sample_root), "status"]).output


def test_index_says_when_there_is_no_model_for_summaries(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    monkeypatch.setattr("genea.cli.common.get_summarizer", lambda kb: None)
    result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    assert result.exit_code == 0, result.output
    assert "summary_model" in result.output
