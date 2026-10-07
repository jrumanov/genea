from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder

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
