import pytest

from genea.kb import KnowledgeBase, init_kb

SUBDIRS = ["notes", "changes/pending", "changes/applied", "changes/rejected",
           "changes/stale", "backups", "logs", "exports", "summaries"]


def test_init_creates_layout(tmp_path):
    kb = init_kb(tmp_path)
    for sub in SUBDIRS:
        assert (tmp_path / ".genea" / sub).is_dir(), sub
    assert kb.meta_dir == tmp_path.resolve() / ".genea"
    assert (kb.meta_dir / "config.toml").is_file()


def test_init_keeps_existing_config(tmp_path):
    init_kb(tmp_path)
    cfg = tmp_path / ".genea" / "config.toml"
    cfg.write_text('model = "ollama:qwen3:8b"\n', encoding="utf-8")
    kb = init_kb(tmp_path)
    assert cfg.read_text(encoding="utf-8") == 'model = "ollama:qwen3:8b"\n'
    assert kb.settings.model == "ollama:qwen3:8b"


def test_resolve_inside_and_rel(tmp_path):
    kb = init_kb(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("x", encoding="utf-8")
    assert kb.resolve_inside("docs/a.txt") == (tmp_path / "docs" / "a.txt").resolve()
    assert kb.rel(tmp_path / "docs" / "a.txt") == "docs/a.txt"
    with pytest.raises(ValueError, match="outside"):
        kb.resolve_inside("../secret.txt")
    with pytest.raises(ValueError, match=".genea"):
        kb.resolve_inside(".genea/config.toml")


def test_open(tmp_path):
    kb = KnowledgeBase.open(str(tmp_path))
    assert kb.root == tmp_path.resolve()
    assert kb.notes_dir.is_dir()
