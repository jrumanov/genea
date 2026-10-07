from genea.text import normalize


def test_normalize():
    assert normalize("Dvořáková") == "dvorakova"
    assert normalize("  Łódź,  Nowak-Ślązak ") == "lodz nowak slazak"
    assert normalize("Straße") == "strasse"
    assert normalize("") == ""
