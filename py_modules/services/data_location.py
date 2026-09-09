"""DataLocationService — the data-location question the plugin cannot answer itself.

Owns what the panel asks about the move that takes user data out of the plugin's
reach: whether a condition stands, what the candidates look like, and the user's
answer. The move itself happens at the next start, before the database is opened,
so nothing here copies anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    import logging

    from models.data_location import UserDataLocations

    from services.protocols import DataLocationStore

# What a standing condition is: the plugin declined to pick between two
# libraries, or a copy it did start could not finish.
CHOICE_PENDING = "choice"
MIGRATION_FAILED = "failed"


@dataclass(frozen=True)
class DataLocationServiceConfig:
    """Frozen wiring bundle handed to ``DataLocationService.__init__``.

    ``locations`` is what the start-up migration ended up with — the two
    directories this run reads and writes, and the condition it left standing.
    ``store`` is the seam the candidates are described through and the answer is
    recorded on; ``loop`` offloads both, because measuring a library is a walk of
    every file in it.
    """

    locations: UserDataLocations
    store: DataLocationStore
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class DataLocationService:
    """Reports the standing data-location condition and records the user's answer."""

    def __init__(self, *, config: DataLocationServiceConfig) -> None:
        self._locations = config.locations
        self._store = config.store
        self._loop = config.loop
        self._logger = config.logger

    def get_data_location_notice(self) -> dict[str, Any]:
        """Report the condition this start's migration left standing.

        Returns ``{"pending": bool, "kind": str | None, "message": str | None}``.
        ``kind`` is ``"choice"`` where two older installs both hold a library and
        the plugin declined to pick between them, or ``"failed"`` where a copy
        was attempted and did not finish; ``message`` carries what went wrong in
        the second case and is absent in the first, where nothing did.

        Read off what the start already decided, never re-probed: the answer must
        describe the directories this process is actually using, and a second
        reading of the disk could disagree with them.
        """
        if self._locations.choice_required:
            return {"pending": True, "kind": CHOICE_PENDING, "message": None}
        if self._locations.failure is not None:
            return {"pending": True, "kind": MIGRATION_FAILED, "message": self._locations.failure}
        return {"pending": False, "kind": None, "message": None}

    async def get_data_location_candidates(self) -> dict[str, Any]:
        """Describe the older locations the user is choosing between.

        Returns ``{"candidates": [...]}``, one entry per location the plugin
        knows about, each carrying its folder name, its path, whether it is on
        disk, its size in bytes and when it last changed. It cannot fail and has
        no failure shape: a location that has gone and a size that could not be
        measured are both stated on the entry, where the modal can render them,
        rather than collapsing the whole answer. Offloaded because measuring a
        location walks every file in it.
        """
        candidates = await self._loop.run_in_executor(None, self._store.describe_sources)
        return {"candidates": candidates}

    async def choose_data_location(self, source: str) -> dict[str, Any]:
        """Record which older location the next start copies from.

        Returns ``{"success": True}``. The choice is recorded rather than carried
        out: this plugin is running from one of the two candidates with its
        database open, and copying a live SQLite file risks a torn copy — so the
        answer is written where the next start looks for it, and the condition
        stands until that start has acted on it.
        """
        try:
            await self._loop.run_in_executor(None, self._store.record_answer, source)
        except ValueError as e:
            return {"success": False, "reason": "unknown_source", "message": str(e)}
        except OSError as e:
            self._logger.warning(f"Could not record the data-location choice: {e}")
            return {"success": False, "reason": "write_failed", "message": f"Could not record your choice: {e}"}
        return {"success": True}
