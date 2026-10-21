"""The game page's ARTWORK tab: each slot's state, a slot's candidates, an image chosen for it, and automatic again.

Reads what decides a slot's state — Steam's grid directory, the shortcut's
icon path, the slot's record and the user's choice — and hands it to
``domain.artwork_slot_view``; downloads go through the package's one
:class:`SgdbArtworkFiles`. The façade checks the conflict rules and hands out
the leases; nothing here does.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.artwork_slot import RECORDED_SLOTS, ArtworkSlot, is_set_in_steam, is_slot_filled, slot_stem
from domain.artwork_slot_choice import ArtworkSlotChoice
from domain.artwork_slot_outcome import SlotOutcome
from domain.artwork_slot_view import SlotState, SlotView, describe_slot
from domain.sgdb_artwork import (
    choose_candidate,
    parse_candidate_page,
    parse_game_name,
    sgdb_candidates_page_path,
)
from domain.shortcut_icon import is_icon_filled, is_icon_set_in_steam
from lib.errors import Refused, SgdbApiError, SgdbRateLimitedError, SteamGridDirMissingError
from models.shortcut_artwork import SlotAnswer
from services.steamgrid._files import CachedArtwork, NoArtwork
from services.steamgrid._refusals import RATE_LIMITED, refusal_for

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Collection, Mapping

    from domain.artwork_slot_outcome import ArtworkSlotOutcome
    from domain.sgdb_artwork import SgdbCandidate
    from services.protocols import (
        Clock,
        CoverSlotReader,
        SgdbArtworkCache,
        SteamConfigStore,
        SteamGridDbApi,
        UnitOfWorkFactory,
    )
    from services.steamgrid._files import SgdbArtworkFiles

# The tab's rows, in the order it shows them.
TAB_SLOTS: tuple[ArtworkSlot, ...] = (
    ArtworkSlot.COVER,
    ArtworkSlot.WIDE_GRID,
    ArtworkSlot.HERO,
    ArtworkSlot.LOGO,
    ArtworkSlot.ICON,
)

# How many pages of a slot's candidates are walked to find an image chosen in
# the picker once the listing it came from is no longer held (a backend restart
# between the listing and the choice).
_PAGES_WALKED_FOR_A_CHOICE = 10


@dataclass(frozen=True)
class ArtworkSlotsConfig:
    """Wiring for :class:`ArtworkSlots`.

    ``cache`` reads Tender's cached copy of a slot to hash it; ``cover`` is the
    cover's owner, for the cover's state.
    """

    sgdb_api: SteamGridDbApi
    files: SgdbArtworkFiles
    cache: SgdbArtworkCache
    steam_config: SteamConfigStore
    uow_factory: UnitOfWorkFactory
    cover: CoverSlotReader
    clock: Clock
    logger: logging.Logger


@dataclass(frozen=True)
class _GridState:
    """What Steam's grid directory and ``shortcuts.vdf`` hold for the shortcut, read once per question."""

    grid_dir: str | None
    file_names: frozenset[str]
    icon: str | None


class ArtworkSlots:
    """The ARTWORK tab's reads and writes of one ROM's five slots (synchronous; the façade offloads them)."""

    def __init__(self, *, config: ArtworkSlotsConfig) -> None:
        self._sgdb_api = config.sgdb_api
        self._files = config.files
        self._cache = config.cache
        self._steam_config = config.steam_config
        self._uow_factory = config.uow_factory
        self._cover = config.cover
        self._clock = config.clock
        self._logger = config.logger
        # Per ROM and slot, the candidates the picker was last shown, by image
        # id, so a choice downloads the image listed without asking again.
        # Replaced whole on each listing, never changed in place.
        self._listed: dict[tuple[int, ArtworkSlot], dict[int, SgdbCandidate]] = {}

    # -- reading ------------------------------------------------------------

    def read_io(self, rom_id: int, app_id: int, sgdb_id: int | None) -> list[SlotView]:
        """The five slots of *rom_id*'s shortcut *app_id*, in :data:`TAB_SLOTS` order.

        Refuses with ``no_grid_dir`` when Steam's grid directory cannot be found or listed.
        """
        grid = self._grid_state_io(app_id)
        if grid.grid_dir is None:
            raise Refused("no_grid_dir", "Steam grid directory not found")
        return self._views_io(rom_id, app_id, grid, has_game=sgdb_id is not None)

    def slots_left_to_tender_io(self, rom_id: int) -> list[ArtworkSlot]:
        """The SteamGridDB slots of *rom_id* on automatic and not set in Steam: the ones Tender may replace.

        Without a bound shortcut, or where Steam's grid directory cannot be
        read, no slot counts as set in Steam.
        """
        return [
            view.slot
            for view in self._views_of_rom_io(rom_id)
            if view.slot is not ArtworkSlot.COVER and view.state is SlotState.AUTOMATIC
        ]

    def slots_not_set_in_steam_io(self, rom_id: int) -> list[ArtworkSlot]:
        """The SteamGridDB slots of *rom_id* not set in Steam, chosen or not, read as the method above reads them."""
        return [
            view.slot
            for view in self._views_of_rom_io(rom_id)
            if view.slot is not ArtworkSlot.COVER and view.state is not SlotState.SET_IN_STEAM
        ]

    def is_set_in_steam_io(self, rom_id: int, slot: ArtworkSlot) -> bool:
        """Whether *rom_id*'s *slot* is set in Steam, read as :meth:`slots_left_to_tender_io`."""
        return any(view.slot is slot and view.state is SlotState.SET_IN_STEAM for view in self._views_of_rom_io(rom_id))

    def game_name_io(self, sgdb_id: int) -> str | None:
        """SteamGridDB's name for game *sgdb_id*; ``None`` where it cannot be asked, which is logged."""
        try:
            return parse_game_name(self._sgdb_api.request(f"/games/id/{sgdb_id}"))
        except Exception as e:
            self._logger.warning(f"SGDB game name lookup failed for game {sgdb_id}: {e}")
            return None

    def _views_of_rom_io(self, rom_id: int) -> list[SlotView]:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
        app_id = rom.shortcut_app_id if rom is not None else None
        grid = self._grid_state_io(app_id) if app_id is not None else _GridState(None, frozenset(), None)
        return self._views_io(rom_id, app_id or 0, grid, has_game=True)

    def _grid_state_io(self, app_id: int) -> _GridState:
        grid_dir = self._steam_config.grid_dir()
        try:
            file_names = self._steam_config.grid_file_names()
        except OSError as e:
            self._logger.warning(f"Could not list Steam's grid directory to read the artwork slots: {e}")
            file_names = None
        if grid_dir is None or file_names is None:
            return _GridState(None, frozenset(), None)
        icons = self._steam_config.read_shortcut_icons() or {}
        return _GridState(grid_dir, file_names, icons.get(app_id))

    def _views_io(self, rom_id: int, app_id: int, grid: _GridState, *, has_game: bool) -> list[SlotView]:
        with self._uow_factory() as uow:
            choices = {choice.slot: choice for choice in uow.artwork_slot_choices.list_for_rom(rom_id)}
            recorded = {slot: uow.artwork_slot_outcomes.get(rom_id, slot) for slot in RECORDED_SLOTS}
        return [
            self._view_io(rom_id, app_id, slot, grid, choices.get(slot), recorded.get(slot), has_game=has_game)
            for slot in TAB_SLOTS
        ]

    def _view_io(
        self,
        rom_id: int,
        app_id: int,
        slot: ArtworkSlot,
        grid: _GridState,
        choice: ArtworkSlotChoice | None,
        recorded: ArtworkSlotOutcome | None,
        *,
        has_game: bool,
    ) -> SlotView:
        cached = self._files.cached(rom_id, slot.value) if slot is not ArtworkSlot.COVER else None
        return describe_slot(
            slot,
            set_in_steam=self._is_set_in_steam_io(rom_id, app_id, slot, grid, recorded, cached),
            choice=choice,
            recorded=recorded,
            has_game=has_game,
            filled=self._is_filled(app_id, slot, grid),
            cached_image_id=cached.image_id if cached is not None else None,
        )

    def _is_filled(self, app_id: int, slot: ArtworkSlot, grid: _GridState) -> bool:
        if grid.grid_dir is None:
            return False
        if slot is ArtworkSlot.ICON:
            return grid.icon is not None and is_icon_filled(grid.icon, grid.grid_dir, grid.file_names)
        return is_slot_filled(grid.file_names, app_id, slot)

    def _is_set_in_steam_io(
        self,
        rom_id: int,
        app_id: int,
        slot: ArtworkSlot,
        grid: _GridState,
        recorded: ArtworkSlotOutcome | None,
        cached: CachedArtwork | None,
    ) -> bool:
        if grid.grid_dir is None:
            return False
        if slot is ArtworkSlot.ICON:
            return grid.icon is not None and is_icon_set_in_steam(grid.icon, app_id, grid.grid_dir)
        if slot is ArtworkSlot.COVER:
            return self._cover.is_cover_set_in_steam(rom_id, app_id)
        png = f"{slot_stem(app_id, slot)}.png"
        png_sha256 = tenders_sha256 = None
        if png in grid.file_names:
            if recorded is not None and recorded.outcome is SlotOutcome.SET:
                tenders_sha256 = recorded.image_sha256
            elif cached is not None:
                tenders_sha256 = self._sha256_io(self._cache.read_bytes, cached.path)
            if tenders_sha256 is not None:
                png_sha256 = self._sha256_io(self._steam_config.read_grid_file, png)
        return is_set_in_steam(grid.file_names, app_id, slot, png_sha256=png_sha256, tenders_sha256=tenders_sha256)

    def _sha256_io(self, read: Callable[[str], bytes], path: str) -> str | None:
        """The SHA-256 of the bytes *read* answers for *path*; ``None`` where they cannot be read, which is logged."""
        try:
            return hashlib.sha256(read(path)).hexdigest()
        except (OSError, SteamGridDirMissingError) as e:
            self._logger.warning(f"Could not read {path} to tell whether its slot is set in Steam: {e}")
            return None

    # -- the picker ---------------------------------------------------------

    def candidates_io(self, rom_id: int, slot: ArtworkSlot, page: int, sgdb_id: int) -> dict[str, Any]:
        """Page *page* of SteamGridDB's candidates for *slot* of game *sgdb_id*, in SteamGridDB's order.

        Each candidate carries its image id, its thumbnail and its stated size.
        On page 0, ``tenders_pick`` names the candidate Tender's own rule takes
        from that page; it is ``None`` for the cover, whose automatic image is
        RomM's, and on every later page. A game SteamGridDB does not know lists
        nothing. Refuses as :func:`refusal_for` answers SteamGridDB's errors, and
        an unsuccessful answer as ``server_unreachable``.
        """
        payload = self._request_page_io(slot, page, sgdb_id)
        listed = parse_candidate_page(payload, page) if payload is not None else None
        if payload is not None and listed is None:
            raise Refused("server_unreachable", "SteamGridDB did not list the images")
        candidates = listed.candidates if listed is not None else []
        held = self._listed.get((rom_id, slot), {}) if page else {}
        self._listed[(rom_id, slot)] = {**held, **{candidate.image_id: candidate for candidate in candidates}}
        pick = None
        if page == 0 and slot is not ArtworkSlot.COVER and payload is not None:
            chosen = choose_candidate(slot.value, payload.get("data"))
            pick = chosen.image_id if chosen is not None else None
        return {
            "candidates": [
                {"image_id": c.image_id, "thumb": c.thumb, "width": c.width, "height": c.height} for c in candidates
            ],
            "has_more": listed.has_more if listed is not None else False,
            "tenders_pick": pick,
        }

    def _request_page_io(self, slot: ArtworkSlot, page: int, sgdb_id: int) -> dict[str, Any] | None:
        """SteamGridDB's answer for one page; ``None`` for a game it does not know (a 404)."""
        path = sgdb_candidates_page_path(slot.value, sgdb_id, page)
        if path is None:
            raise Refused("invalid_payload", f"The {slot.value} slot has no SteamGridDB images")
        try:
            return self._sgdb_api.request(path) or {}
        except SgdbRateLimitedError as e:
            raise Refused("rate_limited", RATE_LIMITED) from e
        except SgdbApiError as e:
            if e.status_code == 404:
                return None
            self._logger.warning(f"SGDB {slot.value} listing failed for game {sgdb_id}: {e}")
            raise refusal_for(e) from e

    def choose_io(self, rom_id: int, slot: ArtworkSlot, image_id: int, sgdb_id: int) -> CachedArtwork:
        """Download image *image_id* as *rom_id*'s *slot* and record it as the user's choice.

        The choice is recorded once the image is in the artwork cache. Refuses
        with ``image_not_found`` when SteamGridDB no longer lists it for the
        game, ``download_failed`` when the download did not succeed, and as
        :meth:`candidates_io` refuses SteamGridDB's errors.
        """
        candidate = self._find_candidate_io(rom_id, slot, image_id, sgdb_id)
        if candidate is None:
            raise Refused("image_not_found", "SteamGridDB no longer lists this image for the game")
        try:
            found = self._files.store(rom_id, slot.value, candidate)
        except SgdbRateLimitedError as e:
            raise Refused("rate_limited", RATE_LIMITED) from e
        if not isinstance(found, CachedArtwork):
            raise Refused("download_failed", "The image could not be downloaded from SteamGridDB")
        choice = ArtworkSlotChoice(rom_id=rom_id, slot=slot, image_id=image_id, chosen_at=self._clock.time())
        with self._uow_factory() as uow:
            if uow.roms.get(rom_id) is None:
                raise Refused("not_synced", "ROM is not synced to Steam")
            uow.artwork_slot_choices.save(choice)
        return found

    def _find_candidate_io(self, rom_id: int, slot: ArtworkSlot, image_id: int, sgdb_id: int) -> SgdbCandidate | None:
        candidate = self._listed.get((rom_id, slot), {}).get(image_id)
        if candidate is not None:
            return candidate
        for page in range(_PAGES_WALKED_FOR_A_CHOICE):
            listed = self.candidates_io(rom_id, slot, page, sgdb_id)
            candidate = self._listed.get((rom_id, slot), {}).get(image_id)
            if candidate is not None or not listed["has_more"]:
                return candidate
        return None

    def download_io(self, rom_id: int, slot: ArtworkSlot, sgdb_id: int) -> CachedArtwork | NoArtwork | SlotAnswer:
        """*rom_id*'s cached *slot*, downloaded first when it is not cached: the user's chosen image, else Tender's.

        A chosen image the cache no longer holds is downloaded again as chosen,
        never replaced by Tender's pick; ``FAILED`` where SteamGridDB no longer
        lists it, which is logged. Answers SteamGridDB's errors as
        :meth:`SgdbArtworkFiles.fetch_or_answer` does.
        """
        with self._uow_factory() as uow:
            choice = uow.artwork_slot_choices.get(rom_id, slot)
        if choice is None:
            return self._files.fetch_or_answer(rom_id, slot.value, sgdb_id)
        cached = self._files.cached(rom_id, slot.value)
        if cached is not None and cached.image_id == choice.image_id:
            return cached
        try:
            candidate = self._find_candidate_io(rom_id, slot, choice.image_id, sgdb_id)
            if candidate is None:
                self._logger.warning(f"SGDB no longer lists the {slot.value} chosen for rom_id={rom_id}")
                return NoArtwork.FAILED
            return self._files.store(rom_id, slot.value, candidate)
        except Refused as e:
            self._logger.warning(f"SGDB {slot.value} chosen for rom_id={rom_id} could not be fetched: {e.message}")
            return NoArtwork.FAILED
        except SgdbRateLimitedError:
            return SlotAnswer.RATE_LIMITED

    # -- automatic again ----------------------------------------------------

    def automatic_io(self, rom_id: int, slot: ArtworkSlot, sgdb_id: int) -> CachedArtwork | NoArtwork | SlotAnswer:
        """Download Tender's pick for *rom_id*'s SteamGridDB *slot* anew, and drop the user's choice for it.

        The choice goes once SteamGridDB has answered with an image or with
        none, and stays when the download failed or was refused.
        """
        found = self._files.fetch_or_answer(rom_id, slot.value, sgdb_id, anew=True)
        if isinstance(found, CachedArtwork) or found is NoArtwork.NONE:
            with self._uow_factory() as uow:
                uow.artwork_slot_choices.delete_slot(rom_id, slot)
        return found

    def forget_cover_choice_io(self, rom_id: int) -> None:
        """Drop *rom_id*'s cover choice and the record of the image it set, once RomM's cover is back."""
        with self._uow_factory() as uow:
            uow.artwork_slot_choices.delete_slot(rom_id, ArtworkSlot.COVER)
            uow.artwork_slot_outcomes.delete_slot(rom_id, ArtworkSlot.COVER)

    def forget_choices_io(self, rom_id: int) -> set[ArtworkSlot]:
        """Drop every choice of *rom_id*, answering the slots that had one."""
        with self._uow_factory() as uow:
            chosen = {choice.slot for choice in uow.artwork_slot_choices.list_for_rom(rom_id)}
            uow.artwork_slot_choices.delete_for_rom(rom_id)
        return chosen


def views_payload(views: Collection[SlotView]) -> list[Mapping[str, Any]]:
    """The wire shape of *views*: per slot, its name, state, source and the SteamGridDB image it shows."""
    return [
        {"slot": view.slot.value, "state": view.state.value, "source": view.source.value, "image_id": view.image_id}
        for view in views
    ]
