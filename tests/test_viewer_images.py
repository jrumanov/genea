import json
import sqlite3
import urllib.error
import urllib.request

import pytest
from PIL import Image

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.viewer.server import start_viewer


def get(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


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
    conn = sqlite3.connect(kb.index_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("INSERT INTO files (path, kind, sha256, mtime, indexed_at, status) "
                       "VALUES (?, 'image', 'h', 0, 'now', 'ok')", (rel,))
    conn.execute("INSERT INTO images (file_id, title, description, date_taken, keywords, persons_shown, "
                 "place, search_norm) VALUES (?, ?, 'Popis', '1932-06-12', ?, ?, 'Brno', '')",
                 (cur.lastrowid, title, json.dumps(list(keywords)), json.dumps(["Jan Novák"])))
    conn.commit()
    return conn


def make_tiff(kb, rel="photos/a.tif", sizes=((40, 20), (30, 50)), orientation=None):
    (kb.root / "photos").mkdir(exist_ok=True)
    images = [Image.new("RGB", size, (200, 10 * i, 5)) for i, size in enumerate(sizes)]
    extra = {}
    if orientation:
        exif = Image.Exif()
        exif[274] = orientation
        extra["exif"] = exif
    images[0].save(kb.root / rel, save_all=True, append_images=images[1:], **extra)
    add_photo(kb, rel, "Sken")


def size_of(body):
    import io
    return Image.open(io.BytesIO(body)).size


def test_tiff_raw_is_served_as_jpeg(kb, start):
    url = start()
    make_tiff(kb)
    status, headers, body = get(url + "/raw/photos/a.tif")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert body.startswith(b"\xff\xd8")


def test_tiff_page_param_picks_page(kb, start):
    url = start()
    make_tiff(kb)
    first = size_of(get(url + "/raw/photos/a.tif")[2])
    second = size_of(get(url + "/raw/photos/a.tif?page=1")[2])
    assert first == (40, 20) and second == (30, 50)


def test_tiff_conversion_is_cached(kb, start, monkeypatch):
    url = start()
    make_tiff(kb)
    calls = []
    real_open = Image.open

    def counting_open(*args, **kwargs):
        calls.append(args)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", counting_open)
    assert get(url + "/raw/photos/a.tif")[0] == 200
    opened = len(calls)
    assert opened >= 1
    assert get(url + "/raw/photos/a.tif")[0] == 200
    assert len(calls) == opened


def test_tiff_applies_exif_orientation(kb, start):
    url = start()
    make_tiff(kb, sizes=((40, 20),), orientation=6)
    assert size_of(get(url + "/raw/photos/a.tif")[2]) == (20, 40)


def test_tiff_bad_page_and_corrupt_file(kb, start):
    url = start()
    make_tiff(kb)
    assert get(url + "/raw/photos/a.tif?page=9")[0] == 404
    assert get(url + "/raw/photos/a.tif?page=x")[0] < 500
    (kb.root / "photos" / "bad.tif").write_bytes(b"x")
    add_photo(kb, "photos/bad.tif", "Bad")
    assert get(url + "/raw/photos/bad.tif")[0] == 404


def test_tiff_photo_page_shows_img_tag(kb, start):
    url = start()
    make_tiff(kb)
    status, _, body = get(url + "/v/photos/a.tif")
    assert status == 200
    assert b'<img src="/raw/photos/a.tif' in body
