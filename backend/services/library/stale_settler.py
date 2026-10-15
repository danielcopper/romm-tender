"""Which stale rows a library sync removes, and under which names the rest keep their platform's collection.

A stale row is a bound ROM the run did not deliver. What becomes of one is
decided by :func:`domain.sync_diff.sort_stale_rows` and, for the rows it cannot
decide, by one exact-id question to RomM settled by
:func:`domain.sync_diff.settle_asked_rows`. The preview and the apply both ask
here, so the removals a preview promises are the ones its apply makes, and both
name a kept row's platform collection the way the apply's finalize does.

Nothing here writes: RomM is asked through the fetcher, this device's record
through the local library reader, and the removal itself is the orchestrator's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.sync_diff import settle_asked_rows, sort_stale_rows

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Collection, Iterable

    from domain.sync_diff import StaleRow
    from services.library.fetcher import LibraryFetcher
    from services.library.local_library_reader import LocalLibraryReader


@dataclass(frozen=True)
class StaleSettlerConfig:
    """Frozen wiring bundle handed to ``StaleSettler.__init__``.

    Holds the loop the local reads are offloaded through, the logger, the
    :class:`LibraryFetcher` peer RomM is asked through, and the
    :class:`LocalLibraryReader` peer the cached platform names are read through.
    """

    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    fetcher: LibraryFetcher
    local_library_reader: LocalLibraryReader


class StaleSettler:
    """Settles a run's stale rows into removals and rows kept in their platform's collection."""

    def __init__(self, *, config: StaleSettlerConfig) -> None:
        self._loop = config.loop
        self._logger = config.logger
        self._fetcher = config.fetcher
        self._local_library_reader = config.local_library_reader

    async def settle(
        self,
        rows: Iterable[StaleRow],
        *,
        read_platform_slugs: Collection[str],
        listed_platform_slugs: Collection[str] | None,
        collection_listed_rom_ids: Collection[int],
    ) -> tuple[list[StaleRow], list[StaleRow]]:
        """``(remove, kept_in_platform_collections)`` for the stale *rows*.

        Sorts them by :func:`sort_stale_rows`, asks RomM once about the ones it
        cannot decide, and settles those by :func:`settle_asked_rows`.
        """
        sort = sort_stale_rows(
            rows,
            read_platform_slugs=read_platform_slugs,
            listed_platform_slugs=listed_platform_slugs,
            collection_listed_rom_ids=collection_listed_rom_ids,
        )
        answers = await self._fetcher.ask_romm_for_roms([row.rom_id for row in sort.ask]) if sort.ask else {}
        asked_remove, asked_keep = settle_asked_rows(sort.ask, answers)
        if sort.keep or asked_keep:
            self._logger.info(
                f"Stale removal: keeping {len(sort.keep)} bound version(s) gone from RomM in their platform's "
                f"collection and {len(asked_keep)} reached through a collection RomM did not confirm present"
            )
        return sort.remove + asked_remove, sort.keep

    async def with_kept_platform_names(self, kept: list[StaleRow], live: dict[str, str]) -> dict[str, str]:
        """*live*, plus the cached name of each platform outside it that a *kept* row is on, for the preview.

        Such a platform is one RomM no longer lists, and its Steam collection
        keeps that name: the apply's finalize carries it forward
        (``SyncReporter._names_with_bound_platforms_kept``), so the preview
        counts the collection under the same name.
        """
        outside = {row.platform_slug for row in kept} - live.keys()
        if not outside:
            return live
        kept_names = await self._loop.run_in_executor(
            None, self._local_library_reader.do_read_kept_platform_names, outside
        )
        return {**kept_names, **live}
