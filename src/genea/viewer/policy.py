"""What the viewer must never serve: photos tagged as personal identity documents."""

from __future__ import annotations

from collections.abc import Iterable

EXCLUDED_TAGS = frozenset({"travel card", "resident permit", "id card", "driving license",
                           "health insurance card"})


def is_excluded(keywords: Iterable[str]) -> bool:
    return any(k.strip().casefold() in EXCLUDED_TAGS for k in keywords)
