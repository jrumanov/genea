import pytest

from genea.config import KBNotFound, Settings, load_settings, resolve_kb_root


def test_defaults():
    s = load_settings(None)
    assert s == Settings()
    assert s.model == "ollama:qwen3:14b"
    assert s.embed_dim == 1024


def test_precedence_global_then_kb_then_overrides(isolated_home, tmp_path):
    (isolated_home / "config.toml").write_text(
        'model = "ollama:qwen3:8b"\nmax_tool_calls = 9\n', encoding="utf-8"
    )
    kb = tmp_path / "kb"
    (kb / ".genea").mkdir(parents=True)
    (kb / ".genea" / "config.toml").write_text(
        'model = "anthropic:claude-sonnet-5-5"\nexclude = ["private/**"]\n', encoding="utf-8"
    )
    s = load_settings(kb, {"max_tool_calls": 3})
    assert s.model == "anthropic:claude-sonnet-5-5"
    assert s.exclude == ("private/**",)
    assert s.max_tool_calls == 3


def test_unknown_keys_are_ignored(isolated_home):
    (isolated_home / "config.toml").write_text('nonsense = 1\nembed_dim = 8\n', encoding="utf-8")
    assert load_settings(None).embed_dim == 8


def test_resolve_explicit_path(tmp_path):
    assert resolve_kb_root(str(tmp_path)) == tmp_path.resolve()


def test_resolve_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GENEA_KB", str(tmp_path))
    assert resolve_kb_root(None) == tmp_path.resolve()


def test_resolve_named_default(isolated_home, tmp_path):
    (isolated_home / "config.toml").write_text(
        f"default_kb = \"novak\"\n[kbs]\nnovak = '{tmp_path}'\n", encoding="utf-8"
    )
    assert resolve_kb_root(None) == tmp_path.resolve()
    assert resolve_kb_root("novak") == tmp_path.resolve()


def test_resolve_errors(tmp_path):
    with pytest.raises(KBNotFound, match="No knowledge base"):
        resolve_kb_root(None)
    with pytest.raises(KBNotFound, match="not found"):
        resolve_kb_root(str(tmp_path / "missing"))


def test_transcribe_defaults_and_overrides(isolated_home, tmp_path):
    s = load_settings(None)
    assert s.transcribe_model == "medium"
    assert s.transcribe_language == ""  # empty: auto-detect
    kb = tmp_path / "kb"
    (kb / ".genea").mkdir(parents=True)
    (kb / ".genea" / "config.toml").write_text(
        'transcribe_model = "large-v3"\ntranscribe_language = "sk"\n', encoding="utf-8")
    s = load_settings(kb)
    assert (s.transcribe_model, s.transcribe_language) == ("large-v3", "sk")
