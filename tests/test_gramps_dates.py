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
