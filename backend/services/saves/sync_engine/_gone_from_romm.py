"""The games a whole-library sweep found RomM no longer has, and the clause its answer names them in.

How a sync learns that RomM no longer has a game is
``docs/architecture/save-file-sync-architecture.md``'s, "A game RomM no longer has".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.saves._messages import GONE_FROM_ROMM_ONE, GONE_FROM_ROMM_SEVERAL
from services.saves.sync_engine._result_message import joined_names


@dataclass
class GoneRoms:
    """The games a run over several ROMs found RomM no longer has, in the order it reached them."""

    names: list[str] = field(default_factory=list)

    def record(self, name: str) -> None:
        """Record that the run found RomM no longer has the game called *name*."""
        self.names.append(name)

    def annotate(self, message: str) -> str:
        """*message*, with a clause naming the games RomM no longer has where there were any."""
        if not self.names:
            return message
        if len(self.names) == 1:
            return f"{message}; {GONE_FROM_ROMM_ONE.format(name=self.names[0])}"
        return f"{message}; {GONE_FROM_ROMM_SEVERAL.format(names=joined_names(self.names))}"
