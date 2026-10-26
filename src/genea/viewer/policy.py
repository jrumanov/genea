"""What the viewer must never serve: photos tagged as personal identity documents."""

from __future__ import annotations

from collections.abc import Iterable

EXCLUDED_TAGS = frozenset({"travel card", "resident permit", "id card", "driving license",
                           "driving licence", "health insurance card"})


def _norm(keyword: str) -> str:
    return " ".join(keyword.casefold().replace("-", " ").replace("_", " ").replace("|", " ").split())


def is_excluded(keywords: Iterable[str]) -> bool:
    """True if any keyword contains an excluded tag (keywords may be compound, like `_documents|travel card`)."""
    return any(tag in _norm(k) for k in keywords for tag in EXCLUDED_TAGS)
