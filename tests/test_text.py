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


def test_snippet_long_needles_match_as_prefixes():
    from genea.text import snippet
    s = snippet("Kronika obce. " * 40 + "Pozdravujem pani Fabikovej." + " Koniec." * 40, ["fabik"], 60)
    assert "Fabikovej" in s


def test_snippet_prefers_the_window_with_the_most_needles():
    from genea.text import snippet
    text = ("Kedy sa narodil prvý učiteľ, nevieme. " + "Kronika obce. " * 140
            + "Štefan Fábik sa narodil v roku 1890." + " Koniec." * 20)
    s = snippet(text, ["kedy", "narodil", "stefan", "fabik"], 1000)
    assert "Štefan" in s and "Fábik" in s
    assert len(s) <= 1000


def test_snippet_ties_prefer_the_earliest_match():
    from genea.text import snippet
    text = "Fabik prvy. " + "Kronika obce. " * 100 + "Fabik druhy. " + "Kronika obce. " * 100 + "Fabik treti."
    s = snippet(text, ["fabik"], 60)
    assert "prvy" in s and "druhy" not in s and "treti" not in s


def test_snippet_ties_prefer_longer_needles():
    from genea.text import snippet
    text = "Kedy prisiel. " + "Kronika obce. " * 100 + "Fabikova tu. " + "Kronika obce. " * 100
    s = snippet(text, ["kedy", "fabikova"], 60)
    assert "Fabikova" in s


def test_snippet_short_needles_stay_whole_words():
    from genea.text import snippet
    assert snippet("Janasik " * 50 + "pan Jan.", ["jan"], 20).endswith("pan Jan.")  # not the first Janasik


def test_match_span_returns_the_sentence_around_the_match():
    from genea.text import match_span
    text = "Prvy vyrok. Tu zil Simon v Brne! Treti vyrok."
    s, e = match_span(text, ["simon"])
    assert text[s:e] == "Tu zil Simon v Brne!"


def test_match_span_is_accent_insensitive():
    from genea.text import match_span
    text = "Ahoj. Šimon prišiel domov. Koniec."
    s, e = match_span(text, ["simon"])
    assert text[s:e] == "Šimon prišiel domov."


def test_match_span_first_match_wins():
    from genea.text import match_span
    text = "Jan prisiel. Potom odisiel Jan."
    s, e = match_span(text, ["jan"])
    assert text[s:e] == "Jan prisiel."


def test_match_span_stops_at_blank_line():
    from genea.text import match_span
    text = "Nadpis bez bodky\n\nFabik bol tu\nna dvore\n\nDalsi odstavec"
    s, e = match_span(text, ["fabik"])
    assert text[s:e] == "Fabik bol tu\nna dvore"


def test_match_span_cuts_a_long_sentence_to_a_window():
    from genea.text import match_span, normalize
    text = "slovo " * 200 + "Fabik " + "slovo " * 200
    s, e = match_span(text, ["fabik"])
    assert e - s <= 400
    assert "fabik" in normalize(text[s:e])


def test_match_span_without_match_is_none():
    from genea.text import match_span
    assert match_span("Nic tu nie je.", ["fabik"]) is None


def test_match_span_slice_contains_the_needle():
    from genea.text import match_span, normalize
    text = "Raz. Dvořáková bola doma? Nie."
    s, e = match_span(text, ["dvorakova"])
    assert "dvorakova" in normalize(text[s:e])
