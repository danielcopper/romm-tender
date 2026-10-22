"""The ARTWORK tab's use cases: each checks its endpoint's conflict rules and hands out the artwork lease.

The reads and writes are ``_slots.ArtworkSlots``'s; this holds what the
endpoints add around them, apart from the façade, which only delegates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.artwork_slot import ArtworkSlot
from domain.sgdb_artwork import effective_sgdb_id
from lib.errors import AuthFailed, Refused
from models.shortcut_artwork import SlotAnswer
from services.steamgrid._files import CachedArtwork, NoArtwork
from services.steamgrid._refusals import KEY_REFUSED, RATE_LIMITED, refuse_a_key_that_cannot_be_sent
from services.steamgrid._slots import views_payload

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from services.protocols import ConflictRules, CoverSlotReader
    from services.steamgrid._slots import ArtworkSlots


def _tab_slot(slot: object) -> ArtworkSlot:
    """The slot *slot* names among the ARTWORK tab's five; refuses any other."""
    try:
        return ArtworkSlot(slot)
    except ValueError as e:
        raise Refused("invalid_payload", f"Unknown artwork slot: {slot}") from e


@dataclass(frozen=True)
class ArtworkTabUseCasesConfig:
    """Wiring for :class:`ArtworkTabUseCases`.

    ``sgdb_ids`` answers RomM's SteamGridDB id and the user's for a ROM,
    ``effective_sgdb_id`` the one its artwork comes from, and ``image_answer``
    the ``image`` answer serving a cached file — the façade's, so the tab and
    the per-slot fetch answer alike.
    """

    slots: ArtworkSlots
    cover: CoverSlotReader
    rules: ConflictRules
    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    sgdb_ids: Callable[[int], tuple[int | None, int | None]]
    effective_sgdb_id: Callable[[int], int | None]
    image_answer: Callable[[CachedArtwork, int | None], dict[str, Any] | None]


class ArtworkTabUseCases:
    """The ARTWORK tab's endpoints (GLOSSARY.md → Conflict rules for what each checks)."""

    def __init__(self, *, config: ArtworkTabUseCasesConfig) -> None:
        self._slots = config.slots
        self._cover = config.cover
        self._rules = config.rules
        self._settings = config.settings
        self._loop = config.loop
        self._sgdb_ids = config.sgdb_ids
        self._effective_sgdb_id = config.effective_sgdb_id
        self._image_answer = config.image_answer

    async def get_artwork_game(self, rom_id):
        """The SteamGridDB game *rom_id*'s artwork comes from, as the game page asks on every visit.

        Answers ``has_api_key``, ``game`` (``sgdb_id`` and ``source`` as
        :meth:`get_artwork_slots` names them; no ``name``, so a visit asks
        SteamGridDB nothing) and ``superseded``: where RomM's id has just
        replaced the game the user chose, the choices are discarded
        (#2381 D17) and it names RomM's game and the slots that had a choice,
        once; ``None`` otherwise. Refuses ``auth_failed`` for a key no header
        can carry.
        """
        async with self._rules.hold("get_artwork_game", prune=True):
            answer = await self._game_answer(int(rom_id))
            return {"success": True, **answer}

    async def get_artwork_slots(self, rom_id, app_id):
        """The game page's ARTWORK tab for *rom_id*'s shortcut *app_id*: its SteamGridDB game and its five slots.

        :meth:`get_artwork_game`'s answer, the ``game`` with its ``name`` on
        SteamGridDB (``None`` where that cannot be asked), and ``slots``: the
        cover, wide grid, hero, logo and icon, each with its ``state``
        (``automatic`` / ``chosen`` / ``set_in_steam``), its ``source``, the
        SteamGridDB ``image_id`` it shows where Tender knows it
        (``domain.artwork_slot_view``) and ``has_choice``. Refuses with
        ``no_grid_dir`` when Steam's grid directory cannot be found, and
        ``auth_failed`` for a key no header can carry.
        """
        async with self._rules.hold("get_artwork_slots", prune=True):
            rom_id, app_id = int(rom_id), int(app_id)
            answer = await self._game_answer(rom_id)
            sgdb_id = answer["game"]["sgdb_id"]
            views = await self._loop.run_in_executor(None, self._slots.read_io, rom_id, app_id, sgdb_id)
            chosen = await self._loop.run_in_executor(None, self._slots.chosen_slots_io, rom_id)
            if sgdb_id and answer["has_api_key"]:
                answer["game"]["name"] = await self._loop.run_in_executor(None, self._slots.game_name_io, sgdb_id)
            return {"success": True, **answer, "slots": views_payload(views, chosen)}

    async def _game_answer(self, rom_id: int) -> dict[str, Any]:
        """The answer :meth:`get_artwork_game` gives, once choices a RomM id superseded are discarded."""
        api_key = self._settings.get("steamgriddb_api_key", "")
        if api_key:
            refuse_a_key_that_cannot_be_sent(api_key)
        romm_id, chosen_id = await self._loop.run_in_executor(None, self._sgdb_ids, rom_id)
        discarded = await self._loop.run_in_executor(
            None, self._slots.discard_superseded_choices_io, rom_id, romm_id, chosen_id
        )
        if discarded:
            chosen_id = None
        sgdb_id = effective_sgdb_id(romm_id, chosen_id)
        superseded = None
        if discarded and sgdb_id:
            name = await self._loop.run_in_executor(None, self._slots.game_name_io, sgdb_id) if api_key else None
            superseded = {"name": name, "slots": sorted(slot.value for slot in discarded)}
        source = "romm" if romm_id else ("chosen" if chosen_id else None)
        return {
            "has_api_key": bool(api_key),
            "game": {"sgdb_id": sgdb_id, "source": source, "name": None},
            "superseded": superseded,
        }

    async def get_artwork_slot_candidates(self, rom_id, slot, page=0):
        """Page *page* of SteamGridDB's candidates for *rom_id*'s *slot*, for the picker.

        Answers ``candidates`` (``image_id``, ``thumb``, ``width``,
        ``height``), ``has_more`` and ``tenders_pick``
        (``_slots.ArtworkSlots.candidates_io``). Refuses with
        ``invalid_payload`` for an unknown slot, ``no_api_key``,
        ``no_sgdb_game`` without a SteamGridDB game, and as SteamGridDB's
        errors answer.
        """
        async with self._rules.hold("get_artwork_slot_candidates", prune=True):
            rom_id, artwork_slot = int(rom_id), _tab_slot(slot)
            sgdb_id = await self._tab_game_or_refuse(rom_id)
            listed = await self._loop.run_in_executor(
                None, self._slots.candidates_io, rom_id, artwork_slot, max(int(page), 0), sgdb_id
            )
            return {"success": True, **listed}

    async def choose_artwork_slot_image(self, rom_id, slot, image_id):
        """Download SteamGridDB image *image_id* as *rom_id*'s *slot*, record it as the user's choice, and serve it.

        Answers ``image`` with the image in ``base64``, its ``image_id``, the
        bytes' ``sha256`` and the ``sgdb_id``, or, for the icon, ``ready`` for
        :meth:`save_shortcut_icon` to write — each with an ``sgdb_artwork``
        lease for the frontend's Steam write, which it reports through
        :meth:`report_sgdb_slot_artwork`. Refuses as
        :meth:`get_artwork_slot_candidates` does, and with ``image_not_found``,
        ``download_failed`` and ``not_synced`` (``_slots.ArtworkSlots.choose_io``).
        """
        async with self._rules.hold("choose_artwork_slot_image", prune=True):
            rom_id, artwork_slot = int(rom_id), _tab_slot(slot)
            sgdb_id = await self._tab_game_or_refuse(rom_id)
            found = await self._loop.run_in_executor(
                None, self._slots.choose_io, rom_id, artwork_slot, int(image_id), sgdb_id
            )
            return await self._served_with_a_lease(artwork_slot, found, sgdb_id)

    async def set_artwork_slot_automatic(self, rom_id, slot):
        """Set *rom_id*'s *slot* back to automatic, and serve what automatic is now.

        For a SteamGridDB slot, Tender's pick is downloaded anew and served as
        :meth:`choose_artwork_slot_image` serves an image, and the choice goes;
        where SteamGridDB has none the answer is ``none`` and the choice stays,
        since Steam still shows the chosen image. For the cover, RomM's cover is
        reloaded at once, the choice and the record of the image it set go, and
        ``cover`` answers with a lease, for the frontend to set the cover
        ``get_artwork_base64`` serves. Refuses with ``invalid_payload`` for an
        unknown slot; for the cover as its reload refuses; for the others with
        ``no_api_key``, ``no_sgdb_game`` without a SteamGridDB game, and as
        SteamGridDB's errors answer. A refusal keeps the choice.
        """
        async with self._rules.hold("set_artwork_slot_automatic", update=True, migration=True, prune=True):
            rom_id, artwork_slot = int(rom_id), _tab_slot(slot)
            if artwork_slot is ArtworkSlot.COVER:
                await self._cover.refresh_cover_unchecked(rom_id)
                await self._loop.run_in_executor(None, self._slots.forget_cover_choice_io, rom_id)
                return {"status": "cover", "prune_lease_token": await self._rules.acquire_lease("sgdb_artwork")}
            sgdb_id = await self._tab_game_or_refuse(rom_id)
            found = await self._loop.run_in_executor(None, self._slots.automatic_io, rom_id, artwork_slot, sgdb_id)
            if found is NoArtwork.NONE:
                return {"status": "none"}
            if found is SlotAnswer.KEY_REFUSED:
                raise AuthFailed(KEY_REFUSED)
            if found is SlotAnswer.RATE_LIMITED:
                raise Refused("rate_limited", RATE_LIMITED)
            if not isinstance(found, CachedArtwork):
                raise Refused("download_failed", "Tender's image could not be downloaded from SteamGridDB")
            return await self._served_with_a_lease(artwork_slot, found, sgdb_id)

    async def _tab_game_or_refuse(self, rom_id: int) -> int:
        """*rom_id*'s effective SteamGridDB game; refuses ``no_api_key``, an unsendable key, and ``no_sgdb_game``."""
        api_key = self._settings.get("steamgriddb_api_key")
        if not api_key:
            raise Refused("no_api_key", "No SteamGridDB API key configured")
        refuse_a_key_that_cannot_be_sent(api_key)
        sgdb_id = await self._loop.run_in_executor(None, self._effective_sgdb_id, rom_id)
        if not sgdb_id:
            raise Refused("no_sgdb_game", "Choose a SteamGridDB game for this ROM first")
        return int(sgdb_id)

    async def _served_with_a_lease(self, slot: ArtworkSlot, found: CachedArtwork, sgdb_id: int) -> dict[str, Any]:
        if slot is ArtworkSlot.ICON:
            answer: dict[str, Any] | None = {"status": "ready", "image_id": found.image_id, "sgdb_id": sgdb_id}
        else:
            answer = await self._loop.run_in_executor(None, self._image_answer, found, sgdb_id)
        if answer is None:
            raise Refused("download_failed", "The downloaded image could not be read")
        answer["prune_lease_token"] = await self._rules.acquire_lease("sgdb_artwork")
        return answer
