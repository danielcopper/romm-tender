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
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from models.shortcut_icon import IconAnswer, IconFetch

from domain.sgdb_artwork import (
    asset_type_endpoint,
    asset_type_name,
    build_autocomplete_path,
    classify_resolution,
    first_grid_url,
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

if TYPE_CHECKING:
    import logging

    from services.protocols import (
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


def _refusal_for(error: SgdbApiError) -> Refused:
    """The refusal an ``SgdbApiError`` answers with; never the error's own text."""
    if error.status_code in (401, 403):
        return AuthFailed("Invalid API key")
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
    Unit-of-Work factory (the ``sgdb_id`` cross-ref is persisted onto the
    ``roms`` aggregate via the UoW), the pending-sync read seam, the
    debug-logger seam SteamGridService needs at construction time, the
    ``ConflictRules`` the artwork, icon, resolution and game-id use cases check
    at their entry, and the artwork one takes the ``sgdb_artwork`` lease through,
    and the downscale every icon passes through before it is written.
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
        # Per app id: the count ``icon_generation`` answers, and the lock a page
        # save and the icon job's write take turns under.
        self._icon_generations: dict[int, int] = {}
        self._icon_locks: dict[int, asyncio.Lock] = {}

    # -- SGDB lookup -------------------------------------------------------

    def _get_sgdb_game_id(self, igdb_id):
        try:
            result = self._sgdb_api.request(f"/games/igdb/{igdb_id}")
            if result and result.get("success") and result.get("data"):
                return result["data"]["id"]
        except Exception as e:
            self._logger.warning(f"SGDB lookup failed for IGDB {igdb_id}: {e}")
        return None

    # -- artwork download --------------------------------------------------

    def _download_sgdb_artwork(self, sgdb_game_id, rom_id, asset_type):
        if asset_type_endpoint(asset_type) is None:
            return None

        art_dir = self._sgdb_artwork_cache.cache_dir()
        cached = os.path.join(art_dir, f"{rom_id}_{asset_type}.png")
        if self._sgdb_artwork_cache.exists(cached):
            return cached

        path = sgdb_endpoint_path(asset_type, sgdb_game_id)
        if path is None:
            return None

        try:
            result = self._sgdb_api.request(path)
            if not result or not result.get("success") or not result.get("data"):
                return None
            image_url = result["data"][0]["url"]
            success = self._sgdb_api.download_image(image_url, cached)
            return cached if success else None
        except Exception as e:
            self._logger.warning(f"SGDB {asset_type} download failed for game {sgdb_game_id}: {e}")
            return None

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

    async def _fetch_ids_from_romm(self, rom_id):
        """Fetch ``(sgdb_id, igdb_id, rom_data)`` from RomM without persisting.

        Reads the ROM detail from RomM and surfaces its ``sgdb_id`` /
        ``igdb_id`` fields. Persistence is the caller's decision — this
        method never writes the registry. Returns ``(None, None, None)``
        on a network failure (logged, never raised).
        """
        sgdb_id = None
        igdb_id = None
        rom_data = None
        try:
            rom_data = await self._loop.run_in_executor(None, self._romm_api.get_rom, rom_id)
            if rom_data:
                sgdb_id = rom_data.get("sgdb_id")
                igdb_id = rom_data.get("igdb_id")
            self._log_debug(f"SGDB artwork: fetched sgdb_id={sgdb_id}, igdb_id={igdb_id} from RomM for rom_id={rom_id}")
        except Exception as e:
            self._logger.warning(f"SGDB artwork: failed to fetch IDs from RomM for rom_id={rom_id}: {e}")
        return sgdb_id, igdb_id, rom_data

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
        name search; the shortcut icon job runs the IGDB cross-ref without
        one. RomM's ``sgdb_id`` always wins when present. Returns one of:

        - ``{"decision": "no_api_key"}`` — no key configured.
        - ``{"decision": "resolved", "sgdb_id": int}`` — a winning id was
          found (and persisted when it came from RomM or IGDB).
        - ``{"decision": "needs_pick", "candidates": [...]}`` — nothing
          resolved automatically; offer a manual name-search picker, with no
          candidates when the name search is refused.
        """
        async with self._rules.hold("get_sgdb_resolution", prune=True):
            return await self._get_sgdb_resolution(rom_id)

    async def _get_sgdb_resolution(self, rom_id):
        rom_id = int(rom_id)
        rom_id_str = str(rom_id)
        if not self._settings.get("steamgriddb_api_key"):
            return {"decision": "no_api_key"}
        _refuse_a_key_that_cannot_be_sent(self._settings["steamgriddb_api_key"])

        state_id = self._resolve_sgdb_id_state_only(rom_id)
        romm_id, igdb_id, rom_data = await self._fetch_ids_from_romm(rom_id)

        decision = classify_resolution(state_id, romm_id)
        if decision == "use_romm" and romm_id is not None:
            if romm_id != state_id:
                self._persist_sgdb_id(rom_id_str, romm_id)
            return {"decision": "resolved", "sgdb_id": int(romm_id)}
        if decision == "use_state" and state_id is not None:
            return {"decision": "resolved", "sgdb_id": int(state_id)}

        # Unresolved: try IGDB cross-ref, then fall back to a name search.
        if igdb_id:
            resolved = await self._loop.run_in_executor(None, self._get_sgdb_game_id, igdb_id)
            if resolved:
                self._persist_sgdb_id(rom_id_str, resolved)
                return {"decision": "resolved", "sgdb_id": int(resolved)}

        name = (rom_data or {}).get("name") or ""
        try:
            search = await self.search_sgdb_games(name)
        except Refused:
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
        Steam shortcut) and persists **nothing**. Only authoritative ids
        (RomM ``sgdb_id`` or an IGDB cross-ref) are remembered as the
        resolved id; a manual pick is not. So a later "Refresh Artwork"
        on an otherwise-unresolvable rom stays ``needs_pick`` and reopens
        the picker, giving the user a free re-pick. The previously
        applied art stays visible until replaced.
        """
        async with self._rules.hold("apply_sgdb_game_id", prune=True):
            return await self._apply_sgdb_game_id(rom_id, sgdb_id)

    async def _apply_sgdb_game_id(self, rom_id, sgdb_id):
        rom_id = int(rom_id)
        sgdb_id = int(sgdb_id)
        _refuse_a_key_that_cannot_be_sent(self._settings.get("steamgriddb_api_key", ""))

        # Start clean: ``_download_sgdb_artwork`` early-returns an
        # existing cache file, so a re-pick of a different game must
        # evict the prior PNGs before downloading.
        await self._loop.run_in_executor(None, self._clear_cached_artwork, rom_id)

        downloads = [
            self._loop.run_in_executor(None, self._download_sgdb_artwork, sgdb_id, rom_id, asset_type)
            for asset_type in ("hero", "logo", "grid", "icon")
        ]
        await asyncio.gather(*downloads)
        return {"success": True}

    def _clear_cached_artwork(self, rom_id):
        """Remove the four cached artwork PNGs for *rom_id* (sync)."""
        art_dir = self._sgdb_artwork_cache.cache_dir()
        for asset_type in ("hero", "logo", "grid", "icon"):
            path = os.path.join(art_dir, f"{rom_id}_{asset_type}.png")
            if self._sgdb_artwork_cache.exists(path):
                try:
                    self._sgdb_artwork_cache.remove_file(path)
                except OSError as e:
                    self._logger.warning(f"Failed to clear cached artwork {path}: {e}")

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

        art_dir = self._sgdb_artwork_cache.cache_dir()
        cached = os.path.join(art_dir, f"{rom_id}_{asset_type}.png")

        # Return from cache if available
        if self._sgdb_artwork_cache.exists(cached):
            self._log_debug(f"SGDB artwork cache hit: {cached}")
            b64 = await self._read_file_as_base64(cached)
            if b64:
                return {"base64": b64, "no_api_key": False}

        if not self._settings.get("steamgriddb_api_key"):
            self._log_debug("SGDB artwork skipped: no API key configured")
            return {"base64": None, "no_api_key": True}
        _refuse_a_key_that_cannot_be_sent(self._settings["steamgriddb_api_key"])

        sgdb_id = self._resolve_sgdb_id_state_only(rom_id)
        if not sgdb_id:
            self._log_debug(f"SGDB artwork skipped: no SGDB game found for rom_id={rom_id}")
            return {"base64": None, "no_api_key": False}

        path = await self._loop.run_in_executor(None, self._download_sgdb_artwork, sgdb_id, rom_id, asset_type)
        if path and self._sgdb_artwork_cache.exists(path):
            self._log_debug(f"SGDB artwork download success: rom_id={rom_id}, asset_type={asset_type}")
            b64 = await self._read_file_as_base64(path)
            if b64:
                return {"base64": b64, "no_api_key": False}
        else:
            self._log_debug(f"SGDB artwork download failed: rom_id={rom_id}, asset_type={asset_type}")

        return {"base64": None, "no_api_key": False}

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
            raise _refusal_for(e) from e
        if not data.get("success"):
            raise AuthFailed("API key rejected by SteamGridDB")
        return {"success": True, "message": "API key is valid"}

    def save_sgdb_api_key(self, api_key):
        if api_key and api_key != "••••":
            self._settings["steamgriddb_api_key"] = api_key
            self._settings_persister.save_settings()
        return {"success": True, "message": "SteamGridDB API key saved"}

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

    # -- icons for the shortcut icon job ------------------------------------

    def _icon_lock(self, app_id: int) -> asyncio.Lock:
        lock = self._icon_locks.get(app_id)
        if lock is None:
            lock = self._icon_locks[app_id] = asyncio.Lock()
        return lock

    def icon_generation(self, app_id: int) -> int:
        """Implements ``services.protocols.ShortcutIconSource.icon_generation``.

        The icon job reads it before it fetches, and its write lands only while
        it is unchanged, so an icon picked on the game page meanwhile wins.
        """
        return self._icon_generations.get(int(app_id), 0)

    async def write_job_icon(self, app_id: int, icon_bytes: bytes, generation: int) -> str | None:
        """Write the icon job's icon for *app_id*, which its fetch downscaled; answer its path.

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
        """Implements ``services.protocols.ShortcutIconSource.do_fetch_shortcut_icon``."""
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
            found = self._cross_ref_igdb_io(rom_id)
            if isinstance(found, IconAnswer):
                return found
            sgdb_id = found
        path = sgdb_endpoint_path("icon", sgdb_id)
        result = self._sgdb_api.request(path) if path else None
        if result is None or not result.get("success"):
            return IconAnswer.FAILED
        if not result.get("data"):
            return IconAnswer.NO_ICON
        if not self._sgdb_api.download_image(result["data"][0]["url"], cached):
            return IconAnswer.FAILED
        return None

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

    def _cross_ref_igdb_io(self, rom_id: int) -> int | IconAnswer:
        """Resolve *rom_id*'s SteamGridDB id through its stored IGDB id, and save it.

        ``NO_ICON`` when the ROM has no IGDB id or SteamGridDB knows no game
        for it, ``FAILED`` for an answer that says neither. Lets the API's
        errors through, so the caller can tell a 429 from a game SteamGridDB
        does not know.
        """
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
        igdb_id = rom.igdb_id if rom is not None else None
        if not igdb_id:
            return IconAnswer.NO_ICON
        result = self._sgdb_api.request(f"/games/igdb/{igdb_id}")
        if result is None or not result.get("success"):
            return IconAnswer.FAILED
        if not result.get("data"):
            return IconAnswer.NO_ICON
        sgdb_id = int(result["data"]["id"])
        self._persist_sgdb_id(str(rom_id), sgdb_id)
        return sgdb_id
