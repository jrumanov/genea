"""A knowledge base is a root folder; everything genea generates lives in <root>/.genea/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from genea.config import Settings, load_settings, resolve_kb_root

DEFAULT_KB_CONFIG = """\
# genea settings for this knowledge base. Everything is optional; values here
# override the global ~/.genea/config.toml.
# model = "ollama:qwen3:14b"          # or "anthropic:claude-sonnet-5-5"
# exclude = ["private/**", "*.tmp"]   # glob patterns relative to this folder
"""

SUBDIRS = ("notes", "changes/pending", "changes/applied", "changes/rejected",
           "changes/stale", "backups", "logs", "exports", "summaries")


@dataclass(frozen=True)
class KnowledgeBase:
    root: Path
    settings: Settings

    @classmethod
    def open(cls, kb: str | None, overrides: dict | None = None) -> KnowledgeBase:
        root = resolve_kb_root(kb)
        instance = cls(root, load_settings(root, overrides))
        instance.ensure_dirs()
        return instance

    @property
    def meta_dir(self) -> Path:
        return self.root / ".genea"

    @property
    def index_path(self) -> Path:
        return self.meta_dir / "index.db"

    @property
    def notes_dir(self) -> Path:
        return self.meta_dir / "notes"

    @property
    def changes_dir(self) -> Path:
        return self.meta_dir / "changes"

    @property
    def backups_dir(self) -> Path:
        return self.meta_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.meta_dir / "logs"

    @property
    def exports_dir(self) -> Path:
        return self.meta_dir / "exports"

    @property
    def summaries_dir(self) -> Path:
        return self.meta_dir / "summaries"

    @property
    def summary_work_dir(self) -> Path:
        """Transcript sections for an outside summarizer and its answers (`genea summaries`)."""
        return self.meta_dir / "summary-work"

    def ensure_dirs(self) -> None:
        for sub in SUBDIRS:
            (self.meta_dir / sub).mkdir(parents=True, exist_ok=True)

    def rel(self, path: Path) -> str:
        """Path relative to the root, with forward slashes."""
        return Path(path).resolve().relative_to(self.root).as_posix()

    def resolve_inside(self, rel_path: str) -> Path:
        """Resolve a user/agent supplied path; refuse anything outside the root or inside .genea."""
        path = (self.root / rel_path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Path is outside the knowledge base: {rel_path}")
        if path.is_relative_to(self.meta_dir):
            raise ValueError(f"Path is inside .genea and not readable: {rel_path}")
        return path


def init_kb(root: Path) -> KnowledgeBase:
    root = Path(root).resolve()
    kb = KnowledgeBase(root, load_settings(root))
    kb.ensure_dirs()
    config = kb.meta_dir / "config.toml"
    if not config.exists():
        config.write_text(DEFAULT_KB_CONFIG, encoding="utf-8")
    return KnowledgeBase(root, load_settings(root))
