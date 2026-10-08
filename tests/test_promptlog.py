import re
from dataclasses import replace

import pytest

from genea.agent.promptlog import record
from genea.config import Settings
from genea.kb import KnowledgeBase


def _log(kb):
    return (kb.meta_dir / "prompts.md").read_text(encoding="utf-8")


def _fields(entry_text):
    """The `key: value` lines of the first yaml block in an entry, as a dict of strings."""
    block = entry_text.split("```yaml\n", 1)[1].split("```", 1)[0]
    return dict(line.split(":", 1) for line in block.splitlines() if ":" in line)


def _entries(text):
    return [part for part in re.split(r"(?m)^(?=## )", text) if part.strip()]


def test_log_prompts_defaults_to_on():
    assert Settings().log_prompts is True


def test_record_writes_question_answer_and_fields(kb):
    record(kb, session="a3f9", turn=1, question="Kdo je Josef?", answer="Josef Novák [tree.ged:L7]",
           tool_calls=["find_person", "get_person"], duration_s=14.2)
    text = _log(kb)
    fields = {k.strip(): v.strip() for k, v in _fields(text).items()}
    assert fields["id"] == "a3f9-1"
    assert fields["session"] == "a3f9"
    assert fields["turn"] == "1"
    assert fields["followup"] == "false"
    assert fields["model"] == kb.settings.model
    assert fields["tools"] == "[find_person, get_person]"
    assert fields["tool_calls"] == "2"
    assert fields["citations"] == "1"
    assert fields["duration_s"] == "14.2"
    assert fields["failed"] == "false"
    assert fields["verdict"] == ""
    assert fields["note"] == ""
    assert "**Q:** Kdo je Josef?" in text
    assert "> Josef Novák [tree.ged:L7]" in text


def test_later_turns_are_marked_as_followups(kb):
    record(kb, session="s1", turn=1, question="Kdo je Josef?", answer="a")
    record(kb, session="s1", turn=2, question="A kde se narodil?", answer="b")
    first, second = _entries(_log(kb))
    assert _fields(first)["followup"].strip() == "false"
    assert _fields(second)["followup"].strip() == "true"
    assert _fields(second)["id"].strip() == "s1-2"


def test_entries_are_appended_not_overwritten(kb):
    record(kb, session="s1", turn=1, question="první", answer="a")
    record(kb, session="s2", turn=1, question="druhá", answer="b")
    text = _log(kb)
    assert len(_entries(text)) == 2
    assert text.index("první") < text.index("druhá")


def test_czech_text_roundtrips_as_utf8(kb):
    record(kb, session="s1", turn=1, question="Kde se narodila Zdeňka Řezníčková?",
           answer="Narodila se v Příbrami.")
    raw = (kb.meta_dir / "prompts.md").read_bytes().decode("utf-8")
    assert "Zdeňka Řezníčková" in raw
    assert "Příbrami" in raw


def test_failed_turn_is_logged_with_the_error(kb):
    record(kb, session="s1", turn=1, question="Kdo je Josef?", error="ConnectError: ollama is down")
    text = _log(kb)
    fields = _fields(text)
    assert fields["failed"].strip() == "true"
    assert fields["citations"].strip() == "0"
    assert "ConnectError: ollama is down" in text


def test_citations_count_gedcom_document_and_photo_references(kb):
    answer = "Born 1900 [tree.ged:L7]. Baptised [docs/matrika.pdf p.3]. Photo [photos/x.jpg]. Not [a link]."
    record(kb, session="s1", turn=1, question="q", answer=answer)
    assert _fields(_log(kb))["citations"].strip() == "3"


def test_answer_headings_do_not_start_a_new_entry(kb):
    record(kb, session="s1", turn=1, question="q", answer="## Documented:\nfact\n\n## Inferred:\nmore")
    assert len(_entries(_log(kb))) == 1


def test_nothing_is_written_when_logging_is_off(kb):
    off = KnowledgeBase(kb.root, replace(kb.settings, log_prompts=False))
    record(off, session="s1", turn=1, question="q", answer="a")
    assert not (kb.meta_dir / "prompts.md").exists()


def test_unwritable_log_raises_oserror(kb):
    (kb.meta_dir / "prompts.md").mkdir()  # a folder where the file should be
    with pytest.raises(OSError):
        record(kb, session="s1", turn=1, question="q", answer="a")
