import json
from dataclasses import replace

from genea.kb import KnowledgeBase
from genea.tool_runner import TRUNCATION_MARKER, run_tool
from genea.tools import people


def test_run_tool_returns_result_json(tools):
    out = run_tool(tools, people.find_person, "Jan Novák")
    assert out.is_error is False
    assert json.loads(out.text)["candidates"]


def test_tool_error_becomes_error_text(tools):
    out = run_tool(tools, people.get_person, "tree.ged#@I999@")
    assert out.is_error is True
    assert "find_person" in json.loads(out.text)["error"]


def test_unexpected_exception_becomes_error_text(tools):
    def boom(ctx):
        raise RuntimeError("corrupt vec table")

    out = run_tool(tools, boom)
    assert out.is_error is True
    assert json.loads(out.text)["error"] == "boom failed: RuntimeError: corrupt vec table"


def test_oversize_result_is_truncated_and_keeps_sources(tools):
    small = replace(tools, kb=KnowledgeBase(tools.kb.root, replace(tools.kb.settings, tool_output_chars=600)))
    out = run_tool(small, people.get_person, "tree.ged#@I3@")
    assert out.is_error is False
    assert out.text.endswith(TRUNCATION_MARKER)
    body = json.loads(out.text[: -len(TRUNCATION_MARKER)])
    assert body["sources"] and body["truncated"] is True
