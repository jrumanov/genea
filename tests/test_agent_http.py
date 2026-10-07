"""Regression: several questions in one session against a real (loopback) HTTP model.

PydanticAI caches its httpx client, whose keep-alive connections are bound to the event loop
that opened them; a new loop per question broke the 2nd question ("Event loop is closed").
"""

import asyncio
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from genea.agent.core import ask, build_agent
from genea.agent.model import build_model
from genea.cli.main import app
from genea.index.embed import FakeEmbedder


class _FakeOpenAI(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like Ollama
    calls = 0

    def do_POST(self):  # noqa: N802 - http.server API
        self.rfile.read(int(self.headers["Content-Length"]))
        type(self).calls += 1
        body = json.dumps({
            "id": f"c{self.calls}", "object": "chat.completion", "created": 0, "model": "fake",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": f"answer {self.calls}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_ollama_url():
    handler = type("Handler", (_FakeOpenAI,), {"calls": 0})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_three_questions_share_one_runner(tools, fake_ollama_url):
    settings = replace(tools.kb.settings, model="ollama:fake", ollama_url=fake_ollama_url)
    agent = build_agent(build_model(settings))
    history = None
    with asyncio.Runner() as runner:
        for n in (1, 2, 3):
            answer = ask(agent, tools, f"question {n}", history, runner=runner)
            assert answer.text == f"answer {n}"
            history = answer.messages


def test_chat_answers_every_question_over_http(sample_root, monkeypatch, fake_ollama_url):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text(
        f'exclude = ["photos/**"]\nmodel = "ollama:fake"\nollama_url = "{fake_ollama_url}"\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    runner = CliRunner()
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0
    result = runner.invoke(app, ["--kb", str(sample_root), "chat"], input="a\nb\nc\nexit\n")
    assert result.exit_code == 0, result.output
    for n in (1, 2, 3):
        assert f"answer {n}" in result.output
    assert "Event loop is closed" not in result.output
