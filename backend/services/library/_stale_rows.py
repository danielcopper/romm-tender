"""The end-of-run stale removal's verdict on each bound row no unit of the run returned.

Shared by :class:`~services.library.sync_orchestrator.SyncOrchestrator`'s
preview and its apply's finalize, so the removals a preview promises are the
ones its apply makes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.sync_diff import settle_asked_rows, sort_stale_rows

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable, Collection, Iterable

    from domain.rom import GoneEvidence
    from domain.sync_diff import RomAnswer, StaleRow

    # ``LibraryFetcher.ask_romm_for_roms``: one exact-id question per ROM.
    AskRommFn = Callable[[Collection[int]], Awaitable[dict[int, RomAnswer]]]


async def settle_stale_rows(
    rows: Iterable[StaleRow],
    *,
    ask_romm: AskRommFn,
    logger: logging.Logger,
    read_platform_slugs: Collection[str],
    listed_platform_slugs: Collection[str] | None,
    collection_listed_rom_ids: Collection[int],
) -> tuple[list[StaleRow], list[StaleRow], dict[int, GoneEvidence | None]]:
    """``(remove, kept_in_platform_collections, gone_marks)`` for the stale *rows*.

    Sorts them by :func:`sort_stale_rows`, asks RomM once about the ones it
    cannot decide, settles those by :func:`settle_asked_rows`, and marks by
    :meth:`~domain.sync_diff.StaleSort.gone_from_romm_marks`, which only the
    apply writes.
    """
    sort = sort_stale_rows(
        rows,
        read_platform_slugs=read_platform_slugs,
        listed_platform_slugs=listed_platform_slugs,
        collection_listed_rom_ids=collection_listed_rom_ids,
    )
    answers = await ask_romm([row.rom_id for row in sort.ask]) if sort.ask else {}
    asked_remove, asked_keep = settle_asked_rows(sort.ask, answers)
    if sort.keep or asked_keep:
        logger.info(
            f"Stale removal: keeping {len(sort.keep)} bound version(s) gone from RomM in their platform's "
            f"collection and {len(asked_keep)} reached through a collection RomM did not confirm present"
        )
    return sort.remove + asked_remove, sort.keep, sort.gone_from_romm_marks(answers, read_platform_slugs)
