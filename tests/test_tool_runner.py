import json

import pytest
from dataclasses import replace

from genea.kb import KnowledgeBase
from genea.tool_runner import TRUNCATION_MARKER, run_tool, shrink
from genea.tools import conflicts, mentions, people, search


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


def test_shrink_names_the_fields_it_cut():
    data = {"query": "q", "hits": [{"text": "x" * 3000}, {"text": "y" * 3000}], "note": "short",
            "sources": [{"file": "a"}, {"file": "a"}]}
    body = json.loads(shrink(data, 1500))
    assert body["truncated"] is True and body["cut_fields"] == ["hits"]
    assert body["sources"] == [{"file": "a"}] and "query" in body


def test_shrink_to_sources_only_names_every_dropped_field():
    body = json.loads(shrink({"a": "x" * 500, "b": [1, 2, 3], "sources": [{"file": "a"}]}, 100))
    assert body["cut_fields"] == ["a", "b"] and "a" not in body


def _small(tools, limit):
    return replace(tools, kb=KnowledgeBase(tools.kb.root, replace(tools.kb.settings, tool_output_chars=limit)))


@pytest.mark.parametrize("limit", [300, 600, 1500])
@pytest.mark.parametrize(("tool", "args"), [
    (people.find_person, ("Jan Novák", None, None)),
    (people.get_person, ("tree.ged#@I12@",)),
    (people.relatives, ("tree.ged#@I12@", "ancestors", 2)),
    (people.timeline, ("tree.ged#@I12@",)),
    (search.search_documents, ("Josef", 8, 1)),
    (mentions.documents_about, ("tree.ged#@I12@", 1)),
    (search.read_document, ("docs/kronika.txt", None, None)),
    (search.find_images, (None, None, None, None, "Anna")),
    (conflicts.find_conflicts, ("tree.ged#@I12@",)),
], ids=lambda v: getattr(v, "__name__", None))
def test_no_tool_loses_text_silently(tools, tool, args, limit):
    full = run_tool(_small(tools, 100000), tool, *args).text
    out = run_tool(_small(tools, limit), tool, *args).text
    if out == full:
        return
    if not out.endswith(TRUNCATION_MARKER):  # search trims itself and says so: more_files/more_chunks, cut passages
        body = json.loads(out)
        assert tool is search.search_documents and "more_files" in body and "more_chunks" in body
        return
    body = json.loads(out[: -len(TRUNCATION_MARKER)])
    assert body["truncated"] is True and body["cut_fields"] and body["sources"]
