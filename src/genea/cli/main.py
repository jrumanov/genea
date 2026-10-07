"""Console entry point: importing the command modules registers their commands."""

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from genea.cli import agentcmds, changecmds, indexcmds, kbcmds, querycmds  # noqa: E402,F401
from genea.cli.app import app  # noqa: E402

__all__ = ["app"]
