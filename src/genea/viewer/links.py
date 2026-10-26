"""Build viewer URLs for citations; no base URL (chat, tests) means no links."""

from __future__ import annotations

from urllib.parse import quote, urlencode

_base_url: str | None = None


def set_base_url(url: str | None) -> None:
    global _base_url
    _base_url = url.rstrip("/") if url else None


def get_base_url() -> str | None:
    return _base_url


def build_link(
    file: str,
    *,
    line: int | None = None,
    page: int | None = None,
    chunk: int | None = None,
    start_s: float | None = None,
    span: tuple[int, int] | None = None,
) -> str | None:
    if _base_url is None:
        return None
    query: list[tuple[str, int]] = []
    if line is not None:
        query.append(("line", line))
    if page is not None:
        query.append(("page", page))
    if chunk is not None:
        query.append(("chunk", chunk))
    if start_s is not None:
        query.append(("t", int(start_s)))
    if span is not None:
        query += [("o", span[0]), ("n", span[1])]
    url = f"{_base_url}/v/{quote(file, safe='/')}"
    return f"{url}?{urlencode(query)}" if query else url
