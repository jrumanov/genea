import math
import struct
import sys
import wave
from types import SimpleNamespace

import pytest

from genea.transcribe.audio import EXTRA_HINT, SAMPLE_RATE, AudioUnavailable, audio_duration, find_audio, load_span


def write_wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(seconds * SAMPLE_RATE))))


def test_find_audio_looks_for_a_sibling_with_the_same_stem(tmp_path):
    transcript = tmp_path / "2023_x.txt"
    transcript.write_text("x", encoding="utf-8")
    assert find_audio(transcript) is None
    (tmp_path / "2023_x.mp3").write_bytes(b"x")
    assert find_audio(transcript) == tmp_path / "2023_x.mp3"
    (tmp_path / "2023_x.m4a").write_bytes(b"x")
    assert find_audio(transcript) == tmp_path / "2023_x.m4a"  # .m4a is searched first


def test_duration_and_span(tmp_path):
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    path = tmp_path / "a.wav"
    write_wav(path, 3.0)
    assert audio_duration(path) == pytest.approx(3.0, abs=0.05)
    whole = load_span(path)
    assert whole.dtype.name == "float32" and abs(len(whole) - 3 * SAMPLE_RATE) <= SAMPLE_RATE // 10
    part = load_span(path, 1.0, 2.0)
    assert abs(len(part) - SAMPLE_RATE) <= SAMPLE_RATE // 10
    tail = load_span(path, 2.0)
    assert abs(len(tail) - SAMPLE_RATE) <= SAMPLE_RATE // 10


def test_unreadable_audio_has_no_duration_and_fails_to_load(tmp_path):
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    bad = tmp_path / "bad.m4a"
    bad.write_bytes(b"not audio")
    assert audio_duration(bad) is None
    with pytest.raises(AudioUnavailable):
        load_span(bad)


def test_find_audio_finds_upper_case_extensions(tmp_path):
    transcript = tmp_path / "2023_x.txt"
    transcript.write_text("x", encoding="utf-8")
    (tmp_path / "2023_x.MP3").write_bytes(b"x")
    found = find_audio(transcript)
    assert found is not None and found.is_file() and found.suffix.lower() == ".mp3"


def test_without_av_there_is_no_duration_and_loading_explains_the_extra(tmp_path, monkeypatch):
    path = tmp_path / "a.wav"
    write_wav(path, 0.1)
    monkeypatch.setitem(sys.modules, "av", None)  # makes `import av` fail like a missing extra
    assert audio_duration(path) is None
    with pytest.raises(AudioUnavailable, match="uv sync --extra transcribe") as err:
        load_span(path)
    assert str(err.value) == EXTRA_HINT


def fake_av(np, frames):
    """An `av` stand-in that decodes `frames` (time, samples at SAMPLE_RATE) and records seeks."""
    seeks = []

    class Frame:
        def __init__(self, time, count):
            self.time, self.samples, self.sample_rate = time, count, SAMPLE_RATE
            self.data = np.full(count, time, dtype="float32")  # every sample holds its frame's start time

        def to_ndarray(self):
            return self.data.reshape(1, -1)

    class Container:
        streams = SimpleNamespace(audio=["stream"])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def seek(self, offset):
            seeks.append(offset)

        def decode(self, stream):
            return iter([Frame(time, count) for time, count in frames])

    class Resampler:
        def __init__(self, **kwargs):
            pass

        def resample(self, frame):
            return [] if frame is None else [frame]

    module = SimpleNamespace(open=lambda path: Container(), time_base=1_000_000, AudioResampler=Resampler)
    return module, seeks


def test_a_first_frame_at_zero_seconds_is_trimmed_to_the_start(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    module, seeks = fake_av(np, [(0.0, SAMPLE_RATE), (1.0, SAMPLE_RATE), (2.0, SAMPLE_RATE)])
    monkeypatch.setitem(sys.modules, "av", module)
    samples = load_span(tmp_path / "a.wav", 0.5, 2.0)  # the seek lands at 0.0, not at 0.5
    assert seeks == [500_000]
    assert len(samples) == round(1.5 * SAMPLE_RATE)
    assert samples[0] == 0.0 and samples[SAMPLE_RATE // 2] == 1.0  # it starts half way into the first frame
