"""A real model summarizes a short transcript: run with `uv run pytest -m live tests/live/test_live_summary.py`."""

import pytest
from fixtures.transcripts import whisper

from genea.config import Settings
from genea.doctor import _has_model, ollama_models
from genea.ingest.summarize import make_summarizer
from genea.ingest.transcript import parse_transcript

pytestmark = pytest.mark.live

ROWS = [
    (0.0, 6.0, "Takže vlastne v 54. sa zobrali v novembri a nasťahovali sa k babke do Prískupíc."),
    (6.0, 12.0, "Babka Fábiková bola vtedy už vdova a býval s nimi aj ujo Juro."),
    (12.0, 18.0, "V 53. zomrel dedo a v 54. sa brali moji rodičia."),
]


def test_a_real_model_summarizes_a_short_transcript():
    summarizer = make_summarizer(Settings())
    names = ollama_models(Settings().ollama_url)
    if summarizer is None or names is None or not _has_model(names, summarizer.model):
        pytest.skip("Ollama is not running or the summary model is not pulled")
    summary = summarizer.summarize(parse_transcript(whisper(ROWS)))
    assert summary.overall.strip()
    assert len(summary.sections) == 1 and summary.sections[0].end_s == 18.0
    assert any("1954" in year or "54" in year for year in summary.sections[0].years) or "54" in summary.overall
