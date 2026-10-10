import weakref

import pytest

from genea.ingest.transcript import Segment
from genea.transcribe.audio import SAMPLE_RATE
from genea.transcribe.doublepass import NoSpeech, clean_boundaries, double_pass


class Samples:
    """Stands in for audio: slicing returns the (start, end) seconds, so tests see which span was cut."""

    def __init__(self, seconds):
        self.count = round(seconds * SAMPLE_RATE)

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        return (key.start / SAMPLE_RATE, key.stop / SAMPLE_RATE)


class Structure:
    def __init__(self, rows, detected=""):
        self.rows, self.detected_language = rows, detected

    def __call__(self, samples, language, on_segment):
        for start, end, text in self.rows:
            on_segment(Segment(start, end, text))


class Text:
    """Pass 2 stand-in: `answers` maps a cut span to the texts to return, in order; others return one 'T'."""

    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def __call__(self, samples, language, on_segment):
        self.calls.append((samples, language))
        for text in self.answers.get(samples, ["T"]):
            on_segment(Segment(0.0, 1.0, text))


def run(structure, text, seconds=30, language="sk", **hooks):
    out = []
    double_pass(Samples(seconds), language, lambda: structure, lambda: text, out.append, **hooks)
    return out


def test_boundaries_come_from_pass_one_and_text_from_pass_two():
    text = Text({(0.0, 10.3): ["Prvý."], (9.7, 25.3): ["Druhý.", "Veta."]})
    out = run(Structure([(0.0, 10.0, "aaaa"), (10.0, 25.0, "bbbb")]), text)
    assert out == [Segment(0.0, 10.0, "Prvý."), Segment(10.0, 25.0, "Druhý. Veta.")]
    assert [call[0] for call in text.calls] == [(0.0, 10.3), (9.7, 25.3)]  # padded, clamped at 0


def test_padding_is_clamped_at_the_end_of_the_recording():
    text = Text()
    run(Structure([(20.0, 30.0, "aaaa")]), text, seconds=30)
    assert text.calls[0][0] == (19.7, 30.0)


def test_short_segments_merge_into_a_neighbour_and_the_first_merges_forward():
    rows = [Segment(0.0, 0.5, "x"), Segment(0.5, 10.0, "y"), Segment(10.0, 10.4, "z"), Segment(10.4, 20.0, "w")]
    assert clean_boundaries(rows) == [Segment(0.0, 10.4, "x y z"), Segment(10.4, 20.0, "w")]


def test_a_recording_with_only_a_short_segment_keeps_it():
    assert clean_boundaries([Segment(0.0, 0.5, "x")]) == [Segment(0.0, 0.5, "x")]


def test_looped_and_empty_pass_one_segments_are_dropped_before_pass_two():
    loop = [(float(i), float(i + 2), "Dookola.") for i in range(0, 10, 2)]
    text = Text()
    out = run(Structure([*loop, (10.0, 14.0, "  "), (14.0, 20.0, "skutočná veta")]), text)
    assert len(text.calls) == 1 and out == [Segment(14.0, 20.0, "T")]


def test_pass_two_text_that_is_empty_or_loops_drops_the_segment_without_falling_back():
    said = []
    text = Text({(0.0, 10.3): [], (9.7, 20.3): ["Zase.", "Zase.", "Zase."]})
    out = run(Structure([(0.0, 10.0, "pass one text"), (10.0, 20.0, "more pass one text")]), text, say=said.append)
    assert out == [] and len(said) >= 2


def test_a_text_length_that_differs_a_lot_is_kept_but_flagged():
    said = []
    long_text = ("dlhý text " * 20).strip()
    out = run(Structure([(0.0, 10.0, "a short guess of thirty chars")]), Text({(0.0, 10.3): [long_text]}),
              say=said.append)
    assert out == [Segment(0.0, 10.0, long_text)]
    assert any(line.startswith("check:") and "0:00" in line for line in said)


def test_a_short_pass_one_text_is_not_compared():
    said = []
    run(Structure([(0.0, 10.0, "ab")]), Text({(0.0, 10.3): ["x" * 200]}), say=said.append)
    assert not any(line.startswith("check:") for line in said)


def test_nothing_usable_in_pass_one_stops_before_the_text_model_is_made():
    made = []
    loop = [(float(i), float(i + 2), "Dookola.") for i in range(0, 10, 2)]
    with pytest.raises(NoSpeech, match="no speech"):
        double_pass(Samples(30), "sk", lambda: Structure(loop), lambda: made.append("text"), lambda s: None)
    assert made == []


def test_the_structure_model_is_released_before_the_text_model_is_made():
    refs = []

    def make_structure():
        model = Structure([(0.0, 5.0, "aaaa")])
        refs.append(weakref.ref(model))
        return model

    def make_text():
        assert refs[0]() is None, "the structure model is still in memory"
        return Text()

    out = []
    double_pass(Samples(30), "sk", make_structure, make_text, out.append)
    assert len(out) == 1


def test_pass_two_reuses_the_language_pass_one_detected_unless_one_is_set():
    text = Text()
    run(Structure([(0.0, 5.0, "aaaa")], detected="sk"), text, language="")
    assert text.calls[0][1] == "sk"
    text = Text()
    run(Structure([(0.0, 5.0, "aaaa")], detected="cs"), text, language="sk")
    assert text.calls[0][1] == "sk"


def test_without_any_language_it_says_so_and_goes_on():
    said = []
    text = Text()
    out = run(Structure([(0.0, 5.0, "aaaa")]), text, language="", say=said.append)
    assert len(out) == 1 and text.calls[0][1] == ""
    assert any("language" in line and "--language" in line for line in said)


def test_the_hooks_fire_in_order():
    events = []
    structure = Structure([(0.0, 5.0, "aaaa"), (5.0, 9.0, "bbbb")])
    text = Text()
    double_pass(Samples(30), "sk", lambda: structure, lambda: (events.append("make_text"), text)[1],
                lambda s: events.append("segment"), on_structure=lambda s: events.append("structure"),
                on_text_start=lambda: events.append("text_start"))
    assert events == ["structure", "structure", "make_text", "text_start", "segment", "segment"]
