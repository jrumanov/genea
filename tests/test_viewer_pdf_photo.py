import json
import sqlite3
import urllib.error
import urllib.request

import pytest
from fixtures.make_kb import damage_pdf

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.viewer import pages
from genea.viewer.server import start_viewer


def get(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8")


@pytest.fixture
def start(kb):
    handles = []

    def _start():
        index_kb(kb, FakeEmbedder())
        handles.append(start_viewer(kb, port=0))
        return handles[-1].url

    yield _start
    for handle in handles:
        handle.close()


def add_photo(kb, rel, title, keywords=()):
    (kb.root / rel).write_bytes(b"x")
    conn = sqlite3.connect(kb.index_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("INSERT INTO files (path, kind, sha256, mtime, indexed_at, status) "
                       "VALUES (?, 'image', 'h', 0, 'now', 'ok')", (rel,))
    conn.execute("INSERT INTO images (file_id, title, description, date_taken, keywords, persons_shown, "
                 "place, search_norm) VALUES (?, ?, 'Popis', '1932-06-12', ?, ?, 'Brno', '')",
                 (cur.lastrowid, title, json.dumps(list(keywords)), json.dumps(["Jan Novák"])))
    conn.commit()
    return conn


def test_pdf_page_param_goes_into_iframe_src(kb, start):
    url = start()
    status, body = get(url + "/v/docs/matrika.pdf?page=3")
    assert status == 200
    assert '<iframe src="/raw/docs/matrika.pdf#page=3"' in body


def test_pdf_chunk_is_marked_in_page_text(kb, start):
    url = start()
    status, body = get(url + "/v/docs/matrika.pdf?page=1&chunk=0")
    assert status == 200
    assert '<mark id="hit">' in body
    assert "Jan Novak" in body
    assert "1875 pokrten" not in body  # page 2 is not shown


def test_pdf_span_marks_sentence_inside_chunk(kb, start):
    url = start()
    conn = sqlite3.connect(kb.index_path)
    text = conn.execute("SELECT c.text FROM chunks c JOIN files f ON f.id = c.file_id "
                        "WHERE f.path = 'docs/matrika.pdf' AND c.chunk_no = 0").fetchone()[0]
    conn.close()
    status, body = get(f"{url}/v/docs/matrika.pdf?page=1&chunk=0&o=1&n=4")
    assert status == 200
    assert 'class="chunk"' in body and f'<mark id="hit">{text[1:5]}</mark>' in body
    status, body = get(f"{url}/v/docs/matrika.pdf?page=1&chunk=0&o=abc&n=4")
    assert status == 200 and body.count('id="hit"') == 1 and 'class="chunk"' not in body


def test_scanned_pdf_has_note_and_no_mark(kb, start):
    url = start()
    status, body = get(url + "/v/docs/sken.pdf")
    assert status == 200
    assert "<iframe" in body and "<mark" not in body
    assert "No text layer: this PDF has not been read as text yet" in body


def test_damaged_pdf_gives_error_page_and_server_survives(kb, start):
    url = start()
    damage_pdf(kb.root / "docs" / "matrika.pdf")
    assert get(url + "/v/docs/matrika.pdf")[0] == 200  # pypdf repairs this one
    (kb.root / "docs" / "matrika.pdf").write_bytes(b"not a pdf at all")
    status, body = get(url + "/v/docs/matrika.pdf")
    assert status == 200
    assert "Cannot read this file" in body
    assert get(url + "/v/docs/poznamky.md")[0] == 200


def test_photo_shows_metadata_escaped(kb, start):
    url = start()
    (kb.root / "photos").mkdir(exist_ok=True)
    conn = add_photo(kb, "photos/a.jpg", "Svatba <b>1932</b>")
    body = pages.render(kb, conn, "photos/a.jpg", {})
    assert '<img src="/raw/photos/a.jpg"' in body
    assert "Svatba &lt;b&gt;1932&lt;/b&gt;" in body and "<b>1932" not in body
    assert "Brno" in body and "Jan Novák" in body and "1932-06-12" in body
    assert get(url + "/v/photos/a.jpg")[0] == 200


def test_heic_photo_has_no_img_tag(kb, start):
    start()
    (kb.root / "photos").mkdir(exist_ok=True)
    conn = add_photo(kb, "photos/b.heic", "Sken")
    body = pages.render(kb, conn, "photos/b.heic", {})
    assert "<img" not in body
    assert "Sken" in body and "cannot show" in body


def test_unicode_digit_page_param_does_not_crash(kb, start):
    url = start()
    for path in ("/v/docs/matrika.pdf?page=%C2%B2", "/v/docs/poznamky.md?page=%E2%91%A0",
                 "/v/tree.ged?line=%C2%B2", "/v/docs/kronika.txt?t=%C2%B2"):
        assert get(url + path)[0] == 200, path


def test_pdf_page_without_text_has_note(kb, start):
    url = start()
    for page in (0, 9):
        status, body = get(url + f"/v/docs/matrika.pdf?page={page}")
        assert status == 200 and "has no extracted text on page" in body


def test_needs_ocr_allowed_only_for_docs(kb, start):
    from genea.viewer.server import lookup
    start()
    (kb.root / "photos").mkdir(exist_ok=True)
    conn = add_photo(kb, "photos/c.jpg", "C")
    conn.execute("UPDATE files SET status = 'needs_ocr' WHERE path = 'photos/c.jpg'")
    assert lookup(kb, conn, "photos/c.jpg") is None
    assert lookup(kb, conn, "docs/sken.pdf") is not None


def test_photo_alt_and_quoted_paths(kb, start):
    start()
    (kb.root / "photos").mkdir(exist_ok=True)
    conn = add_photo(kb, "photos/a b#1%.jpg", 'Dom "x"')
    body = pages.render(kb, conn, "photos/a b#1%.jpg", {})
    assert 'src="/raw/photos/a%20b%231%25.jpg"' in body and 'alt="Dom &quot;x&quot;"' in body
    from genea.viewer import pdfview
    (kb.root / "docs" / "r s#1.pdf").write_bytes((kb.root / "docs" / "matrika.pdf").read_bytes())
    page = pdfview.render_pdf(kb, conn, "docs/r s#1.pdf", {"page": "2"})
    assert '<iframe src="/raw/docs/r%20s%231.pdf#page=2"' in page
