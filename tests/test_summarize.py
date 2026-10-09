import json

import httpx
import pytest

from genea.config import Settings
from genea.ingest.summarize import (
    FakeSummarizer,
    OllamaSummarizer,
    SectionSummary,
    Summary,
    SummaryError,
    SummaryUnavailable,
    make_summarizer,
    split_sections,
)
from genea.ingest.transcript import Segment

SEGMENTS = [Segment(0, 10, "a" * 60), Segment(10, 20, "b" * 60), Segment(20, 30, "c" * 60)]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _reply(payload):
    return httpx.Response(200, json={"message": {"content": json.dumps(payload, ensure_ascii=False)}})


def test_split_sections_at_segment_boundaries():
    sections = split_sections(SEGMENTS, max_chars=130)
    assert [[s.start for s in part] for part in sections] == [[0, 10], [20]]
    assert split_sections([], 130) == []


def test_a_segment_longer_than_the_limit_gets_its_own_section():
    big = [Segment(0, 5, "x" * 500), Segment(5, 6, "y")]
    assert [len(part) for part in split_sections(big, max_chars=100)] == [1, 1]


def test_summary_round_trip():
    summary = Summary("Celkovo.", [SectionSummary(0, 10, "Časť.", ["rodina"], ["babka Fábiková"], ["Prískupice"], ["1954"])])
    assert Summary.from_dict(json.loads(json.dumps(summary.to_dict()))) == summary


def test_fake_summarizer_is_deterministic_and_counts_calls():
    fake = FakeSummarizer(section_chars=130)
    first, second = fake.summarize(SEGMENTS), fake.summarize(SEGMENTS)
    assert first == second and fake.calls == 2
    assert [(s.start_s, s.end_s) for s in first.sections] == [(0, 20), (20, 30)]


def test_ollama_summarizer_one_section():
    seen = []

    def handler(request):
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        seen.append(body)
        return _reply({"summary": "Rozprávanie o babke.", "topics": ["rodina"], "people": ["babka Fábiková"],
                       "places": ["Prískupice"], "years": [1954, "1953"]})

    summarizer = OllamaSummarizer("http://ollama:11434", "qwen3:14b", client=_client(handler))
    summary = summarizer.summarize(SEGMENTS)
    assert len(seen) == 1  # one section needs no second pass
    assert seen[0]["model"] == "qwen3:14b" and seen[0]["think"] is False and seen[0]["format"] == "json"
    assert seen[0]["options"]["num_ctx"] >= 8192  # Ollama's default window can be smaller than one section
    assert seen[0]["messages"][1]["content"] == " ".join(s.text for s in SEGMENTS)
    assert summary.overall == "Rozprávanie o babke."
    assert summary.sections == [SectionSummary(0, 30, "Rozprávanie o babke.", ["rodina"], ["babka Fábiková"],
                                               ["Prískupice"], ["1954", "1953"])]


def test_ollama_summarizer_several_sections_then_overall():
    answers = iter([{"summary": "Prvá časť."}, {"summary": "Druhá časť."}, {"overall": "Celá nahrávka."}])
    prompts = []

    def handler(request):
        prompts.append(json.loads(request.content)["messages"][1]["content"])
        return _reply(next(answers))

    summarizer = OllamaSummarizer("http://ollama:11434", "m", client=_client(handler), section_chars=130)
    summary = summarizer.summarize(SEGMENTS)
    assert [s.summary for s in summary.sections] == ["Prvá časť.", "Druhá časť."]
    assert summary.overall == "Celá nahrávka."
    assert prompts[2] == "[0:00] Prvá časť.\n[0:20] Druhá časť."


@pytest.mark.parametrize("content", ["not json at all", "[1, 2]", '{"topics": ["x"]}', ""])
def test_malformed_model_output_is_a_summary_error(content):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": content}})

    with pytest.raises(SummaryError):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(handler)).summarize(SEGMENTS)


def test_missing_message_is_a_summary_error():
    with pytest.raises(SummaryError):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(lambda r: httpx.Response(200, json={}))).summarize(SEGMENTS)


def test_http_error_is_a_summary_error_naming_the_model():
    summarizer = OllamaSummarizer("http://ollama:11434", "qwen3:14b",
                                  client=_client(lambda r: httpx.Response(404, json={"error": "model not found"})))
    with pytest.raises(SummaryError, match="qwen3:14b") as excinfo:
        summarizer.summarize(SEGMENTS)
    assert not isinstance(excinfo.value, SummaryUnavailable)


def test_unreachable_ollama_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SummaryUnavailable, match="not reachable"):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(handler)).summarize(SEGMENTS)


def test_empty_transcript_is_a_summary_error():
    with pytest.raises(SummaryError, match="no text"):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(lambda r: _reply({}))).summarize([])


def test_make_summarizer_model_choice():
    assert make_summarizer(Settings()).model == "qwen3:14b"  # the chat model's name
    assert make_summarizer(Settings(model="ollama:qwen3:8b", summary_model="gemma3:12b")).model == "gemma3:12b"
    assert make_summarizer(Settings(model="anthropic:claude-sonnet-5-5")) is None  # no local model to use
    assert make_summarizer(Settings(model="anthropic:claude-sonnet-5-5", summary_model="qwen3:14b")).model == "qwen3:14b"
