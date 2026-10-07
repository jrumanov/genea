from genea.ingest.gedcom.ansel import decode_ansel


def test_ascii_passthrough():
    assert decode_ansel(b"0 HEAD") == "0 HEAD"


def test_combining_marks_precede_base_letter():
    assert decode_ansel(b"Nov\xe2ak") == "Novák"
    assert decode_ansel(b"Dvo\xe9r\xe2akov\xe2a") == "Dvořáková"
    assert decode_ansel(b"\xea") == "̊"  # dangling mark is kept


def test_special_characters():
    assert decode_ansel(b"\xa1\xe2od\xe2z") == "Łódź"
    assert decode_ansel(b"Stra\xcfe") == "Straße"


def test_unknown_byte_becomes_replacement_char():
    assert decode_ansel(b"a\x80b") == "a�b"
