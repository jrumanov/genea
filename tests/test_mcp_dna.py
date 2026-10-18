import asyncio
import json
from dataclasses import replace

import pytest

pytest.importorskip("mcp")

from fixtures.dna import add_person  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError as McpToolError  # noqa: E402

from genea.dna import store  # noqa: E402
from genea.dna.store import HaploCall, Haplogroups  # noqa: E402
from genea.kb import KnowledgeBase, init_kb  # noqa: E402
from genea.mcp_server import IndexGate, build_server  # noqa: E402


def _never_opened():
    raise AssertionError("DNA tools must not open the index")


@pytest.fixture
def server(tmp_path):
    """A knowledge base that is not indexed: DNA tools still answer."""
    base = init_kb(tmp_path / "kb")
    kb = KnowledgeBase(base.root, replace(base.settings, dna_session_rsids=25))
    add_person(kb)
    store.save_haplogroups(kb, "tree.ged-I12", Haplogroups(analyzed_at="x", calls=[
        HaploCall(line="Y", haplogroup="R-P312", markers_used=16)]))
    return build_server(IndexGate(kb, _never_opened))


def _call(server, tool, arguments):
    return asyncio.run(server.call_tool(tool, arguments))


def test_dna_tools_work_without_an_index(server):
    summary = json.loads(_call(server, "dna_summary", {"person": "tree.ged#@I12@"}).content[0].text)
    assert summary["person"] == "tree.ged-I12"
    groups = json.loads(_call(server, "haplogroups", {"person": "tree.ged-I12"}).content[0].text)
    assert groups["calls"][0]["haplogroup"] == "R-P312"


def test_snp_lookup_budget_lasts_the_whole_server_session(server):
    first = json.loads(_call(server, "snp_lookup", {"person": "tree.ged-I12",
                                                    "rsids": [f"rs{1000 + i}" for i in range(20)]}).content[0].text)
    assert first["lookups_left"] == 5
    with pytest.raises(McpToolError, match="Privacy limit"):
        _call(server, "snp_lookup", {"person": "tree.ged-I12", "rsids": [f"rs{1100 + i}" for i in range(6)]})


def test_cap_reaches_the_client_as_an_error(server):
    with pytest.raises(McpToolError, match="At most 20"):
        _call(server, "snp_lookup", {"person": "tree.ged-I12", "rsids": [f"rs{i}" for i in range(21)]})
