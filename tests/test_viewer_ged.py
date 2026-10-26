import re
import urllib.error
import urllib.request

import pytest

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.ingest.gedcom.parser import parse_gedcom
from genea.viewer.gedview import record_lines
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


def birt_line(kb, xref="@I12@"):
    doc = parse_gedcom(kb.root / "tree.ged")
    record = next(r for r in doc.records if r.xref == xref)
    return doc, record, record.first("BIRT").line_no


def test_record_lines_cover_the_record(kb):
    doc, record, line_no = birt_line(kb)
    rows = record_lines(doc, line_no)
    assert rows[0] == (record.line_no, "0 @I12@ INDI")
    assert (line_no, "1 BIRT") in rows
    nxt = next(r for r in doc.records if r.line_no > record.line_no)
    assert rows[-1][0] == nxt.line_no - 1


def test_cited_line_is_highlighted(kb, start):
    _, _, line_no = birt_line(kb)
    status, body = get(start() + f"/v/tree.ged?line={line_no}")
    assert status == 200
    assert body.count('<mark id="hit">') == 1
    assert re.search(r'<mark id="hit">[^<]*BIRT[^<]*</mark>', body)
    assert "@I12@" in body and "changed since" not in body


def test_line_outside_file_shows_file_top_with_note(kb, start):
    status, body = get(start() + "/v/tree.ged?line=99999")
    assert status == 200
    assert "<mark" not in body
    assert "0 HEAD" in body
    assert "changed since" in body


def test_ansel_diacritics_render(kb, start):
    (kb.root / "stary.ged").write_bytes(
        b"0 HEAD\n1 CHAR ANSEL\n0 @I1@ INDI\n1 NAME Jan /Nov\xe2ak/\n0 TRLR\n")
    status, body = get(start() + "/v/stary.ged?line=4")
    assert status == 200
    assert "Jan /Novák/" in body
