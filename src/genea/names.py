"""Names a woman is known by through marriage."""

from __future__ import annotations

from genea.text import normalize


def married_surnames(husband_surname: str) -> list[str]:
    """Normalized forms of a husband's surname as used for his wife: the feminine form first (Novák -> Nováková,
    Novotný -> Novotná, Svoboda -> Svobodová), then his own form. Derived, never recorded: callers keep these
    out of the stored name variants."""
    husband = normalize(husband_surname)
    if not husband:
        return []
    if husband.endswith("y"):
        feminine = husband[:-1] + "a"
    elif husband.endswith("i"):
        feminine = husband
    elif husband.endswith("a"):
        feminine = husband[:-1] + "ova"
    else:
        feminine = husband + "ova"
    return list(dict.fromkeys([feminine, husband]))
