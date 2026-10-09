"""Find the audio that belongs to a transcript and cut parts of it for whisper.cpp."""

from __future__ import annotations

from pathlib import Path

AUDIO_EXTS = (".m4a", ".mp3", ".wav", ".mp4", ".ogg", ".flac")
SAMPLE_RATE = 16000
EXTRA_HINT = "Audio support is not installed: run `uv sync --extra transcribe`."


class AudioUnavailable(RuntimeError):
    """The audio cannot be read (the extra is missing, or the file is damaged)."""


def find_audio(transcript: Path) -> Path | None:
    """The recording next to a transcript: same stem, an audio extension in lower or upper case."""
    for ext in AUDIO_EXTS:
        for spelling in (ext, ext.upper()):  # case-sensitive file systems tell .mp3 and .MP3 apart
            candidate = transcript.with_suffix(spelling)
            if candidate.is_file():
                return candidate
    return None


def audio_duration(path: Path) -> float | None:
    """Length in seconds; None without the `av` package or when the file cannot be read."""
    try:
        import av
    except ImportError:
        return None
    try:
        with av.open(str(path)) as container:
            return float(container.duration) / av.time_base if container.duration else None
    except Exception:  # noqa: BLE001 - any decoder failure means "unknown length"
        return None


def load_span(path: Path, start: float = 0.0, end: float | None = None):
    """Mono 16 kHz float32 samples of [start, end) seconds; `end=None` runs to the end of the audio."""
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise AudioUnavailable(EXTRA_HINT) from exc
    try:
        chunks = []
        first_time = None
        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            if start > 0:
                container.seek(int(start * av.time_base))
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                frame_time = frame.time or 0.0
                if frame_time + frame.samples / frame.sample_rate <= start:
                    continue  # the seek lands before the start; skip whole frames that end before it
                if end is not None and frame_time >= end:
                    break
                if first_time is None:
                    first_time = frame_time
                chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    except AudioUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - av raises many error types
        raise AudioUnavailable(f"Could not read {path.name}: {exc}") from exc
    # One copy: the chunks are float32 already ("flt"), and the list is dropped once they are joined.
    samples = np.concatenate(chunks, dtype="float32") if chunks else np.zeros(0, dtype="float32")
    chunks.clear()
    decoded_from = start if first_time is None else first_time  # a first frame at 0.0 is a real time
    samples = samples[max(0, round((start - decoded_from) * SAMPLE_RATE)):]
    if end is not None:
        samples = samples[:round((end - start) * SAMPLE_RATE)]
    return samples
