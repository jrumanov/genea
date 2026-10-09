"""Whisper-style transcripts for tests."""

from __future__ import annotations

from pathlib import Path

NAME = "2022_05_10-fabik_lubica_rozhovor_1.txt"


def whisper(rows) -> str:
    """Whisper output as the real files have it: start, end and ' text', separated by tabs."""
    return "".join(f"{start:.6f}\t{end:.6f}\t {text}\n" for start, end, text in rows)


def sample_rows(count: int = 60) -> list[tuple[float, float, str]]:
    """`count` segments, one every 15 s; the one at 14:00 (840 s) mentions Prískupice."""
    rows = [(i * 15.0, i * 15.0 + 14.0,
             f"Veta číslo {i} o rodine a dome na dedine, ktorú si pamätám z detstva a z rozprávania starých rodičov.")
            for i in range(count)]
    rows[56] = (840.0, 854.0, "V roku 1954 sa zobrali a nasťahovali sa do Prískupíc k babke.")
    return rows


def write_transcript(root: Path, name: str = NAME, rows=None) -> Path:
    path = root / "docs" / name
    path.write_text(whisper(sample_rows() if rows is None else rows), encoding="utf-8")
    return path
