import asyncio
import json
from dataclasses import replace

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError as McpToolError  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

from genea.agent.core import build_agent  # noqa: E402
from genea.index.embed import EmbeddingError  # noqa: E402
from genea.mcp_server import build_server  # noqa: E402
from genea.tool_runner import run_tool  # noqa: E402
from genea.tools import conflicts, people, search  # noqa: E402

READ_ONLY = {"find_person", "get_person", "relatives", "timeline", "search_documents",
             "read_document", "find_images", "find_conflicts"}
JAN = "tree.ged#@I12@"

# (tool name, MCP arguments, tools.* function, positional args the wrapper must pass to it)
CASES = [
    ("find_person", {"name": "Jan Novák", "born_around": 1900}, people.find_person, ("Jan Novák", 1900, None)),
    ("get_person", {"person_id": JAN}, people.get_person, (JAN,)),
    ("relatives", {"person_id": JAN, "relation": "ancestors", "depth": 2}, people.relatives, (JAN, "ancestors", 2)),
    ("timeline", {"person_id": JAN}, people.timeline, (JAN,)),
    ("search_documents", {"query": "Josef", "k": 3}, search.search_documents, ("Josef", 3)),
    ("read_document", {"path": "docs/kronika.txt"}, search.read_document, ("docs/kronika.txt", None)),
    ("find_images", {"text": "Anna"}, search.find_images, (None, None, None, None, "Anna")),
    ("find_conflicts", {"person_id": JAN}, conflicts.find_conflicts, (JAN,)),
]


def _call(server, tool, arguments):
    # arguments stay a dict: find_person has its own `name` argument
    return asyncio.run(server.call_tool(tool, arguments))


def test_registers_exactly_the_read_only_tools(tools):
    names = {t.name for t in asyncio.run(build_server(tools).list_tools())}
    assert names == READ_ONLY
    assert not names & {"write_note", "propose_change"}


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
