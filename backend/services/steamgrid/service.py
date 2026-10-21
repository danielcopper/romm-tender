"""SteamGridDB orchestration — API key flow, artwork fetch/cache, icon save.

Owns the runtime decisions for SteamGridDB integration: resolving SGDB
game IDs from registry/RomM hints, fanning out cached vs. remote
artwork requests, and routing icon writes into Steam's grid directory.
All raw I/O is delegated to adapters (``SgdbArtworkCache``,
``SteamConfigStore``); pure asset-type / endpoint compute lives in
``domain.sgdb_artwork``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.artwork_slot import RECORDED_SLOTS, ArtworkSlot, is_slot_filled
from domain.artwork_slot_outcome import ArtworkSlotOutcome, SlotOutcome, icon_waits
from domain.header_value import no_header_can_carry
from domain.sgdb_artwork import (
    asset_type_name,
    build_autocomplete_path,
    classify_resolution,
    effective_sgdb_id,
    first_grid_url,
    parse_autocomplete_results,
)
from lib.errors import (
    AuthFailed,
    Refused,
    ServerUnreachable,
    SgdbApiError,
    SgdbKeyUnsendableError,
    SteamGridDirMissingError,
)
from models.shortcut_artwork import SlotAnswer, WrittenIcon
from services.steamgrid._files import CachedArtwork, NoArtwork, SgdbArtworkFiles, SgdbArtworkFilesConfig

if TYPE_CHECKING:
    import logging

    from services.protocols import (
        Clock,
        ConflictRules,
        DebugLogger,
        IconDownscaleFn,
        PendingSyncReader,
        RommRomReader,
        SettingsPersister,
        SgdbArtworkCache,
        SteamConfigStore,
        SteamGridDbApi,
        UnitOfWorkFactory,
    )


def _artwork_slot(slot: object) -> ArtworkSlot:
    """The slot *slot* names among the four Tender fills from SteamGridDB; refuses any other."""
    try:
        artwork_slot = ArtworkSlot(slot)
    except ValueError as e:
        raise Refused("invalid_payload", f"Unknown artwork slot: {slot}") from e
    if artwork_slot not in RECORDED_SLOTS:
        raise Refused("invalid_payload", f"The {slot} slot is not filled from SteamGridDB")
    return artwork_slot


_KEY_REFUSED = "SteamGridDB refused the API key — check it in Settings"
_INVALID_KEY = "Invalid API key"
_RATE_LIMITED = "SteamGridDB is rate-limiting requests — try again in a moment"
_ASSET_TYPES = ("hero", "logo", "grid", "icon")


def _refusal_for(error: SgdbApiError, *, key_refused: str = _KEY_REFUSED) -> Refused:
    """The refusal an ``SgdbApiError`` answers with; never the error's own text.

    *key_refused* for a 401 or 403, and the same refusal as
    :func:`_refuse_a_key_that_cannot_be_sent` for a key no header can carry.
    """
    if isinstance(error, SgdbKeyUnsendableError):
        return AuthFailed(_INVALID_KEY)
    if error.status_code in (401, 403):
        return AuthFailed(key_refused)
    if error.status_code == 429:
        return Refused("rate_limited", _RATE_LIMITED)
    if error.status_code is None:
        return ServerUnreachable("Could not reach SteamGridDB")
    return ServerUnreachable(f"SteamGridDB error: HTTP {error.status_code}")


def _refuse_a_key_that_cannot_be_sent(api_key: str) -> None:
    """Refuse a key no HTTP header can carry (``domain.header_value``) as ``auth_failed``, before any other work.

    The adapter refuses the same key at the request as well, which is what
    covers the shortcut artwork job; this check gives each endpoint its answer
    before it does anything else.
    """
    if no_header_can_carry(api_key):
        raise AuthFailed(_INVALID_KEY)


@dataclass(frozen=True)
class SteamGridServiceConfig:
    """Frozen wiring bundle handed to ``SteamGridService.__init__``.

    Holds the Protocol-typed adapters (``sgdb_api``, ``romm_api``,
    ``steam_config``, ``sgdb_artwork_cache``), the live settings dict,
    runtime infrastructure, the ``settings.json`` persister, the SQLite
    Unit-of-Work factory (a resolved ``sgdb_id`` and the game the user chose
    are persisted onto the ``roms`` aggregate via the UoW), the pending-sync
    read seam, the debug-logger seam SteamGridService needs at construction time, the
    ``ConflictRules`` the artwork, icon, resolution and game-id use cases check
    at their entry, and the artwork ones take the ``sgdb_artwork`` lease through,
    the downscale every icon passes through before it is written, and the clock
    an artwork slot's outcome is timed by.
    """

    sgdb_api: SteamGridDbApi
    romm_api: RommRomReader
    steam_config: SteamConfigStore
    sgdb_artwork_cache: SgdbArtworkCache
    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    settings_persister: SettingsPersister
    get_pending_sync: PendingSyncReader
    log_debug: DebugLogger
    uow_factory: UnitOfWorkFactory
    conflict_rules: ConflictRules
    downscale_icon: IconDownscaleFn
    clock: Clock


class SteamGridService:
    """SteamGridDB orchestration: API key flow, artwork fetch/cache, icon save.

    The artwork fetch, the icon save, the resolution and the manual pick check
    their endpoint's conflict rules at their entry, under that endpoint's name,
    and raise the rule's refusal when one holds (GLOSSARY.md → Conflict rules).
    """

    def __init__(self, *, config: SteamGridServiceConfig) -> None:
        self._sgdb_api = config.sgdb_api
        self._romm_api = config.romm_api
        self._steam_config = config.steam_config
        self._sgdb_artwork_cache = config.sgdb_artwork_cache
        self._settings = config.settings
        self._loop = config.loop
        self._logger = config.logger
        self._settings_persister = config.settings_persister
        self._get_pending_sync = config.get_pending_sync
        self._log_debug = config.log_debug
        self._uow_factory = config.uow_factory
        self._rules = config.conflict_rules
        self._downscale_icon = config.downscale_icon
        self._clock = config.clock
        self._files = SgdbArtworkFiles(
            config=SgdbArtworkFilesConfig(
                sgdb_api=config.sgdb_api, cache=config.sgdb_artwork_cache, logger=config.logger
            )
        )
        # Per app id: the count ``icon_generation`` answers, and the lock a page
        # save and the artwork job's write take turns under.
        self._icon_generations: dict[int, int] = {}
        self._icon_locks: dict[int, asyncio.Lock] = {}

    # -- artwork download --------------------------------------------------

    def _download_sgdb_artwork(self, sgdb_game_id, rom_id, asset_type):
        """Answer the cached file of *rom_id*'s *asset_type*, downloading it first when it is not cached.

        ``None`` when SteamGridDB has no candidate or the download failed; an
        exception is logged.
        """
        found = self._download_cached_artwork(sgdb_game_id, rom_id, asset_type)
        return found.path if found is not None else None

    def _download_cached_artwork(self, sgdb_game_id, rom_id, asset_type) -> CachedArtwork | None:
        """:meth:`_download_sgdb_artwork`, answering the cached file with the image id its name carries."""
        try:
            found = self._files.download(sgdb_game_id, rom_id, asset_type)
        except Exception as e:
            self._logger.warning(f"SGDB {asset_type} download failed for game {sgdb_game_id}: {e}")
            return None
        return found if isinstance(found, CachedArtwork) else None

    # -- artwork base64 (use case) -----------------------------------------

    async def _read_file_as_base64(self, path):
        """Read a file and return base64-encoded string, or None on failure."""
        try:
            data = await self._loop.run_in_executor(None, self._sgdb_artwork_cache.read_bytes, path)
            return base64.b64encode(data).decode("ascii")
        except Exception as e:
            self._logger.warning(f"Failed to read file {path}: {e}")
            return None

    def _resolve_sgdb_id_state_only(self, rom_id):
        """Resolve the SGDB game *rom_id*'s artwork comes from, from local state only — no network.

        RomM's id where :meth:`_sgdb_ids_state_only` finds one, else the game
        the user chose (``domain.sgdb_artwork.effective_sgdb_id``); ``None``
        when there is neither. RomM re-reads and the name search are left to
        the explicit ``get_sgdb_resolution`` user action.
        """
        return effective_sgdb_id(*self._sgdb_ids_state_only(rom_id))

    def _sgdb_ids_state_only(self, rom_id) -> tuple[int | None, int | None]:
        """RomM's SGDB id for *rom_id* and the one the user chose, from local state only.

        RomM's comes from the persisted ROM row first, then the in-memory
        pending-sync map; the user's from the ROM row.
        """
        rom_id = int(rom_id)
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
        romm_id = rom.sgdb_id if rom is not None else None
        if not romm_id:
            romm_id = self._get_pending_sync().get(rom_id, {}).get("sgdb_id")
        return romm_id or None, rom.chosen_sgdb_id if rom is not None else None

    async def _fetch_sgdb_id_from_romm(self, rom_id):
        """Fetch ``(sgdb_id, rom_data)`` from RomM without persisting.

        Reads the ROM detail from RomM and surfaces its ``sgdb_id`` field.
        Persistence is the caller's decision — this method never writes the
        registry. Returns ``(None, None)`` on a network failure (logged, never
        raised).
        """
        sgdb_id = None
        rom_data = None
        try:
            rom_data = await self._loop.run_in_executor(None, self._romm_api.get_rom, rom_id)
            if rom_data:
                sgdb_id = rom_data.get("sgdb_id")
            self._log_debug(f"SGDB artwork: fetched sgdb_id={sgdb_id} from RomM for rom_id={rom_id}")
        except Exception as e:
            self._logger.warning(f"SGDB artwork: failed to fetch the SGDB id from RomM for rom_id={rom_id}: {e}")
        return sgdb_id, rom_data

    def _persist_sgdb_id(self, rom_id_str, sgdb_id):
        """Stamp a resolved ``sgdb_id`` on the ROM row in a short write UoW.

        No-op when the ROM is absent from ``roms`` — a resolved id only
        matters for a synced ROM, and the schema has no standalone row to
        stamp otherwise.
        """
        rom_id = int(rom_id_str)
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None:
                self._logger.warning(f"Cannot persist sgdb_id for unsynced rom_id={rom_id}")
                return
            rom.assign_sgdb_id(int(sgdb_id))
            uow.roms.save(rom)

    def _persist_chosen_sgdb_id(self, rom_id: int, sgdb_id: int) -> None:
        """Store the SGDB game the user chose for *rom_id*; a ROM with no ``roms`` row has nothing to store it on."""
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None:
                self._logger.warning(f"Cannot store the chosen SteamGridDB game for unsynced rom_id={rom_id}")
                return
            rom.choose_sgdb_game(sgdb_id)
            uow.roms.set_chosen_sgdb_id(rom_id, rom.chosen_sgdb_id)

    def _forget_slot_outcomes(self, rom_id: int) -> None:
        with self._uow_factory() as uow:
            uow.artwork_slot_outcomes.delete_for_rom(rom_id)

    def _first_grid_thumb(self, sgdb_id):
        """Return a thumbnail URL for *sgdb_id*'s first grid, or ``None``.

        Synchronous — callers offload via ``run_in_executor``. Network
        and parse errors are swallowed to ``None`` so a preview thumbnail
        never breaks the resolution flow.
        """
        try:
            payload = self._sgdb_api.request(f"/grids/game/{sgdb_id}?limit=1")
            return first_grid_url(payload)
        except Exception as e:
            self._logger.warning(f"SGDB grid thumb lookup failed for game {sgdb_id}: {e}")
            return None

    # -- resolution cascade (use case) -------------------------------------

    async def get_sgdb_resolution(self, rom_id):
        """Resolve which SGDB game id to use for *rom_id*, picker-driven.

        The single explicit user action that may re-read RomM and offer the
        name search. Deletes the ROM's recorded artwork slot outcomes before
        it resolves: an explicit request asks SteamGridDB again. RomM's
        ``sgdb_id`` always wins when present, then the game the user chose.
        Returns one of:

        - ``{"decision": "no_api_key"}`` — no key configured.
        - ``{"decision": "resolved", "sgdb_id": int}`` — a winning id was
          found (and persisted when it came from RomM).
        - ``{"decision": "needs_pick", "candidates": [...]}`` — no id is
          stored or pending, RomM has none and the user chose none; offer a
          manual name-search picker, with no candidates when the name search
          fails.

        Refuses with ``auth_failed`` when SteamGridDB refuses the key or no
        header can carry it, and ``rate_limited`` when it answers the name
        search with a 429.
        """
        async with self._rules.hold("get_sgdb_resolution", prune=True):
            return await self._get_sgdb_resolution(rom_id)

    async def _get_sgdb_resolution(self, rom_id):
        rom_id = int(rom_id)
        rom_id_str = str(rom_id)
        await self._loop.run_in_executor(None, self._forget_slot_outcomes, rom_id)
        if not self._settings.get("steamgriddb_api_key"):
            return {"decision": "no_api_key"}
        _refuse_a_key_that_cannot_be_sent(self._settings["steamgriddb_api_key"])

        stored_id, chosen_id = self._sgdb_ids_state_only(rom_id)
        state_id = effective_sgdb_id(stored_id, chosen_id)
        romm_id, rom_data = await self._fetch_sgdb_id_from_romm(rom_id)

        decision = classify_resolution(state_id, romm_id)
        if decision == "use_romm" and romm_id is not None:
            if romm_id != stored_id:
                self._persist_sgdb_id(rom_id_str, romm_id)
            return {"decision": "resolved", "sgdb_id": int(romm_id)}
        if decision == "use_state" and state_id is not None:
            return {"decision": "resolved", "sgdb_id": int(state_id)}

        name = (rom_data or {}).get("name") or ""
        try:
            search = await self.search_sgdb_games(name)
        except Refused as e:
            if e.reason in (AuthFailed.reason, "rate_limited"):
                raise
            return {"decision": "needs_pick", "candidates": []}
        return {"decision": "needs_pick", "candidates": search["games"]}

    async def search_sgdb_games(self, term):
        """Search SGDB by name and enrich the top candidates with thumbnails.

        Returns ``{"success": True, "games": [{"id", "name",
        "release_year", "thumb_url"}]}``. Refuses with ``no_api_key`` when no
        API key is configured, with ``auth_failed`` when the key cannot be sent,
        and answers an ``SgdbApiError`` as ``auth_failed`` or
        ``server_unreachable``.
        """
        if not self._settings.get("steamgriddb_api_key"):
            raise Refused("no_api_key", "No SteamGridDB API key configured")
        _refuse_a_key_that_cannot_be_sent(self._settings["steamgriddb_api_key"])
        path = build_autocomplete_path(str(term))
        try:
            payload = await self._loop.run_in_executor(None, self._sgdb_api.request, path)
        except SgdbApiError as e:
            self._logger.warning(f"SGDB name search failed for term={term!r}: {e}")
            raise _refusal_for(e) from e
        candidates = parse_autocomplete_results(payload)

        capped = candidates[:6]
        thumb_futures = [
            self._loop.run_in_executor(None, self._first_grid_thumb, candidate["id"]) for candidate in capped
        ]
        thumbs = await asyncio.gather(*thumb_futures)
        games = [
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "release_year": candidate["release_year"],
                "thumb_url": thumb,
            }
            for candidate, thumb in zip(capped, thumbs, strict=True)
        ]
        return {"success": True, "games": games}

    async def apply_sgdb_game_id(self, rom_id, sgdb_id):
        """Store *sgdb_id* as the SteamGridDB game the user chose for *rom_id*, and cache its artwork.

        Downloads the chosen game's four asset types into the ROM's artwork
        cache, which the cache-first ``get_sgdb_artwork_base64`` serves for
        the hero, the logo and the wide grid, and ``get_sgdb_slot_artwork``
        for the icon. The choice is stored beside RomM's id, which wins where
        RomM gives one, so a later Refresh Artwork resolves to it instead of
        asking again. The previously applied art stays visible until replaced.

        The previous pick's cached images go, and the choice is stored, only
        once SteamGridDB has answered without refusing: ``auth_failed`` for a
        refused key, ``rate_limited`` for a 429, as :meth:`refresh_sgdb_artwork`
        refuses.
        """
        async with self._rules.hold("apply_sgdb_game_id", prune=True):
            return await self._apply_sgdb_game_id(rom_id, sgdb_id)

    async def _apply_sgdb_game_id(self, rom_id, sgdb_id):
        rom_id = int(rom_id)
        sgdb_id = int(sgdb_id)
        _refuse_a_key_that_cannot_be_sent(self._settings.get("steamgriddb_api_key", ""))
        answers = await self._download_all_anew(rom_id, sgdb_id)
        await self._loop.run_in_executor(None, self._persist_chosen_sgdb_id, rom_id, sgdb_id)
        for asset_type, answer in zip(_ASSET_TYPES, answers, strict=True):
            if not isinstance(answer, CachedArtwork):
                await self._loop.run_in_executor(None, self._files.drop, rom_id, asset_type)
        return {"success": True}

    async def _download_all_anew(self, rom_id: int, sgdb_id: int) -> list[CachedArtwork | NoArtwork | SlotAnswer]:
        """Download the four asset types past the cache, in ``_ASSET_TYPES`` order; refuses a refused key or a 429."""
        downloads = [
            self._loop.run_in_executor(None, self._files.fetch_or_answer, rom_id, asset_type, sgdb_id, True)
            for asset_type in _ASSET_TYPES
        ]
        answers = await asyncio.gather(*downloads)
        if SlotAnswer.KEY_REFUSED in answers:
            raise AuthFailed(_KEY_REFUSED)
        if SlotAnswer.RATE_LIMITED in answers:
            raise Refused("rate_limited", _RATE_LIMITED)
        return answers

    async def refresh_sgdb_artwork(self, rom_id, sgdb_id):
        """Download *rom_id*'s hero, logo, wide grid and icon anew from SteamGridDB game *sgdb_id*.

        A cached image is replaced only by a download that succeeded, so one
        that did not keeps the image the cache held. Refuses with
        ``no_api_key``, with ``auth_failed`` when SteamGridDB refuses the key or
        no header can carry it, and with ``rate_limited`` on a 429.
        """
        async with self._rules.hold("refresh_sgdb_artwork", prune=True):
            api_key = self._settings.get("steamgriddb_api_key")
            if not api_key:
                raise Refused("no_api_key", "No SteamGridDB API key configured")
            _refuse_a_key_that_cannot_be_sent(api_key)
            await self._download_all_anew(int(rom_id), int(sgdb_id))
            return {"success": True}

    async def get_sgdb_artwork_base64(self, rom_id, asset_type_num):
        """Serve one SteamGridDB hero, logo or wide grid for *rom_id* as base64, from the cache first.

        An answer that carries an image also carries an ``sgdb_artwork`` lease
        in ``prune_lease_token`` for the frontend's Steam write. Refuses the
        icon (asset type 4) with ``invalid_payload``: it is served by
        :meth:`get_sgdb_slot_artwork` and written by :meth:`save_shortcut_icon`.
        """
        async with self._rules.hold("get_sgdb_artwork_base64", prune=True):
            result = await self._get_sgdb_artwork_base64(rom_id, asset_type_num)
            if result.get("base64") is not None:
                result["prune_lease_token"] = await self._rules.acquire_lease("sgdb_artwork")
            return result

    async def _get_sgdb_artwork_base64(self, rom_id, asset_type_num):
        rom_id = int(rom_id)
        asset_type_num = int(asset_type_num)
        asset_type = asset_type_name(asset_type_num)
        self._log_debug(f"SGDB artwork request: rom_id={rom_id}, asset_type={asset_type_num}")
        if not asset_type:
            return {"base64": None, "no_api_key": False}
        if asset_type == ArtworkSlot.ICON.value:
            raise Refused("invalid_payload", "The icon is served by the per-slot fetch")

        # Return from cache if available
        cached = await self._loop.run_in_executor(None, self._files.cached, rom_id, asset_type)
        if cached is not None:
            self._log_debug(f"SGDB artwork cache hit: {cached.path}")
            answer = await self._artwork_answer(rom_id, cached)
            if answer is not None:
                return answer

        if not self._settings.get("steamgriddb_api_key"):
            self._log_debug("SGDB artwork skipped: no API key configured")
            return {"base64": None, "no_api_key": True}
        _refuse_a_key_that_cannot_be_sent(self._settings["steamgriddb_api_key"])

        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        if not sgdb_id:
            self._log_debug(f"SGDB artwork skipped: no SGDB game found for rom_id={rom_id}")
            return {"base64": None, "no_api_key": False}

        found = await self._loop.run_in_executor(None, self._download_cached_artwork, sgdb_id, rom_id, asset_type)
        if found is not None and self._sgdb_artwork_cache.exists(found.path):
            self._log_debug(f"SGDB artwork download success: rom_id={rom_id}, asset_type={asset_type}")
            answer = await self._artwork_answer(rom_id, found)
            if answer is not None:
                return answer
        else:
            self._log_debug(f"SGDB artwork download failed: rom_id={rom_id}, asset_type={asset_type}")

        return {"base64": None, "no_api_key": False}

    async def _artwork_answer(self, rom_id: int, cached: CachedArtwork) -> dict[str, Any] | None:
        """The answer serving *cached*, or ``None`` when it cannot be read.

        It also carries its SteamGridDB image id, the SHA-256 of its bytes and
        the ROM's SteamGridDB id, which the frontend passes to
        ``report_sgdb_slot_artwork`` once Steam has taken it or refused it.
        """
        b64 = await self._read_file_as_base64(cached.path)
        if not b64:
            return None
        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        return {
            "base64": b64,
            "no_api_key": False,
            "image_id": cached.image_id,
            "sha256": hashlib.sha256(base64.b64decode(b64)).hexdigest(),
            "sgdb_id": int(sgdb_id) if sgdb_id else None,
        }

    # -- one artwork slot, filled only when empty (use cases) ---------------

    async def get_sgdb_slot_artwork(self, rom_id, app_id, slot, replace=False):
        """Serve SteamGridDB's image for one of *app_id*'s artwork slots, only while the slot is empty.

        *slot* is ``"grid"`` (the wide grid), ``"logo"``, ``"hero"`` or
        ``"icon"``. With *replace* none of the checks that leave a slot alone
        is made: the slot is served whatever it holds, whatever its record
        says, and whether ``shortcuts.vdf`` can be read. Without a SteamGridDB
        game — RomM's or one the user chose — what the artwork cache holds for
        the slot is served. Answers a discriminated ``status``:

        - ``"image"`` — for a wide grid, logo or hero: ``base64``, SteamGridDB's
          ``image_id``, the bytes' ``sha256`` and the
          ``sgdb_id`` it was found under, which the frontend passes back to
          ``report_sgdb_slot_artwork``, and an ``sgdb_artwork`` lease in
          ``prune_lease_token`` for its Steam write;
        - ``"ready"`` — for the icon: the artwork cache holds it, for
          :meth:`save_shortcut_icon` to write, with the same lease;
        - ``"skip"`` — the slot is filled — Steam's grid directory holds a file
          for it (``domain.artwork_slot.is_slot_filled``), or, for the icon,
          ``shortcuts.vdf`` points at one (``domain.shortcut_icon.is_icon_filled``)
          — or a recorded ``none`` or ``refused`` rules it out;
        - ``"none"`` — SteamGridDB's list is empty or it does not know the game,
          or the ROM has no SteamGridDB game and the cache holds nothing for the
          slot; recorded;
        - ``"failed"`` — an unsuccessful answer, a list with no usable entry, a
          download or read that failed, or, for the icon, a ``shortcuts.vdf``
          that could not be read or does not hold the shortcut yet; nothing is
          recorded;
        - ``"key_refused"`` — SteamGridDB answered 401 or 403; nothing is recorded;
        - ``"rate_limited"`` — SteamGridDB answered 429; nothing is recorded;
        - ``"no_api_key"`` — no key configured.

        Refuses with ``invalid_payload`` for any other slot, ``no_grid_dir``
        when Steam's grid directory cannot be found (without *replace* only),
        and ``auth_failed`` for a key no header can carry.
        """
        async with self._rules.hold("get_sgdb_slot_artwork", prune=True):
            result = await self._get_sgdb_slot_artwork(rom_id, app_id, slot, bool(replace))
            if result["status"] in ("image", "ready"):
                result["prune_lease_token"] = await self._rules.acquire_lease("sgdb_artwork")
            return result

    async def _get_sgdb_slot_artwork(self, rom_id, app_id, slot, replace: bool):
        rom_id = int(rom_id)
        app_id = int(app_id)
        artwork_slot = _artwork_slot(slot)
        if not self._settings.get("steamgriddb_api_key"):
            return {"status": "no_api_key"}
        _refuse_a_key_that_cannot_be_sent(self._settings["steamgriddb_api_key"])
        result = await self._loop.run_in_executor(None, self._slot_artwork_io, rom_id, app_id, artwork_slot, replace)
        self._log_debug(f"SGDB slot artwork: rom_id={rom_id}, app_id={app_id}, slot={slot}: {result['status']}")
        return result

    def _slot_artwork_io(self, rom_id: int, app_id: int, slot: ArtworkSlot, replace: bool) -> dict[str, Any]:
        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        sgdb_id = int(sgdb_id) if sgdb_id else None
        now = self._clock.time()
        if not replace:
            held = self._held_slot_io(rom_id, app_id, slot, sgdb_id, now)
            if held is not None:
                return {"status": held}
        if sgdb_id is not None:
            found = self._files.fetch_or_answer(rom_id, slot.value, sgdb_id)
        else:
            found = self._cached_without_an_id_io(rom_id, slot.value)
        if found is NoArtwork.NONE:
            self._record_none(rom_id, slot, sgdb_id, now)
            return {"status": "none"}
        if found is NoArtwork.FAILED:
            return {"status": "failed"}
        if isinstance(found, SlotAnswer):
            return {"status": found.value}
        if slot is ArtworkSlot.ICON:
            return {"status": "ready"}
        try:
            data = self._sgdb_artwork_cache.read_bytes(found.path)
        except OSError as e:
            self._logger.warning(f"Failed to read cached artwork {found.path}: {e}")
            return {"status": "failed"}
        return {
            "status": "image",
            "base64": base64.b64encode(data).decode("ascii"),
            "image_id": found.image_id,
            "sha256": hashlib.sha256(data).hexdigest(),
            "sgdb_id": sgdb_id,
        }

    def _cached_without_an_id_io(self, rom_id: int, asset_type: str) -> CachedArtwork | NoArtwork:
        """What the artwork cache holds for *rom_id*'s *asset_type* where the ROM has no SteamGridDB game.

        A manual pick an older release made filled the cache and stored no
        game, so its images are served from there; ``NONE`` when the cache
        holds none, since nothing can be downloaded without an id, and
        ``FAILED`` when the cache cannot be looked up, which is logged.
        """
        try:
            cached = self._files.cached(rom_id, asset_type)
        except Exception as e:
            self._logger.warning(f"SGDB {asset_type} cache lookup failed for rom_id={rom_id}: {e}")
            return NoArtwork.FAILED
        return cached if cached is not None else NoArtwork.NONE

    def _held_slot_io(self, rom_id: int, app_id: int, slot: ArtworkSlot, sgdb_id: int | None, now: float) -> str | None:
        """The status that leaves *slot* alone, or ``None`` where it waits.

        ``skip``, or ``failed`` for an icon ``shortcuts.vdf`` cannot answer for.
        """
        if slot is ArtworkSlot.ICON:
            return self._held_icon_io(rom_id, app_id, sgdb_id, now)
        file_names = self._steam_config.grid_file_names()
        if file_names is None:
            raise Refused("no_grid_dir", "Steam grid directory not found")
        if is_slot_filled(file_names, app_id, slot):
            return "skip"
        recorded = self._recorded_outcome_io(rom_id, slot)
        if recorded is not None and recorded.rules_out(sgdb_id=sgdb_id, now=now):
            return "skip"
        return None

    def _held_icon_io(self, rom_id: int, app_id: int, sgdb_id: int | None, now: float) -> str | None:
        """:meth:`_held_slot_io` for the icon, by the shortcut artwork job's own rule (``icon_waits``).

        Where the grid directory cannot be listed, an icon path in it is left
        alone as filled, as the job leaves it.
        """
        grid_dir = self._steam_config.grid_dir()
        if grid_dir is None:
            raise Refused("no_grid_dir", "Steam grid directory not found")
        icons = self._steam_config.read_shortcut_icons()
        if icons is None or app_id not in icons:
            return "failed"
        try:
            file_names = self._steam_config.grid_file_names()
        except OSError as e:
            self._logger.warning(f"Could not list Steam's grid directory: {e}")
            file_names = None
        recorded = self._recorded_outcome_io(rom_id, ArtworkSlot.ICON)
        waits = icon_waits(icons[app_id], recorded, grid_dir=grid_dir, file_names=file_names, sgdb_id=sgdb_id, now=now)
        return None if waits else "skip"

    def _recorded_outcome_io(self, rom_id: int, slot: ArtworkSlot) -> ArtworkSlotOutcome | None:
        with self._uow_factory() as uow:
            return uow.artwork_slot_outcomes.get(rom_id, slot)

    def _record_none(self, rom_id: int, slot: ArtworkSlot, sgdb_id: int | None, now: float) -> None:
        self._record_slot_outcome(
            ArtworkSlotOutcome.record_none(rom_id=rom_id, slot=slot, sgdb_id=sgdb_id, recorded_at=now)
        )

    def _record_slot_outcome(self, record: ArtworkSlotOutcome) -> None:
        """Save *record*; a ROM with no ``roms`` row has nothing to record it against."""
        with self._uow_factory() as uow:
            if uow.roms.get(record.rom_id) is None:
                self._log_debug(f"SGDB slot outcome not recorded for unsynced rom_id={record.rom_id}")
                return
            uow.artwork_slot_outcomes.save(record)

    async def report_sgdb_slot_artwork(self, rom_id, slot, outcome, sgdb_id, image_id, sha256):
        """Record what Steam made of a wide grid, logo, hero or icon Tender served or wrote.

        *outcome* is ``"set"`` (``SetCustomArtworkForApp``, or
        ``SetShortcutIcon`` for the icon, took it) or ``"refused"`` (it failed
        on the image); *sgdb_id*, *image_id* and *sha256* are what
        ``get_sgdb_slot_artwork`` or ``get_sgdb_artwork_base64`` answered for a
        wide grid, logo or hero, or, for an icon, what :meth:`save_shortcut_icon`
        answered or the shortcut artwork job's batch named, passed back.
        Refuses with ``invalid_payload`` for a slot that is not recorded, an
        unknown outcome, or a ``set`` without its image id and hash.
        """
        async with self._rules.hold("report_sgdb_slot_artwork", prune=True):
            record = self._reported_outcome(int(rom_id), slot, outcome, sgdb_id, image_id, sha256)
            await self._loop.run_in_executor(None, self._record_slot_outcome, record)
            return {"success": True}

    def _reported_outcome(self, rom_id, slot, outcome, sgdb_id, image_id, sha256) -> ArtworkSlotOutcome:
        artwork_slot = _artwork_slot(slot)
        sgdb_id = int(sgdb_id) if sgdb_id is not None else None
        now = self._clock.time()
        if outcome == SlotOutcome.REFUSED:
            return ArtworkSlotOutcome.record_refused(rom_id=rom_id, slot=artwork_slot, sgdb_id=sgdb_id, recorded_at=now)
        if outcome != SlotOutcome.SET:
            raise Refused("invalid_payload", f"Unknown artwork outcome: {outcome}")
        if image_id is None or not sha256:
            raise Refused("invalid_payload", "A set image is reported with its image id and hash")
        return ArtworkSlotOutcome.record_set(
            rom_id=rom_id,
            slot=artwork_slot,
            sgdb_id=sgdb_id,
            image_id=int(image_id),
            image_sha256=str(sha256),
            recorded_at=now,
        )

    # -- API key management ------------------------------------------------

    async def verify_sgdb_api_key(self, api_key=None):
        # Use saved key if no valid key provided (modal pattern doesn't hold the real key)
        if not api_key or api_key == "••••":
            api_key = self._settings.get("steamgriddb_api_key", "")
        if not api_key:
            raise Refused("no_api_key", "No API key configured")
        _refuse_a_key_that_cannot_be_sent(api_key)
        try:
            data = await self._loop.run_in_executor(None, self._sgdb_api.verify_api_key, api_key)
        except SgdbApiError as e:
            self._logger.warning(f"SGDB API key verification failed: {e}")
            raise _refusal_for(e, key_refused=_INVALID_KEY) from e
        if not data.get("success"):
            raise AuthFailed("API key rejected by SteamGridDB")
        return {"success": True, "message": "API key is valid"}

    def save_sgdb_api_key(self, api_key):
        """Store *api_key*; the empty string and the mask store nothing. Refuses a key no header can carry."""
        if api_key and api_key != "••••":
            _refuse_a_key_that_cannot_be_sent(api_key)
            self._settings["steamgriddb_api_key"] = api_key
            self._settings_persister.save_settings()
        return {"success": True, "message": "SteamGridDB API key saved"}

    def remove_sgdb_api_key(self):
        """Forget the stored key; the artwork cache and the images Steam already has stay."""
        self._settings["steamgriddb_api_key"] = ""
        self._settings_persister.save_settings()
        return {"success": True, "message": "SteamGridDB API key removed"}

    # -- cache pruning -----------------------------------------------------

    def prune_orphaned_artwork_cache(self):
        """Remove SGDB artwork cache files for rom_ids not bound in ``uow.roms``."""
        art_dir = self._sgdb_artwork_cache.cache_dir()
        if not self._sgdb_artwork_cache.is_dir(art_dir):
            return
        with self._uow_factory() as uow:
            registry = {str(rom.rom_id) for rom in uow.roms.iter_all() if rom.shortcut_app_id is not None}
        pruned = 0
        for filename in self._sgdb_artwork_cache.listdir(art_dir):
            # Always remove leftover .tmp files
            if filename.endswith(".tmp"):
                try:
                    self._sgdb_artwork_cache.remove_file(os.path.join(art_dir, filename))
                    pruned += 1
                    self._logger.info(f"Removed leftover artwork tmp: {filename}")
                except OSError as e:
                    self._logger.warning(f"Failed to remove artwork tmp {filename}: {e}")
                continue
            # Expected format: {rom_id}_{type}_{image_id}.png, or {rom_id}_{type}.png
            # as an older release named them.
            parts = filename.split("_", 1)
            rom_id = parts[0]
            if rom_id not in registry:
                try:
                    self._sgdb_artwork_cache.remove_file(os.path.join(art_dir, filename))
                    pruned += 1
                except OSError as e:
                    self._logger.warning(f"Failed to remove orphaned artwork {filename}: {e}")
        if pruned:
            self._logger.info(f"Pruned {pruned} orphaned SGDB artwork cache file(s)")

    # -- icon saving -------------------------------------------------------

    def _write_icon_to_grid(self, app_id, icon_bytes):
        """Write *icon_bytes* into Steam's grid dir as they are; return the path or ``None``.

        Pointing the shortcut at this file is the frontend's job via
        ``SteamClient.Apps.SetShortcutIcon`` — Steam holds shortcuts.vdf in
        memory and silently clobbers any external write while it runs — so
        this method only lays down the grid file and never touches
        shortcuts.vdf.
        """
        try:
            return self._steam_config.write_shortcut_icon(app_id, icon_bytes)
        except SteamGridDirMissingError as e:
            self._logger.warning(f"Cannot save icon: {e}")
            return None
        except Exception as e:
            self._logger.error(f"Failed to write icon file for app_id {app_id}: {e}")
            return None

    async def save_shortcut_icon(self, app_id, rom_id):
        """Write *rom_id*'s SteamGridDB icon from the artwork cache, downscaled, as *app_id*'s icon in Steam's grid dir.

        The game page's write, after ``get_sgdb_slot_artwork`` answered the
        icon ``ready``. It moves *app_id*'s icon generation on, so the shortcut
        artwork job writes and hands over no icon over it. Answers the written
        ``icon_path``, for the frontend to point the shortcut at through
        ``SteamClient.Apps.SetShortcutIcon``, with the ``image_id``, the
        ``sha256`` of the bytes written and the ``sgdb_id`` that
        ``report_sgdb_slot_artwork`` records. Refuses with ``icon_write_failed``
        when the cache no longer holds an icon it can read, or the file cannot
        be written.
        """
        async with self._rules.hold("save_shortcut_icon", prune=True):
            app_id = int(app_id)
            async with self._icon_lock(app_id):
                self._icon_generations[app_id] = self.icon_generation(app_id) + 1
                icon = await self._loop.run_in_executor(None, self._write_icon_io, app_id, int(rom_id))
            if icon is None:
                raise Refused("icon_write_failed", "Failed to write icon to Steam grid directory")
            return {
                "success": True,
                "icon_path": icon.path,
                "image_id": icon.image_id,
                "sha256": icon.sha256,
                "sgdb_id": icon.sgdb_id,
            }

    # -- for the shortcut artwork job --------------------------------------

    def _icon_lock(self, app_id: int) -> asyncio.Lock:
        lock = self._icon_locks.get(app_id)
        if lock is None:
            lock = self._icon_locks[app_id] = asyncio.Lock()
        return lock

    def icon_generation(self, app_id: int) -> int:
        """Implements ``services.protocols.ShortcutArtworkSource.icon_generation``.

        The artwork job takes it when it plans a run, before it reads
        ``shortcuts.vdf``, and writes and hands over an icon only while it is
        unchanged, so an icon the game page writes since wins.
        """
        return self._icon_generations.get(int(app_id), 0)

    async def write_job_icon(self, app_id: int, rom_id: int, generation: int) -> WrittenIcon | None:
        """Implements ``services.protocols.ShortcutArtworkSource.write_job_icon``.

        Takes turns with the page's save under one lock per app id, so the
        check and the write cannot be split by one.
        """
        app_id = int(app_id)
        async with self._icon_lock(app_id):
            if self.icon_generation(app_id) != generation:
                return None
            return await self._loop.run_in_executor(None, self._write_icon_io, app_id, int(rom_id))

    def _write_icon_io(self, app_id: int, rom_id: int) -> WrittenIcon | None:
        try:
            return self._write_cached_icon_io(app_id, rom_id)
        except Exception as e:
            self._logger.warning(f"SGDB icon for rom_id={rom_id} could not be read or written: {e}")
            return None

    def _write_cached_icon_io(self, app_id: int, rom_id: int) -> WrittenIcon | None:
        cached = self._files.cached(rom_id, ArtworkSlot.ICON.value)
        if cached is None:
            self._log_debug(f"SGDB icon for rom_id={rom_id} is no longer cached; it is asked for again")
            return None
        icon = self._downscale_icon(self._sgdb_artwork_cache.read_bytes(cached.path))
        if icon is None:
            self._logger.warning(f"SGDB icon for rom_id={rom_id} is not an image Tender reads; removed from the cache")
            self._files.drop(rom_id, ArtworkSlot.ICON.value)
            return None
        path = self._write_icon_to_grid(app_id, icon)
        if path is None:
            return None
        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        return WrittenIcon(
            path=path,
            image_id=cached.image_id,
            sha256=hashlib.sha256(icon).hexdigest(),
            sgdb_id=int(sgdb_id) if sgdb_id else None,
        )

    def do_fetch_slot_artwork(self, rom_id: int, slot: ArtworkSlot) -> SlotAnswer:
        """Implements ``services.protocols.ShortcutArtworkSource.do_fetch_slot_artwork``."""
        rom_id = int(rom_id)
        if not self._settings.get("steamgriddb_api_key"):
            return SlotAnswer.FAILED
        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        sgdb_id = int(sgdb_id) if sgdb_id else None
        found = self._files.fetch_or_answer(rom_id, slot.value, sgdb_id) if sgdb_id is not None else NoArtwork.NONE
        if found is NoArtwork.NONE:
            self._record_none(rom_id, slot, sgdb_id, self._clock.time())
            return SlotAnswer.NONE
        if found is NoArtwork.FAILED:
            return SlotAnswer.FAILED
        if isinstance(found, SlotAnswer):
            return found
        return SlotAnswer.READY
