import asyncio
import os
import sys

import pytest
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase

runner = CliRunner()


def _prepare(sample_root):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")


def _params(sample_root):
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable,
        args=["-c", "from genea.cli.main import app; app()", "--kb", str(sample_root), "mcp"],
        env={**os.environ, "PYTHONUNBUFFERED": "1"},  # a stray print must reach the pipe while the session is live
    )


def test_not_indexed_server_still_connects_and_explains(sample_root, tmp_path):
    """Claude Code showed only CONNECTION_CLOSED when the server exited before the handshake."""
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    _prepare(sample_root)
    stderr_path = tmp_path / "server-stderr.txt"

    async def session_run():
        with stderr_path.open("w", encoding="utf-8") as errlog:
            async with stdio_client(_params(sample_root), errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    found = await session.call_tool("find_person", {"name": "Jan"})
                    status = await session.call_tool("index_status", {})
                    return init, found, status

    init, found, status = asyncio.run(session_run())
    assert "not indexed yet" in init.instructions
    assert found.is_error is True and "not indexed yet" in found.content[0].text
    assert status.is_error is False and "State: missing" in status.content[0].text
    assert "not indexed yet" in stderr_path.read_text(encoding="utf-8")


def test_missing_extra_exits_1_with_hint(sample_root, monkeypatch):
    _prepare(sample_root)
    monkeypatch.setitem(sys.modules, "genea.mcp_server", None)  # makes the import fail like a missing extra
    result = runner.invoke(app, ["--kb", str(sample_root), "mcp"])
    assert result.exit_code == 1
    assert "uv sync --extra mcp" in result.stderr
    assert result.stdout == ""


class _IndexEmbedder(FakeEmbedder):
    """Same model name and size as the default settings, so the subprocess can open the index."""

    model = "bge-m3"


def test_stdio_server_end_to_end(sample_root, tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    from genea.agent.prompt import MCP_INSTRUCTIONS

    _prepare(sample_root)
    index_kb(KnowledgeBase.open(str(sample_root)), _IndexEmbedder(dim=1024))
    stderr_path = tmp_path / "server-stderr.txt"
    params = _params(sample_root)

    transport_faults: list[Exception] = []

    async def record_faults(message):
        # The SDK hands non-JSON stdout lines to the message handler as Exceptions (and ignores them by default).
        if isinstance(message, Exception):
            transport_faults.append(message)

    async def session_run():
        with stderr_path.open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, message_handler=record_faults) as session:
                    init = await session.initialize()
                    listed = await session.list_tools()
                    found = await session.call_tool("find_person", {"name": "Jan Novák", "born_around": 1900})
                    bad_id = await session.call_tool("get_person", {"person_id": "tree.ged#@I999@"})
                    bad_args = await session.call_tool("relatives", {"person_id": "tree.ged#@I12@", "relation": "cousins"})
                    again = await session.call_tool("get_person", {"person_id": "tree.ged#@I12@"})
                    return init, listed, found, bad_id, bad_args, again

    init, listed, found, bad_id, bad_args, again = asyncio.run(session_run())
    assert transport_faults == []  # stdout carried only protocol messages
    assert init.instructions.startswith(MCP_INSTRUCTIONS)
    assert "Index state when this session started: The index is complete." in init.instructions
    assert {t.name for t in listed.tools} == {
        "find_person", "get_person", "relatives", "timeline", "search_documents",
        "read_document", "find_images", "find_conflicts", "index_status"}
    assert found.is_error is False and "Jan Novák" in found.content[0].text  # diacritics survive stdio
    assert bad_id.is_error is True and "find_person" in bad_id.content[0].text
    assert bad_args.is_error is True            # invalid arguments are an error, not a crash...
    assert again.is_error is False              # ...and the server keeps serving afterwards
    assert "will be sent" in stderr_path.read_text(encoding="utf-8")  # privacy notice goes to stderr
