"""Shared state of an index run, readable by other processes (`genea status`, `genea mcp`).

Whether a run is active comes from an OS lock on .genea/index.lock, held by the indexer for the whole
run and released by the OS even when it crashes. How far it got comes from .genea/index.progress.json.
Whether the last run finished comes from the `index_state` row in the index's `meta` table.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Literal

from genea.index.db import connect_readonly
from genea.kb import KnowledgeBase

IndexState = Literal["missing", "building", "interrupted", "ready"]

LOCK_FILE = "index.lock"
PROGRESS_FILE = "index.progress.json"


class IndexBusy(RuntimeError):
    """Another process is indexing this knowledge base."""

    def __init__(self) -> None:
        super().__init__("Another `genea index` is already running for this knowledge base.")


class IndexInUse(RuntimeError):
    """The index file cannot be deleted for a rebuild because another program has it open."""

    def __init__(self) -> None:
        super().__init__("The index is open in another program (e.g. Claude via `genea mcp`). "
                         "Close it and run `genea index --rebuild` again.")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 90:
        return f"~{minutes} min"
    return f"~{minutes // 60} h {minutes % 60} min"


@dataclass
class IndexProgress:
    pid: int
    started_at: str
    phase: Literal["scanning", "indexing", "linking"]
    phase_started_at: str
    updated_at: str
    done: int = 0
    total: int = 0  # 0 while scanning: the number of files to index is not known yet
    current: str | None = None

    def eta_seconds(self, now: datetime | None = None) -> float | None:
        if self.phase != "indexing" or self.done <= 0 or self.done >= self.total:
            return None
        elapsed = ((now or datetime.now()) - datetime.fromisoformat(self.phase_started_at)).total_seconds()
        return max(elapsed, 0.0) / self.done * (self.total - self.done)

    def describe(self, now: datetime | None = None) -> str:
        if self.phase == "scanning":
            return f"scanning files ({self.done} found)"
        if self.phase == "linking":
            return "linking people to documents and photos (almost done)"
        text = f"{self.done} / {self.total} files ({self.done * 100 // max(self.total, 1)} %)"
        eta = self.eta_seconds(now)
        if eta is not None:
            text += f", ETA {_duration(eta)}"
        if self.current:
            text += f", now: {self.current}"
        return text


def write_progress(kb: KnowledgeBase, progress: IndexProgress) -> None:
    """Atomic: readers see the old or the new file, never half of one."""
    path = kb.meta_dir / PROGRESS_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(progress), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_progress(kb: KnowledgeBase) -> IndexProgress | None:
    """None when there is no usable progress file; a broken one is never an error."""
    try:
        data = json.loads((kb.meta_dir / PROGRESS_FILE).read_text(encoding="utf-8"))
        return IndexProgress(**{f.name: data[f.name] for f in fields(IndexProgress) if f.name in data})
    except (OSError, ValueError, TypeError):
        return None


def clear_progress(kb: KnowledgeBase) -> None:
    (kb.meta_dir / PROGRESS_FILE).unlink(missing_ok=True)


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class IndexLock:
    """Exclusive lock for an index run. `wait` retries briefly, so a status probe that holds the lock
    for a moment does not make a starting run fail. The lock file itself is never deleted."""

    def __init__(self, kb: KnowledgeBase, wait: float = 0.0) -> None:
        self.path = kb.meta_dir / LOCK_FILE
        self.wait = wait
        self._fd: int | None = None

    def __enter__(self) -> IndexLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        deadline = time.monotonic() + self.wait
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise IndexBusy() from None
                time.sleep(0.05)
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                _unlock(fd)
            finally:
                os.close(fd)


def is_index_running(kb: KnowledgeBase) -> bool:
    if not (kb.meta_dir / LOCK_FILE).exists():
        return False
    try:
        with IndexLock(kb):
            return False
    except IndexBusy:
        return True
    except OSError:  # e.g. .genea not writable: assume nobody is indexing, so readers still work
        return False


def _stored_state(kb: KnowledgeBase) -> str | None:
    try:
        conn = connect_readonly(kb.index_path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'index_state'").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def index_state(kb: KnowledgeBase) -> IndexState:
    running = is_index_running(kb)
    if not kb.index_path.exists():
        return "building" if running else "missing"
    if running:
        return "building"
    return "interrupted" if _stored_state(kb) == "running" else "ready"
