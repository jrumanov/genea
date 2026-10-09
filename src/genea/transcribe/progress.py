"""The live progress line of the transcribe commands: one line per decoded segment."""

from __future__ import annotations

import time
from collections.abc import Callable

from genea.ingest.transcript import format_time


def summary_line(count: int, seconds: float, path) -> str:
    return f"{count} segments written to {path} in {seconds / 60:.1f}m"


class Progress:
    """`total_s` is the length of the audio being transcribed; `offset_s` is where that audio starts in
    the recording, so the time shown matches the transcript file and not the position inside a cut."""

    def __init__(self, total_s: float, offset_s: float = 0.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.total = max(total_s, 0.001)
        self.offset = offset_s
        self._clock = clock
        self._started = clock()
        self.count = 0

    def elapsed(self) -> float:
        return self._clock() - self._started

    def line(self, end_s: float, text: str) -> str:
        self.count += 1
        done = min(max(end_s / self.total, 0.0), 1.0)
        elapsed = self.elapsed()
        eta = "?" if done < 0.01 else f"{(elapsed / done - elapsed) / 60:.1f}m"
        return f"[{done * 100:5.1f}%] {format_time(end_s + self.offset)}  elapsed {elapsed / 60:.1f}m  eta {eta}  {text}"

    def summary(self, path) -> str:
        return summary_line(self.count, self.elapsed(), path)
