import json
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel

from genea.agent.core import AgentFailed, ask, build_agent
from genea.agent.model import build_model, is_local
from genea.changes.store import ChangeStore
from genea.config import Settings
from genea.kb import KnowledgeBase


def _parts(messages):
    return [p for m in messages for p in m.parts]


def script(*responses):
    """A fake model that returns the next scripted response on each request."""
    def fn(messages, info):
        n = sum(1 for m in messages if isinstance(m, ModelResponse))
        response = responses[n]
        return response(messages) if callable(response) else response
    return FunctionModel(fn)


def _with_settings(tools, **changes):
    return replace(tools, kb=KnowledgeBase(tools.kb.root, replace(tools.kb.settings, **changes)))


def test_answer_uses_tool_and_cites(tools):
    def final(messages):
        result = json.loads([p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1].content)
        top = result["candidates"][0]
        return ModelResponse(parts=[TextPart(f"{top['name']} {top['id']} [tree.ged:L{top['source']['line']}]")])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("find_person", {"name": "Jan Novák", "born_around": 1900})]), final))
    answer = ask(agent, tools, "Kdo je Jan Novák narozený kolem roku 1900?")
    assert answer.text.startswith("Jan Novák tree.ged#@I12@ [tree.ged:L")
    assert [name for name, _ in answer.tool_calls] == ["find_person"]


def test_tool_error_is_returned_as_result(tools):
    def final(messages):
        out = [p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1]
        return ModelResponse(parts=[TextPart(f"error: {out.content}")])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("get_person", {"person_id": "tree.ged#@I999@"})]), final))
    assert "find_person" in ask(agent, tools, "q").text


def test_unexpected_tool_exception_is_returned_as_result(tools, monkeypatch):
    def boom(ctx, query, k=8):
        raise RuntimeError("corrupt vec table")

    monkeypatch.setattr("genea.agent.core.search.search_documents", boom)

    def final(messages):
        out = [p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1]
        return ModelResponse(parts=[TextPart(f"error: {out.content}")])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("search_documents", {"query": "x"})]), final))
    text = ask(agent, tools, "q").text
    assert "RuntimeError" in text and "corrupt vec table" in text


def test_propose_change_through_agent(tools):
    call = ToolCallPart("propose_change", {
        "target_kind": "image_xmp", "target_path": "photos/svatba_1932.jpg", "operation": "add_keywords",
        "payload": {"keywords": ["Jan Novák"]}, "rationale": "He is in the photo",
        "sources": [{"file": "tree.ged", "line": 7}],
    })
    agent = build_agent(script(ModelResponse(parts=[call]), ModelResponse(parts=[TextPart("Proposed.")])))
    ask(agent, tools, "Tag the wedding photo")
    [change] = ChangeStore(tools.kb).list("pending")
    assert change.payload == {"keywords": ["Jan Novák"]}
    assert change.sources[0].line == 7


def test_runaway_tool_loop_fails_cleanly(tools):
    looping = FunctionModel(lambda messages, info: ModelResponse(
        parts=[ToolCallPart("find_person", {"name": "Novák"})]))
    with pytest.raises(AgentFailed, match="tool_calls_limit"):
        ask(build_agent(looping), _with_settings(tools, max_tool_calls=2), "q")
    assert list(tools.kb.logs_dir.glob("*-failure.json"))


def test_history_is_passed_on(tools):
    seen = []

    def fn(messages, info):
        seen.append(len(messages))
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent(FunctionModel(fn))
    first = ask(agent, tools, "a")
    ask(agent, tools, "b", first.messages)
    assert seen[1] > seen[0]


def test_tool_output_is_truncated(tools):
    def final(messages):
        return ModelResponse(parts=[TextPart([p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1].content)])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("get_person", {"person_id": "tree.ged#@I3@"})]), final))
    text = ask(agent, _with_settings(tools, tool_output_chars=50), "q").text
    assert text.endswith("[truncated: narrow the query]")
    assert len(text) < 120


def test_truncated_output_keeps_sources_marker_and_limit(tools):
    def final(messages):
        return ModelResponse(parts=[TextPart([p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1].content)])

    agent = build_agent(script(
        ModelResponse(parts=[ToolCallPart("get_person", {"person_id": "tree.ged#@I3@"})]), final))
    limit = 600
    text = ask(agent, _with_settings(tools, tool_output_chars=limit), "q").text
    marker = " …[truncated: narrow the query]"
    assert text.endswith(marker)
    body = json.loads(text[: -len(marker)])
    assert body["sources"] and body["truncated"] is True
    assert len(text) <= limit + len(marker)


def test_shrink_trims_lists_and_keeps_unicode_intact():
    from genea.tool_runner import shrink as _shrink

    data = {"items": [{"t": "Žluťoučký kůň úpěl ďábelské ódy " * 20} for _ in range(50)],
            "sources": [{"file": "tree.ged", "line": n % 3} for n in range(30)]}
    out = _shrink(data, 800)
    parsed = json.loads(out)
    assert len(out) <= 800
    assert parsed["sources"] == [{"file": "tree.ged", "line": n} for n in range(3)]
    assert all(t["t"].startswith("Žluťoučký") for t in parsed["items"])


def test_repeatedly_failing_tool_does_not_end_the_run(tools):
    calls = [ToolCallPart("get_person", {"person_id": "tree.ged#@I999@"}) for _ in range(5)]
    agent = build_agent(script(*[ModelResponse(parts=[c]) for c in calls], ModelResponse(parts=[TextPart("done")])))
    assert ask(agent, tools, "q").text == "done"


def test_huge_error_message_is_capped(tools, monkeypatch):
    def boom(ctx, query, k=8):
        raise RuntimeError("x" * 5000)

    monkeypatch.setattr("genea.agent.core.search.search_documents", boom)

    def final(messages):
        return ModelResponse(parts=[TextPart([p for p in _parts(messages) if isinstance(p, ToolReturnPart)][-1].content)])

    agent = build_agent(script(ModelResponse(parts=[ToolCallPart("search_documents", {"query": "x"})]), final))
    assert len(ask(agent, _with_settings(tools, tool_output_chars=200), "q").text) < 300


def test_build_model():
    model = build_model(Settings(model="ollama:qwen3:8b", ollama_url="http://gpu:11434"))
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "qwen3:8b"
    assert build_model(Settings(model="anthropic:claude-sonnet-5-5")) == "anthropic:claude-sonnet-5-5"
    assert is_local(Settings()) and not is_local(Settings(model="anthropic:claude-sonnet-5-5"))
    with pytest.raises(ValueError):
        build_model(Settings(model="ollama:"))
