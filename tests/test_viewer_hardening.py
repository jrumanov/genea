import json
import sqlite3
import urllib.error
import urllib.request

import pytest

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase
from genea.viewer import links, pages
from genea.viewer.policy import is_excluded
from genea.viewer.server import start_viewer


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers


@pytest.fixture
def viewer(kb):
    index_kb(kb, FakeEmbedder())
    handle = start_viewer(kb, port=0)
    yield handle
    handle.close()


@pytest.mark.parametrize("path", ["/raw///evil.example/share/x.jpg", "/raw/%5C%5Cevil.example%5Cshare%5Cx.jpg",
                                  "/raw/C:/Windows/win.ini", "/raw/C%3A%5CWindows%5Cwin.ini",
                                  "/v//evil.example/share/x", "/raw/docs/../docs/kronika.txt"])
def test_hostile_paths_are_404_and_never_resolved(viewer, monkeypatch, path):
    seen = []
    real = KnowledgeBase.resolve_inside

    def spy(self, rel):
        seen.append(str(rel))
        return real(self, rel)

    monkeypatch.setattr(KnowledgeBase, "resolve_inside", spy)
    assert get(viewer.url + path)[0] == 404
    assert not any("evil" in s or "Windows" in s or ".." in s for s in seen)


@pytest.mark.parametrize("tag", ["_documents|travel card", "Id Card (front)", "Driving Licence",
                                 "driving-license", "Resident_Permit"])
def test_excluded_tags_match_by_containment(tag):
    assert is_excluded(["family", tag])


@pytest.mark.parametrize("tag", ["postcard", "Pas", "house"])
def test_harmless_tags_stay_allowed(tag):
    assert not is_excluded([tag])


def test_int_param_ignores_huge_digit_strings():
    assert pages.int_param({"page": "9" * 5000}, "page") is None
    assert pages.int_param({"page": "123456789"}, "page") == 123456789


def test_huge_page_param_is_not_a_500(viewer):
    assert get(viewer.url + "/v/docs/kronika.txt?page=" + "9" * 5000)[0] == 200


def test_sec_fetch_site_rules(viewer):
    raw, view = viewer.url + "/raw/docs/kronika.txt", viewer.url + "/v/docs/kronika.txt"
    assert get(raw)[0] == 200
    assert get(raw, {"Sec-Fetch-Site": "none"})[0] == 200
    assert get(raw, {"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert get(raw, {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert get(view, {"Sec-Fetch-Site": "cross-site"})[0] == 403
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert get(view, nav)[0] == 200
    assert get(raw, nav)[0] == 403
    assert get(view, {**nav, "Sec-Fetch-Site": "same-site"})[0] == 200
    assert get(view, {**nav, "Sec-Fetch-Dest": "iframe"})[0] == 403


def test_dna_sources_are_not_linked(kb, no_base_url):
    from genea.dna.tools import _source
    links.set_base_url("http://127.0.0.1:1")
    assert "link" not in _source("x", "meta.json").model_dump()


def test_shrink_keeps_links_whole():
    from genea.tool_runner import shrink
    link = "http://127.0.0.1:8765/v/" + "a" * 300 + ".md"
    data = {"text": "x" * 5000, "sources": [{"file": "a", "link": link}]}
    assert json.loads(shrink(data, 700))["sources"][0]["link"] == link


def test_render_sqlite_error_gives_503(viewer, monkeypatch):
    def boom(*args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pages, "render", boom)
    status, body, _ = get(viewer.url + "/v/docs/kronika.txt")
    assert status == 503 and b"genea index" in body


def test_awkward_filename_round_trips(kb, no_base_url):
    (kb.root / "docs" / "Šimon #2 100%+.md").write_text("# Šimon\n\nunikátnyretazec\n", encoding="utf-8")
    index_kb(kb, FakeEmbedder())
    handle = start_viewer(kb, port=0)
    try:
        links.set_base_url(handle.url)
        view = links.build_link("docs/Šimon #2 100%+.md")
        status, body, _ = get(view)
        assert status == 200 and "unikátnyretazec".encode() in body
        raw = view.replace("/v/", "/raw/")
        status, body, headers = get(raw)
        assert status == 200 and "unikátnyretazec".encode() in body
        assert "charset=utf-8" in headers["Content-Type"]
    finally:
        handle.close()


def test_raw_text_types_carry_charset(viewer):
    assert get(viewer.url + "/raw/docs/kronika.txt")[2]["Content-Type"] == "text/plain; charset=utf-8"


@pytest.mark.parametrize("port", [70000, -1, 10**12])
def test_invalid_port_falls_back(kb, port):
    handle = start_viewer(kb, port=port)
    try:
        assert handle.url.startswith("http://127.0.0.1:")
    finally:
        handle.close()


def test_transcript_chunk_without_times_does_not_crash(kb):
    from fixtures.transcripts import NAME, write_transcript
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    conn = sqlite3.connect(kb.index_path)
    with conn:
        conn.execute("UPDATE chunks SET start_s = NULL, end_s = NULL WHERE file_id IN "
                     "(SELECT id FROM files WHERE path LIKE ?)", (f"%{NAME}",))
        no = conn.execute("SELECT chunk_no FROM chunks WHERE start_s IS NULL LIMIT 1").fetchone()
    conn.close()
    assert no is not None
    handle = start_viewer(kb, port=0)
    try:
        c = sqlite3.connect(kb.index_path)
        rel = c.execute("SELECT path FROM files WHERE path LIKE ?", (f"%{NAME}",)).fetchone()[0]
        c.close()
        from urllib.parse import quote
        assert get(f"{handle.url}/v/{quote(rel)}?chunk={no[0]}")[0] == 200
    finally:
        handle.close()
