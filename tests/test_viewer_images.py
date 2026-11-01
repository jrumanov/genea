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


@pytest.mark.parametrize("exc", [__import__("struct").error("x"), SyntaxError("x"), KeyError("x"),
                                 ZeroDivisionError(), NotImplementedError(), OverflowError(), TypeError()])
def test_tiff_decoder_errors_give_404(kb, start, monkeypatch, exc):
    url = start()
    make_tiff(kb)

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(Image, "open", boom)
    assert get(url + "/raw/photos/a.tif")[0] == 404


def test_zero_byte_tiff_gives_404(kb, start):
    url = start()
    make_tiff(kb)
    (kb.root / "photos" / "empty.tif").write_bytes(b"")
    add_photo(kb, "photos/empty.tif", "Empty")
    assert get(url + "/raw/photos/empty.tif")[0] == 404


def test_tiff_replace_failure_gives_404_and_no_tmp(kb, start, monkeypatch):
    import os
    url = start()
    make_tiff(kb)

    def denied(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", denied)
    assert get(url + "/raw/photos/a.tif")[0] == 404
    assert not list((kb.index_path.parent / "viewer-cache").glob("*.tmp"))


def add_person(conn, xref, names_norm):
    file_id = conn.execute("SELECT id FROM files LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO persons (file_id, ged_file, xref, name_norm, names_norm, name_variants, line_no) "
                 "VALUES (?, 'tree.ged', ?, ?, ?, '', 1)", (file_id, xref, names_norm.split("|")[0].strip(), names_norm))
    conn.commit()


def add_regions(conn, rel, regions):
    image_id = conn.execute("SELECT i.id FROM images i JOIN files f ON f.id = i.file_id WHERE f.path = ?",
                            (rel,)).fetchone()[0]
    conn.executemany("INSERT INTO image_regions (image_id, name, x, y, w, h) VALUES (?, ?, ?, ?, ?, ?)",
                     [(image_id, *r) for r in regions])
    conn.commit()


def photo_with_regions(kb, regions, rel="photos/f.jpg"):
    (kb.root / "photos").mkdir(exist_ok=True)
    Image.new("RGB", (10, 10)).save(kb.root / rel)
    conn = add_photo(kb, rel, "Foto")
    add_regions(conn, rel, regions)
    add_person(conn, "@T1@", "jan novak")
    add_person(conn, "@T2@", "marie novakova")
    add_person(conn, "@T3@", "jan novak")
    return conn


def test_matching_region_is_boxed(kb, start):
    url = start()
    photo_with_regions(kb, [("Novak Jan", 0.1, 0.2, 0.3, 0.4), ("Novakova Marie", 0.5, 0.25, 0.125, 0.5)])
    _, _, body = get(url + "/v/photos/f.jpg?p=tree.ged%23%40T1%40")
    text = body.decode()
    assert text.count('class="box"') == 1
    assert 'class="box" style="left:10.0%;top:20.0%;width:30.0%;height:40.0%" title="Novak Jan"' in text
    assert 'class="box faint"' in text and 'title="Novakova Marie"' in text
    assert '<div class="frame"><img' in text


def test_no_match_shows_note(kb, start):
    url = start()
    photo_with_regions(kb, [("Novakova Marie", 0.5, 0.25, 0.125, 0.5)])
    _, _, body = get(url + "/v/photos/f.jpg?p=tree.ged%23%40T1%40")
    text = body.decode()
    assert "No face position stored for this person." in text
    assert 'class="box"' not in text


def test_unknown_p_shows_no_box_and_no_note_no_crash(kb, start):
    url = start()
    photo_with_regions(kb, [("Novak Jan", 0.1, 0.2, 0.3, 0.4)])
    for p in ("tree.ged%23%40T99%40", "garbage", "%23", "tree.ged%23", ""):
        status, _, body = get(url + "/v/photos/f.jpg?p=" + p)
        text = body.decode()
        assert status == 200
        assert "No face position stored" not in text
        assert 'class="box"' not in text


def test_two_matching_people_marks_both(kb, start):
    url = start()
    photo_with_regions(kb, [("Novak Jan", 0.1, 0.2, 0.3, 0.4), ("Novakova Marie", 0.5, 0.25, 0.125, 0.5),
                            ("Other Person", 0.7, 0.7, 0.1, 0.1)])
    _, _, body = get(url + "/v/photos/f.jpg?p=tree.ged%23%40T1%40")
    assert body.decode().count('class="box"') == 1
    # a second person form matching Marie: make I3 also match her
    conn = sqlite3.connect(kb.index_path)
    conn.execute("UPDATE persons SET names_norm = 'jan novak | marie novakova' WHERE xref = '@T3@'")
    conn.commit()
    _, _, body = get(url + "/v/photos/f.jpg?p=tree.ged%23%40T3%40")
    text = body.decode()
    assert text.count('class="box"') == 2
    assert text.count('class="box faint"') == 1


def test_regions_only_on_first_tiff_page(kb, start):
    url = start()
    make_tiff(kb)
    conn = sqlite3.connect(kb.index_path)
    add_regions(conn, "photos/a.tif", [("Novak Jan", 0.1, 0.2, 0.3, 0.4)])
    add_person(conn, "@T1@", "jan novak")
    p = "?p=tree.ged%23%40T1%40"
    assert 'class="box"' in get(url + "/v/photos/a.tif" + p)[2].decode()
    later = get(url + "/v/photos/a.tif" + p + "&page=1")[2].decode()
    assert 'class="box' not in later
    assert "No face position stored" not in later


def test_names_html_escaped(kb, start):
    url = start()
    photo_with_regions(kb, [("<script>alert(1)</script>", 0.1, 0.2, 0.3, 0.4)])
    _, _, body = get(url + "/v/photos/f.jpg?p=tree.ged%23%40T1%40")
    text = body.decode()
    assert "<script>alert" not in text
    assert "&lt;script&gt;" in text


def pair(kb, stem="docs/scan_a1", ext=".jpg", text="Narodil sa Jan Novak v Brne.", keywords=()):
    (kb.root / stem).parent.mkdir(exist_ok=True)
    (kb.root / (stem + ".md")).write_text(text, encoding="utf-8")
    return stem


def add_image_file(kb, rel, keywords=()):
    Image.new("RGB", (10, 10)).save(kb.root / rel)
    return add_photo(kb, rel, "Sken", keywords)


def test_md_page_shows_image_pane(kb, start):
    pair(kb)
    url = start()
    add_image_file(kb, "docs/scan_a1.jpg")
    status, _, body = get(url + "/v/docs/scan_a1.md")
    text = body.decode()
    assert status == 200
    assert '<img src="/raw/docs/scan_a1.jpg"' in text
    assert "Narodil sa Jan Novak" in text
    assert text.index('class="two"') < text.index("<img") < text.index("Narodil")


def test_image_page_shows_md_pane(kb, start):
    pair(kb)
    url = start()
    add_image_file(kb, "docs/scan_a1.jpg")
    _, _, body = get(url + "/v/docs/scan_a1.jpg")
    text = body.decode()
    assert 'class="two"' in text and "Narodil sa Jan Novak" in text and "<dt>Title</dt>" in text


def test_md_without_image_unchanged(kb, start):
    pair(kb)
    url = start()
    _, _, body = get(url + "/v/docs/scan_a1.md")
    text = body.decode()
    assert 'class="two"' not in text and "<img" not in text and "max-width: none" not in text


def test_image_without_md_unchanged(kb, start):
    url = start()
    add_image_file(kb, "docs/lone.jpg")
    _, _, body = get(url + "/v/docs/lone.jpg")
    assert 'class="two"' not in body.decode()


def test_excluded_image_not_shown_from_md(kb, start):
    pair(kb)
    url = start()
    add_image_file(kb, "docs/scan_a1.jpg", keywords=["resident permit"])
    status, _, body = get(url + "/v/docs/scan_a1.md")
    text = body.decode()
    assert status == 200 and "Narodil sa Jan Novak" in text
    assert "<img" not in text and "/raw/" not in text.replace("/raw/docs/scan_a1.md", "")
    assert "scan_a1.jpg" not in text
    assert get(url + "/v/docs/scan_a1.jpg")[0] == 404


def test_sides_links_to_other_side(kb, start):
    pair(kb)
    pair(kb, "docs/scan_a2", text="Druha strana.")
    url = start()
    add_image_file(kb, "docs/scan_a1.jpg")
    add_image_file(kb, "docs/scan_a2.jpg")
    _, _, body = get(url + "/v/docs/scan_a1.md")
    text = body.decode()
    assert 'href="/v/docs/scan_a2.md"' in text and 'href="/v/docs/scan_a2.jpg"' in text
    assert 'href="/v/docs/scan_a1.md"' not in text


def test_chunk_highlight_still_applies_in_md_pane(kb, start):
    pair(kb)
    url = start()
    add_image_file(kb, "docs/scan_a1.jpg")
    _, _, body = get(url + "/v/docs/scan_a1.md?chunk=0&o=0&n=7")
    text = body.decode()
    assert '<mark id="hit">Narodil</mark>' in text
    assert text.index('class="two"') < text.index("<img") < text.index('<mark id="hit">')


def test_uppercase_extension_scan_shows_beside_lowercase_md(kb, start):
    pair(kb, "docs/scan")
    url = start()
    add_image_file(kb, "docs/scan.JPG")
    status, _, body = get(url + "/v/docs/scan.md")
    text = body.decode()
    assert status == 200 and '<img src="/raw/docs/scan.JPG"' in text and "Narodil sa Jan Novak" in text


def test_uppercase_extension_excluded_scan_not_shown_from_md(kb, start):
    pair(kb, "docs/scan")
    url = start()
    add_image_file(kb, "docs/scan.JPG", keywords=["resident permit"])
    text = get(url + "/v/docs/scan.md")[2].decode()
    assert "<img" not in text and "scan.JPG" not in text


def test_register_number_is_not_a_side(kb, start):
    from genea.viewer.sidebyside import sides
    pair(kb, "docs/reg_d046")
    pair(kb, "docs/reg_d047")
    pair(kb, "docs/scan_a1")
    pair(kb, "docs/scan_a2")
    start()
    conn = sqlite3.connect(kb.index_path)
    conn.row_factory = sqlite3.Row
    assert sides(kb, conn, "docs/reg_d046.md") == []
    assert sides(kb, conn, "docs/scan_a1.md") == ["docs/scan_a2.md"]


def test_tiff_temp_cleanup_failure_is_not_a_500(kb, start, monkeypatch):
    import os
    from pathlib import Path
    url = start()
    make_tiff(kb)
    real_unlink = Path.unlink

    def denied_unlink(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise PermissionError("locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied_unlink)
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    assert get(url + "/raw/photos/a.tif")[0] == 404
    monkeypatch.undo()
    assert get(url + "/raw/photos/a.tif")[0] == 200


def test_tiff_scan_with_md(kb, start):
    pair(kb, "docs/a")
    url = start()
    make_tiff(kb, "docs/a.tif")
    _, _, body = get(url + "/v/docs/a.md")
    assert '<img src="/raw/docs/a.tif?page=0"' in body.decode()
