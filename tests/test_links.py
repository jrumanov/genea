import json
import random

from genea.index.db import connect
from genea.index.indexer import name_forms, rebuild_links
from genea.text import normalize


def _reference_links(conn):
    """The original quadratic algorithm: every person form is searched in every text."""
    people = [(r["ged_file"], r["xref"], name_forms(r["given"], r["surname"], json.loads(r["name_variants"])))
              for r in conn.execute("SELECT ged_file, xref, given, surname, name_variants FROM persons")]
    sources = [("chunk", r["id"], normalize(r["text"])) for r in conn.execute("SELECT id, text FROM chunks")]
    sources += [("image", r["id"], r["search_norm"]) for r in conn.execute("SELECT id, search_norm FROM images")]
    rows = []
    for kind, source_id, text in sources:
        padded = f" {text} "
        for ged, xref, forms in people:
            best = max((conf for form, conf in forms if f" {form} " in padded), default=0.0)
            if best:
                rows.append((kind, source_id, ged, xref, best))
    return rows


def _links(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT source_kind, source_id, person_ged_file, person_xref, confidence FROM links ORDER BY id")]


def test_rebuild_links_matches_the_exhaustive_search(tmp_path):
    rng = random.Random(7)
    conn = connect(tmp_path / "i.db", "fake", 4)
    conn.execute("INSERT INTO files (id, path, kind, sha256, mtime, indexed_at, status) "
                 "VALUES (1, 'a.ged', 'ged', 'x', 0, 'n', 'ok')")
    givens, surnames = ["Jan Josef", "Marie", "Anna Marie", "Žofie", "Karel"], ["Novák", "Nowak", "Černý", "Dvořák"]
    for i in range(40):
        variants = [f"{rng.choice(givens)} {rng.choice(surnames)}"] if i % 3 == 0 else []
        conn.execute("INSERT INTO persons (file_id, ged_file, xref, given, surname, name_norm, names_norm, "
                     "name_variants, line_no) VALUES (1, 'a.ged', ?, ?, ?, '', '', ?, 1)",
                     (f"@I{i}@", rng.choice(givens), rng.choice(surnames), json.dumps(variants)))
    words = ["Jan", "Josef", "Marie", "Anna", "Zofie", "Karel", "Novák", "Nowak", "Cerny", "Dvořák", "J.", "A.",
             "se", "narodil", "v", "Brně", "Johann", "Novak"]
    for i in range(30):
        conn.execute("INSERT INTO files (id, path, kind, sha256, mtime, indexed_at, status) "
                     "VALUES (?, ?, 'doc', 'x', 0, 'n', 'ok')", (i + 2, f"d{i}.txt"))
        text = " ".join(rng.choice(words) for _ in range(rng.randint(0, 40)))
        conn.execute("INSERT INTO chunks (file_id, chunk_no, text) VALUES (?, 0, ?)", (i + 2, text))
        conn.execute("INSERT INTO images (file_id, keywords, persons_shown, search_norm) VALUES (?, '[]', '[]', ?)",
                     (i + 2, normalize(text)))
    expected = _reference_links(conn)
    assert expected  # the random data must produce matches, or the comparison proves nothing
    rebuild_links(conn)
    assert _links(conn) == expected
    rebuild_links(conn)  # replaces, never appends
    assert _links(conn) == expected
    conn.close()
