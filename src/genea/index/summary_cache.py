"""Summaries cached beside the index, keyed by the transcript's sha256.

They live outside index.db on purpose: `genea index --rebuild` deletes the database, and rebuilding must
not run the LLM again for transcripts that have not changed. Changing `summary_model` does not invalidate
an entry (the model is only recorded); bump SUMMARY_VERSION when the prompt or format improves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from genea.ingest.summarize import Summary
from genea.kb import KnowledgeBase

SUMMARY_VERSION = 1


def cache_path(kb: KnowledgeBase, digest: str) -> Path:
    return kb.summaries_dir / f"{digest}.json"


def load(kb: KnowledgeBase, digest: str) -> Summary | None:
    """None for a missing, damaged or out-of-date entry: the caller then summarizes again."""
    try:
        data = json.loads(cache_path(kb, digest).read_text(encoding="utf-8"))
        if data["version"] != SUMMARY_VERSION:
            return None
        return Summary.from_dict(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(kb: KnowledgeBase, digest: str, summary: Summary, model: str) -> None:
    path = cache_path(kb, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": SUMMARY_VERSION, "model": model, **summary.to_dict()},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
