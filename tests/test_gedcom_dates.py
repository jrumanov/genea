import pytest

from genea.ingest.gedcom.dates import DateRange, parse_gedcom_date

CASES = [
    ("12 MAR 1850", "1850-03-12", "1850-03-12", None),
    ("MAR 1850", "1850-03-01", "1850-03-31", None),
    ("FEB 1852", "1852-02-01", "1852-02-29", None),
    ("1850", "1850-01-01", "1850-12-31", None),
    ("abt 1850", "1845-01-01", "1855-12-31", "ABT"),
    ("EST 1850", "1845-01-01", "1855-12-31", "EST"),
    ("CAL 1850", "1845-01-01", "1855-12-31", "CAL"),
    ("ABT 29 FEB 1852", "1847-02-28", "1857-02-28", "ABT"),
    ("BEF 1850", "1750-01-01", "1850-12-31", "BEF"),
    ("AFT 1850", "1850-01-01", "1950-12-31", "AFT"),
    ("BET 1849 AND 1851", "1849-01-01", "1851-12-31", "BET"),
    ("FROM 1849 TO MAR 1851", "1849-01-01", "1851-03-31", "FROM"),
    ("FROM 1849", "1849-01-01", "1849-12-31", "FROM"),
    ("TO 1851", "1851-01-01", "1851-12-31", "TO"),
    ("11 FEB 1731/32", "1732-02-11", "1732-02-11", None),
    ("@#DJULIAN@ 1 JAN 1700", "1700-01-01", "1700-01-01", None),
    ("INT 1850 (the year of the flood)", "1850-01-01", "1850-12-31", "INT"),
    ("850", "0850-01-01", "0850-12-31", None),
]


@pytest.mark.parametrize("raw, start, end, qualifier", CASES)
def test_parse(raw, start, end, qualifier):
    assert parse_gedcom_date(raw) == DateRange(start, end, qualifier)


@pytest.mark.parametrize("raw", [None, "", "   ", "31 FEB 1850", "sometime", "@#DFRENCH R@ 1 VEND 11"])
def test_unparseable_gives_empty_range(raw):
    result = parse_gedcom_date(raw)
    assert result.date_from is None and result.date_to is None


def test_phrase():
    assert parse_gedcom_date("(after the war)") == DateRange(None, None, "PHRASE")


def test_qualifier_kept_when_inner_date_is_bad():
    assert parse_gedcom_date("ABT spring") == DateRange(None, None, "ABT")
