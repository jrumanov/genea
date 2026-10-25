from genea.declension import CASES, given_cases, surname_cases


def test_feminine_given_name():
    c = given_cases("anna", "F")
    assert set(c) == set(CASES) and "anna" in c["nom"]
    assert {"anny", "anne", "annu", "annou"} <= c["gen"] | c["dat"] | c["acc"] | c["ins"]
    assert "marie" in given_cases("maria", "F")["gen"] and "mariou" in given_cases("maria", "F")["ins"]


def test_masculine_given_name():
    c = given_cases("jan", "M")
    assert "jana" in c["gen"] and "janovi" in c["dat"] and "janom" in c["ins"]


def test_feminine_surname():
    c = surname_cases("fabikova", "F")
    assert "fabikovej" in c["gen"] and "fabikovu" in c["acc"] and "fabikovou" in c["ins"]
    assert surname_cases("krejci", "F")["ins"] == {"krejci"}


def test_masculine_surnames():
    assert "fabika" in surname_cases("fabik", "M")["gen"] and "fabikom" in surname_cases("fabik", "M")["ins"]
    assert "rumanovskeho" in surname_cases("rumanovsky", "M")["gen"]
    assert "ouzednicka" in surname_cases("ouzednicek", "M")["gen"]


def test_unknown_sex_is_the_union():
    c = surname_cases("fabik", None)
    assert "fabika" in c["gen"]
