import json
from dataclasses import replace

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.tools.family import family_key
from genea.tools.models import DocHit, SourceRef
from genea.tools.search import search_documents


def test_family_key_census_sides():
    a = "rodinna historia/_census/1930_12_03-Trencin_999-scitaci_harok_a.md"
    assert family_key(a) == family_key(a.replace("_a.md", "_b.md"))


def test_family_key_scan_sides():
    base = "Scanning initiative_sorted/1970/1977/1977_09_19-vojdan_172"
    assert len({family_key(base + s + ".md") for s in ("a1", "b1", "b2")}) == 1


def test_family_key_keeps_different_documents_apart():
    assert family_key("docs/1930_12_03-x.md") != family_key("docs/1931_12_03-x.md")
    assert family_key("a/x_a.md") != family_key("b/x_a.md")
    assert family_key("docs/kniha1.pdf") != family_key("docs/kniha2.pdf")
    assert family_key("docs/tab1.md") == "docs/tab1.md"
    assert family_key("a/x_A.md") == family_key("a/x_B.md")


def test_hit_json_drops_fields_that_repeat_or_never_help():
    hit = DocHit(file="a.txt", chunk=0, text="x", score=0.03, end_s=9.0, source=SourceRef(file="a.txt", chunk=0))
    assert json.loads(hit.model_dump_json(exclude_none=True)) == {"file": "a.txt", "chunk": 0, "text": "x"}
    summary = DocHit(file="a.txt", chunk=0, text="x", score=0.03, kind="summary", source=SourceRef(file="a.txt"))
    assert json.loads(summary.model_dump_json(exclude_none=True))["kind"] == "summary"


def test_result_json_has_sources_but_no_query_or_per_hit_source(tools):
    data = json.loads(search_documents(tools, "Terezie Dvořáková Tišnov").model_dump_json(exclude_none=True))
    assert "query" not in data and data["sources"]
    assert all(not {"score", "end_s", "source"} & hit.keys() for hit in data["hits"])


def test_sides_of_one_document_fold_into_also(tools):
    docs = tools.kb.root / "docs"
    (docs / "harok_a.md").write_text("Maria Vrbová dcéra 17/V. 1914 Trenčín. " * 20, encoding="utf-8")
    (docs / "harok_b.md").write_text("Maria Vrbová dcéra 17/V. 1914 Trenčín. " * 19, encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(replace(tools), "Maria Vrbová dcéra")
    files = [h.file for h in result.hits]
    assert ("docs/harok_a.md" in files) != ("docs/harok_b.md" in files)  # one side only
    folded = next(h for h in result.hits if h.file.startswith("docs/harok_"))
    assert folded.also == [f for f in ("docs/harok_a.md", "docs/harok_b.md") if f != folded.file]
    assert json.loads(folded.model_dump_json(exclude_none=True))["also"] == folded.also


def test_other_chunks_of_the_same_file_are_not_folded(tools):
    (tools.kb.root / "docs" / "dlhy.txt").write_text("Jozef Fabik oral pole. " * 300, encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(tools, "Jozef Fabik", k=3, per_file=3)
    mine = [h for h in result.hits if h.file == "docs/dlhy.txt"]
    assert len(mine) > 1 and all(not h.also for h in mine)
