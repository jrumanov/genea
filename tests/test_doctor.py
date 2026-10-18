from genea import doctor
from genea.config import Settings


def _patch(monkeypatch, exif="13.33", models=("qwen3:14b", "bge-m3:latest")):
    monkeypatch.setattr(doctor, "exiftool_version", lambda exe: exif)
    monkeypatch.setattr(doctor, "ollama_models", lambda url: None if models is None else list(models))
    monkeypatch.setattr(doctor, "dna_checks", lambda: [])


def test_all_good(monkeypatch):
    _patch(monkeypatch)
    checks = doctor.run_checks(Settings())
    assert [c.name for c in checks] == ["sqlite-vec", "ExifTool", "Ollama", "Embedding model", "Chat model"]
    assert all(c.ok for c in checks), checks


def test_missing_chat_model_suggests_pull(monkeypatch):
    _patch(monkeypatch, models=("bge-m3",))
    chat = {c.name: c for c in doctor.run_checks(Settings())}["Chat model"]
    assert not chat.ok
    assert chat.fix == "ollama pull qwen3:14b"


def test_ollama_down_and_no_exiftool(monkeypatch):
    _patch(monkeypatch, exif=None, models=None)
    checks = {c.name: c for c in doctor.run_checks(Settings())}
    assert not checks["ExifTool"].ok
    assert not checks["Ollama"].ok
    assert "Embedding model" not in checks


def test_cloud_chat_model_is_not_checked_in_ollama(monkeypatch):
    _patch(monkeypatch, models=("bge-m3",))
    checks = {c.name: c for c in doctor.run_checks(Settings(model="anthropic:claude-sonnet-5-5"))}
    assert checks["Chat model"].ok
    assert "cloud" in checks["Chat model"].detail


def test_dna_checks_point_to_setup(monkeypatch):
    from genea.dna import runtime

    monkeypatch.setattr(runtime, "find_java", lambda run=None: runtime.JavaChoice(None, "none", "no Java 11+"))
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: None)
    checks = {c.name: c for c in doctor.dna_checks()}
    assert not checks["Java (DNA)"].ok and "genea dna setup" in checks["Java (DNA)"].fix
    assert not checks["Haplogrep"].ok and checks["Haplogrep"].fix == "genea dna setup"
    assert checks["yhaplo"].fix == "uv sync --extra dna"


def test_dna_checks_hidden_without_the_extra(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    assert doctor.dna_checks() == []
