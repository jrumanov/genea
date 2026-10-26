import json
import urllib.request

import pytest
from fixtures.analysis_tree import event_line

from genea.analysis import api as analysis_api
from genea.analysis import store
from genea.tools import mentions, people, search
from genea.tools.models import SourceRef
from genea.viewer import links

BASE = "http://127.0.0.1:8765"
JAN = "tree.ged#@I12@"


def _json(result) -> dict:
    return json.loads(result.model_dump_json(exclude_none=True))


def _keys(node, found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            found.append(key)
            _keys(value, found)
    elif isinstance(node, list):
        for value in node:
            _keys(value, found)
    return found


def test_search_hit_has_link_and_sources_do_not(tools, no_base_url):
    links.set_base_url(BASE)
    data = _json(search.search_documents(tools, "Josef"))
    assert data["hits"]
    for hit in data["hits"]:
        assert hit["link"].startswith(f"{BASE}/v/") and f"chunk={hit['chunk']}" in hit["link"]
    assert data["sources"] and all("link" not in s for s in data["sources"])


def test_documents_about_mentions_have_links(tools, no_base_url):
    links.set_base_url(BASE)
    data = _json(mentions.documents_about(tools, JAN))
    assert data["files"]
    for entry in data["files"]:
        for mention in entry["mentions"]:
            assert mention["link"].startswith(BASE) and f"chunk={mention['chunk']}" in mention["link"]


def test_get_person_event_sources_have_links(tools, no_base_url):
    links.set_base_url(BASE)
    data = _json(people.get_person(tools, JAN))
    assert data["events"]
    for event in data["events"]:
        assert f"line={event['source']['line']}" in event["source"]["link"]


def test_find_images_source_has_link(tools_with_images, no_base_url):
    links.set_base_url(BASE)
    data = _json(search.find_images(tools_with_images, text="Anna"))
    assert data["images"]
    assert all(i["source"]["link"].startswith(f"{BASE}/v/") for i in data["images"])


def test_read_document_source_has_link(tools, no_base_url):
    links.set_base_url(BASE)
    plain = _json(search.read_document(tools, "docs/kronika.txt"))
    assert plain["sources"][0]["link"] == f"{BASE}/v/docs/kronika.txt"
    paged = search.read_document(tools, "docs/kronika.txt")
    assert paged.sources[0].page is None
    timed = tools.conn.execute(
        "SELECT f.path, c.start_s FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE c.start_s IS NOT NULL LIMIT 1").fetchone()
    if timed is not None:
        data = _json(search.read_document(tools, timed["path"], start_s=timed["start_s"]))
        assert "t=" in data["sources"][0]["link"]
    assert "page=2" in SourceRef(file="docs/x.pdf", page=2).link


def test_output_without_viewer_is_unchanged(tools, tools_with_images):
    assert links.get_base_url() is None
    outputs = [_json(search.search_documents(tools, "Josef")), _json(mentions.documents_about(tools, JAN)),
               _json(people.get_person(tools, JAN)), _json(search.find_images(tools_with_images, text="Anna"))]
    for out in outputs:
        assert "link" not in _keys(out)


def test_links_count_against_the_output_budget(kb, tools, no_base_url):
    from dataclasses import replace

    from genea.kb import KnowledgeBase
    from genea.tools.base import ToolContext

    links.set_base_url(BASE)
    small = ToolContext(**{**tools.__dict__, "kb": KnowledgeBase(kb.root, replace(kb.settings, tool_output_chars=1500))})
    text = search.search_documents(small, "Josef").model_dump_json(exclude_none=True)
    assert '"truncated"' not in text and len(text) <= 1500


def test_stored_proposal_has_no_link(actx, analysis_kb, no_base_url):
    links.set_base_url(BASE)
    name = json.loads(analysis_api.analyze(actx, "person", "tree.ged#@I3@").model_dump_json())["name"]
    line = event_line(actx, "@I3@", "BIRT")
    analysis_api.propose(actx, name, "update_event",
                         {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                         ["[docs/zapis.txt p.1]", f"[tree.ged:L{line}]"], "the register", None, "claude")
    raw = (analysis_kb.analyses_dir / name / "analysis.json").read_text(encoding="utf-8")
    assert '"link"' not in raw and BASE not in raw
    shown = analysis_api.read_analysis(actx, name, None, 1).model_dump_json(exclude_none=True)
    assert BASE in shown  # tool output to Claude still carries links


def test_stored_change_has_no_link(kb, no_base_url):
    from genea.changes.store import Change, ChangeStore

    links.set_base_url(BASE)
    store_ = ChangeStore(kb)
    change = Change(id="c-1", created="now", target={}, operation="add_note", payload={}, rationale="why",
                    sources=[SourceRef(file="docs/kronika.txt", page=1)])
    store_._write(change)
    raw = store_._path(change.status, change.id).read_text(encoding="utf-8")
    assert '"link"' not in raw and BASE not in raw


def test_serve_starts_and_stops_viewer(tools, no_base_url, monkeypatch):
    from genea import mcp_server

    seen = {}

    class FakeServer:
        def run(self, transport):
            base = links.get_base_url()
            seen["base"] = base
            with urllib.request.urlopen(f"{base}/raw/docs/kronika.txt", timeout=5) as resp:
                seen["status"] = resp.status

    monkeypatch.setattr(mcp_server, "build_server", lambda gate: FakeServer())
    mcp_server.serve(mcp_server.IndexGate.ready(tools))
    assert seen["status"] == 200 and seen["base"].startswith("http://127.0.0.1:")
    assert links.get_base_url() is None
    with pytest.raises(OSError):
        urllib.request.urlopen(f"{seen['base']}/raw/docs/kronika.txt", timeout=2)
