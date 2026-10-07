from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder

runner = CliRunner()


def _setup(sample_root, monkeypatch, extra_config=""):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n' + extra_config, encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    monkeypatch.setattr("genea.cli.agentcmds.build_model", lambda settings: FunctionModel(
        lambda messages, info: ModelResponse(parts=[TextPart("Josef Novák [tree.ged:L7]")])))
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0


def test_ask(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 0, result.output
    assert "Josef Novák" in result.output
    assert "will be sent" not in result.output


def test_privacy_notice_for_cloud_models(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch, 'model = "anthropic:claude-sonnet-5-5"\n')
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 0, result.output
    assert "will be sent to anthropic" in result.output


def test_chat(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "chat"], input="Ahoj\nexit\n")
    assert result.exit_code == 0, result.output
    assert "Josef Novák" in result.output
