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

from typing import TYPE_CHECKING, Any, NamedTuple

from domain.sync_diff import (
    ExactAnswers,
    StaleRow,
    group_keys_of,
    settle_asked_rows,
    settle_unplaced_rows,
    sort_stale_rows,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping

    from domain.rom import GoneEvidence

    # ``LibraryFetcher.ask_romm_for_roms``: one exact-id question per ROM.
    AskRommFn = Callable[[Collection[int]], Awaitable[ExactAnswers]]
    # ``LocalLibraryReader.do_read_kept_platform_names``, offloaded by the caller.
    ReadKeptNamesFn = Callable[[Collection[str]], Awaitable[dict[str, str]]]


def stale_rows_of(stale_rom_ids: Iterable[int], registry: Mapping[str, Mapping[str, Any]]) -> list[StaleRow]:
    """A :class:`StaleRow` per *stale_rom_ids* entry, off the preview's registry (``do_read_preview_baseline``)."""
    rows: list[StaleRow] = []
    for rom_id in stale_rom_ids:
        reg = registry[str(rom_id)]
        rows.append(
            StaleRow(
                rom_id,
                reg["app_id"],
                reg.get("platform_fs_slug"),
                reg.get("delivered_through"),
                reg.get("sibling_group_key"),
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


class StaleVerdict(NamedTuple):
    """What the stale removal makes of a run's stale rows: :func:`settle_stale_rows`'s answer.

    ``found_folders`` is the ``fs_slug`` RomM answered each asked row with,
    where it is not the one the row records (#2452 D5); only the apply writes it.
    """

    remove: list[StaleRow]
    kept: list[StaleRow]
    gone_marks: dict[int, GoneEvidence | None]
    found_folders: dict[int, str]


async def settle_stale_rows(
    rows: Iterable[StaleRow],
    *,
    ask_romm: AskRommFn,
    logger: logging.Logger,
    read_platform_folders: Collection[str],
    listed_platform_folders: Collection[str] | None,
    collection_listed_rom_ids: Collection[int],
    synced_group_keys: Collection[str],
    hidden_rom_ids: Collection[int] | None = None,
) -> StaleVerdict:
    """The rows to remove, the rows kept in their platform's collections, the marks and the ``fs_slug`` values found.

    Sorts them by :func:`sort_stale_rows`, asks RomM once about the ones it
    cannot decide, settles those by :func:`settle_asked_rows` and
    :func:`settle_unplaced_rows`, and answers the marks by
    :meth:`~domain.sync_diff.StaleSort.gone_from_romm_marks`. A row RomM
    was asked about and did not return — a proven 404, or an answer that
    established nothing — is in neither list: its shortcut stays, and it is
    filed in its platform's collection only where the platform-groups setting
    files every bound row. Neither list holds a row whose game the run synced
    (*synced_group_keys*) on a platform the run did not read either, though
    RomM is asked about it where it would be asked about any other row. A row
    the user hid (*hidden_rom_ids*, the run's hidden set, ``None`` where RomM's
    answer could not be read) whose game the run did not sync is removed
    unasked. A row with no ``fs_slug`` recorded is placed by the ``fs_slug``
    RomM answers its exact id with (#2452 D5).
    """
    sort = sort_stale_rows(
        rows,
        read_platform_folders=read_platform_folders,
        listed_platform_folders=listed_platform_folders,
        collection_listed_rom_ids=collection_listed_rom_ids,
        synced_group_keys=synced_group_keys,
        hidden_rom_ids=hidden_rom_ids or frozenset(),
    )
    asked = [*sort.ask, *sort.ask_only, *sort.unplaced]
    exact = await ask_romm([row.rom_id for row in asked]) if asked else ExactAnswers({}, {})
    asked_remove, asked_keep = settle_asked_rows(sort.ask, exact.answers)
    placed = settle_unplaced_rows(
        sort.unplaced,
        exact.answers,
        exact.folders,
        read_platform_folders=read_platform_folders,
        listed_platform_folders=listed_platform_folders,
    )
    kept = sort.keep + placed.keep
    if kept or asked_keep:
        logger.info(
            f"Stale removal: keeping {len(kept)} bound version(s) gone from RomM in their platform's "
            f"collection and {len(asked_keep)} reached through a collection RomM did not confirm present"
        )
    return StaleVerdict(
        remove=sort.remove + asked_remove + placed.remove,
        kept=kept,
        gone_marks={
            **sort.gone_from_romm_marks(exact.answers, read_platform_folders),
            **placed.gone_from_romm_marks(exact.answers, read_platform_folders),
        },
        found_folders={
            row.rom_id: exact.folders[row.rom_id]
            for row in asked
            if row.rom_id in exact.folders and exact.folders[row.rom_id] != row.platform_folder
        },
    )


async def with_kept_platform_names(
    platform_folders: Iterable[str], listed: dict[str, str], *, read_kept_names: ReadKeptNamesFn
) -> dict[str, str]:
    """*listed*, plus the cached name of each of *platform_folders* outside it the cache holds one for, for the preview.

    *listed* is every listed platform's name by its ``fs_slug``, from the run's own listing.

    *platform_folders* must cover every platform the preview's
    platform-collection diff and per-platform table name. Must answer, for
    each, the name the apply's finalize carries forward
    (``RunFinalizer._names_with_bound_platforms_kept``). *read_kept_names* is
    asked only when such a platform exists.
    """
    outside = set(platform_folders) - listed.keys()
    if not outside:
        return listed
    return {**await read_kept_names(outside), **listed}
