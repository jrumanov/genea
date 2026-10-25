from genea.text import normalize


def test_normalize():
    assert normalize("Dvořáková") == "dvorakova"
    assert normalize("  Łódź,  Nowak-Ślązak ") == "lodz nowak slazak"
    assert normalize("Straße") == "strasse"
    assert normalize("") == ""


def test_snippet_centres_on_a_declined_name():
    from genea.text import snippet
    text = "x " * 300 + "Jozef sa oženil s Annou Fabíkovou v Turnej. " + "y " * 300
    s = snippet(text, ["annou fabikovou"], 120)
    assert "Annou Fabíkovou" in s and len(s) <= 120 and s.startswith("…") and s.endswith("…")


def test_snippet_without_match_returns_the_start():
    from genea.text import snippet
    assert snippet("Kronika obce Brno. " * 20, ["nikto"], 50).startswith("Kronika obce")


def test_snippet_matches_whole_words_only():
    from genea.text import snippet
    assert "Annou" not in snippet("Hannou " * 50 + "end", ["annou"], 30)


def test_snippet_maps_back_through_length_changing_normalization():
    import unicodedata
    from genea.text import snippet
    # ß -> ss and decomposed accents change length; the span must still land on the right original text
    text = "Straße " * 40 + unicodedata.normalize("NFD", "Fabíková") + " tu. " + "Maße " * 40
    s = snippet(text, ["fabikova"], 40)
    assert unicodedata.normalize("NFD", "Fabíková") in s and len(s) <= 40
