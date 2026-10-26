import http.client
import sqlite3
import urllib.error
import urllib.request

import pytest

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.viewer.server import start_viewer


@pytest.fixture
def viewer(kb):
    index_kb(kb, FakeEmbedder())
    handle = start_viewer(kb, port=0)
    yield handle
    handle.close()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers


def raw_get(handle, path, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", int(handle.url.rsplit(":", 1)[1]))
    conn.putrequest("GET", path, skip_host=host is not None)
    if host is not None:
        conn.putheader("Host", host)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def test_binds_to_loopback_only(viewer):
    assert viewer.url.startswith("http://127.0.0.1:")
    assert viewer.server.server_address[0] == "127.0.0.1"


def test_port_in_use_falls_back(kb):
    index_kb(kb, FakeEmbedder())
    first = start_viewer(kb, port=0)
    port = int(first.url.rsplit(":", 1)[1])
    second = start_viewer(kb, port=port)
    try:
        assert second.url != first.url
        assert get(second.url + "/raw/docs/kronika.txt")[0] == 200
    finally:
        second.close()
        first.close()


def test_raw_serves_indexed_file(viewer, kb):
    status, body, headers = get(viewer.url + "/raw/docs/matrika.pdf")
    assert status == 200
    assert body == (kb.root / "docs" / "matrika.pdf").read_bytes()
    assert headers["Content-Type"] == "application/pdf"
    assert headers["Content-Disposition"] == "inline"


def test_view_page_for_known_file(viewer):
    status, body, _ = get(viewer.url + "/v/docs/kronika.txt")
    assert (status, body) == (200, b"viewer")


def test_unindexed_and_hidden_files_are_404(viewer, kb):
    (kb.root / "loose.txt").write_text("secret", encoding="utf-8")
    (kb.root.parent / "outside.txt").write_text("secret", encoding="utf-8")
    for path in ("/raw/loose.txt", "/raw/.genea/index.db", "/raw/../outside.txt",
                 "/raw/%2e%2e/outside.txt", "/raw/docs/%2e%2e/%2e%2e/outside.txt", "/v/loose.txt"):
        status, body = raw_get(viewer, path)
        assert status in (403, 404), path
        assert b"secret" not in body


def test_host_header_must_be_loopback(viewer):
    assert raw_get(viewer, "/raw/docs/kronika.txt", host="evil.example")[0] == 403
    assert raw_get(viewer, "/raw/docs/kronika.txt", host="evil.example:80")[0] == 403
    port = viewer.url.rsplit(":", 1)[1]
    assert raw_get(viewer, "/raw/docs/kronika.txt", host=f"localhost:{port}")[0] == 200


def test_post_is_rejected(viewer):
    req = urllib.request.Request(viewer.url + "/raw/docs/kronika.txt", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 405


def _add_image(kb, name, keywords):
    (kb.root / "photos").mkdir(exist_ok=True)
    (kb.root / "photos" / name).write_bytes(b"\xff\xd8jpeg")
    conn = sqlite3.connect(kb.index_path)
    with conn:
        cur = conn.execute("INSERT INTO files (path, kind, sha256, mtime, indexed_at, status) "
                           "VALUES (?, 'image', 'x', 0, 'now', 'ok')", (f"photos/{name}",))
        conn.execute("INSERT INTO images (file_id, keywords, persons_shown, search_norm) "
                     "VALUES (?, ?, '[]', '')", (cur.lastrowid, keywords))
    conn.close()


def test_excluded_image_is_not_served(kb):
    index_kb(kb, FakeEmbedder())
    _add_image(kb, "pass.jpg", '["family", "Id Card"]')
    _add_image(kb, "dum.jpg", '["house"]')
    handle = start_viewer(kb, port=0)
    try:
        assert get(handle.url + "/raw/photos/pass.jpg")[0] == 404
        assert get(handle.url + "/v/photos/pass.jpg")[0] == 404
        assert get(handle.url + "/raw/photos/dum.jpg")[0] == 200
        assert get(handle.url + "/v/photos/dum.jpg")[0] == 200
    finally:
        handle.close()


def test_no_index_gives_notice(kb):
    handle = start_viewer(kb, port=0)
    try:
        status, body, _ = get(handle.url + "/v/docs/kronika.txt")
        assert status == 503
        assert b"genea index" in body
    finally:
        handle.close()


def test_server_survives_a_bad_request(viewer, monkeypatch):
    from genea.viewer import pages

    def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(pages, "render", boom)
    status, body, headers = get(viewer.url + "/v/docs/kronika.txt")
    assert status == 500 and headers["Content-Type"].startswith("text/plain")
    monkeypatch.undo()
    assert get(viewer.url + "/raw/docs/kronika.txt")[0] == 200
