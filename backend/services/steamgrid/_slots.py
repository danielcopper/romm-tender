"""The game page's ARTWORK tab: each slot's state, a slot's candidates, an image chosen for it, and automatic again.

Reads what decides a slot's state — Steam's grid directory, the shortcut's
icon path, the slot's record and the user's choice — and hands it to
``domain.artwork_slot_view``; downloads go through the package's one
:class:`SgdbArtworkFiles`. The façade checks the conflict rules and hands out
the leases; nothing here does.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from domain.artwork_keep_current import slots_kept_current
from domain.artwork_slot import SET_RECORDED_SLOTS, ArtworkSlot, is_set_in_steam, is_slot_filled, slot_files, slot_stem
from domain.artwork_slot_choice import ArtworkSlotChoice
from domain.artwork_slot_handover import HANDED_SLOTS
from domain.artwork_slot_outcome import ArtworkSlotOutcome, SlotOutcome
from domain.artwork_slot_view import SlotSource, SlotState, SlotView, describe_slot
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
from services.steamgrid._handover import PreviousChoice
from services.steamgrid._refusals import RATE_LIMITED, refusal_for

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Collection, Mapping, Sequence

    from domain.artwork_slot_handover import ArtworkSlotHandover
    from domain.rom import Rom
    from domain.sgdb_artwork import SgdbCandidate
    from services.protocols import (
        Clock,
        CoverSlotReader,
        SgdbArtworkCache,
        SteamConfigStore,
        SteamGridDbApi,
        UnitOfWork,
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

# How many pages of a slot's candidates are walked to find a chosen image once
# the listing it came from is no longer held — a backend restart between the
# listing and the choice, or a chosen image the cache lost. A bound on the
# requests one choice may cost, not a measured size of SteamGridDB's lists.
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
        # SteamGridDB's name per game id, once asked: a name does not change
        # between two reads of the tab, and asking costs a request.
        self._game_names: dict[int, str] = {}

    # -- reading ------------------------------------------------------------

    def read_io(self, rom_id: int, app_id: int, sgdb_id: int | None) -> list[SlotView]:
        """The five slots of *rom_id*'s shortcut *app_id*, in :data:`TAB_SLOTS` order, each with its signature.

        Refuses with ``no_grid_dir`` when Steam's grid directory cannot be found or listed.
        """
        grid = self._grid_state_io(app_id)
        if grid.grid_dir is None:
            raise Refused("no_grid_dir", "Steam grid directory not found")
        views = self._views_io(rom_id, app_id, grid, has_game=sgdb_id is not None)
        files = {view.slot: slot_files(grid.file_names, app_id, view.slot) for view in views}
        stamps = self._stamps_io([name for names in files.values() for name in names])
        return [replace(view, signature=_signature(files[view.slot], stamps)) for view in views]

    def chosen_slots_io(self, rom_id: int) -> set[ArtworkSlot]:
        """The slots of *rom_id* the user chose an image for."""
        with self._uow_factory() as uow:
            return {choice.slot for choice in uow.artwork_slot_choices.get_all_for_rom(rom_id)}

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
        """The SteamGridDB slots of *rom_id* not set in Steam, chosen or not, read as the method above reads them.

        The cover counts only where it shows Tender's SteamGridDB pick on automatic.
        """
        return [
            view.slot
            for view in self._views_of_rom_io(rom_id)
            if view.state is not SlotState.SET_IN_STEAM
            and (view.slot is not ArtworkSlot.COVER or view.source is SlotSource.TENDER)
        ]

    def kept_current_views_io(self, rom_id: int) -> list[SlotView]:
        """The views of *rom_id*'s slots keeping current asks about (``domain.artwork_keep_current``), read as above."""
        return slots_kept_current(self._views_of_rom_io(rom_id))

    def is_set_in_steam_io(self, rom_id: int, slot: ArtworkSlot) -> bool:
        """Whether *rom_id*'s *slot* is set in Steam, read as :meth:`slots_left_to_tender_io`."""
        return any(view.slot is slot and view.state is SlotState.SET_IN_STEAM for view in self._views_of_rom_io(rom_id))

    def game_name_io(self, sgdb_id: int) -> str | None:
        """SteamGridDB's name for game *sgdb_id*, asked once per game; ``None`` where it cannot be asked (logged)."""
        if sgdb_id in self._game_names:
            return self._game_names[sgdb_id]
        try:
            name = parse_game_name(self._sgdb_api.request(f"/games/id/{sgdb_id}"))
        except Exception as e:
            self._logger.warning(f"SGDB game name lookup failed for game {sgdb_id}: {e}")
            return None
        if name is not None:
            self._game_names[sgdb_id] = name
        return name

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

    def _stamps_io(self, names: Sequence[str]) -> Mapping[str, str]:
        """Each of *names* in Steam's grid directory with its stamp; none where they cannot be looked at (logged)."""
        if not names:
            return {}
        try:
            return self._steam_config.grid_file_stamps(names)
        except (OSError, SteamGridDirMissingError) as e:
            self._logger.warning(f"Could not look at Steam's grid files to sign the artwork thumbnails: {e}")
            return {}

    def _views_io(self, rom_id: int, app_id: int, grid: _GridState, *, has_game: bool) -> list[SlotView]:
        with self._uow_factory() as uow:
            choices = {choice.slot: choice for choice in uow.artwork_slot_choices.get_all_for_rom(rom_id)}
            recorded = {slot: uow.artwork_slot_outcomes.get(rom_id, slot) for slot in SET_RECORDED_SLOTS}
            handed = {slot: uow.artwork_slot_handovers.get(rom_id, slot) for slot in HANDED_SLOTS}
        return [
            self._view_io(
                rom_id, app_id, slot, grid, choices.get(slot), recorded.get(slot), handed.get(slot), has_game=has_game
            )
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
        handed: ArtworkSlotHandover | None,
        *,
        has_game: bool,
    ) -> SlotView:
        cached = self._files.cached(rom_id, slot.value) if slot is not ArtworkSlot.COVER else None
        return describe_slot(
            slot,
            set_in_steam=self._is_set_in_steam_io(rom_id, app_id, slot, grid, recorded, handed, cached),
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
        handed: ArtworkSlotHandover | None,
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
        handed_sha256 = handed.image_sha256 if handed is not None else None
        if png in grid.file_names:
            if recorded is not None and recorded.outcome is SlotOutcome.SET:
                tenders_sha256 = recorded.image_sha256
            elif cached is not None:
                tenders_sha256 = self._sha256_io(self._cache.read_bytes, cached.path)
            if tenders_sha256 is not None or handed_sha256 is not None:
                png_sha256 = self._sha256_io(self._steam_config.read_grid_file, png)
        return is_set_in_steam(
            grid.file_names,
            app_id,
            slot,
            png_sha256=png_sha256,
            tenders_sha256=tenders_sha256,
            handed_sha256=handed_sha256,
        )

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
        from that page; for the cover only where its automatic image is
        SteamGridDB's — it shows Tender's pick, or its slot is empty (#2381
        D27) — and on no later page. A game SteamGridDB does not know lists
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
        if page == 0 and payload is not None and self._picks_from_steamgriddb_io(rom_id, slot):
            chosen = choose_candidate(slot.value, payload.get("data"))
            pick = chosen.image_id if chosen is not None else None
        return {
            "candidates": [
                {"image_id": c.image_id, "thumb": c.thumb, "width": c.width, "height": c.height} for c in candidates
            ],
            "has_more": listed.has_more if listed is not None else False,
            "tenders_pick": pick,
        }

    def _picks_from_steamgriddb_io(self, rom_id: int, slot: ArtworkSlot) -> bool:
        """Whether Tender's rule picks *rom_id*'s *slot* from SteamGridDB, read as :meth:`slots_left_to_tender_io`.

        Every slot but the cover, and an automatic cover that shows Tender's
        pick or is empty; RomM's cover and a chosen one are not.
        """
        if slot is not ArtworkSlot.COVER:
            return True
        cover = next(view for view in self._views_of_rom_io(rom_id) if view.slot is ArtworkSlot.COVER)
        return cover.state is SlotState.AUTOMATIC and cover.source is not SlotSource.ROMM

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

    def choose_io(
        self, rom_id: int, slot: ArtworkSlot, image_id: int, sgdb_id: int
    ) -> tuple[CachedArtwork, PreviousChoice]:
        """Download image *image_id* as *rom_id*'s *slot* and record it as the user's choice.

        The choice is recorded once the image is in the artwork cache. Answers
        the image and the choice it replaced, read before anything is stored,
        for the hand-over to keep (#2381 D20). Refuses
        with ``image_not_found`` when SteamGridDB no longer lists it for the
        game, ``download_failed`` when the download did not succeed, and as
        :meth:`candidates_io` refuses SteamGridDB's errors.
        """
        with self._uow_factory() as uow:
            replaced = uow.artwork_slot_choices.get(rom_id, slot)
        previous = PreviousChoice(replaced.image_id if replaced is not None else None)
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
        return found, previous

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

    def has_choice_io(self, rom_id: int, slot: ArtworkSlot) -> bool:
        """Whether the user chose an image for *rom_id*'s *slot*."""
        with self._uow_factory() as uow:
            return uow.artwork_slot_choices.get(rom_id, slot) is not None

    def cached_as_chosen_io(self, rom_id: int, slot: ArtworkSlot) -> CachedArtwork | None:
        """What the cache holds for *rom_id*'s *slot*; ``None`` where it holds another image than the user chose."""
        with self._uow_factory() as uow:
            choice = uow.artwork_slot_choices.get(rom_id, slot)
        cached = self._files.cached(rom_id, slot.value)
        if cached is not None and choice is not None and cached.image_id != choice.image_id:
            return None
        return cached

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

        The choice goes once Tender's image is in the cache, and stays where
        SteamGridDB has none — Steam keeps showing the chosen image then — and
        where the download failed or was refused.
        """
        found = self._files.fetch_or_answer(rom_id, slot.value, sgdb_id, anew=True)
        if isinstance(found, CachedArtwork):
            with self._uow_factory() as uow:
                uow.artwork_slot_choices.delete_slot(rom_id, slot)
        return found

    def forget_cover_choice_io(self, rom_id: int) -> None:
        """Drop *rom_id*'s cover choice and the record of the image it set, once RomM's cover is back."""
        with self._uow_factory() as uow:
            uow.artwork_slot_choices.delete_slot(rom_id, ArtworkSlot.COVER)
            uow.artwork_slot_outcomes.delete_slot(rom_id, ArtworkSlot.COVER)

    def forget_choices_io(self, rom_id: int) -> set[ArtworkSlot]:
        """Drop the choices a change of *rom_id*'s SteamGridDB game discards (#2381 D12); answers the slots chosen.

        A chosen cover that is not set in Steam keeps its choice: it goes back to
        RomM's through ``set_artwork_slot_automatic``, which reloads RomM's cover
        under its own conflict rules, so the cover never reads automatic while
        Steam still shows the chosen image. A chosen cover set in Steam loses its
        choice and the record of the image the choice set, which Steam no longer
        shows.
        """
        with self._uow_factory() as uow:
            chosen = {choice.slot for choice in uow.artwork_slot_choices.get_all_for_rom(rom_id)}
            rom = uow.roms.get(rom_id)
        keep_cover = self._keeps_cover_choice_io(rom, chosen)
        with self._uow_factory() as uow:
            _forget_discarded_choices(uow, rom_id, chosen, keep_cover=keep_cover)
        return chosen

    def discard_superseded_choices_io(
        self, rom_id: int, romm_id: int | None, chosen_id: int | None
    ) -> set[ArtworkSlot]:
        """Where RomM's SteamGridDB id has replaced the game the user chose, discard that game's choices (#2381 D17).

        As a change of game does (:meth:`forget_choices_io`), in one Unit of Work
        with the chosen game, so this happens once; the discarded SteamGridDB
        slots' cached images go too, so Tender's pick under RomM's game is
        downloaded in their place rather than the old game's chosen image
        served. A cover that shows Tender's SteamGridDB pick loses its cached
        image as well, so the page's re-pick downloads it under RomM's game. A
        chosen cover set in Steam loses its choice and the record of the image the
        choice set, as :meth:`forget_choices_io` drops them. Nothing changes while
        RomM gives no id, gives the chosen one, or no slot was chosen. Answers the
        slots to set back to automatic: the SteamGridDB slots that had a choice,
        and the cover only where its choice was kept — a cover set in Steam is
        named nowhere.
        """
        if not romm_id or not chosen_id or romm_id == chosen_id:
            return set()
        with self._uow_factory() as uow:
            chosen = {choice.slot for choice in uow.artwork_slot_choices.get_all_for_rom(rom_id)}
            rom = uow.roms.get(rom_id)
        if not chosen:
            return set()
        keep_cover = self._keeps_cover_choice_io(rom, chosen)
        steamgriddb_cover = ArtworkSlot.COVER not in chosen and self._shows_tenders_cover_io(rom_id)
        with self._uow_factory() as uow:
            held = uow.roms.get(rom_id)
            if held is None or held.chosen_sgdb_id != chosen_id:
                return set()
            _forget_discarded_choices(uow, rom_id, chosen, keep_cover=keep_cover)
            uow.roms.set_chosen_sgdb_id(rom_id, None)
        discarded = chosen - {ArtworkSlot.COVER}
        for slot in discarded | ({ArtworkSlot.COVER} if steamgriddb_cover else set()):
            self._files.drop(rom_id, slot.value)
        self._logger.info(f"RomM's SteamGridDB game replaced the one chosen for rom_id={rom_id}; its choices went")
        return discarded | ({ArtworkSlot.COVER} if keep_cover else set())

    def _shows_tenders_cover_io(self, rom_id: int) -> bool:
        """Whether *rom_id*'s cover shows Tender's SteamGridDB pick on automatic."""
        cover = next(view for view in self._views_of_rom_io(rom_id) if view.slot is ArtworkSlot.COVER)
        return cover.state is SlotState.AUTOMATIC and cover.source is SlotSource.TENDER

    def _keeps_cover_choice_io(self, rom: Rom | None, chosen: Collection[ArtworkSlot]) -> bool:
        """Whether a change of game keeps *rom*'s cover choice: it has one, and its cover is not set in Steam."""
        if ArtworkSlot.COVER not in chosen or rom is None or rom.shortcut_app_id is None:
            return False
        return not self._cover.is_cover_set_in_steam(rom.rom_id, rom.shortcut_app_id)


def _forget_discarded_choices(
    uow: UnitOfWork, rom_id: int, chosen: Collection[ArtworkSlot], *, keep_cover: bool
) -> None:
    """Delete *rom_id*'s choices among *chosen* a change of game discards; a dropped cover choice takes its record."""
    for slot in chosen:
        if slot is ArtworkSlot.COVER and keep_cover:
            continue
        uow.artwork_slot_choices.delete_slot(rom_id, slot)
        if slot is ArtworkSlot.COVER:
            uow.artwork_slot_outcomes.delete_slot(rom_id, slot)


def _signature(names: Sequence[str], stamps: Mapping[str, str]) -> str:
    """A short digest of *names* with their stamps; empty for no names."""
    if not names:
        return ""
    listed = "\n".join(f"{name}:{stamps.get(name, '')}" for name in names)
    return hashlib.sha256(listed.encode()).hexdigest()[:16]


def views_payload(views: Collection[SlotView], chosen: Collection[ArtworkSlot]) -> list[Mapping[str, Any]]:
    """The wire shape of *views*: per slot, its name, state, source, the SteamGridDB image it shows, ``has_choice``.

    ``has_choice`` holds also where the slot reads set in Steam over the
    choice, which a change of game still discards (#2381 D12). ``signature``
    is the view's, which the panel's thumbnail of the slot carries.
    """
    return [
        {
            "slot": view.slot.value,
            "state": view.state.value,
            "source": view.source.value,
            "image_id": view.image_id,
            "has_choice": view.slot in chosen,
            "signature": view.signature,
        }
        for view in views
    ]
