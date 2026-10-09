"""Transcript summaries written outside genea, for example by Claude Code instead of a slow local model.

`write_requests` puts, for each transcript that has no cached summary, <digest>.sections.md (its text,
cut into the sections the Ollama summarizer would ask about) and <digest>.request.json (where those
sections start and end) into kb.summary_work_dir. Whoever
summarizes writes <digest>.result.json next to it; `import_results` checks it and stores it in the
summary cache, where the next `genea index` picks it up.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field

from genea.index import summary_cache
from genea.index.indexer import file_hash, scan_files
from genea.ingest.docs import read_transcript
from genea.ingest.summarize import (OVERALL_PROMPT, SECTION_CHARS, SECTION_PROMPT, Summary, SummaryError,
                                    overall_from_answer, section_from_answer, split_sections)
from genea.ingest.transcript import format_time
from genea.kb import KnowledgeBase

REQUEST = ".request.json"
SECTIONS = ".sections.md"
RESULT = ".result.json"
WRAP = 100  # characters per line of a sections file: a whole section on one line is hard to read in parts

INSTRUCTIONS = f"""\
# Transcript summaries

Each `<digest>{SECTIONS}` here holds the transcript of one recorded family interview, cut into
numbered sections. For each one, write `<digest>{RESULT}` beside it, then run
`genea summaries import`. Do not change the other files.

## Each section

{SECTION_PROMPT}

## The whole recording

{OVERALL_PROMPT}

## The result file

UTF-8 JSON, one object:

    {{"sections": [{{"summary": "...", "topics": [], "people": [], "places": [], "years": []}}, ...],
     "overall": "..."}}

`sections` has exactly one entry per section of the sections file, in the same order. Leave out
the times; they are taken from `<digest>{REQUEST}`. `overall` may be left out when there is only one section.
"""


@dataclass
class Request:
    path: str
    digest: str
    sections: int


@dataclass
class ImportReport:
    imported: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path or file name, reason)


def write_requests(kb: KnowledgeBase, section_chars: int = SECTION_CHARS) -> list[Request]:
    """A request file for each transcript without a cached summary; requests that are no longer
    needed (summarized meanwhile, or the transcript changed) are removed with their results."""
    work = kb.summary_work_dir
    work.mkdir(parents=True, exist_ok=True)
    requests = []
    for path, kind in scan_files(kb):
        if kind != "doc" or path.suffix.lower() != ".txt":
            continue
        try:
            digest = file_hash(path, kind)
            if summary_cache.load(kb, digest) is not None:
                continue
            segments = read_transcript(path)
        except Exception:  # noqa: BLE001 - `genea index` reports an unreadable file
            continue
        if not segments:
            continue
        parts = split_sections(segments, section_chars)
        data = {"path": kb.rel(path), "digest": digest,
                "sections": [{"start_s": part[0].start, "end_s": part[-1].end} for part in parts]}
        (work / f"{digest}{REQUEST}").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        (work / f"{digest}{SECTIONS}").write_text(_sections_text(kb.rel(path), parts), encoding="utf-8")
        requests.append(Request(kb.rel(path), digest, len(parts)))
    wanted = {r.digest for r in requests}
    for stale in [*work.glob(f"*{REQUEST}"), *work.glob(f"*{SECTIONS}"), *work.glob(f"*{RESULT}")]:
        if stale.name.split(".")[0] not in wanted:
            stale.unlink()
    (work / "INSTRUCTIONS.md").write_text(INSTRUCTIONS, encoding="utf-8")
    return requests


def _sections_text(rel: str, parts: list) -> str:
    lines = [f"# {rel}", ""]
    for number, part in enumerate(parts, 1):
        times = f"{format_time(part[0].start)}-{format_time(part[-1].end)}"
        lines += [f"## Section {number} of {len(parts)} ({times})", ""]
        lines += textwrap.wrap(" ".join(s.text for s in part), WRAP) + [""]
    return "\n".join(lines)


def _summary(request: dict, answer: object) -> Summary:
    if not isinstance(answer, dict) or not isinstance(answer.get("sections"), list):
        raise SummaryError("the result has no 'sections' list")
    wanted, got = request["sections"], answer["sections"]
    if len(got) != len(wanted):
        raise SummaryError(f"the request has {len(wanted)} sections, the result {len(got)}")
    sections = []
    for part, data in zip(wanted, got):
        if not isinstance(data, dict):
            raise SummaryError("a section of the result is not a JSON object")
        sections.append(section_from_answer(data, part["start_s"], part["end_s"]))
    if len(sections) == 1 and "overall" not in answer:
        return Summary(sections[0].summary, sections)
    return Summary(overall_from_answer(answer), sections)


def import_results(kb: KnowledgeBase, model: str = "claude-code") -> ImportReport:
    """Store every valid result in the summary cache and delete its work files; a bad result is kept
    and reported, so it can be redone."""
    report = ImportReport()
    for result in sorted(kb.summary_work_dir.glob(f"*{RESULT}")):
        digest = result.name.removesuffix(RESULT)
        request_path = kb.summary_work_dir / f"{digest}{REQUEST}"
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report.failed.append((result.name, f"no readable {request_path.name} beside it"))
            continue
        try:
            answer = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report.failed.append((request["path"], f"not valid JSON ({exc})"))
            continue
        try:
            summary = _summary(request, answer)
        except SummaryError as exc:
            report.failed.append((request["path"], str(exc)))
            continue
        summary_cache.store(kb, digest, summary, model)
        for done in (result, request_path, kb.summary_work_dir / f"{digest}{SECTIONS}"):
            done.unlink(missing_ok=True)
        report.imported.append(request["path"])
    return report
