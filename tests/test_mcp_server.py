import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError as McpToolError  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

from genea.agent.core import build_agent  # noqa: E402
from genea.index.embed import EmbeddingError  # noqa: E402
from genea.index.embed import FakeEmbedder  # noqa: E402
from genea.index.indexer import index_kb  # noqa: E402
from genea.index.progress import IndexLock, IndexProgress, now_iso, write_progress  # noqa: E402
from genea.mcp_server import IndexGate, build_server  # noqa: E402
from genea.tool_runner import run_tool  # noqa: E402
from genea.tools import conflicts, people, search  # noqa: E402
from genea.tools.base import open_tools  # noqa: E402

READ_ONLY = {"find_person", "get_person", "relatives", "timeline", "search_documents",
             "read_document", "find_images", "find_conflicts"}
MCP_ONLY = {"index_status"}
JAN = "tree.ged#@I12@"

# (tool name, MCP arguments, tools.* function, positional args the wrapper must pass to it)
CASES = [
    ("find_person", {"name": "Jan Novák", "born_around": 1900}, people.find_person, ("Jan Novák", 1900, None)),
    ("get_person", {"person_id": JAN}, people.get_person, (JAN,)),
    ("relatives", {"person_id": JAN, "relation": "ancestors", "depth": 2}, people.relatives, (JAN, "ancestors", 2)),
    ("timeline", {"person_id": JAN}, people.timeline, (JAN,)),
    ("search_documents", {"query": "Josef", "k": 3}, search.search_documents, ("Josef", 3)),
    ("read_document", {"path": "docs/kronika.txt"}, search.read_document, ("docs/kronika.txt", None, None)),
    ("find_images", {"text": "Anna"}, search.find_images, (None, None, None, None, "Anna")),
    ("find_conflicts", {"person_id": JAN}, conflicts.find_conflicts, (JAN,)),
]


def _call(server, tool, arguments):
    # arguments stay a dict: find_person has its own `name` argument
    return asyncio.run(server.call_tool(tool, arguments))


ANALYSIS = {"analyze", "read_analysis", "propose", "withdraw", "add_analysis_note", "build_iteration"}


def test_registers_exactly_the_expected_tools(tools):
    names = {t.name for t in asyncio.run(build_server(tools).list_tools())}
    assert names == READ_ONLY | MCP_ONLY | ANALYSIS
    assert not names & {"write_note", "propose_change", "dismiss"}


@pytest.mark.parametrize(("name", "arguments", "fn", "positional"), CASES, ids=[c[0] for c in CASES])
def test_tool_payload_matches_direct_call(tools, name, arguments, fn, positional):
    result = _call(build_server(tools), name, arguments)
    assert result.is_error is False
    assert result.content[0].text == run_tool(tools, fn, *positional).text


def test_descriptions_match_the_agent_tools(tools):
    agent_tools = build_agent(TestModel())._function_toolset.tools
    served = {t.name: t.description for t in asyncio.run(build_server(tools).list_tools())}
    for name in READ_ONLY:
        assert " ".join(served[name].split()) == " ".join(agent_tools[name].description.split())


def test_tool_error_reaches_the_client_as_an_error(tools):
    with pytest.raises(McpToolError, match="find_person"):
        _call(build_server(tools), "get_person", {"person_id": "tree.ged#@I999@"})


class _Down:
    model = "fake"
    dim = 64

    def embed(self, texts):
        raise EmbeddingError("ollama is down")


def test_search_degrades_without_embeddings(tools):
    server = build_server(replace(tools, embedder=_Down()))
    body = json.loads(_call(server, "search_documents", {"query": "Josef"}).content[0].text)
    assert "full-text" in body["note"]


def _gate(kb, embedder=None):
    return IndexGate(kb, lambda: open_tools(kb, embedder or FakeEmbedder()))


def _text(result):
    return result.content[0].text


def _building(kb, done=2, total=6):
    write_progress(kb, IndexProgress(pid=1, started_at=now_iso(), phase="indexing", phase_started_at=now_iso(),
                                     updated_at=now_iso(), done=done, total=total, current="docs/kronika.txt"))


def test_server_starts_without_an_index_and_explains(kb):
    server = build_server(_gate(kb))
    assert {t.name for t in asyncio.run(server.list_tools())} == READ_ONLY | MCP_ONLY | ANALYSIS
    with pytest.raises(McpToolError, match="not indexed yet"):
        _call(server, "find_person", {"name": "Jan"})
    assert "not indexed yet" in _text(_call(server, "index_status", {}))
    assert not kb.index_path.exists()  # asking never creates an empty index


def test_first_index_in_progress_reports_progress(kb):
    server = build_server(_gate(kb))
    _building(kb)
    with IndexLock(kb):
        with pytest.raises(McpToolError, match=r"being built: 2 / 6 files"):
            _call(server, "get_person", {"person_id": JAN})
        assert "2 / 6 files" in _text(_call(server, "index_status", {}))


def test_partial_index_results_carry_a_note(kb):
    index_kb(kb, FakeEmbedder())
    server = build_server(_gate(kb))
    _building(kb, done=3)
    with IndexLock(kb):
        text = _text(_call(server, "find_person", {"name": "Jan Novák"}))
        assert "Jan Novák" in text
        assert "still being built (3 / 6 files" in text and "may be incomplete" in text
    assert "still being built" not in _text(_call(server, "find_person", {"name": "Jan Novák"}))


def test_interrupted_index_results_carry_a_note(kb):
    index_kb(kb, FakeEmbedder())
    conn = sqlite3.connect(kb.index_path)
    conn.execute("UPDATE meta SET value = 'running' WHERE key = 'index_state'")
    conn.commit()
    conn.close()
    text = _text(_call(build_server(_gate(kb)), "find_person", {"name": "Jan Novák"}))
    assert "did not finish" in text


def test_index_status_when_ready(tools):
    text = _text(_call(build_server(tools), "index_status", {}))
    assert "complete" in text
    assert "People: 20" in text


def test_schema_mismatch_is_a_tool_error(kb):
    index_kb(kb, FakeEmbedder(64))
    server = build_server(_gate(kb, FakeEmbedder(32)))
    with pytest.raises(McpToolError, match="--rebuild"):
        _call(server, "find_person", {"name": "Jan"})
