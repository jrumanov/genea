from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder

runner = CliRunner()


def _indexed(sample_root, monkeypatch):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0


def test_person_search_and_detail(sample_root, monkeypatch):
    _indexed(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "person", "Jan Novák"])
    assert result.exit_code == 0, result.output
    assert "tree.ged#@I3@" in result.output and "tree.ged#@I12@" in result.output
    result = runner.invoke(app, ["--kb", str(sample_root), "person", "tree.ged#@I3@"])
    assert result.exit_code == 0, result.output
    assert "Josef Novák" in result.output
    assert "Tišnov" in result.output


def test_person_unknown_id(sample_root, monkeypatch):
    _indexed(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "person", "tree.ged#@I999@"])
    assert result.exit_code == 1


def test_conflicts_command(sample_root, monkeypatch):
    _indexed(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "conflicts"])
    assert result.exit_code == 0, result.output
    assert "event_after_death" in result.output
    assert "tree.ged#@I4@" in result.output
