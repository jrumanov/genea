"""Face-region name matching against tree people."""

from __future__ import annotations

import sqlite3

import pytest

from genea.viewer.regions import image_regions_for, parse_person_param, person_forms, region_matches

FORMS = ["jakub rumanovsky"]


def test_diacritics_and_order_ignored():
    assert region_matches("Rumanovsky Jakub", FORMS, None)


def test_trailing_year_must_equal_birth_year():
    assert region_matches("Rumanovsky Jakub 1985", FORMS, 1985)
    assert not region_matches("Rumanovsky Jakub 1985", FORMS, 1986)
    assert region_matches("Rumanovsky Jakub 1985", FORMS, None)


def test_year_only_stripped_when_four_digits_at_end():
    assert not region_matches("Rumanovsky Jakub 85", FORMS, 1985)
    assert not region_matches("1985 Rumanovsky Jakub", FORMS, 1985)


def test_different_words_do_not_match():
    assert not region_matches("Rumanovsky Jan", FORMS, None)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE persons (ged_file TEXT, xref TEXT, names_norm TEXT);
    CREATE TABLE events (ged_file TEXT, owner_xref TEXT, type TEXT, date_from TEXT, line_no INT);
    CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
    CREATE TABLE images (id INTEGER PRIMARY KEY, file_id INT);
    CREATE TABLE image_regions (id INTEGER PRIMARY KEY, image_id INT, name TEXT, x REAL, y REAL, w REAL, h REAL);
    INSERT INTO persons VALUES ('tree.ged', '@I1@', 'jakub rumanovsky | jacob rumanovsky'),
                               ('tree.ged', '@I2@', 'jakub rumanovsky');
    INSERT INTO events VALUES ('tree.ged', '@I1@', 'BIRT', '1985-03-01', 5), ('tree.ged', '@I2@', 'BIRT', '1960', 9);
    INSERT INTO files VALUES (1, 'a/b.jpg');
    INSERT INTO images VALUES (1, 1);
    INSERT INTO image_regions VALUES (1, 1, 'Rumanovsky Jakub', 0.1, 0.2, 0.3, 0.4);
    """)
    return c


def test_two_people_same_name_both_match_without_year(conn):
    forms = [person_forms(conn, "tree.ged", x) for x in ("@I1@", "@I2@")]
    assert [y for _, y in forms] == [1985, 1960]
    assert all(region_matches("Rumanovsky Jakub", f, y) for f, y in forms)
    assert [region_matches("Rumanovsky Jakub 1985", f, y) for f, y in forms] == [True, False]


def test_unknown_or_malformed_person_param_returns_none(conn):
    assert parse_person_param(conn, "tree.ged#@I1@") == ("tree.ged", "@I1@")
    for raw in ("x", "tree.ged#@I999@", ""):
        assert parse_person_param(conn, raw) is None


def test_image_regions_for(conn):
    assert [r["name"] for r in image_regions_for(conn, "a/b.jpg")] == ["Rumanovsky Jakub"]
    assert image_regions_for(conn, "none.jpg") == []
