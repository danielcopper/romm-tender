"""The shortcut artwork job: every bound shortcut gets its SteamGridDB icon, wide grid and logo, in the background.

Contract: docs/architecture/steam-non-steam-shortcuts.md, "Shortcut artwork". The
job derives its worklist each time it runs and stores none of it, so a run that
is stopped leaves exactly the rest for the next one: the icons from
``shortcuts.vdf`` (:func:`domain.shortcut_icon.icon_worklist`), the wide grids
and logos from Steam's grid directory and their recorded outcomes
(:func:`domain.artwork_slot_outcome.slot_waits`). It works in two passes, the
icon and the wide grid shortcut by shortcut first, then the logos. It fetches
four shortcuts at a time, writes each icon into Steam's grid directory, puts
each wide grid and logo into the artwork cache, and hands the finished ones to
the frontend in batches as the ``shortcut_artwork`` event, which names a wide
grid or logo for the frontend to fetch and set. Before each batch it reads
Steam's renderer memory, and above the session budget's ceiling the run ends.

It yields to a sync, a removed-game cleanup and a shortcut removal, each of which
asks for it again when it ends (a removal whose lease expires or is released as
orphaned leaves that to the next trigger). Each is seen before every fetch,
write and hand-over; how a run under way is stopped from outside is
``services.protocols.ShortcutArtworkJob``'s. The job is no endpoint, so it takes no
``hold``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import slot_waits
from domain.shortcut_icon import (
    LOGO_ICON_NAME,
    PLACEHOLDER_ICON_NAME,
    PLACEHOLDER_ICON_PNG,
    icon_worklist,
)
from lib.errors import SteamGridDirMissingError
from models.shortcut_artwork import IconAnswer, IconFetch, SlotAnswer

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Coroutine

    from domain.artwork_slot_outcome import ArtworkSlotOutcome
    from domain.rom import Rom
    from services.protocols import (
        Clock,
        ConflictRules,
        DebugLogger,
        EventEmitter,
        PanelConnectedFn,
        RendererAboveCeilingProvider,
        ShortcutArtworkSource,
        Sleeper,
        SteamConfigStore,
        TenderLogoFn,
        UnitOfWorkFactory,
        WorkInFlightFn,
    )

_WORKERS = 4
_BATCH_SIZE = 25
# How long a run after a sync waits for Steam to rewrite shortcuts.vdf with the
# shortcuts it minted (docs/architecture/steam-non-steam-shortcuts.md,
# "Shortcut artwork").
_SETTLE_SECONDS = 10.0
_FIRST_BACKOFF_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 15 * 60.0
_PANEL_POLL_SECONDS = 5.0


@dataclass(frozen=True)
class ShortcutArtworkServiceConfig:
    """Frozen wiring bundle handed to ``ShortcutArtworkService.__init__``.

    Holds the Steam config store the worklist is read from and the two fixed
    icons are written through, the SteamGridDB service's icon and artwork seam,
    the Unit-of-Work factory the bound shortcuts and their recorded outcomes
    are read with, the live settings (the API key), the conflict rules and the
    emitter a batch goes out under, the sleeper every wait goes through, the
    clock a recorded outcome is judged by, the four readings the job asks
    before it acts (a sync in flight, a removed-game cleanup running, a
    shortcut removal under way, a panel connected), the renderer-memory reading
    it takes before each batch, the shipped logo's reader, and runtime
    infrastructure.
    """

    steam_config: SteamConfigStore
    source: ShortcutArtworkSource
    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    conflict_rules: ConflictRules
    emit: EventEmitter
    sleeper: Sleeper
    clock: Clock
    sync_in_flight: WorkInFlightFn
    cleanup_running: WorkInFlightFn
    removal_in_flight: WorkInFlightFn
    panel_connected: PanelConnectedFn
    renderer_above_ceiling: RendererAboveCeilingProvider
    tender_logo: TenderLogoFn
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger


@dataclass(frozen=True)
class _Item:
    """One shortcut's work in a pass: its icon where it waits for one, and the slots it waits for, in order."""

    rom_id: int
    app_id: int
    icon: bool
    slots: tuple[ArtworkSlot, ...]


@dataclass(frozen=True)
class _Plan:
    """One run's two passes and the logo's path, ``None`` where there is no logo to give."""

    logo_path: str | None
    passes: tuple[list[_Item], list[_Item]]


@dataclass(frozen=True)
class _Finished:
    """An icon written for *app_id*, and the game-page generation the job read before it fetched."""

    app_id: int
    icon_path: str
    generation: int


@dataclass(frozen=True)
class _SlotReady:
    """A wide grid or logo the artwork cache holds for *rom_id*, for the frontend to set on *app_id*."""

    app_id: int
    rom_id: int
    slot: ArtworkSlot


class ShortcutArtworkService:
    """Runs the shortcut artwork job, one run at a time, and answers the start and end of what it yields to."""

    def __init__(self, *, config: ShortcutArtworkServiceConfig) -> None:
        self._steam_config = config.steam_config
        self._source = config.source
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._rules = config.conflict_rules
        self._emit = config.emit
        self._sleeper = config.sleeper
        self._clock = config.clock
        self._sync_in_flight = config.sync_in_flight
        self._cleanup_running = config.cleanup_running
        self._removal_in_flight = config.removal_in_flight
        self._panel_connected = config.panel_connected
        self._renderer_above_ceiling = config.renderer_above_ceiling
        self._tender_logo = config.tender_logo
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._task: asyncio.Task[None] | None = None
        self._waiting_for_a_panel: asyncio.Task[None] | None = None
        self._again = False
        self._settle = False
        self._stopped = False
        self._key_refused = False
        self._cleanup_holds = 0
        self._closed = False
        self._writing = 0
        self._no_write = asyncio.Event()
        self._no_write.set()
        self._not_paused = asyncio.Event()
        self._not_paused.set()
        self._backoff = 0.0

    # -- triggers ------------------------------------------------------------

    def request_run(self, *, settle: bool = False) -> None:
        """Start a run, or mark one to follow the run under way; *settle* waits for Steam's file first."""
        if self._closed:
            return
        self._settle = self._settle or settle
        if self._cleanup_holds:
            return
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = self._loop.create_task(self._run())

    async def run_when_a_panel_connects(self) -> None:
        """Start a run once a panel is there to hear its batches; the backend's start-up trigger."""
        while not self._panel_connected():
            await self._sleeper.sleep(_PANEL_POLL_SECONDS)
        self.request_run()

    async def placeholder_path(self) -> str | None:
        """Implements ``services.protocols.ShortcutArtworkJob.placeholder_path``.

        Where it answers ``None`` the sync gives the shortcuts it mints no icon
        at all.
        """
        if not self._settings.get("steamgriddb_api_key"):
            return None
        try:
            return await self._loop.run_in_executor(
                None, self._steam_config.ensure_grid_file, PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG
            )
        except (SteamGridDirMissingError, OSError) as e:
            self._logger.warning(f"Shortcut artwork: no placeholder to give new shortcuts: {e}")
            return None

    async def stop_for_cleanup(self) -> None:
        """Implements ``services.protocols.ShortcutArtworkJob.stop_for_cleanup``."""
        self._cleanup_holds += 1
        await self._stop()

    def resume_after_cleanup(self) -> None:
        """Implements ``services.protocols.ShortcutArtworkJob.resume_after_cleanup``."""
        self._cleanup_holds = max(0, self._cleanup_holds - 1)
        self.request_run()

    async def stop_for_removal(self) -> None:
        """Implements ``services.protocols.ShortcutArtworkJob.stop_for_removal``.

        A later run yields to the removal by itself, through ``removal_in_flight``.
        """
        await self._stop()

    async def shutdown(self) -> None:
        """Stop for good: no later trigger starts a run."""
        self._closed = True
        if self._waiting_for_a_panel is not None:
            self._waiting_for_a_panel.cancel()
        await self._stop()

    async def _stop(self) -> None:
        self._stopped = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})
        await self._no_write.wait()

    def _wait_for_a_panel(self) -> None:
        """Run again once a panel is connected, after a batch nobody heard."""
        self._wait_for(self.run_when_a_panel_connects)

    def _wait_for_a_new_panel(self) -> None:
        """Run again once the panel connected now has gone and one connects, as after Steam restarts."""
        self._wait_for(self._run_when_a_new_panel_connects)

    def _wait_for(self, wait: Callable[[], Coroutine[Any, Any, None]]) -> None:
        if self._closed or (self._waiting_for_a_panel is not None and not self._waiting_for_a_panel.done()):
            return
        self._waiting_for_a_panel = self._loop.create_task(wait())

    async def _run_when_a_new_panel_connects(self) -> None:
        while self._panel_connected():
            await self._sleeper.sleep(_PANEL_POLL_SECONDS)
        await self.run_when_a_panel_connects()

    # -- one run ---------------------------------------------------------------

    async def _run(self) -> None:
        try:
            while True:
                self._again = False
                self._stopped = False
                if self._settle:
                    self._settle = False
                    await self._sleeper.sleep(_SETTLE_SECONDS)
                await self._run_once()
                if not self._again:
                    return
        except Exception:
            self._logger.exception("Shortcut artwork: the run stopped on an unexpected error")

    def _must_yield(self) -> bool:
        return self._sync_in_flight() or self._cleanup_running() or self._removal_in_flight()

    async def _run_once(self) -> None:
        if not self._settings.get("steamgriddb_api_key"):
            self._log_debug("Shortcut artwork: no SteamGridDB API key, nothing to do")
            return
        if self._must_yield():
            self._log_debug(
                "Shortcut artwork: a sync, a cleanup or a shortcut removal is under way; its end asks again"
            )
            return
        self._backoff = 0.0
        self._key_refused = False
        plan = await self._loop.run_in_executor(None, self._plan_io)
        if plan is None:
            return
        for work in plan.passes:
            if self._stopped or self._key_refused:
                return
            if work:
                self._log_pass(work)
                await self._run_pass(work, plan)

    def _log_pass(self, work: list[_Item]) -> None:
        counts = (
            (sum(1 for item in work if item.icon), "an icon"),
            (sum(1 for item in work if ArtworkSlot.WIDE_GRID in item.slots), "a wide grid"),
            (sum(1 for item in work if ArtworkSlot.LOGO in item.slots), "a logo"),
        )
        waiting = ", ".join(f"{count} for {what}" for count, what in counts if count)
        self._logger.info(f"Shortcut artwork: shortcut(s) waiting: {waiting}")

    async def _run_pass(self, work: list[_Item], plan: _Plan) -> None:
        queue: asyncio.Queue[_Item] = asyncio.Queue()
        for item in work:
            queue.put_nowait(item)
        batch: list[_Finished | _SlotReady] = []
        workers = [self._loop.create_task(self._work(queue, batch, plan)) for _ in range(_WORKERS)]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
            if not self._stopped:
                await self._flush(batch)

    def _plan_io(self) -> _Plan | None:
        """Lay down the two fixed icons and derive the two passes.

        ``None`` when the grid directory cannot be written or ``shortcuts.vdf``
        cannot be read. A logo that cannot be read is no reason to stop: the
        plan then has no logo path, and a game SteamGridDB has no icon for keeps
        its placeholder. Where the grid directory cannot be listed, no wide grid
        or logo is asked for.
        """
        try:
            self._steam_config.ensure_grid_file(PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG)
            logo = self._tender_logo()
            logo_path = self._steam_config.ensure_grid_file(LOGO_ICON_NAME, logo) if logo else None
        except SteamGridDirMissingError as e:
            self._logger.warning(f"Shortcut artwork: {e}")
            return None
        except OSError as e:
            self._logger.warning(f"Shortcut artwork: could not write Tender's icons: {e}")
            return None
        icons = self._steam_config.read_shortcut_icons()
        if icons is None:
            return None
        try:
            file_names = self._steam_config.grid_file_names()
        except OSError as e:
            self._logger.warning(f"Shortcut artwork: could not list Steam's grid directory: {e}")
            file_names = None
        with self._uow_factory() as uow:
            roms = {rom.shortcut_app_id: rom for rom in uow.roms.iter_all() if rom.shortcut_app_id is not None}
            outcomes = {
                (rom.rom_id, slot): uow.artwork_slot_outcomes.get(rom.rom_id, slot)
                for rom in roms.values()
                for slot in (ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO)
            }
        return _Plan(logo_path, self._passes(roms, icons, file_names, outcomes))

    def _passes(
        self,
        roms: dict[int, Rom],
        icons: dict[int, str],
        file_names: frozenset[str] | None,
        outcomes: dict[tuple[int, ArtworkSlot], ArtworkSlotOutcome | None],
    ) -> tuple[list[_Item], list[_Item]]:
        """The icon and wide grid pass, then the logo pass, over *roms* keyed by app id, in ROM order."""
        bound = {app_id: rom.rom_id for app_id, rom in roms.items()}
        missing = sum(1 for app_id in bound if app_id not in icons)
        if missing:
            self._logger.info(f"Shortcut artwork: shortcuts.vdf does not hold {missing} bound shortcut(s) yet")
        waiting_icons = {app_id for _rom_id, app_id in icon_worklist(bound, icons)}
        now = self._clock.time()

        def waits(app_id: int, rom: Rom, slot: ArtworkSlot) -> bool:
            recorded = outcomes[(rom.rom_id, slot)]
            return file_names is not None and slot_waits(
                file_names, app_id, slot, recorded, sgdb_id=rom.sgdb_id, now=now
            )

        first: list[_Item] = []
        second: list[_Item] = []
        for app_id, rom in sorted(roms.items(), key=lambda pair: pair[1].rom_id):
            grid = (ArtworkSlot.WIDE_GRID,) if waits(app_id, rom, ArtworkSlot.WIDE_GRID) else ()
            if app_id in waiting_icons or grid:
                first.append(_Item(rom.rom_id, app_id, app_id in waiting_icons, grid))
            if waits(app_id, rom, ArtworkSlot.LOGO):
                second.append(_Item(rom.rom_id, app_id, False, (ArtworkSlot.LOGO,)))
        return first, second

    async def _work(self, queue: asyncio.Queue[_Item], batch: list[_Finished | _SlotReady], plan: _Plan) -> None:
        while (item := await self._next_item(queue)) is not None:
            rest = await self._serve(item, batch, plan)
            if rest is not None:
                queue.put_nowait(rest)
                await self._wait_out_rate_limit()

    def _must_stop(self) -> bool:
        return self._stopped or self._key_refused or self._must_yield()

    async def _next_item(self, queue: asyncio.Queue[_Item]) -> _Item | None:
        await self._not_paused.wait()
        if self._must_stop():
            return None
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _serve(self, item: _Item, batch: list[_Finished | _SlotReady], plan: _Plan) -> _Item | None:
        """Fetch what *item* waits for, in order; answer what is left of it after a 429, else ``None``."""
        if item.icon:
            if not await self._serve_icon(item, batch, plan):
                return item
            item = replace(item, icon=False)
        for index, slot in enumerate(item.slots):
            if self._must_stop():
                return None
            if not await self._serve_slot(item, slot, batch):
                return replace(item, slots=item.slots[index:])
        return None

    async def _serve_icon(self, item: _Item, batch: list[_Finished | _SlotReady], plan: _Plan) -> bool:
        """Fetch and write *item*'s icon; ``False`` after a 429, so it is asked again."""
        generation = self._source.icon_generation(item.app_id)
        fetch = await self._loop.run_in_executor(None, self._source.do_fetch_shortcut_icon, item.rom_id)
        if fetch.answer is IconAnswer.RATE_LIMITED:
            return False
        if fetch.answer is IconAnswer.KEY_REFUSED:
            self._refuse_key()
        elif fetch.answer is IconAnswer.FAILED:
            self._log_debug(f"Shortcut artwork: no answer for rom_id={item.rom_id}; the next run asks again")
        else:
            self._backoff = 0.0
            path = await self._write(item.app_id, fetch, generation, plan)
            if path is not None:
                await self._add(batch, _Finished(item.app_id, path, generation))
        return True

    async def _serve_slot(self, item: _Item, slot: ArtworkSlot, batch: list[_Finished | _SlotReady]) -> bool:
        """Put *item*'s *slot* into the artwork cache and queue it; ``False`` after a 429, so it is asked again."""
        answer = await self._loop.run_in_executor(None, self._source.do_fetch_slot_artwork, item.rom_id, slot)
        if answer is SlotAnswer.RATE_LIMITED:
            return False
        if answer is SlotAnswer.KEY_REFUSED:
            self._refuse_key()
        elif answer is SlotAnswer.FAILED:
            self._log_debug(f"Shortcut artwork: no {slot.value} for rom_id={item.rom_id}; the next run asks again")
        else:
            self._backoff = 0.0
            if answer is SlotAnswer.READY:
                await self._add(batch, _SlotReady(item.app_id, item.rom_id, slot))
        return True

    def _refuse_key(self) -> None:
        if not self._key_refused:
            self._key_refused = True
            self._logger.warning("Shortcut artwork: SteamGridDB refused the API key; the run ends")

    async def _add(self, batch: list[_Finished | _SlotReady], finished: _Finished | _SlotReady) -> None:
        batch.append(finished)
        if len(batch) >= _BATCH_SIZE:
            await self._flush(batch)

    async def _wait_out_rate_limit(self) -> None:
        """Pause every worker, twice as long at each 429 in a row, up to the cap.

        A 429 met during a pause waits that pause out and starts none of its own.
        """
        if not self._not_paused.is_set():
            await self._not_paused.wait()
            return
        self._backoff = min(_MAX_BACKOFF_SECONDS, self._backoff * 2 if self._backoff else _FIRST_BACKOFF_SECONDS)
        self._logger.info(f"Shortcut artwork: SteamGridDB answered 429, waiting {self._backoff:.0f}s")
        self._not_paused.clear()
        try:
            await self._sleeper.sleep(self._backoff)
        finally:
            self._not_paused.set()

    async def _write(self, app_id: int, fetch: IconFetch, generation: int, plan: _Plan) -> str | None:
        return await self._shielded(self._write_unless_the_job_yields(app_id, fetch, generation, plan))

    async def _flush(self, batch: list[_Finished | _SlotReady]) -> None:
        """Hand what is finished to the frontend under one lease, and empty *batch*.

        Nothing is handed over while Steam's renderer stands above the session
        budget's ceiling: the run ends there, and what was finished waits for the
        next run.
        """
        if not batch:
            return
        finished = list(batch)
        batch.clear()
        if self._yields_now() or await self._renderer_is_full():
            return
        await self._shielded(self._hand_over_unless_the_job_yields(finished))

    async def _renderer_is_full(self) -> bool:
        if not await self._renderer_above_ceiling()():
            return False
        self._stopped = True
        self._logger.info(
            "Shortcut artwork: Steam's renderer memory is above the session budget's ceiling; the job runs again "
            "after the next sync, or once a panel connects after Steam restarts"
        )
        self._wait_for_a_new_panel()
        return True

    async def _shielded[T](self, work: Coroutine[Any, Any, T]) -> T:
        """Run *work* in a task of its own and count it, so :meth:`_stop` waits for it to end."""
        self._writing += 1
        self._no_write.clear()
        task = self._loop.create_task(work)
        task.add_done_callback(self._write_done)
        return await asyncio.shield(task)

    def _write_done(self, _task: asyncio.Task[Any]) -> None:
        self._writing -= 1
        if not self._writing:
            self._no_write.set()

    def _yields_now(self) -> bool:
        """Whether the run must stop here; what it wrote and has not handed over waits for the next run."""
        if self._stopped or self._must_yield():
            self._stopped = True
        return self._stopped

    async def _write_unless_the_job_yields(
        self, app_id: int, fetch: IconFetch, generation: int, plan: _Plan
    ) -> str | None:
        if self._yields_now():
            return None
        if fetch.answer is IconAnswer.ICON and fetch.data is not None:
            return await self._source.write_job_icon(app_id, fetch.data, generation)
        if plan.logo_path is None or self._source.icon_generation(app_id) != generation:
            return None
        return plan.logo_path

    async def _hand_over_unless_the_job_yields(self, finished: list[_Finished | _SlotReady]) -> None:
        if self._yields_now():
            return
        # A pick on the game page since the job read the generation wins: for
        # the logo the batch would otherwise set Tender's file over the pick.
        icons = [
            {"app_id": icon.app_id, "icon_path": icon.icon_path}
            for icon in finished
            if isinstance(icon, _Finished) and self._source.icon_generation(icon.app_id) == icon.generation
        ]
        artwork = [
            {"app_id": ready.app_id, "rom_id": ready.rom_id, "slot": ready.slot.value}
            for ready in finished
            if isinstance(ready, _SlotReady)
        ]
        if not icons and not artwork:
            return
        heard = await self._rules.emit_under_lease(
            "shortcut_artwork",
            lambda token: self._emit(
                "shortcut_artwork", {"icons": icons, "artwork": artwork, "prune_lease_token": token}
            ),
        )
        if not heard:
            self._stopped = True
            self._logger.info("Shortcut artwork: no panel heard the batch; the job runs again once one connects")
            self._wait_for_a_panel()
