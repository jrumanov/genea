"""End-to-end questions against a real local model.

Run:  uv run pytest -m live            (uses the model from ~/.genea/config.toml or the default)
      GENEA_LIVE_MODEL=ollama:qwen3:8b uv run pytest -m live   (compare models)
Needs Ollama running with the chat model and bge-m3 pulled.
"""

import asyncio
import os
from dataclasses import replace

import pytest
from fixtures.make_kb import build_sample_kb

from genea.agent.core import ask, build_agent
from genea.agent.model import build_model
from genea.doctor import _has_model, ollama_models
from genea.index.embed import make_embedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase, init_kb
from genea.text import normalize
from genea.tools.base import open_tools

pytestmark = pytest.mark.live

QUESTIONS = [
    ("Kdo byli rodiče Jana Nováka narozeného roku 1846?", ["Josef", "Anna"]),
    ("Who were the parents of Jan Novák born in 1846?", ["Josef", "Anna"]),
    ("Kdy a kde se oženil Jan Novák narozený 1846?", ["1870", "Tišnov"]),
    ("Jak se jmenovala manželka Karla Nováka?", ["Rosalie"]),
    ("Jmenuj děti Jana Nováka (1846) a Terezie Dvořákové.", ["Karel", "Ludmila", "Václav"]),
    ("Who were the grandparents of Jiří Novák born 1934?", ["Karel", "Rosalie"]),
    ("Which children did Josef Novák born 1820 have?", ["Jan", "Marie", "František"]),
    ("Kdo byli sourozenci Karla Nováka narozeného 1871?", ["Ludmila", "Václav"]),
    ("Find inconsistencies in the data about Marie Nováková born 1848.", ["1855"]),
    ("Co o Terezii Dvořákové říkají dokumenty?", ["1851"]),
    ("Which document mentions Václav Novák and on which page?", ["matrika", "2"]),
    ("What was the occupation of Josef Novák born 1820?", ["sedl"]),
    ("Koho si vzala Ludmila Nováková?", ["Jindřich"]),
    ("When did Anna Svobodová die?", ["1890"]),
    ("What do the documents say about Anna Svobodová?", ["1845"]),
    ("Is there a letter about Karel Novák's wedding?", ["dopis", "1895"]),
]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    root = build_sample_kb(tmp_path_factory.mktemp("live") / "kb")
    base = init_kb(root)
    settings = replace(base.settings, exclude=("photos/**",),
                       model=os.environ.get("GENEA_LIVE_MODEL", base.settings.model))
    names = ollama_models(settings.ollama_url)
    if names is None:
        pytest.skip("Ollama is not running")
    wanted = [settings.embed_model]
    if settings.model.startswith("ollama:"):
        wanted.append(settings.model.removeprefix("ollama:"))
    for model in wanted:
        if not _has_model(names, model):
            pytest.skip(f"Ollama model `{model}` is not pulled: run `ollama pull {model}`")
    kb = KnowledgeBase(base.root, settings)
    embedder = make_embedder(settings)
    index_kb(kb, embedder)
    tools = open_tools(kb, embedder)
    # One event loop for all questions: the shared agent's HTTP client keeps keep-alive connections
    # bound to the loop that opened them (see `ask`).
    with asyncio.Runner() as runner:
        yield tools, build_agent(build_model(settings)), runner
    tools.conn.close()


@pytest.mark.parametrize("question, expected", QUESTIONS)
def test_live_question(live, question, expected):
    tools, agent, runner = live
    answer = ask(agent, tools, question, runner=runner)
    text = normalize(answer.text)
    for word in expected:
        assert normalize(word) in text, answer.text
    assert "[" in answer.text, f"no citation in: {answer.text}"
