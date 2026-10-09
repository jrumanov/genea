"""Summaries of transcripts: sections of ~12,000 characters, then one overall summary."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Protocol

import httpx

from genea.config import Settings
from genea.ingest.transcript import Segment, format_time

SECTION_CHARS = 12000
NUM_CTX = 8192  # tokens: a 12,000-character Slovak section is ~4-5k tokens, and Ollama's default window may be smaller
REQUEST_TIMEOUT = 600.0  # seconds: a local model can need minutes for one section

SECTION_PROMPT = """\
You summarize one part of a recorded family interview for a genealogy archive.
The text is a raw speech-to-text transcript (Slovak or Czech, no punctuation structure, names may be misspelled).
Write in the language of the transcript. Use ONLY what is said; add no facts. Mark a name you are unsure of with "?".
Reply with JSON only: {"summary": "3-5 sentences", "topics": [...], "people": [...], "places": [...], "years": [...]}
people are names or kin terms as spoken (for example "babka Fábiková"); years are four-digit years that are mentioned."""

OVERALL_PROMPT = """\
You are given summaries of consecutive parts of one recorded family interview.
Write an overall summary of 3-6 sentences in the same language as the parts. Use only what the parts say.
Reply with JSON only: {"overall": "..."}"""


class SummaryError(RuntimeError):
    """A summary could not be produced."""


class SummaryUnavailable(SummaryError):
    """Ollama cannot be reached: the next transcript would fail the same way."""


@dataclass
class SectionSummary:
    start_s: float
    end_s: float
    summary: str
    topics: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)


@dataclass
class Summary:
    overall: str
    sections: list[SectionSummary]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Summary:
        return cls(str(data["overall"]), [SectionSummary(**s) for s in data["sections"]])


class Summarizer(Protocol):
    model: str

    def summarize(self, segments: list[Segment]) -> Summary: ...


def split_sections(segments: list[Segment], max_chars: int = SECTION_CHARS) -> list[list[Segment]]:
    """Consecutive runs of segments of up to `max_chars` characters; a longer segment is alone."""
    sections: list[list[Segment]] = []
    current: list[Segment] = []
    length = 0
    for seg in segments:
        if current and length + len(seg.text) + 1 > max_chars:
            sections.append(current)
            current, length = [], 0
        current.append(seg)
        length += len(seg.text) + 1
    if current:
        sections.append(current)
    return sections


class FakeSummarizer:
    """Deterministic and offline, for tests."""

    model = "fake"

    def __init__(self, section_chars: int = SECTION_CHARS) -> None:
        self.section_chars = section_chars
        self.calls = 0

    def summarize(self, segments: list[Segment]) -> Summary:
        self.calls += 1
        sections = [SectionSummary(part[0].start, part[-1].end, "Súhrn: " + " ".join(s.text for s in part)[:80],
                                   topics=["rodina"])
                    for part in split_sections(segments, self.section_chars)]
        return Summary(f"Nahrávka o rodine, {len(sections)} častí.", sections)


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SummaryError(f"the model's answer has no '{key}'")
    return value.strip()


def _strings(data: dict, key: str) -> list[str]:
    value = data.get(key)
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


class OllamaSummarizer:
    def __init__(self, url: str, model: str, timeout: float = REQUEST_TIMEOUT,
                 client: httpx.Client | None = None, section_chars: int = SECTION_CHARS) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.client = client or httpx.Client()
        self.section_chars = section_chars

    def summarize(self, segments: list[Segment]) -> Summary:
        parts = split_sections(segments, self.section_chars)
        if not parts:
            raise SummaryError("the transcript has no text")
        sections = []
        for part in parts:
            data = self._ask(SECTION_PROMPT, " ".join(s.text for s in part))
            sections.append(SectionSummary(part[0].start, part[-1].end, _text(data, "summary"),
                                           _strings(data, "topics"), _strings(data, "people"),
                                           _strings(data, "places"), _strings(data, "years")))
        if len(sections) == 1:
            return Summary(sections[0].summary, sections)
        joined = "\n".join(f"[{format_time(s.start_s)}] {s.summary}" for s in sections)
        return Summary(_text(self._ask(OVERALL_PROMPT, joined), "overall"), sections)

    def _ask(self, system: str, content: str) -> dict:
        payload = {"model": self.model, "stream": False, "think": False, "format": "json",
                   "options": {"temperature": 0, "num_ctx": NUM_CTX},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        try:
            response = self.client.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SummaryUnavailable(f"Ollama is not reachable ({exc}). Is it running?") from exc
        except httpx.HTTPError as exc:
            raise SummaryError(f"Ollama summary request failed ({exc}). Is `{self.model}` pulled?") from exc
        try:
            data = json.loads(response.json()["message"]["content"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SummaryError("Ollama did not return the JSON that was asked for") from exc
        if not isinstance(data, dict):
            raise SummaryError("Ollama did not return a JSON object")
        return data


def make_summarizer(settings: Settings) -> OllamaSummarizer | None:
    """None when there is no local model to use: set summary_model, or use an ollama: chat model."""
    name = settings.summary_model or (
        settings.model.removeprefix("ollama:") if settings.model.startswith("ollama:") else "")
    return OllamaSummarizer(settings.ollama_url, name) if name else None
