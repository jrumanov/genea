"""Slovak declension of given names and surnames, on normalized single words."""

CASES = ("nom", "gen", "dat", "acc", "loc", "ins")


def _empty(word: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {c: set() for c in CASES}
    out["nom"].add(word)
    return out


def _add(out: dict[str, set[str]], forms: dict[str, tuple[str, ...]], stem: str) -> None:
    for case, endings in forms.items():
        for e in endings:
            out[case].add(stem + e)


def _unchanged(out: dict[str, set[str]], word: str) -> None:
    for c in CASES:
        out[c].add(word)


def _feminine(word: str, surname: bool, out: dict[str, set[str]]) -> None:
    if not word.endswith("a"):
        _unchanged(out, word)
        return
    stem = word[:-1]
    if not surname and word.endswith("ia"):
        forms = {"gen": ("e",), "dat": ("i",), "acc": ("u",), "loc": ("i",), "ins": ("ou",)}
    elif surname:
        forms = {"gen": ("ej",), "dat": ("ej",), "acc": ("u",), "loc": ("ej",), "ins": ("ou",)}
    else:
        forms = {"gen": ("y", "e"), "dat": ("e",), "acc": ("u",), "loc": ("e",), "ins": ("ou",)}
    _add(out, forms, stem)


def _masculine(word: str, surname: bool, out: dict[str, set[str]]) -> None:
    if surname and word[-1:] in ("y", "i"):
        stem = word[:-1]
        _add(out, {"gen": ("eho",), "dat": ("emu",), "acc": ("eho",), "loc": ("om",), "ins": ("ym",)}, stem)
        return
    if word.endswith("a"):
        _add(out, {"gen": ("u",), "dat": ("ovi",), "acc": ("u",), "loc": ("ovi",), "ins": ("om",)}, word[:-1])
        return
    if word.endswith("o"):
        _add(out, {"gen": ("a",), "dat": ("ovi",), "acc": ("a",), "loc": ("ovi",), "ins": ("om",)}, word[:-1])
        return
    forms = {"gen": ("a",), "dat": ("ovi",), "acc": ("a",), "loc": ("ovi",), "ins": ("om",)}
    _add(out, forms, word)
    if word.endswith(("ek", "ec")) and len(word) > 3:
        _add(out, forms, word[:-2] + word[-1])


def _cases(word: str, sex: str | None, surname: bool) -> dict[str, set[str]]:
    out = _empty(word)
    if sex in ("F", None):
        _feminine(word, surname, out)
    if sex in ("M", None):
        _masculine(word, surname, out)
    return out


def given_cases(given: str, sex: str | None) -> dict[str, set[str]]:
    return _cases(given, sex, surname=False)


def surname_cases(surname: str, sex: str | None) -> dict[str, set[str]]:
    return _cases(surname, sex, surname=True)
