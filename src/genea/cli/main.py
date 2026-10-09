"""Console entry point: importing the command modules registers their commands."""

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from genea.cli import (  # noqa: E402,F401
    agentcmds,
    changecmds,
    indexcmds,
    kbcmds,
    mcpcmds,
    querycmds,
    summarycmds,
    transcribecmds,
)
from genea.cli.app import app  # noqa: E402

__all__ = ["app"]
