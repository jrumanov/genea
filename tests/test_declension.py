import pytest

from genea.declension import CASES, given_cases, surname_cases


def _forms(gen, dat, acc, loc, ins):
    return dict(zip(CASES[1:], (set(gen), set(dat), set(acc), set(loc), set(ins))))


# One row per line of the ending-rule table (plan Task 1); every listed form must be generated.
ROWS = [
    pytest.param(given_cases, "maria", "F", _forms(["marie"], ["marii"], ["mariu"], ["marii"], ["mariou"]),
                 id="f-given-ia"),
    pytest.param(given_cases, "anna", "F", _forms(["anny", "anne"], ["anne"], ["annu"], ["anne"], ["annou"]),
                 id="f-given-a"),
    pytest.param(surname_cases, "fabikova", "F",
                 _forms(["fabikovej"], ["fabikovej"], ["fabikovu"], ["fabikovej"], ["fabikovou"]), id="f-sur-a"),
    pytest.param(surname_cases, "rumanovska", "F",
                 _forms(["rumanovskej"], ["rumanovskej"], ["rumanovsku"], ["rumanovskej"], ["rumanovskou"]),
                 id="f-sur-ska"),
    pytest.param(surname_cases, "rumanovsky", "M",
                 _forms(["rumanovskeho"], ["rumanovskemu"], ["rumanovskeho"], ["rumanovskom"], ["rumanovskym"]),
                 id="m-sur-y"),
    pytest.param(surname_cases, "krejci", "M",
                 _forms(["krejceho"], ["krejcemu"], ["krejceho"], ["krejcom"], ["krejcym"]), id="m-sur-i"),
    pytest.param(surname_cases, "kubica", "M", _forms(["kubicu"], ["kubicovi"], ["kubicu"], ["kubicovi"], ["kubicom"]),
                 id="m-a"),
    pytest.param(surname_cases, "belinko", "M",
                 _forms(["belinka"], ["belinkovi"], ["belinka"], ["belinkovi"], ["belinkom"]), id="m-o"),
    pytest.param(surname_cases, "fabik", "M", _forms(["fabika"], ["fabikovi"], ["fabika"], ["fabikovi"], ["fabikom"]),
                 id="m-cons-sur"),
    pytest.param(given_cases, "jozef", "M", _forms(["jozefa"], ["jozefovi"], ["jozefa"], ["jozefovi"], ["jozefom"]),
                 id="m-cons-given"),
    pytest.param(surname_cases, "ouzednicek", "M",
                 _forms(["ouzednicka"], ["ouzednickovi"], ["ouzednicka"],
                        ["ouzednickovi"], ["ouzednickom"]), id="m-ek"),
    pytest.param(surname_cases, "nemec", "M", _forms(["nemca"], ["nemcovi"], ["nemca"], ["nemcovi"], ["nemcom"]),
                 id="m-ec"),
]


@pytest.mark.parametrize(("cases", "word", "sex", "expected"), ROWS)
def test_ending_rule(cases, word, sex, expected):
    c = cases(word, sex)
    for case, forms in expected.items():
        assert forms <= c[case], case


@pytest.mark.parametrize(("word", "full"), [("ouzednicek", "ouzednicek"), ("moncek", "moncek"), ("nemec", "nemec")],
                         ids=["ouzed", "moncek", "nemec"])
def test_fleeting_vowel_keeps_the_full_stem_forms(word, full):
    c = surname_cases(word, "M")
    assert {full + "a"} <= c["gen"] and {full + "ovi"} <= c["dat"] and {full + "om"} <= c["ins"]


def test_moncek_fleeting_vowel():
    assert "moncka" in surname_cases("moncek", "M")["gen"]


@pytest.mark.parametrize(("cases", "word"), [(surname_cases, "krejci"), (given_cases, "ruth")], ids=["krejci", "ruth"])
def test_feminine_word_not_ending_in_a_is_unchanged(cases, word):
    c = cases(word, "F")
    assert all(c[case] == {word} for case in CASES)


def test_unknown_sex_is_the_union():
    union = surname_cases("fabik", None)
    for sex in ("M", "F"):
        for case, forms in surname_cases("fabik", sex).items():
            assert forms <= union[case]
    assert "fabika" in union["gen"] and "fabik" in union["ins"]


def test_sex_filters_the_rules():
    assert "fabika" not in surname_cases("fabik", "F")["gen"]
    assert "fabikovej" not in surname_cases("fabikova", "M")["gen"]


@pytest.mark.parametrize(("cases", "word", "sex"), [(given_cases, "anna", "F"), (surname_cases, "fabik", "M"),
                                                     (surname_cases, "krejci", None)], ids=["anna", "fabik", "krejci"])
def test_every_case_key_present_and_nom_has_the_input(cases, word, sex):
    c = cases(word, sex)
    assert set(c) == set(CASES)
    assert word in c["nom"]
