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


def _prompts(sample_root):
    return (sample_root / ".genea" / "prompts.md").read_text(encoding="utf-8")


def test_ask_logs_the_prompt(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 0, result.output
    text = _prompts(sample_root)
    assert text.count("\n## ") + text.startswith("## ") == 1
    assert "**Q:** Kdo je Josef?" in text
    assert "> Josef Novák [tree.ged:L7]" in text
    assert "followup: false" in text


def test_chat_logs_each_turn_and_marks_followups(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)
    result = runner.invoke(app, ["--kb", str(sample_root), "chat"], input="Kdo je Josef?\nA kdy se narodil?\nexit\n")
    assert result.exit_code == 0, result.output
    text = _prompts(sample_root)
    assert text.count("followup: false") == 1
    assert text.count("followup: true") == 1
    assert text.index("Kdo je Josef?") < text.index("A kdy se narodil?")
    assert text.count("session: ") == 2  # same session id on both turns
    session_ids = {line for line in text.splitlines() if line.startswith("session: ")}
    assert len(session_ids) == 1


def test_log_prompts_can_be_turned_off(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch, "log_prompts = false\n")
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 0, result.output
    assert not (sample_root / ".genea" / "prompts.md").exists()


def test_failed_question_is_logged(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)

    def boom(messages, info):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("genea.cli.agentcmds.build_model", lambda settings: FunctionModel(boom))
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 1
    text = _prompts(sample_root)
    assert "failed: true" in text
    assert "model exploded" in text


def test_unwritable_prompt_log_does_not_break_the_answer(sample_root, monkeypatch):
    _setup(sample_root, monkeypatch)
    (sample_root / ".genea" / "prompts.md").mkdir()  # a folder where the file should be
    result = runner.invoke(app, ["--kb", str(sample_root), "ask", "Kdo je Josef?"])
    assert result.exit_code == 0, result.output
    assert "Josef Novák" in result.output
    assert "prompt log" in result.output.lower()
