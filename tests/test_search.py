from dataclasses import replace

import pytest

from genea.index.embed import EmbeddingError
from genea.kb import KnowledgeBase
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


def test_read_document(tools):
    page = read_document(tools, "docs/matrika.pdf", page=2)
    assert "Vaclav Novak" in page.text
    assert not page.truncated
    assert page.sources[0].cite() == "[docs/matrika.pdf p.2]"
    assert "Josef Novák" in read_document(tools, "docs/kronika.txt").text


def test_read_document_truncates(tools):
    small = KnowledgeBase(tools.kb.root, replace(tools.kb.settings, tool_output_chars=20))
    doc = read_document(replace(tools, kb=small), "docs/kronika.txt")
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
