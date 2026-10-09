from genea.transcribe.progress import Progress, summary_line


class Clock:
    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


def test_line_has_the_documented_format():
    progress = Progress(total_s=100.0, offset_s=2297.0, clock=Clock(0.0, 660.0))
    assert progress.line(98.1, "To sú kokosové.") == "[ 98.1%] 39:55  elapsed 11.0m  eta 0.2m  To sú kokosové."


def test_eta_is_unknown_below_one_percent():
    progress = Progress(total_s=100.0, clock=Clock(0.0, 5.0))
    assert progress.line(0.5, "Dobre.") == "[  0.5%] 0:00  elapsed 0.1m  eta ?  Dobre."


def test_time_is_on_the_recordings_timeline_and_percent_is_capped():
    progress = Progress(total_s=10.0, offset_s=3600.0, clock=Clock(0.0, 60.0))
    assert progress.line(12.0, "x").startswith("[100.0%] 1:00:12")


def test_count_and_summary():
    progress = Progress(total_s=10.0, clock=Clock(0.0, 1.0, 2.0, 120.0))
    progress.line(1.0, "a")
    progress.line(2.0, "b")
    assert progress.count == 2
    assert progress.summary("out.txt") == "2 segments written to out.txt in 2.0m"
    assert summary_line(1, 30.0, "x.txt") == "1 segments written to x.txt in 0.5m"
