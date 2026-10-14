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
from enum import Enum
from typing import TYPE_CHECKING, Any

from domain.artwork_slot import RECORDED_SLOTS, ArtworkSlot, is_slot_filled
from domain.artwork_slot_outcome import ArtworkSlotOutcome, SlotOutcome
from domain.sgdb_artwork import (
    asset_type_name,
    build_autocomplete_path,
    cached_artwork_name,
    cached_image_id,
    cached_slot_of,
    choose_candidate,
    classify_resolution,
    first_grid_url,
    named_by_image_id,
    parse_autocomplete_results,
    sgdb_endpoint_path,
)
from lib.errors import (
    AuthFailed,
    Refused,
    ServerUnreachable,
    SgdbApiError,
    SgdbRateLimitedError,
    SteamGridDirMissingError,
)
from models.shortcut_artwork import IconAnswer, IconFetch, SlotAnswer

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


@dataclass(frozen=True)
class _CachedArtwork:
    """A file the artwork cache holds, and the SteamGridDB image id its name carries."""

    path: str
    image_id: int | None


class _NoArtwork(Enum):
    """Why a download answered no file: SteamGridDB's list is empty, or the answer or download did not succeed."""

    NONE = "none"
    FAILED = "failed"


def _artwork_slot(slot: object) -> ArtworkSlot:
    """The slot *slot* names; refuses one that names none."""
    try:
        return ArtworkSlot(slot)
    except ValueError as e:
        raise Refused("invalid_payload", f"Unknown artwork slot: {slot}") from e


_KEY_REFUSED = "SteamGridDB refused the API key — check it in Settings"
_RATE_LIMITED = "SteamGridDB is rate-limiting requests — try again in a moment"
_ASSET_TYPES = ("hero", "logo", "grid", "icon")


def _refusal_for(error: SgdbApiError, *, key_refused: str = _KEY_REFUSED) -> Refused:
    """The refusal an ``SgdbApiError`` answers with, *key_refused* for a 401 or 403; never the error's own text."""
    if error.status_code in (401, 403):
        return AuthFailed(key_refused)
    if error.status_code == 429:
        return Refused("rate_limited", _RATE_LIMITED)
    if error.status_code is None:
        return ServerUnreachable("Could not reach SteamGridDB")
    return ServerUnreachable(f"SteamGridDB error: HTTP {error.status_code}")


def _refuse_a_key_that_cannot_be_sent(api_key: str) -> None:
    """Refuse a key no HTTP header can carry, one with a CR or LF or past latin-1, as ``auth_failed``.

    Checked before the key reaches the adapter: ``http.client`` raises on such a
    header value, and the exception holds the whole ``Bearer`` value.
    """
    if any(ch in "\r\n" or ch > "\xff" for ch in api_key):
        raise AuthFailed("Invalid API key")


@dataclass(frozen=True)
class SteamGridServiceConfig:
    """Frozen wiring bundle handed to ``SteamGridService.__init__``.

    Holds the Protocol-typed adapters (``sgdb_api``, ``romm_api``,
    ``steam_config``, ``sgdb_artwork_cache``), the live settings dict,
    runtime infrastructure, the ``settings.json`` persister, the SQLite
    Unit-of-Work factory (a resolved ``sgdb_id`` is persisted onto the
    ``roms`` aggregate via the UoW), the pending-sync read seam, the
    debug-logger seam SteamGridService needs at construction time, the
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
        # Per app id: the count ``icon_generation`` answers, and the lock a page
        # save and the artwork job's write take turns under.
        self._icon_generations: dict[int, int] = {}
        self._icon_locks: dict[int, asyncio.Lock] = {}
        # Per ROM and asset type, the wide grid and logo names in the artwork
        # cache: listed once, then kept by this service's own downloads and
        # removals, so a lookup does not list the whole cache. Lookups run on
        # executor threads and a service takes no lock, so each entry is
        # replaced rather than changed: a reader holds a frozen snapshot. A note
        # two threads race to lose costs one download more, nothing else.
        self._slot_cache_names: dict[tuple[int, str], frozenset[str]] | None = None

    # -- artwork download --------------------------------------------------

    def _download_sgdb_artwork(self, sgdb_game_id, rom_id, asset_type):
        """Answer the cached file of *rom_id*'s *asset_type*, downloading it first when it is not cached.

        ``None`` when SteamGridDB has no candidate or the download failed; an
        exception is logged.
        """
        found = self._download_cached_artwork(sgdb_game_id, rom_id, asset_type)
        return found.path if found is not None else None

    def _download_cached_artwork(self, sgdb_game_id, rom_id, asset_type) -> _CachedArtwork | None:
        """:meth:`_download_sgdb_artwork`, answering the cached file with the image id its name carries."""
        try:
            found = self._download_sgdb_artwork_io(sgdb_game_id, rom_id, asset_type)
        except Exception as e:
            self._logger.warning(f"SGDB {asset_type} download failed for game {sgdb_game_id}: {e}")
            return None
        return found if isinstance(found, _CachedArtwork) else None

    def _download_sgdb_artwork_io(self, sgdb_game_id, rom_id, asset_type) -> _CachedArtwork | _NoArtwork:
        """The cached file of *rom_id*'s *asset_type*, downloaded first when it is not cached.

        The image is chosen among SteamGridDB's candidates by ``choose_candidate``.
        Answers ``NONE`` when SteamGridDB's list is empty, and ``FAILED`` for an
        unsuccessful answer, a list with no usable entry or a failed download;
        lets the API's errors through. A downloaded wide grid or logo replaces every other cache file
        of that slot.
        """
        cached = self._cached_artwork(rom_id, asset_type)
        if cached is not None:
            return cached
        return self._fetch_sgdb_artwork_io(sgdb_game_id, rom_id, asset_type)

    def _fetch_sgdb_artwork_io(self, sgdb_game_id, rom_id, asset_type) -> _CachedArtwork | _NoArtwork:
        """:meth:`_download_sgdb_artwork_io` without its cache check.

        It drops the slot's other cache files only once the download succeeded.
        """
        path = sgdb_endpoint_path(asset_type, sgdb_game_id)
        if path is None:
            return _NoArtwork.FAILED
        result = self._sgdb_api.request(path)
        if not result or not result.get("success"):
            return _NoArtwork.FAILED
        if not result.get("data"):
            return _NoArtwork.NONE
        candidate = choose_candidate(asset_type, result["data"])
        if candidate is None:
            return _NoArtwork.FAILED
        art_dir = self._sgdb_artwork_cache.cache_dir()
        target = os.path.join(art_dir, cached_artwork_name(rom_id, asset_type, candidate.image_id))
        if not self._sgdb_api.download_image(candidate.url, target):
            return _NoArtwork.FAILED
        self._note_cache_name(rom_id, asset_type, os.path.basename(target), held=True)
        self._drop_cached_artwork(rom_id, asset_type, keep=os.path.basename(target))
        return _CachedArtwork(path=target, image_id=cached_image_id(os.path.basename(target), rom_id, asset_type))

    def _cached_artwork(self, rom_id, asset_type) -> _CachedArtwork | None:
        """The cache file of *rom_id*'s *asset_type*, or ``None`` when the cache holds none.

        A wide grid or logo whose name carries no image id is not counted, so
        it is downloaded again and replaced; nor is a name the cache no longer
        holds.
        """
        art_dir = self._sgdb_artwork_cache.cache_dir()
        for name in self._cache_names(rom_id, asset_type):
            image_id = cached_image_id(name, rom_id, asset_type)
            if named_by_image_id(asset_type) and image_id is None:
                continue
            path = os.path.join(art_dir, name)
            if self._sgdb_artwork_cache.exists(path):
                return _CachedArtwork(path=path, image_id=image_id)
            self._note_cache_name(rom_id, asset_type, name, held=False)
        return None

    def _drop_cached_artwork(self, rom_id, asset_type, *, keep: str | None = None) -> None:
        """Remove every cache file of *rom_id*'s *asset_type* but *keep* (sync)."""
        art_dir = self._sgdb_artwork_cache.cache_dir()
        for name in self._cache_names(rom_id, asset_type):
            if name == keep:
                continue
            try:
                self._sgdb_artwork_cache.remove_file(os.path.join(art_dir, name))
            except OSError as e:
                self._logger.warning(f"Failed to clear cached artwork {name}: {e}")
                continue
            self._note_cache_name(rom_id, asset_type, name, held=False)

    def _cache_names(self, rom_id, asset_type) -> list[str]:
        """The names *rom_id*'s *asset_type* may be cached under, sorted; the cache is listed only the first time."""
        rom_id = int(rom_id)
        if not named_by_image_id(asset_type):
            return [cached_artwork_name(rom_id, asset_type, None)]
        names = self._slot_cache_names
        if names is None:
            listed: dict[tuple[int, str], set[str]] = {}
            for name in self._sgdb_artwork_cache.listdir(self._sgdb_artwork_cache.cache_dir()):
                slot = cached_slot_of(name)
                if slot is not None:
                    listed.setdefault(slot, set()).add(name)
            names = self._slot_cache_names = {slot: frozenset(held) for slot, held in listed.items()}
        return sorted(names.get((rom_id, asset_type), ()))

    def _note_cache_name(self, rom_id, asset_type, name: str, *, held: bool) -> None:
        """Note that the cache now holds *name* for *rom_id*'s wide grid or logo, or no longer does."""
        names = self._slot_cache_names
        if names is None or not named_by_image_id(asset_type):
            return
        key = (int(rom_id), asset_type)
        before = names.get(key, frozenset())
        names[key] = before | {name} if held else before - {name}

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
        """Resolve a SGDB game ID from local state only — no network.

        Checks the persisted ROM row first, then the in-memory
        pending-sync map. Returns ``None`` when neither carries a
        ``sgdb_id``. RomM re-reads and the name search are left to the
        explicit ``get_sgdb_resolution`` user action.
        """
        rom_id = int(rom_id)
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
        sgdb_id = rom.sgdb_id if rom is not None else None
        if not sgdb_id:
            pending = self._get_pending_sync().get(rom_id, {})
            sgdb_id = pending.get("sgdb_id")
        return sgdb_id

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
        ``sgdb_id`` always wins when present. Returns one of:

        - ``{"decision": "no_api_key"}`` — no key configured.
        - ``{"decision": "resolved", "sgdb_id": int}`` — a winning id was
          found (and persisted when it came from RomM).
        - ``{"decision": "needs_pick", "candidates": [...]}`` — no id is
          stored or pending and RomM has none; offer a manual name-search
          picker, with no candidates when the name search fails.

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

        state_id = self._resolve_sgdb_id_state_only(rom_id)
        romm_id, rom_data = await self._fetch_sgdb_id_from_romm(rom_id)

        decision = classify_resolution(state_id, romm_id)
        if decision == "use_romm" and romm_id is not None:
            if romm_id != state_id:
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
        """Paint a manually-picked game's artwork into *rom_id*'s cache.

        A manual pick only paints pixels — it downloads the chosen
        game's four asset types into the rom's artwork cache (which the
        cache-first ``get_sgdb_artwork_base64`` then serves onto the
        Steam shortcut) and persists **nothing**. Only RomM's ``sgdb_id``
        is remembered as the resolved id; a manual pick is not. So a later
        "Refresh Artwork" on an otherwise-unresolvable rom stays
        ``needs_pick`` and reopens the picker, giving the user a free
        re-pick. The previously applied art stays visible until replaced.

        The previous pick's cached images go only once SteamGridDB has answered
        without refusing: ``auth_failed`` for a refused key, ``rate_limited``
        for a 429, as :meth:`refresh_sgdb_artwork` refuses.
        """
        async with self._rules.hold("apply_sgdb_game_id", prune=True):
            return await self._apply_sgdb_game_id(rom_id, sgdb_id)

    async def _apply_sgdb_game_id(self, rom_id, sgdb_id):
        rom_id = int(rom_id)
        sgdb_id = int(sgdb_id)
        _refuse_a_key_that_cannot_be_sent(self._settings.get("steamgriddb_api_key", ""))
        answers = await self._download_all_anew(rom_id, sgdb_id)
        for asset_type, answer in zip(_ASSET_TYPES, answers, strict=True):
            if not isinstance(answer, _CachedArtwork):
                await self._loop.run_in_executor(None, self._drop_cached_artwork, rom_id, asset_type)
        return {"success": True}

    async def _download_all_anew(self, rom_id: int, sgdb_id: int) -> list[_CachedArtwork | _NoArtwork | SlotAnswer]:
        """Download the four asset types past the cache, in ``_ASSET_TYPES`` order; refuses a refused key or a 429."""
        downloads = [
            self._loop.run_in_executor(None, self._download_or_answer_io, rom_id, asset_type, sgdb_id, True)
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
        """Serve one SteamGridDB asset for *rom_id* as base64, from the cache first.

        An answer that carries an image also carries an ``sgdb_artwork`` lease
        in ``prune_lease_token`` for the frontend's Steam write.
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

        # Return from cache if available
        cached = await self._loop.run_in_executor(None, self._cached_artwork, rom_id, asset_type)
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

    async def _artwork_answer(self, rom_id: int, cached: _CachedArtwork) -> dict[str, Any] | None:
        """The answer serving *cached*, or ``None`` when it cannot be read.

        A wide grid or logo also carries its SteamGridDB image id, the SHA-256
        of its bytes and the ROM's SteamGridDB id, which the frontend passes to
        ``report_sgdb_slot_artwork`` once Steam has taken it or refused it.
        """
        b64 = await self._read_file_as_base64(cached.path)
        if not b64:
            return None
        answer: dict[str, Any] = {"base64": b64, "no_api_key": False}
        if cached.image_id is not None:
            sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
            answer["image_id"] = cached.image_id
            answer["sha256"] = hashlib.sha256(base64.b64decode(b64)).hexdigest()
            answer["sgdb_id"] = int(sgdb_id) if sgdb_id else None
        return answer

    # -- one artwork slot, filled only when empty (use cases) ---------------

    async def get_sgdb_slot_artwork(self, rom_id, app_id, slot):
        """Serve SteamGridDB's image for one of *app_id*'s artwork slots, only while the slot is empty.

        *slot* is ``"grid"`` (the wide grid), ``"logo"`` or ``"hero"``. Answers
        a discriminated ``status``:

        - ``"image"`` — ``base64``, SteamGridDB's ``image_id`` (``None`` for the
          hero), the bytes' ``sha256`` and the ``sgdb_id`` it was found under,
          which the frontend passes back to ``report_sgdb_slot_artwork``, and an
          ``sgdb_artwork`` lease in ``prune_lease_token`` for its Steam write;
        - ``"skip"`` — Steam's grid directory already fills the slot
          (``domain.artwork_slot.is_slot_filled``), or a recorded ``none`` or
          ``refused`` rules it out;
        - ``"none"`` — SteamGridDB's list is empty or it does not know the game,
          or the ROM has no SteamGridDB id; recorded for the wide grid and the
          logo;
        - ``"failed"`` — an unsuccessful answer, a list with no usable entry, or
          a download or read that failed; nothing is recorded;
        - ``"key_refused"`` — SteamGridDB answered 401 or 403; nothing is recorded;
        - ``"rate_limited"`` — SteamGridDB answered 429; nothing is recorded;
        - ``"no_api_key"`` — no key configured.

        Refuses with ``invalid_payload`` for an unknown slot, ``no_grid_dir``
        when Steam's grid directory cannot be found, and ``auth_failed`` for a
        key no header can carry.
        """
        async with self._rules.hold("get_sgdb_slot_artwork", prune=True):
            result = await self._get_sgdb_slot_artwork(rom_id, app_id, slot)
            if result["status"] == "image":
                result["prune_lease_token"] = await self._rules.acquire_lease("sgdb_artwork")
            return result

    async def _get_sgdb_slot_artwork(self, rom_id, app_id, slot):
        rom_id = int(rom_id)
        app_id = int(app_id)
        artwork_slot = _artwork_slot(slot)
        if not self._settings.get("steamgriddb_api_key"):
            return {"status": "no_api_key"}
        _refuse_a_key_that_cannot_be_sent(self._settings["steamgriddb_api_key"])
        result = await self._loop.run_in_executor(None, self._slot_artwork_io, rom_id, app_id, artwork_slot)
        self._log_debug(f"SGDB slot artwork: rom_id={rom_id}, app_id={app_id}, slot={slot}: {result['status']}")
        return result

    def _slot_artwork_io(self, rom_id: int, app_id: int, slot: ArtworkSlot) -> dict[str, Any]:
        file_names = self._steam_config.grid_file_names()
        if file_names is None:
            raise Refused("no_grid_dir", "Steam grid directory not found")
        if is_slot_filled(file_names, app_id, slot):
            return {"status": "skip"}
        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        sgdb_id = int(sgdb_id) if sgdb_id else None
        now = self._clock.time()
        if slot in RECORDED_SLOTS:
            with self._uow_factory() as uow:
                recorded = uow.artwork_slot_outcomes.get(rom_id, slot)
            if recorded is not None and recorded.rules_out(sgdb_id=sgdb_id, now=now):
                return {"status": "skip"}
        found = self._download_or_answer_io(rom_id, slot.value, sgdb_id) if sgdb_id is not None else _NoArtwork.NONE
        if found is _NoArtwork.NONE:
            if slot in RECORDED_SLOTS:
                self._record_none(rom_id, slot, sgdb_id, now)
            return {"status": "none"}
        if found is _NoArtwork.FAILED:
            return {"status": "failed"}
        if isinstance(found, SlotAnswer):
            return {"status": found.value}
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
        """Record what Steam made of an image ``get_sgdb_slot_artwork`` served for a wide grid or logo.

        *outcome* is ``"set"`` (``SetCustomArtworkForApp`` took it) or
        ``"refused"`` (it failed on the image); *sgdb_id*, *image_id* and
        *sha256* are the fetch's own answer, passed back. Refuses with
        ``invalid_payload`` for a slot that is not recorded, an unknown outcome,
        or a ``set`` without its image id and hash.
        """
        async with self._rules.hold("report_sgdb_slot_artwork", prune=True):
            record = self._reported_outcome(int(rom_id), slot, outcome, sgdb_id, image_id, sha256)
            await self._loop.run_in_executor(None, self._record_slot_outcome, record)
            return {"success": True}

    def _reported_outcome(self, rom_id, slot, outcome, sgdb_id, image_id, sha256) -> ArtworkSlotOutcome:
        artwork_slot = _artwork_slot(slot)
        if artwork_slot not in RECORDED_SLOTS:
            raise Refused("invalid_payload", f"No outcome is recorded for the {slot} slot")
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
            raise _refusal_for(e, key_refused="Invalid API key") from e
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
            # Expected format: {rom_id}_{type}.png
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

    def _save_icon_to_grid(self, app_id, icon_bytes):
        """Write the icon, downscaled, into Steam's grid dir; return its path or ``None``.

        One the downscale cannot read is written as it came.
        """
        icon = self._downscale_icon(icon_bytes)
        if icon is None:
            self._logger.warning(f"Icon for app_id {app_id} is not an image Tender reads; written as it came")
            icon = icon_bytes
        return self._write_icon_to_grid(app_id, icon)

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

    async def save_shortcut_icon(self, app_id, icon_base64):
        """Write a frontend-supplied icon PNG into Steam's grid directory.

        Returns the written ``icon_path`` on success so the frontend can
        point the shortcut at it via ``SteamClient.Apps.SetShortcutIcon``.
        Refuses with ``invalid_payload`` when the data does not decode as base64
        and with ``icon_write_failed`` when the file cannot be written.
        """
        async with self._rules.hold("save_shortcut_icon", prune=True):
            return await self._save_shortcut_icon(app_id, icon_base64)

    async def _save_shortcut_icon(self, app_id, icon_base64):
        app_id = int(app_id)
        try:
            icon_bytes = base64.b64decode(icon_base64)
        except ValueError as e:
            self._logger.error(f"Failed to decode icon base64: {e}")
            raise Refused("invalid_payload", "Failed to decode icon data") from e

        async with self._icon_lock(app_id):
            self._icon_generations[app_id] = self._icon_generations.get(app_id, 0) + 1
            icon_path = await self._loop.run_in_executor(None, self._save_icon_to_grid, app_id, icon_bytes)
        if not icon_path:
            raise Refused("icon_write_failed", "Failed to write icon to Steam grid directory")
        return {"success": True, "icon_path": icon_path}

    # -- for the shortcut artwork job --------------------------------------

    def _icon_lock(self, app_id: int) -> asyncio.Lock:
        lock = self._icon_locks.get(app_id)
        if lock is None:
            lock = self._icon_locks[app_id] = asyncio.Lock()
        return lock

    def icon_generation(self, app_id: int) -> int:
        """Implements ``services.protocols.ShortcutArtworkSource.icon_generation``.

        The artwork job reads it before it fetches, and its write lands only while
        it is unchanged, so an icon picked on the game page meanwhile wins.
        """
        return self._icon_generations.get(int(app_id), 0)

    async def write_job_icon(self, app_id: int, icon_bytes: bytes, generation: int) -> str | None:
        """Write the artwork job's icon for *app_id*, which its fetch downscaled; answer its path.

        ``None`` when the game page has saved an icon for it since *generation*
        was read, or when the file could not be written. Takes turns with the
        page's save under one lock per app id, so the check and the write
        cannot be split by one.
        """
        app_id = int(app_id)
        async with self._icon_lock(app_id):
            if self.icon_generation(app_id) != generation:
                return None
            return await self._loop.run_in_executor(None, self._write_icon_to_grid, app_id, icon_bytes)

    def do_fetch_shortcut_icon(self, rom_id: int) -> IconFetch:
        """Implements ``services.protocols.ShortcutArtworkSource.do_fetch_shortcut_icon``."""
        rom_id = int(rom_id)
        if not self._settings.get("steamgriddb_api_key"):
            return IconFetch(IconAnswer.FAILED)
        try:
            cached = os.path.join(self._sgdb_artwork_cache.cache_dir(), f"{rom_id}_icon.png")
            if not self._sgdb_artwork_cache.exists(cached):
                answer = self._download_icon_io(rom_id, cached)
                if answer is not None:
                    return IconFetch(answer)
            return self._downscaled(rom_id, cached)
        except SgdbRateLimitedError:
            return IconFetch(IconAnswer.RATE_LIMITED)
        except SgdbApiError as e:
            # SteamGridDB answers 404 ("Game not found.") for a game id it does
            # not know, as its own client library, npm's steamgriddb, documents.
            if e.status_code == 404:
                return IconFetch(IconAnswer.NO_ICON)
            if e.status_code in (401, 403):
                return IconFetch(IconAnswer.KEY_REFUSED)
            self._logger.warning(f"SGDB icon fetch failed for rom_id={rom_id}: HTTP {e.status_code}")
            return IconFetch(IconAnswer.FAILED)
        except Exception as e:
            self._logger.warning(f"SGDB icon fetch failed for rom_id={rom_id}: {e}")
            return IconFetch(IconAnswer.FAILED)

    def _download_icon_io(self, rom_id: int, cached: str) -> IconAnswer | None:
        """Download *rom_id*'s icon to *cached*; ``None`` once it is there, else what to answer instead.

        Lets the API's errors through.
        """
        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        if not sgdb_id:
            return IconAnswer.NO_ICON
        path = sgdb_endpoint_path("icon", sgdb_id)
        result = self._sgdb_api.request(path) if path else None
        if result is None or not result.get("success"):
            return IconAnswer.FAILED
        if not result.get("data"):
            return IconAnswer.NO_ICON
        if not self._sgdb_api.download_image(result["data"][0]["url"], cached):
            return IconAnswer.FAILED
        return None

    def do_fetch_slot_artwork(self, rom_id: int, slot: ArtworkSlot) -> SlotAnswer:
        """Implements ``services.protocols.ShortcutArtworkSource.do_fetch_slot_artwork``."""
        rom_id = int(rom_id)
        if not self._settings.get("steamgriddb_api_key"):
            return SlotAnswer.FAILED
        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        sgdb_id = int(sgdb_id) if sgdb_id else None
        found = self._download_or_answer_io(rom_id, slot.value, sgdb_id) if sgdb_id is not None else _NoArtwork.NONE
        if found is _NoArtwork.NONE:
            self._record_none(rom_id, slot, sgdb_id, self._clock.time())
            return SlotAnswer.NONE
        if found is _NoArtwork.FAILED:
            return SlotAnswer.FAILED
        if isinstance(found, SlotAnswer):
            return found
        return SlotAnswer.READY

    def _download_or_answer_io(
        self, rom_id: int, asset_type: str, sgdb_id: int, anew: bool = False
    ) -> _CachedArtwork | _NoArtwork | SlotAnswer:
        """*rom_id*'s cached *asset_type*, downloaded first when needed or *anew*, or what a 429, 401 or 403 answers.

        A 404 is SteamGridDB not knowing the game, as ``do_fetch_shortcut_icon`` reads it.
        """
        download = self._fetch_sgdb_artwork_io if anew else self._download_sgdb_artwork_io
        try:
            return download(sgdb_id, rom_id, asset_type)
        except SgdbRateLimitedError:
            return SlotAnswer.RATE_LIMITED
        except SgdbApiError as e:
            if e.status_code == 404:
                return _NoArtwork.NONE
            if e.status_code in (401, 403):
                return SlotAnswer.KEY_REFUSED
            self._logger.warning(f"SGDB {asset_type} fetch failed for rom_id={rom_id}: HTTP {e.status_code}")
        except Exception as e:
            self._logger.warning(f"SGDB {asset_type} fetch failed for rom_id={rom_id}: {e}")
        return _NoArtwork.FAILED

    def _downscaled(self, rom_id: int, cached: str) -> IconFetch:
        icon = self._downscale_icon(self._sgdb_artwork_cache.read_bytes(cached))
        if icon is not None:
            return IconFetch(IconAnswer.ICON, icon)
        self._logger.warning(f"SGDB icon for rom_id={rom_id} is not an image Tender reads; removed from the cache")
        try:
            self._sgdb_artwork_cache.remove_file(cached)
        except OSError as e:
            self._logger.warning(f"Failed to remove the cached icon for rom_id={rom_id}: {e}")
        return IconFetch(IconAnswer.FAILED)
