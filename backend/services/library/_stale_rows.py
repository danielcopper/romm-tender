"""The end-of-run stale removal's verdict on each bound row no unit of the run returned.

Shared by :class:`~services.library.sync_preview.SyncPreview` and
:class:`~services.library.sync_orchestrator.SyncOrchestrator`'s finalize, so
the removals a preview promises are the ones its apply makes. The preview also
builds its stale rows and synced group keys here (:func:`stale_rows_of`,
:func:`preview_group_keys`) and reads the names its platform-collection diff
files by and its per-platform table labels with
(:func:`with_kept_platform_names`).
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
    established nothing — is in neither list: its shortcut stays, and it is
    filed in its platform's collection only where the platform-groups setting
    files every bound row. Neither list holds a row whose game the run synced
    (*synced_group_keys*) on a platform the run did not read either, though
    RomM is asked about it where it would be asked about any other row.
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
    platform_slugs: Iterable[str], listed: dict[str, str], *, read_kept_names: ReadKeptNamesFn
) -> dict[str, str]:
    """*listed*, plus the cached name of each of *platform_slugs* outside it the cache holds one for, for the preview.

    *listed* is every listed platform's name, from the run's own listing.

    *platform_slugs* must cover every platform the preview's
    platform-collection diff and per-platform table name. Must answer, for each, the name the apply's
    finalize carries forward
    (``SyncReporter._names_with_bound_platforms_kept``). *read_kept_names* is
    asked only when such a platform exists.
    """
    outside = set(platform_slugs) - listed.keys()
    if not outside:
        return listed
    return {**await read_kept_names(outside), **listed}
