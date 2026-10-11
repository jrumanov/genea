import asyncio
import json

import pytest

pytest.importorskip("mcp")

from fixtures.analysis_tree import event_line  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError as McpToolError  # noqa: E402

from genea.analysis import store  # noqa: E402
from genea.mcp_server import build_server  # noqa: E402
from genea.tool_runner import TRUNCATION_MARKER  # noqa: E402


def _call(server, tool, arguments):
    return asyncio.run(server.call_tool(tool, arguments)).content[0].text


def test_claude_analyzes_proposes_and_builds(actx, analysis_kb):
    server = build_server(actx)
    text = _call(server, "analyze", {"scope": "branch", "target_id": "tree.ged#@I3@", "direction": "ancestors"})
    assert TRUNCATION_MARKER not in text
    out = json.loads(text)
    name = out["name"]
    assert name == "tree-branch-I3-ancestors-4" and len(out["findings"]) <= 10
    line = event_line(actx, "@I3@", "BIRT")
    _call(server, "propose", {"name": name, "operation": "update_event",
                              "payload": {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                              "sources": ["[docs/zapis.txt]"], "rationale": "the register"})
    assert store.load(analysis_kb, name).proposals[0].author == "claude"
    _call(server, "add_analysis_note", {"name": name, "text": "Register checked."})
    built = json.loads(_call(server, "build_iteration", {"name": name}))
    assert (analysis_kb.root / built["iteration"]).is_file() and built["applied"] == ["p-1"]
    page = json.loads(_call(server, "read_analysis", {"name": name, "severity": "info"}))
    assert all(f["severity"] == "info" for f in page["findings"])
    assert json.loads(_call(server, "withdraw", {"name": name, "proposal_id": "p-1"}))["status"] == "withdrawn"


@pytest.mark.parametrize("name", ["..", "../x", r"..\x", "a/b", ""], ids=["dots", "up", "backslash", "slash", "empty"])
def test_names_cannot_leave_the_analyses_folder(actx, name):
    with pytest.raises(McpToolError, match="Invalid analysis name"):
        _call(build_server(actx), "read_analysis", {"name": name})


def test_bad_proposal_is_an_error_for_claude(actx):
    server = build_server(actx)
    name = json.loads(_call(server, "analyze", {"scope": "person", "target_id": "tree.ged#@I3@"}))["name"]
    with pytest.raises(McpToolError, match="Cite at least one source"):
        _call(server, "propose", {"name": name, "operation": "add_note",
                                  "payload": {"person_id": "tree.ged#@I3@", "text": "x"}, "sources": [],
                                  "rationale": "why"})
