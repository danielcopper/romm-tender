"""The end-of-run stale removal's verdict on each bound row no unit of the run returned.

Shared by :class:`~services.library.sync_orchestrator.SyncOrchestrator`'s
preview and its apply's finalize, so the removals a preview promises are the
ones its apply makes. The preview also builds its stale rows and synced group
keys here (:func:`stale_rows_of`, :func:`preview_group_keys`) and reads
the name of a kept row's platform (:func:`with_kept_platform_names`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.sync_diff import StaleRow, group_keys_of, settle_asked_rows, sort_stale_rows

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping

    from domain.rom import GoneEvidence
    from domain.sync_diff import RomAnswer

    # ``LibraryFetcher.ask_romm_for_roms``: one exact-id question per ROM.
    AskRommFn = Callable[[Collection[int]], Awaitable[dict[int, RomAnswer]]]
    # ``LocalLibraryReader.do_read_kept_platform_names``, offloaded by the caller.
    ReadKeptNamesFn = Callable[[Collection[str]], Awaitable[dict[str, str]]]


def stale_rows_of(stale_rom_ids: Iterable[int], registry: Mapping[str, Mapping[str, Any]]) -> list[StaleRow]:
    """A :class:`StaleRow` per *stale_rom_ids* entry, off the preview's registry (``do_read_preview_baseline``)."""
    rows: list[StaleRow] = []
    for rom_id in stale_rom_ids:
        reg = registry[str(rom_id)]
        rows.append(
            StaleRow(
                rom_id, reg["app_id"], reg["platform_slug"], reg.get("delivered_through"), reg.get("sibling_group_key")
            )
        )
    return rows


def preview_group_keys(
    synced_rom_ids: Iterable[int], resident_keys: Mapping[int, str], fetched: Iterable[Mapping[str, Any]]
) -> frozenset[str]:
    """The preview's synced group keys: *resident_keys* with the keys stamped on its *fetched* ROMs laid over them.

    The apply reads the same keys back off the rows its commits upserted
    (``do_read_resident_group_keys``); the preview writes nothing, so its fresh
    keys exist only in memory.
    """
    fresh = {rom["id"]: rom["sibling_group_key"] for rom in fetched if rom.get("sibling_group_key")}
    return group_keys_of(synced_rom_ids, {**resident_keys, **fresh})


async def settle_stale_rows(
    rows: Iterable[StaleRow],
    *,
    ask_romm: AskRommFn,
    logger: logging.Logger,
    read_platform_slugs: Collection[str],
    listed_platform_slugs: Collection[str] | None,
    collection_listed_rom_ids: Collection[int],
    synced_group_keys: Collection[str],
) -> tuple[list[StaleRow], list[StaleRow], dict[int, GoneEvidence | None]]:
    """``(remove, kept_in_platform_collections, gone_marks)`` for the stale *rows*.

    Sorts them by :func:`sort_stale_rows`, asks RomM once about the ones it
    cannot decide, settles those by :func:`settle_asked_rows`, and answers the
    marks by :meth:`~domain.sync_diff.StaleSort.gone_from_romm_marks`. A row RomM
    was asked about and did not return — a proven 404, or an answer that
    established nothing — is in neither list: its shortcut stays, and being kept
    does not file it in its platform's collection; only the platform-groups
    setting can. Neither list holds a row whose game the run synced
    (*synced_group_keys*) either, though RomM is asked about it where it would
    be asked about any other row.
    """
    sort = sort_stale_rows(
        rows,
        read_platform_slugs=read_platform_slugs,
        listed_platform_slugs=listed_platform_slugs,
        collection_listed_rom_ids=collection_listed_rom_ids,
        synced_group_keys=synced_group_keys,
    )
    asked = [*sort.ask, *sort.ask_only]
    answers = await ask_romm([row.rom_id for row in asked]) if asked else {}
    asked_remove, asked_keep = settle_asked_rows(sort.ask, answers)
    if sort.keep or asked_keep:
        logger.info(
            f"Stale removal: keeping {len(sort.keep)} bound version(s) gone from RomM in their platform's "
            f"collection and {len(asked_keep)} reached through a collection RomM did not confirm present"
        )
    return sort.remove + asked_remove, sort.keep, sort.gone_from_romm_marks(answers, read_platform_slugs)


async def with_kept_platform_names(
    kept: list[StaleRow], live: dict[str, str], *, read_kept_names: ReadKeptNamesFn
) -> dict[str, str]:
    """*live*, plus the cached name of each platform outside it that a *kept* row is on, for the preview.

    Must answer the names the apply's finalize carries forward
    (``SyncReporter._names_with_bound_platforms_kept``). *read_kept_names* is
    asked only when such a platform exists.
    """
    outside = {row.platform_slug for row in kept} - live.keys()
    if not outside:
        return live
    return {**await read_kept_names(outside), **live}
