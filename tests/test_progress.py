import sqlite3
from datetime import datetime, timedelta

import pytest

from genea.index.progress import (
    IndexBusy,
    IndexLock,
    IndexProgress,
    clear_progress,
    index_state,
    is_index_running,
    read_progress,
    write_progress,
)
from genea.kb import init_kb


@pytest.fixture
def empty_kb(tmp_path):
    return init_kb(tmp_path / "Foto DB")  # a space in the path, like a real Windows KB


def _progress(**kw):
    base = dict(pid=1, started_at="2026-10-08T10:00:00", phase="indexing",
                phase_started_at="2026-10-08T10:00:00", updated_at="2026-10-08T10:10:00",
                done=100, total=500, current="docs/a.pdf")
    return IndexProgress(**{**base, **kw})


def test_progress_round_trip(empty_kb):
    assert read_progress(empty_kb) is None
    write_progress(empty_kb, _progress())
    assert read_progress(empty_kb) == _progress()
    clear_progress(empty_kb)
    clear_progress(empty_kb)  # a missing file is fine
    assert read_progress(empty_kb) is None


def test_garbage_progress_file_reads_as_none(empty_kb):
    (empty_kb.meta_dir / "index.progress.json").write_text("{not json", encoding="utf-8")
    assert read_progress(empty_kb) is None
    (empty_kb.meta_dir / "index.progress.json").write_text('{"pid": 1}', encoding="utf-8")
    assert read_progress(empty_kb) is None


def test_eta_extrapolates_the_indexing_rate():
    now = datetime.fromisoformat("2026-10-08T10:10:00")  # 100 files in 10 minutes
    assert _progress().eta_seconds(now) == pytest.approx(40 * 60)
    assert _progress(done=0).eta_seconds(now) is None
    assert _progress(phase="scanning").eta_seconds(now) is None


def test_describe_each_phase():
    now = datetime.fromisoformat("2026-10-08T10:10:00")
    assert _progress().describe(now) == "100 / 500 files (20 %), ETA ~40 min, now: docs/a.pdf"
    assert _progress(phase="scanning", done=42, total=0).describe(now) == "scanning files (42 found)"
    assert "linking" in _progress(phase="linking").describe(now)
    late = now + timedelta(hours=2)
    assert "ETA ~" in _progress(done=1).describe(late)  # long ETAs use hours
    assert "h" in _progress(done=1).describe(late).split("ETA ~")[1]


def test_lock_is_exclusive_and_released(empty_kb):
    assert is_index_running(empty_kb) is False
    with IndexLock(empty_kb):
        assert is_index_running(empty_kb) is True
        with pytest.raises(IndexBusy):
            with IndexLock(empty_kb):
                pass
    assert is_index_running(empty_kb) is False
    with IndexLock(empty_kb):  # can be taken again
        pass


def _make_db(kb, state=None):
    conn = sqlite3.connect(kb.index_path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if state is not None:
        conn.execute("INSERT INTO meta VALUES ('index_state', ?)", (state,))
    conn.commit()
    conn.close()


def test_index_state_table(empty_kb):
    assert index_state(empty_kb) == "missing"
    with IndexLock(empty_kb):
        assert index_state(empty_kb) == "building"  # first run: no database yet
    _make_db(empty_kb, "running")
    assert index_state(empty_kb) == "interrupted"
    with IndexLock(empty_kb):
        assert index_state(empty_kb) == "building"


def test_index_state_complete_or_legacy_is_ready(empty_kb, tmp_path):
    _make_db(empty_kb, "complete")
    assert index_state(empty_kb) == "ready"
    legacy = init_kb(tmp_path / "legacy")
    _make_db(legacy)  # built before index_state existed
    assert index_state(legacy) == "ready"
