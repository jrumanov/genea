from dataclasses import replace

import pytest
from fixtures.make_kb import build_sample_kb

from genea.exiftool import exiftool_version
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase, init_kb
from genea.tools.base import open_tools


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Point the global config dir at an empty temp folder for every test."""
    home = tmp_path_factory.mktemp("genea_home")
    monkeypatch.setenv("GENEA_HOME", str(home))
    monkeypatch.delenv("GENEA_KB", raising=False)
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")
    return home


def pytest_collection_modifyitems(config, items):
    if exiftool_version() is None:
        skip = pytest.mark.skip(reason="ExifTool binary not available")
        for item in items:
            if "exiftool" in item.keywords:
                item.add_marker(skip)



@pytest.fixture
def sample_root(tmp_path):
    return build_sample_kb(tmp_path / "kb")


@pytest.fixture
def kb(sample_root):
    """Sample knowledge base with photos excluded, so no ExifTool is needed."""
    base = init_kb(sample_root)
    return KnowledgeBase(base.root, replace(base.settings, exclude=("photos/**",)))


@pytest.fixture
def kb_with_images(sample_root):
    return init_kb(sample_root)


@pytest.fixture
def tools(kb):
    index_kb(kb, FakeEmbedder())
    ctx = open_tools(kb, FakeEmbedder())
    yield ctx
    ctx.conn.close()


@pytest.fixture
def tools_with_images(kb_with_images):
    index_kb(kb_with_images, FakeEmbedder())
    ctx = open_tools(kb_with_images, FakeEmbedder())
    yield ctx
    ctx.conn.close()


@pytest.fixture
def analysis_kb(tmp_path):
    from fixtures.analysis_tree import make_analysis_kb

    return make_analysis_kb(tmp_path / "akb")


@pytest.fixture
def actx(analysis_kb):
    ctx = open_tools(analysis_kb, FakeEmbedder())
    yield ctx
    ctx.conn.close()


@pytest.fixture
def no_base_url():
    """Reset the citation viewer base URL after the test."""
    from genea.viewer import links
    yield
    links.set_base_url(None)
