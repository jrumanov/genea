import pytest

from genea.gramps.dates import same_date, to_gramps_date


@pytest.mark.parametrize("raw, mod, qual, val", [
    ("12 MAR 1883", 0, 0, [12, 3, 1883, False]),
    ("1883", 0, 0, [0, 0, 1883, False]),
    ("ABT 1880", 3, 0, [0, 0, 1880, False]),
    ("EST 1880", 0, 1, [0, 0, 1880, False]),
    ("CAL MAR 1880", 0, 2, [0, 3, 1880, False]),
    ("BEF 1900", 1, 0, [0, 0, 1900, False]),
    ("BET 1880 AND 1885", 4, 0, [0, 0, 1880, False, 0, 0, 1885, False]),
    ("FROM 1880 TO 1885", 5, 0, [0, 0, 1880, False, 0, 0, 1885, False]),
])
def test_to_gramps_date(raw, mod, qual, val):
    date = to_gramps_date(raw)
    assert date == {"_class": "Date", "calendar": 0, "modifier": mod, "quality": qual,
                    "dateval": val, "text": "", "sortval": 0, "newyear": 0}


def test_julian():
    assert to_gramps_date("@#DJULIAN@ 1 JAN 1700")["calendar"] == 1


@pytest.mark.parametrize("raw", [
    "INT 1880 (about then)", "(sometime)", "@#DHEBREW@ 5600", "1731/32", "32 JAN 1880", ""])
def test_unrepresentable_raises(raw):
    with pytest.raises(ValueError):
        to_gramps_date(raw)


def test_same_date_ignores_sortval_and_text():
    server = to_gramps_date("12 MAR 1883") | {"sortval": 2408000, "text": "12 March 1883"}
    assert same_date(server, "12 MAR 1883")
    assert not same_date(server, "13 MAR 1883")


def test_same_date_none():
    assert same_date(None, None) and not same_date(None, "1880")
    assert same_date({}, "") and same_date({"dateval": [0, 0, 0, False], "modifier": 0}, None)
    assert not same_date(to_gramps_date("1880"), None)


def test_julian_range_both_marked():
    date = to_gramps_date("BET @#DJULIAN@ 1 JAN 1700 AND @#DJULIAN@ 1 FEB 1700")
    assert date["calendar"] == 1 and date["modifier"] == 4


def test_julian_marker_before_modifier_word():
    date = to_gramps_date("@#DJULIAN@ ABT 1700")
    assert date["calendar"] == 1 and date["modifier"] == 3


@pytest.mark.parametrize("raw", [
    "BET @#DJULIAN@ 1 JAN 1700 AND 1 FEB 1700",
    "BET 1 JAN 1700 AND @#DJULIAN@ 1 FEB 1700",
    "1 JAN @#DJULIAN@ 1700",
    "@#DJULIAN@ ABT @#DJULIAN@ 1700",
    "BET 1880 TO 1885",
    "FROM 1880 AND 1885",
    "0 JAN 1880",
    "ABT",
    "1880 junk",
    "ABT 1880 junk",
    "12 1880",
])
def test_mixed_or_malformed_raises(raw):
    with pytest.raises(ValueError):
        to_gramps_date(raw)


def test_lowercase_accepted():
    assert to_gramps_date("abt 12 mar 1883")["dateval"] == [12, 3, 1883, False]


def test_same_date_mismatches():
    server = to_gramps_date("ABT 1880")
    assert same_date(server, "ABT 1880")
    assert not same_date(server, "1880")
    assert not same_date(server, "EST 1880")
    assert not same_date(to_gramps_date("EST 1880"), "CAL 1880")
    assert not same_date(server, "@#DJULIAN@ ABT 1880")


def test_same_date_ranges():
    server = to_gramps_date("BET 1880 AND 1885")
    assert same_date(server, "BET 1880 AND 1885")
    assert not same_date(server, "BET 1880 AND 1886")
    assert not same_date(server, "FROM 1880 TO 1885")
