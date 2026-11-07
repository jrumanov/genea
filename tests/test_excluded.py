import json

import pytest

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.tools.base import ToolError
from genea.tools.excluded import EXCLUDED_TAGS, excluded_paths
from genea.tools.mentions import documents_about
from genea.tools.search import find_images, read_document, search_documents


def _tag(tools, image: str, keywords: list[str]) -> None:
    """Index a `docs/zapis.md` and register `image` beside it as an indexed image with these keywords."""
    (tools.kb.root / "docs" / "zapis.md").write_text("Jan Novák kúpil dom v Pezinku.", encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    cur = tools.conn.execute(
        "INSERT INTO files (path, kind, sha256, mtime, indexed_at, status) VALUES (?, 'image', 'x', 0, 'x', 'ok')",
        (image,))
    tools.conn.execute(
        "INSERT INTO images (file_id, keywords, persons_shown, search_norm) VALUES (?, ?, '[]', 'zapis jan')",
        (cur.lastrowid, json.dumps(keywords)))
    tools.conn.commit()


@pytest.fixture
def tagged(tools):
    _tag(tools, "docs/zapis_a1.jpg", ["ID Card"])
    return tools


def test_image_with_tag_excluded(tagged):
    assert "docs/zapis_a1.jpg" in excluded_paths(tagged.conn)


def test_md_next_to_tagged_image_excluded(tagged):
    assert "docs/zapis.md" in excluded_paths(tagged.conn)


def test_untagged_kept(tools):
    _tag(tools, "docs/zapis_a1.jpg", ["rodina"])
    assert excluded_paths(tools.conn) == set()


def test_all_five_tags():
    assert EXCLUDED_TAGS == {"travel card", "resident permit", "id card", "driving license",
                             "health insurance card"}


def test_documents_about_skips_excluded(tagged):
    assert "docs/zapis.md" not in [f.file for f in documents_about(tagged, "tree.ged#@I3@").files]


def test_documents_about_keeps_it_when_untagged(tools):
    _tag(tools, "docs/zapis_a1.jpg", ["rodina"])
    assert "docs/zapis.md" in [f.file for f in documents_about(tools, "tree.ged#@I3@").files]


def test_search_skips_excluded(tagged):
    assert "docs/zapis.md" not in [h.file for h in search_documents(tagged, "Pezinku").hits]


def test_find_images_skips_excluded(tagged):
    assert "docs/zapis_a1.jpg" not in [i.file for i in find_images(tagged, text="zapis").images]


def test_read_document_refuses_excluded(tagged):
    with pytest.raises(ToolError, match="excluded"):
        read_document(tagged, "docs/zapis.md")
