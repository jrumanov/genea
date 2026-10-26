import re
import sqlite3
import urllib.error
import urllib.request

import pytest
from fixtures.transcripts import NAME, write_transcript

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.viewer.server import start_viewer
from genea.viewer.textview import mark_chunk


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


def chunk_row(kb, rel, number):
    conn = sqlite3.connect(kb.index_path)
    row = conn.execute("SELECT c.text, c.start_s, c.end_s FROM chunks c JOIN files f ON f.id = c.file_id "
                       "WHERE f.path = ? AND c.chunk_no = ?", (rel, number)).fetchone()
    conn.close()
    return row


def marked_texts(body):
    return re.findall(r"<mark[^>]*>(.*?)</mark>", body, re.S)


def test_markdown_chunk_is_marked(kb, start):
    (kb.root / "docs" / "poznamky.md").write_text("# Poznámky <b>\n\nTerezie a <i>Jan</i> & spol.\n", encoding="utf-8")
    url = start()
    status, body = get(url + "/v/docs/poznamky.md?chunk=0")
    assert status == 200
    assert '<mark id="hit">' in body
    assert "&lt;b&gt;" in body and "&lt;i&gt;Jan&lt;/i&gt; &amp; spol." in body
    assert "<b>" not in body
    assert "changed since" not in body


def test_changed_file_opens_unmarked_with_note(kb, start):
    url = start()
    (kb.root / "docs" / "poznamky.md").write_text("Úplne iný text.\n", encoding="utf-8")
    status, body = get(url + "/v/docs/poznamky.md?chunk=0")
    assert status == 200
    assert "<mark" not in body
    assert "The file changed since it was indexed; showing it without highlighting" in body
    assert "Úplne iný text." in body


def test_missing_chunk_gets_note(kb, start):
    url = start()
    status, body = get(url + "/v/docs/poznamky.md?chunk=99")
    assert status == 200 and "<mark" not in body and "changed since" in body


def test_mark_chunk_helper_is_reusable():
    assert mark_chunk("  a <b> c ", "<b>") == 'a <mark id="hit">&lt;b&gt;</mark> c'
    assert mark_chunk("abc", "zzz") is None


def test_transcript_marks_chunk_range(kb, start):
    write_transcript(kb.root)
    url = start()
    rel = f"docs/{NAME}"
    _text, start_s, end_s = chunk_row(kb, rel, 1)
    status, body = get(f"{url}/v/{rel}?chunk=1")
    assert status == 200
    expected = [f"{i * 15}" for i in range(60) if i * 15 >= start_s and i * 15 + 14 <= end_s]
    assert len(marked_texts(body)) == len(expected) > 1
    assert body.count('id="hit"') == 1
    assert "[0:00]" in body  # unmarked segments are shown too
    assert "[0:00]" not in "".join(marked_texts(body)) or start_s == 0


def test_transcript_t_marks_segment_containing_time(kb, start):
    write_transcript(kb.root)
    url = start()
    status, body = get(f"{url}/v/docs/{NAME}?t=14")
    assert status == 200
    marks = marked_texts(body)
    assert len(marks) == 1 and "[0:00]" in marks[0]
    _, body = get(f"{url}/v/docs/{NAME}?t=840")
    marks = marked_texts(body)
    assert len(marks) == 1 and "Prískupíc" in marks[0] and "[14:00]" in marks[0]


def test_transcript_has_no_audio_element(kb, start):
    write_transcript(kb.root)
    url = start()
    _, body = get(f"{url}/v/docs/{NAME}?chunk=0")
    assert "<audio" not in body and "<video" not in body


def test_unknown_file_is_404_page(kb, start):
    url = start()
    status, body = get(url + "/v/docs/neexistuje.md?chunk=0")
    assert status == 404 and "not available" in body


def test_page_param_on_text_doc_without_pages_is_ignored(kb, start):
    url = start()
    status, body = get(url + "/v/docs/poznamky.md?page=7&chunk=0")
    assert status == 200
    assert "Terezie Dvořáková" in body and '<mark id="hit">' in body
