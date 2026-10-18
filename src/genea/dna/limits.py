"""How many SNPs a model may read: a per-call cap and a budget per agent or MCP session."""

from __future__ import annotations

from genea.tools.base import ToolError

MAX_RSIDS_PER_CALL = 20


class RsidBudget:
    """rsIDs one session may look up. The per-call cap alone would let a model page the genotype out 20 at a time."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self.used = 0

    @property
    def left(self) -> int:
        return self.limit - self.used

    def take(self, n: int) -> None:
        if n > self.left:
            raise ToolError(f"Privacy limit: this session may look up {self.limit} rsIDs in total and {self.left} "
                            "are left. The limit is deliberate; do not split or repeat requests to get around it. "
                            "The user can raise dna_session_rsids in the knowledge base's config.toml.")
        self.used += n
