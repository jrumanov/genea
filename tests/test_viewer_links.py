"""Citation viewer link builder."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

import pytest

from genea.tools.models import SourceRef
from genea.viewer import links

BASE = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _reset(no_base_url):
    yield


def test_no_base_url_means_no_link():
    assert links.build_link("a.md") is None
    assert SourceRef(file="a.md").model_dump(exclude_none=True) == {"file": "a.md"}


def test_link_shapes():
    links.set_base_url(BASE + "/")
    assert links.get_base_url() == BASE
    assert links.build_link("tree.ged", line=1042) == f"{BASE}/v/tree.ged?line=1042"
    assert links.build_link("docs/matrika.pdf", page=3) == f"{BASE}/v/docs/matrika.pdf?page=3"
    assert links.build_link("rec/x.txt", chunk=2, start_s=842.4) == f"{BASE}/v/rec/x.txt?chunk=2&t=842"
    assert links.build_link("photos/x.jpg") == f"{BASE}/v/photos/x.jpg"
    assert links.build_link("a.md", span=(10, 5)) == f"{BASE}/v/a.md?o=10&n=5"


def test_awkward_paths_round_trip():
    links.set_base_url(BASE)
    name = "rodinná história/Šimon #2 100%+.md"
    url = links.build_link(name, line=1)
    parts = urlsplit(url)
    assert unquote(parts.path) == "/v/" + name
    assert parts.fragment == ""
    path_part = url.split("?")[0]
    assert " " not in path_part and "#" not in path_part and "?" not in path_part
    assert "%25" in path_part and "%23" in path_part


def test_source_ref_link_in_json():
    links.set_base_url(BASE)
    data = SourceRef(file="docs/a.pdf", page=3).model_dump(exclude_none=True)
    assert data["link"].endswith("/v/docs/a.pdf?page=3")
    assert "link" not in SourceRef(file="docs/a.pdf", page=3, linked=False).model_dump(exclude_none=True)
    assert "linked" not in data
