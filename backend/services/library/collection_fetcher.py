"""Collection fetch sub-service: one collection work unit's ROMs, for all three collection kinds.

A collection unit is queued by :class:`~services.library.fetcher.LibraryFetcher`,
which reads the collection listings and keeps each collection's listed
``rom_ids`` on its unit. This module turns such a unit into ROMs: it tries the
collection incremental skip against the ``CollectionSyncState`` stamp (#742,
#2477, ADR-0023), and otherwise fetches only the listed members no unit of the run
delivered, with one walk narrowed to the platforms the sync leaves off — the
listed platforms with no platform unit this run. An own smart collection walks
those platforms even when nothing is left over, and a foreign smart collection
is walked whole. Why, and what each kind's
skip compares, is in docs/architecture/backend-architecture.md, "Incremental
skip".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.collection_skip import CollectionSkip, membership_verdict, probe_verdict, recorded_updated_at
from domain.platform_skip import skip_reference
from lib.romm_paging import LIST_PAGE_SIZE
from services.library._fetch_pages import emit_fetch_page_progress, raise_if_cancelling

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.collection_sync_state import CollectionSyncState
    from domain.work_unit import WorkUnit
    from services.library._fetch_pages import EmitProgressFn
    from services.library._state import LibrarySyncStateBox
    from services.protocols import RommLibraryApi, UnitOfWorkFactory


@dataclass(frozen=True)
class CollectionFetcherConfig:
    """Frozen wiring bundle handed to ``CollectionFetcher.__init__``.

    Holds the Protocol-typed RomM adapter, the loop and logger, the UoW factory
    the stamp read and the registry reconstruction open, the shared
    ``LibrarySyncStateBox`` (read for the cancel signal), and the progress
    emitter the per-page ``fetching`` frames go through.
    """

    romm_api: RommLibraryApi
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    uow_factory: UnitOfWorkFactory
    sync_state_box: LibrarySyncStateBox
    emit_progress: EmitProgressFn


class CollectionFetcher:
    """Collection fetch sub-service: a collection unit's skip, members and walk."""

    def __init__(self, *, config: CollectionFetcherConfig) -> None:
        self._romm_api = config.romm_api
        self._loop = config.loop
        self._logger = config.logger
        self._uow_factory = config.uow_factory
        self._sync_state = config.sync_state_box
        self._emit_progress = config.emit_progress

    def _read_collection_stamp(self, collection_id: str, collection_kind: str) -> CollectionSyncState | None:
        """Read one collection's completion stamp in a short read UoW."""
        with self._uow_factory() as uow:
            return uow.collection_sync_state.get(collection_id, collection_kind)

    def _reconstruct_collection_members(
        self, member_rom_ids: list[int], already_synced: set[int]
    ) -> list[dict[str, Any]]:
        """Reconstruct the bound rows of a skipped collection's not-yet-covered members.

        A collection unit ordinarily returns as ``new_roms`` only its members NOT
        already in ``synced_rom_ids`` — its members on platforms this run did not
        fetch (a disabled platform, or a group edge). On a skip those rows come
        from the registry instead of a fetch, mirroring the platform skip's
        reconstruction (``_read_incremental_baseline``), so the preview union
        stays complete: a member bound in the registry but absent from the fetch
        would otherwise read as stale. A member already synced (its platform unit
        covered it) is skipped; an unbound / absent member is skipped too — it is
        not a shortcut to reconstruct (an unbound sibling maps to its group's
        bound representative at finalize, ADR-0021). One short read UoW.
        """
        with self._uow_factory() as uow:
            reconstructed: list[dict[str, Any]] = []
            for rid in member_rom_ids:
                if rid in already_synced:
                    continue
                rom = uow.roms.get(rid)
                if rom is None or rom.shortcut_app_id is None:
                    continue
                reconstructed.append(
                    {
                        "id": rom.rom_id,
                        "name": rom.name,
                        "fs_name": rom.fs_name,
                        "platform_slug": rom.platform_slug,
                        "platform_name": rom.platform_slug,
                        "igdb_id": rom.igdb_id,
                        "sgdb_id": rom.sgdb_id,
                        "ra_id": rom.ra_id,
                        "sibling_group_key": rom.sibling_group_key,
                        "has_file_on_disk": rom.has_file_on_disk,
                    }
                )
            return reconstructed

    async def _try_collection_incremental_skip(self, unit: WorkUnit) -> CollectionSkip:
        """Per-unit incremental-skip pre-check for a collection unit.

        Answers the stamped member rom-id list when the collection is unchanged —
        the caller replays it into ``synced_rom_ids`` + the Steam-collection
        membership map without paginating — and ``None`` to fall through to a
        fetch, with whether a fresh stamp settles the refusal. The collection
        sibling of :meth:`_try_unit_incremental_skip`, following the platform
        skip's rules (#2477 D1): the listing must match the ``CollectionSyncState``
        stamp (``domain/collection_skip.membership_verdict``), and then so must the
        scoped ``updated_after`` probe (``domain/collection_skip.probe_verdict``).

        A probe that raises, or answers with something the verdict cannot read,
        falls open to a fetch, mirroring the platform gate; a fresh stamp settles
        neither.
        """
        kind = unit.collection_kind
        if kind is None:
            return CollectionSkip(None)
        if recorded_updated_at(kind, unit.collection_updated_at) is None:
            self._logger.info(f"Per-unit fetch {unit.name}: no collection updated_at — fetch")
            return CollectionSkip(None)

        stamp = await self._loop.run_in_executor(None, self._read_collection_stamp, str(unit.id), kind)
        if stamp is None:
            self._logger.info(f"Per-unit fetch {unit.name}: no completion stamp — fetch")
            return CollectionSkip(None, restamp_settles=True)
        listing = membership_verdict(
            stamp, listing_updated_at=unit.collection_updated_at, listing_rom_ids=unit.member_rom_ids
        )
        reference = skip_reference(stamp)
        if not listing.unchanged or reference is None:
            self._logger.info(f"Per-unit fetch {unit.name}: {listing.reason} — fetch")
            return CollectionSkip(None, restamp_settles=listing.restamp_settles)

        try:
            delta_resp: object = await self._loop.run_in_executor(
                None,
                self._romm_api.list_collection_roms_updated_after,
                str(unit.id) if kind == "virtual" else int(unit.id),
                kind,
                reference.isoformat(),
                1,
                0,
            )
        except Exception as e:
            self._logger.warning(
                f"Per-unit collection incremental check failed for {unit.name}, falling back to a fetch: {e}"
            )
            return CollectionSkip(None)

        probe = probe_verdict(delta_resp, reference)
        if not probe.unchanged:
            self._logger.info(f"Per-unit fetch {unit.name}: {listing.reason}; {probe.reason} — fetch")
            return CollectionSkip(None, restamp_settles=probe.restamp_settles)
        self._logger.info(
            f"Per-unit skip: {unit.name} unchanged ({len(stamp.member_rom_ids)} members; "
            f"{listing.reason}; {probe.reason})"
        )
        return CollectionSkip(list(stamp.member_rom_ids))

    async def _fetch_collection_page(
        self, unit: WorkUnit, limit: int, offset: int, platform_ids: tuple[int, ...] | None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch one page of a collection unit's ROMs via its kind-specific endpoint.

        A ``virtual`` collection is a RomM virtual collection (string base64 id,
        the same endpoint for every virtual type), a ``smart`` collection a
        saved-search (int id), and the default a regular standard collection (int id) —
        each has its own list endpoint. *platform_ids* narrows the page to
        members on those platforms; ``None`` walks every member. ``dict | list``
        keeps the caller's isinstance guard genuine: the paginated endpoints
        return ``{"items": [...]}`` but a bare-list response shape is tolerated.
        """
        if unit.collection_kind == "virtual":
            return await self._loop.run_in_executor(
                None, self._romm_api.list_roms_by_virtual_collection, str(unit.id), limit, offset, platform_ids
            )
        if unit.collection_kind == "smart":
            return await self._loop.run_in_executor(
                None, self._romm_api.list_roms_by_smart_collection, int(unit.id), limit, offset, platform_ids
            )
        return await self._loop.run_in_executor(
            None, self._romm_api.list_roms_by_collection, int(unit.id), limit, offset, platform_ids
        )

    async def fetch_collection_unit(
        self, unit: WorkUnit, synced_rom_ids: set[int], *, progress_step: int = 0, progress_total_steps: int = 0
    ) -> tuple[list[dict[str, Any]], list[int], bool, bool]:
        """Fetch ROMs for a single collection unit.

        Tries the incremental-skip path first: when the collection is unchanged
        (:meth:`_try_collection_incremental_skip`, #742), its membership is
        reconstructed from the stamp instead of paginated — no pages fetched
        beyond the ``limit=1`` probe.

        Otherwise the unit's members come from the listing (``member_rom_ids``).
        A member already in *synced_rom_ids* is covered without a request; the
        rest are fetched by one walk of the collection narrowed to the platforms
        the sync leaves off (``unwalked_platform_ids``), so a standard or
        virtual collection whose members are all covered fetches no page
        (:meth:`_fetch_collection_leftovers` has the own-smart exception). A
        foreign smart collection is walked whole instead: its listing is its
        owner's view and its walk the caller's, and the sync follows the
        caller's.

        Mutates *synced_rom_ids* in place: every ROM this unit fetched or
        reconstructed is added, so subsequent units (and the final stale
        cleanup) treat them as covered.

        Returns ``(new_roms, all_collection_rom_ids, skipped, restamp_settles)``:
          * ``new_roms`` — ROMs not already present in *synced_rom_ids*,
            decorated with platform_name/platform_slug for shortcut
            construction. On a skip these are reconstructed from the registry
            (the collection's members on platforms this run did not fetch), so
            the caller's union stays complete. A listed member neither covered
            nor answered by the walk is logged and left out.
          * ``all_collection_rom_ids`` — every rom_id in the collection
            (including those already synced via a platform unit), used
            to build Steam collection memberships at the final phase: the
            listing's ids plus any the walk answered beyond them.
          * ``skipped`` — True when the incremental check succeeded. The caller
            short-circuits the per-unit apply + commit branch (like the platform
            skip), keeping the reconstructed rows and membership for accounting.
          * ``restamp_settles`` — True when the skip refused the collection for
            a reason a stamp the apply writes settles (``CollectionSkip``); False
            on a skip.

        ``progress_step`` / ``progress_total_steps`` are the run's coarse unit
        index / total, threaded through to the throttled per-page ``fetching``
        frames so a large collection fetch narrates its progress like a
        platform fetch does.
        """
        if unit.type != "collection":
            raise ValueError(f"fetch_collection_unit called with non-collection unit type={unit.type}")

        skip = await self._try_collection_incremental_skip(unit)
        skip_member_ids = skip.member_rom_ids
        if skip_member_ids is not None:
            # Reconstruct the not-yet-covered members from the registry BEFORE
            # marking them synced, so the preview union stays complete, then add
            # every member to synced_rom_ids so the stale cleanup treats them as
            # covered (a skipped collection applies nothing new).
            reconstructed = await self._loop.run_in_executor(
                None, self._reconstruct_collection_members, skip_member_ids, set(synced_rom_ids)
            )
            synced_rom_ids.update(skip_member_ids)
            return reconstructed, list(skip_member_ids), True, False

        progress = (progress_step, progress_total_steps)
        if unit.collection_kind == "smart" and not unit.is_own:
            new_roms, walked_ids = await self._walk_collection(
                unit, synced_rom_ids, platform_ids=None, expected_count=unit.rom_count, progress=progress
            )
            return new_roms, walked_ids, False, skip.restamp_settles

        new_roms, member_ids = await self._fetch_collection_leftovers(unit, synced_rom_ids, progress=progress)
        return new_roms, member_ids, False, skip.restamp_settles

    async def _fetch_collection_leftovers(
        self, unit: WorkUnit, synced_rom_ids: set[int], *, progress: tuple[int, int]
    ) -> tuple[list[dict[str, Any]], list[int]]:
        """Fetch the listed members no unit delivered yet; answer ``(new_roms, member_ids)``.

        The walk is narrowed to the platforms the sync leaves off
        (``unwalked_platform_ids``) and runs only when there are such platforms.
        A standard or virtual collection walks only when a listed member is left
        over. An own smart collection walks whenever there are such platforms:
        its listing is RomM's stored set, which a failed refresh can leave
        behind the live criteria, and the walk answers live.

        ``member_ids`` is the listing's ids plus any the walk answered beyond
        them. A leftover the walk did not answer is logged and left out. Three
        cases are known: a scan racing the run, a ROM the walk does not show,
        and an unbound sibling on a skipped platform — a platform skip
        reconstructs only bound rows, so such a member is never in
        *synced_rom_ids*, and the walk does not ask for its platform; the
        collection skip leaves unbound members out the same way.
        """
        member_ids = list(unit.member_rom_ids)
        leftovers = {rid for rid in member_ids if rid not in synced_rom_ids}
        walks = bool(unit.unwalked_platform_ids) and (bool(leftovers) or unit.collection_kind == "smart")
        new_roms: list[dict[str, Any]] = []
        answered: set[int] = set()
        if walks:
            new_roms, walked_ids = await self._walk_collection(
                unit,
                synced_rom_ids,
                platform_ids=unit.unwalked_platform_ids,
                expected_count=len(leftovers),
                progress=progress,
            )
            answered = set(walked_ids)
            listed = set(member_ids)
            member_ids.extend(rid for rid in walked_ids if rid not in listed)
        missing = len(leftovers - answered)
        if missing:
            self._logger.info(
                f"Per-unit fetch {unit.name}: {missing} listed member(s) no unit delivered — left out of this run"
            )
        return new_roms, member_ids

    async def _walk_collection(
        self,
        unit: WorkUnit,
        synced_rom_ids: set[int],
        *,
        platform_ids: tuple[int, ...] | None,
        expected_count: int,
        progress: tuple[int, int],
    ) -> tuple[list[dict[str, Any]], list[int]]:
        """Page through the collection, narrowed to *platform_ids* when given; answer ``(new_roms, walked_ids)``.

        ``walked_ids`` is every id the walk answered; ``new_roms`` those not
        already in *synced_rom_ids*, decorated for shortcut construction and
        added to it. *expected_count* sizes the per-page progress frames.
        """
        new_roms: list[dict[str, Any]] = []
        walked_ids: list[int] = []
        offset = 0
        limit = LIST_PAGE_SIZE
        total_pages = (expected_count + limit - 1) // limit if expected_count else 0
        page_num = 0
        while True:
            raise_if_cancelling(self._sync_state)
            page_num += 1
            await emit_fetch_page_progress(
                self._emit_progress,
                unit_name=unit.name,
                page=page_num,
                total_pages=total_pages,
                progress_step=progress[0],
                progress_total_steps=progress[1],
            )
            page = await self._fetch_collection_page(unit, limit, offset, platform_ids)

            items = page.get("items", []) if isinstance(page, dict) else page
            for rom in items:
                rid = rom["id"]
                walked_ids.append(rid)
                if rid in synced_rom_ids:
                    continue
                synced_rom_ids.add(rid)
                rom["platform_name"] = rom.get("platform_name", rom.get("platform_display_name", "Unknown"))
                rom["platform_slug"] = rom.get("platform_slug", rom.get("platform_fs_slug", ""))
                rom.pop("files", None)
                new_roms.append(rom)

            if len(items) < limit:
                break
            offset += limit
        return new_roms, walked_ids
