"""Thin wrapper around the ExifTool binary (one process per call)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


class ExifToolError(RuntimeError):
    """ExifTool could not be started or reported an error."""


def run_exiftool(args: list[str], exe: str = "exiftool", timeout: float = 120.0) -> str:
    """Run ExifTool once and return stdout.

    Arguments are passed through a UTF-8 argument file (-@) so non-ASCII values and
    file names survive on Windows. An argument file holds one argument per line,
    so line breaks inside values are replaced by spaces.
    """
    clean = [a.replace("\r", " ").replace("\n", " ") for a in args]
    fd, argfile = tempfile.mkstemp(suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(clean) + "\n")
        try:
            proc = subprocess.run(
                [exe, "-charset", "filename=utf8", "-@", argfile],
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExifToolError(f"could not run {exe}: {exc}") from exc
    finally:
        os.unlink(argfile)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise ExifToolError(message or f"{exe} exited with code {proc.returncode}")
    return proc.stdout.decode("utf-8")


def exiftool_version(exe: str = "exiftool") -> str | None:
    try:
        return run_exiftool(["-ver"], exe, timeout=20).strip() or None
    except ExifToolError:
        return None


def read_metadata(paths: list[Path], exe: str = "exiftool") -> list[dict]:
    """Metadata of each file, keys like 'XMP-dc:Title' (-G1), numeric values unformatted (-n)."""
    if not paths:
        return []
    return json.loads(run_exiftool(["-json", "-G1", "-n", *[str(p) for p in paths]], exe))
