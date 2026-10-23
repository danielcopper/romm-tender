"""Keeping a game's automatic artwork current: the game page's weekly check against SteamGridDB (#2381 D5, D22).

The check asks SteamGridDB for the candidates of every slot
``domain.artwork_keep_current.slots_kept_current`` names, applies Tender's
rule, and puts each image the rule now picks instead of the one shown into the
artwork cache; handing it to Steam is the frontend's, through the per-slot
fetch. The time of the check is recorded on the ROM when it starts and taken
back when SteamGridDB left a slot unanswered — no connection, a 429, a server
error, a refused key, an unsuccessful answer, a download that failed — or the
check raised. Every visit, checked or not, also names the slots downloaded so
before and not yet handed to Steam (#2381 D29).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from domain.artwork_keep_current import is_check_due
from domain.sgdb_artwork import choose_candidate, sgdb_endpoint_path
from lib.errors import SgdbApiError, SgdbRateLimitedError
from services.steamgrid._files import CachedArtwork
from services.steamgrid._refusals import refuse_a_key_that_cannot_be_sent
from services.steamgrid._slots import TAB_SLOTS

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Iterable

    from domain.artwork_slot import ArtworkSlot
    from domain.sgdb_artwork import SgdbCandidate
    from services.protocols import Clock, ConflictRules, SteamGridDbApi, UnitOfWorkFactory
    from services.steamgrid._files import SgdbArtworkFiles
    from services.steamgrid._slots import ArtworkSlots


class _Unanswered(Enum):
    """SteamGridDB gave no answer for a slot: no connection, a 429, a server error, a refused key, a failed answer."""

    UNANSWERED = "unanswered"


@dataclass(frozen=True)
class _Started:
    """A check that started at *at*, over a ROM last checked at *previous*."""

    at: float
    previous: float | None


@dataclass(frozen=True)
class KeepingCurrentConfig:
    """Wiring for :class:`KeepingCurrent`.

    ``effective_sgdb_id`` answers the SteamGridDB game a ROM's artwork comes
    from, the façade's, so the check asks the game every other path asks.
    """

    slots: ArtworkSlots
    files: SgdbArtworkFiles
    sgdb_api: SteamGridDbApi
    uow_factory: UnitOfWorkFactory
    rules: ConflictRules
    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    clock: Clock
    effective_sgdb_id: Callable[[int], int | None]
    logger: logging.Logger


class KeepingCurrent:
    """The ``keep_artwork_current`` use case (GLOSSARY.md → Conflict rules for what it checks)."""

    def __init__(self, *, config: KeepingCurrentConfig) -> None:
        self._slots = config.slots
        self._files = config.files
        self._sgdb_api = config.sgdb_api
        self._uow_factory = config.uow_factory
        self._rules = config.rules
        self._settings = config.settings
        self._loop = config.loop
        self._clock = config.clock
        self._effective_sgdb_id = config.effective_sgdb_id
        self._logger = config.logger
        # Two page mounts of one game may ask at once; the second must read the
        # time the first recorded, so reading it and recording it take turns.
        self._starting = asyncio.Lock()

    async def keep_artwork_current(self, rom_id):
        """Check *rom_id*'s automatic artwork against SteamGridDB, at most once in seven days, as a page visit asks.

        Answers ``replaced``, the slots whose image Tender's rule now picks
        instead of the one shown, each downloaded into the artwork cache for
        the frontend to fetch with ``get_sgdb_slot_artwork`` and hand to Steam —
        also, whether due or not and without asking SteamGridDB, every slot
        downloaded so before and not yet handed over (#2381 D29), in the tab's
        order — and ``status``: ``checked``; ``unanswered`` where SteamGridDB
        left a slot without an answer — no connection, a 429, a server error, a
        refused key, an unsuccessful answer, a download that failed — so the
        check stopped there and its time was taken back, as it is for any other
        error the check raises; ``not_due`` within seven days of the last check;
        ``no_api_key``; ``no_sgdb_game``; ``not_synced`` for a ROM with no
        ``roms`` row. Refuses ``auth_failed`` for a key no header can carry.
        """
        async with self._rules.hold("keep_artwork_current", prune=True):
            rom_id = int(rom_id)
            api_key = self._settings.get("steamgriddb_api_key")
            if not api_key:
                return {"status": "no_api_key", "replaced": []}
            refuse_a_key_that_cannot_be_sent(api_key)
            pending = await self._loop.run_in_executor(None, self._slots.downloaded_not_set_io, rom_id)
            sgdb_id = await self._loop.run_in_executor(None, self._effective_sgdb_id, rom_id)
            if not sgdb_id:
                return {"status": "no_sgdb_game", "replaced": _in_tab_order(pending)}
            async with self._starting:
                started = await self._loop.run_in_executor(None, self._start_io, rom_id)
            if not isinstance(started, _Started):
                return {"status": started, "replaced": _in_tab_order(pending)}
            try:
                replaced, answered = await self._loop.run_in_executor(None, self._check_io, rom_id, int(sgdb_id))
            except Exception:
                await self._loop.run_in_executor(None, self._withdraw_io, rom_id, started)
                raise
            if not answered:
                await self._loop.run_in_executor(None, self._withdraw_io, rom_id, started)
            status = "checked" if answered else "unanswered"
            return {"status": status, "replaced": _in_tab_order([*pending, *replaced])}

    def _start_io(self, rom_id: int) -> _Started | str:
        """Record that a check of *rom_id* starts now, where one is due; else the status that says why not."""
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None:
                return "not_synced"
            now = self._clock.time()
            if not is_check_due(rom.artwork_checked_at, now):
                return "not_due"
            previous = rom.artwork_checked_at
            rom.begin_artwork_check(now)
            uow.roms.set_artwork_checked_at(rom_id, rom.artwork_checked_at)
        return _Started(at=now, previous=previous)

    def _withdraw_io(self, rom_id: int, started: _Started) -> None:
        """Take *started* back, unless the ROM's time has moved on since."""
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None or rom.artwork_checked_at != started.at:
                return
            rom.withdraw_artwork_check(started.previous)
            uow.roms.set_artwork_checked_at(rom_id, rom.artwork_checked_at)

    def _check_io(self, rom_id: int, sgdb_id: int) -> tuple[list[ArtworkSlot], bool]:
        """The slots whose new pick is now cached, and whether SteamGridDB answered for every slot asked.

        It stops at the first slot left unanswered; what it replaced before
        stays replaced.
        """
        replaced: list[ArtworkSlot] = []
        for view in self._slots.kept_current_views_io(rom_id):
            pick = self._pick_io(view.slot, sgdb_id)
            if pick is _Unanswered.UNANSWERED:
                return replaced, False
            if pick is None or pick.image_id == view.image_id:
                continue
            if not self._cache_io(rom_id, view.slot, pick):
                return replaced, False
            replaced.append(view.slot)
        return replaced, True

    def _pick_io(self, slot: ArtworkSlot, sgdb_id: int) -> SgdbCandidate | None | _Unanswered:
        """The candidate Tender's rule takes for *slot* now; ``None`` where SteamGridDB has none, a 404 included.

        SteamGridDB answers 404 for a game it does not know
        (``SgdbArtworkFiles.fetch_or_answer``), which is an answer.
        """
        path = sgdb_endpoint_path(slot.value, sgdb_id)
        if path is None:
            return None
        try:
            payload = self._sgdb_api.request(path)
        except SgdbRateLimitedError:
            self._logger.info(f"Keeping artwork current: SteamGridDB answered 429 for the {slot.value}")
            return _Unanswered.UNANSWERED
        except SgdbApiError as e:
            if e.status_code == 404:
                return None
            self._logger.warning(f"Keeping artwork current: the {slot.value} listing failed: {e}")
            return _Unanswered.UNANSWERED
        except Exception as e:
            self._logger.warning(f"Keeping artwork current: the {slot.value} listing failed: {e}")
            return _Unanswered.UNANSWERED
        if not isinstance(payload, dict) or not payload.get("success"):
            return _Unanswered.UNANSWERED
        return choose_candidate(slot.value, payload.get("data"))

    def _cache_io(self, rom_id: int, slot: ArtworkSlot, pick: SgdbCandidate) -> bool:
        """Put *pick* into the artwork cache as *rom_id*'s *slot*, unless it is there; answers whether it is now."""
        cached = self._files.cached(rom_id, slot.value)
        if cached is not None and cached.image_id == pick.image_id:
            return True
        try:
            stored = self._files.store(rom_id, slot.value, pick)
        except Exception as e:
            self._logger.warning(
                f"Keeping artwork current: the {slot.value} for rom_id={rom_id} was not downloaded: {e}"
            )
            return False
        return isinstance(stored, CachedArtwork)


def _in_tab_order(slots: Iterable[ArtworkSlot]) -> list[str]:
    """*slots* once each, in the ARTWORK tab's order, as the wire names them."""
    held = set(slots)
    return [slot.value for slot in TAB_SLOTS if slot in held]
