import json

from genea.index import summary_cache
from genea.ingest.summarize import SectionSummary, Summary

SUMMARY = Summary("Celkovo.", [SectionSummary(0, 30, "Časť.", ["rodina"])])


def test_store_then_load(kb):
    summary_cache.store(kb, "abc123", SUMMARY, "qwen3:14b")
    assert summary_cache.load(kb, "abc123") == SUMMARY
    data = json.loads((kb.summaries_dir / "abc123.json").read_text(encoding="utf-8"))
    assert data["version"] == summary_cache.SUMMARY_VERSION and data["model"] == "qwen3:14b"
    assert data["overall"] == "Celkovo." and data["sections"][0]["summary"] == "Časť."
    assert list(kb.summaries_dir.glob("*.tmp")) == []  # written atomically


def test_missing_entry_is_a_miss(kb):
    assert summary_cache.load(kb, "nope") is None


def test_an_older_version_is_a_miss(kb, monkeypatch):
    summary_cache.store(kb, "abc123", SUMMARY, "m")
    monkeypatch.setattr(summary_cache, "SUMMARY_VERSION", summary_cache.SUMMARY_VERSION + 1)
    assert summary_cache.load(kb, "abc123") is None


def test_a_different_model_is_still_a_hit(kb):
    summary_cache.store(kb, "abc123", SUMMARY, "old-model")
    assert summary_cache.load(kb, "abc123") == SUMMARY  # the model is recorded, not part of the key


def test_damaged_files_are_a_miss(kb):
    for name, content in [("a", "{not json"), ("b", "[]"), ("c", '{"version": 1}'),
                          ("d", json.dumps({"version": 1, "overall": "x", "sections": [{"oops": 1}]}))]:
        (kb.summaries_dir / f"{name}.json").write_text(content, encoding="utf-8")
        assert summary_cache.load(kb, name) is None
    (kb.summaries_dir / "e.json").write_bytes(b"\xff\xfe\x00")
    assert summary_cache.load(kb, "e") is None
