"""A real whisper.cpp run: needs GENEA_TEST_AUDIO (a short recording) and GENEA_TEST_MODEL (a ggml .bin or a model name).

Run with `uv run pytest -m live tests/live/test_live_transcribe.py`.
"""

import os

import pytest

from genea.transcribe.audio import load_span
from genea.transcribe.engine import WhisperCpp, resolve_model

pytestmark = pytest.mark.live


def test_a_real_model_transcribes_ten_seconds():
    pytest.importorskip("pywhispercpp")
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    audio, model = os.environ.get("GENEA_TEST_AUDIO"), os.environ.get("GENEA_TEST_MODEL")
    if not audio or not model:
        pytest.skip("set GENEA_TEST_AUDIO and GENEA_TEST_MODEL")
    segments = []
    WhisperCpp(resolve_model(model))(load_span(audio, 0.0, 10.0), "", segments.append)
    assert segments and all(s.end >= s.start for s in segments)
