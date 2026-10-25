from dataclasses import replace

import pytest

from genea.index.embed import EmbeddingError, FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase
from genea.tool_runner import render
from genea.tools.base import ToolError
from genea.tools.search import find_images, read_document, search_documents


class BrokenEmbedder:
    model = "fake"
    dim = 64

    def embed(self, texts):
        raise EmbeddingError("Ollama is down")


def test_search_finds_relevant_chunk(tools):
    result = search_documents(tools, "Terezie Dvořáková Tišnov")
    assert result.hits[0].file == "docs/poznamky.md"
    assert result.sources[0].file == "docs/poznamky.md"
    assert result.note is None


def test_search_reports_pdf_page(tools):
    hit = next(h for h in search_documents(tools, "Vaclav").hits if h.file == "docs/matrika.pdf")
    assert hit.page == 2
    assert hit.source.cite() == "[docs/matrika.pdf p.2]"


def test_search_falls_back_to_full_text(tools):
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), "Dvořáková")
    assert result.hits
    assert "unavailable" in result.note


def test_search_empty_query(tools):
    with pytest.raises(ToolError):
        search_documents(tools, "   ")


def _long_docs(tools):
    for i in range(12):
        (tools.kb.root / "docs" / f"dlhy_{i}.txt").write_text("Jozef Fabik oral pole. " * 300, encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())


def test_default_search_fits_and_is_one_hit_per_file(tools):
    _long_docs(tools)
    result = search_documents(tools, "Jozef Fabik")
    assert len(result.hits) == 8 and len({h.file for h in result.hits}) == 8
    assert result.more_files >= 4
    out = render(tools, result)
    assert len(out) <= tools.kb.settings.tool_output_chars and '"truncated":true' not in out


def test_search_largest_request_still_fits(tools):
    _long_docs(tools)
    result = search_documents(tools, "Jozef Fabik", k=20, per_file=5)
    out = render(tools, result)
    assert len(out) <= tools.kb.settings.tool_output_chars and '"truncated":true' not in out
    assert len({h.file for h in result.hits}) == 16 and result.more_files == 0  # every candidate file kept
    assert 16 < len(result.hits) <= 100 and result.more_chunks > 0  # trimmed extra chunks are counted
    assert [h.source for h in result.hits] == result.sources


def test_search_groups_chunks_by_file_and_counts_the_rest(tools):
    _long_docs(tools)
    result = search_documents(tools, "Jozef Fabik", k=2, per_file=2)
    files = [h.file for h in result.hits]
    assert len(set(files)) == 2 and files == sorted(files, key=files.index)  # each file's chunks together
    assert result.more_files >= 10 and result.more_chunks >= result.more_files


def test_prefix_matches_declined_forms(tools):
    (tools.kb.root / "docs" / "list.txt").write_text("Pozdravujem pani Fabikovej.", encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), "Fabik")
    assert "docs/list.txt" in {h.file for h in result.hits}


def test_search_snippet_shows_a_declined_form_deep_in_a_long_chunk(tools):
    text = "Kronika obce. " * 200 + "Pozdravujem pani Fabikovej."  # one ~2,800-char chunk, match past 1,500
    (tools.kb.root / "docs" / "list.txt").write_text(text, encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), "Fabik")
    hit = next(h for h in result.hits if h.file == "docs/list.txt")
    assert "Fabikovej" in hit.text


def test_search_snippet_centres_on_the_name_not_on_short_filler_words(tools):
    # short words of the question ("sa") occur from the start; the name only past 1,500 characters
    text = "Tak sa v obci o tom hovorilo. " * 60 + "Narodil sa Štefan Fábik v roku 1890."
    (tools.kb.root / "docs" / "list.txt").write_text(text, encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), "kedy sa narodil Štefan Fábik")
    hit = next(h for h in result.hits if h.file == "docs/list.txt")
    assert "Fábik" in hit.text


def test_search_snippet_centres_on_the_name_not_on_early_question_words(tools):
    # long question words ("kedy", "narodil") occur early; the name only ~2,000 characters later
    text = ("Kedy sa narodil prvý učiteľ, nevieme. " + "Kronika obce. " * 140
            + "Štefan Fábik sa narodil v roku 1890.")
    (tools.kb.root / "docs" / "list.txt").write_text(text, encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), "kedy sa narodil Štefan Fábik")
    hit = next(h for h in result.hits if h.file == "docs/list.txt")
    assert "Fábik" in hit.text


@pytest.mark.parametrize("query", ['"Fabik','Fabik" OR', "NEAR(a b) * - ^ :", "Fabik's (1890)", "AND"])
def test_search_survives_fts_syntax_in_query(tools, query):
    (tools.kb.root / "docs" / "list.txt").write_text("Pozdravujem pani Fabikovej.", encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), query)
    if "Fabik" in query:
        assert "docs/list.txt" in {h.file for h in result.hits}


def test_read_document(tools):
    page = read_document(tools, "docs/matrika.pdf", page=2)
    assert "Vaclav Novak" in page.text
    assert not page.truncated
    assert page.sources[0].cite() == "[docs/matrika.pdf p.2]"
    assert "Josef Novák" in read_document(tools, "docs/kronika.txt").text


def test_read_document_truncates(tools):
    (tools.kb.root / "docs" / "dlouha.txt").write_text("Kronika obce. " * 40, encoding="utf-8")  # 560 > budget floor 200
    small = KnowledgeBase(tools.kb.root, replace(tools.kb.settings, tool_output_chars=650))
    doc = read_document(replace(tools, kb=small), "docs/dlouha.txt")
    assert doc.truncated
    assert doc.text.endswith("[truncated]")


@pytest.mark.parametrize("bad", ["../outside.txt", ".genea/config.toml", "tree.ged", "docs/missing.txt"])
def test_read_document_refuses(tools, bad):
    with pytest.raises(ToolError):
        read_document(tools, bad)


def test_read_document_missing_page(tools):
    with pytest.raises(ToolError, match="Page 9"):
        read_document(tools, "docs/matrika.pdf", page=9)


def test_find_images_without_indexed_images(tools):
    assert find_images(tools, text="svatba").images == []


@pytest.mark.exiftool
def test_find_images(tools_with_images):
    ctx = tools_with_images
    assert [i.file for i in find_images(ctx, person="tree.ged#@I16@").images] == ["photos/svatba_1932.jpg"]
    assert [i.file for i in find_images(ctx, year_from=1930, year_to=1933).images] == ["photos/svatba_1932.jpg"]
    assert [i.file for i in find_images(ctx, text="Anna").images] == ["photos/anna_1935.jpg"]
    assert [i.file for i in find_images(ctx, place="Brno").images] == ["photos/svatba_1932.jpg"]
    wedding = find_images(ctx, person="Jan Novák").images[0]
    assert wedding.persons_shown == ["Jan Novák", "Marie Dvořáková"]
    assert wedding.source.cite() == "[photos/svatba_1932.jpg]"
