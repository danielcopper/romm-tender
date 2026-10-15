"""The shortcut artwork job: every bound shortcut gets its SteamGridDB icon, wide grid and logo, in the background.

Contract: docs/architecture/steam-non-steam-shortcuts.md, "Shortcut artwork". The
job derives its worklist each time it runs and stores none of it, so a run that
is stopped leaves exactly the rest for the next one: the icons from
``shortcuts.vdf`` and their recorded outcomes
(:func:`domain.artwork_slot_outcome.icon_waits`), the wide grids and logos from
Steam's grid directory and theirs (:func:`domain.artwork_slot_outcome.slot_waits`).
It works in two passes, the icon and the wide grid shortcut by shortcut first,
then the logos. It fetches four shortcuts at a time, puts each image into the
artwork cache, writes each icon into Steam's grid directory, and hands the
finished ones to the frontend in batches as the ``shortcut_artwork`` event,
which names each icon's file with what its record is to carry, and each wide
grid or logo for the frontend to fetch and set; it hands the next batch over
only once the frontend has finished the last. Before each batch it reads
Steam's renderer memory, and at or above the session budget's ceiling the run
ends.

It yields to a sync, a removed-game cleanup and a shortcut removal, each of which
asks for it again when it ends (a removal whose lease expires or is released as
orphaned leaves that to the next trigger). Each is seen before every fetch,
write and hand-over; how a run under way is stopped from outside is
``services.protocols.ShortcutArtworkJob``'s. The job is no endpoint, so it takes no
``hold``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import icon_waits, logo_icon_waits, slot_waits
from domain.shortcut_icon import LOGO_ICON_NAME, PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG
from lib.background_tasks import BackgroundTasks
from lib.errors import SteamGridDirMissingError
from models.shortcut_artwork import SlotAnswer

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Coroutine

    from domain.artwork_slot_outcome import ArtworkSlotOutcome
    from domain.rom import Rom
    from models.shortcut_artwork import WrittenIcon
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
# The longest the job waits for the frontend to finish a batch. A healthy batch
# of 25 images takes seconds. The frontend gives a batch up after five minutes
# (CONTINUATION_TIMEOUT_MS, frontend/src/utils/pruneLease.ts), and a lease
# nobody renews expires 300 s later, so a lease outlives this only where
# something has gone wrong, and the run then ends rather than wait on it.
_BATCH_WAIT_SECONDS = 10 * 60.0


@dataclass(frozen=True)
class ShortcutArtworkServiceConfig:
    """Frozen wiring bundle handed to ``ShortcutArtworkService.__init__``.

    Holds the Steam config store the worklist is read from and the two fixed
    icons are written through, the SteamGridDB service's artwork and icon seam,
    the Unit-of-Work factory the bound shortcuts and their recorded outcomes
    are read with, the live settings (the API key), the conflict rules and the
    emitter a batch goes out under, the sleeper every wait goes through, the
    clock a recorded outcome is judged by, the four readings the job asks
    before it acts (a sync in flight, a removed-game cleanup running, a
    shortcut removal under way, a panel connected), the renderer-memory reading
    it takes before each batch, the shipped logo icon's reader, and runtime
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
    """One shortcut's work in a pass: the slots it waits for, in order, after the logo icon where it waits for that.

    *generation* is the game page's icon generation for *app_id*, taken
    before ``shortcuts.vdf`` was read for the plan; an icon is written and
    handed over only while it is unchanged.
    """

    rom_id: int
    app_id: int
    slots: tuple[ArtworkSlot, ...]
    generation: int
    logo_icon: bool = False


@dataclass(frozen=True)
class _Plan:
    """One run's two passes and the logo icon's path, ``None`` where there is no logo icon to give."""

    logo_path: str | None
    passes: tuple[list[_Item], list[_Item]]


@dataclass(frozen=True)
class _Bound:
    """What the plan reads before ``shortcuts.vdf``: Tender's two icons' paths, the bound ROMs and their outcomes."""

    placeholder: str
    logo_path: str | None
    roms: dict[int, Rom]
    outcomes: dict[tuple[int, ArtworkSlot], ArtworkSlotOutcome | None]


@dataclass(frozen=True)
class _Grid:
    """Steam's grid directory and its listing, ``None`` where it could not be taken."""

    path: str
    file_names: frozenset[str] | None


@dataclass(frozen=True)
class _Finished:
    """An icon for *app_id*, and the game-page generation its item carried, taken when the run was planned.

    *written* is SteamGridDB's icon the job wrote, whose outcome the frontend
    reports; ``None`` for the logo icon, which adds nothing to the ``none``
    already recorded.
    """

    app_id: int
    rom_id: int
    icon_path: str
    generation: int
    written: WrittenIcon | None


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
        self._tasks = BackgroundTasks(logger=config.logger)
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
        self._one_batch_at_a_time = asyncio.Lock()
        self._batch_out: str | None = None
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
        self._task = self._tasks.spawn(self._run(), "shortcut artwork run")

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
        await self._tasks.shutdown()

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
        self._waiting_for_a_panel = self._tasks.spawn(wait(), "shortcut artwork waiting for a panel")

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
        plan = await self._plan()
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
            (sum(1 for item in work if ArtworkSlot.ICON in item.slots), "an icon"),
            (sum(1 for item in work if item.logo_icon), "the logo icon"),
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
        workers = [
            self._tasks.start_awaited(self._work(queue, batch, plan), "shortcut artwork worker")
            for _ in range(_WORKERS)
        ]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
            while batch and not self._stopped:
                await self._flush(batch)

    async def _plan(self) -> _Plan | None:
        """Derive the run's two passes, taking the game page's icon generations before ``shortcuts.vdf`` is read.

        A pick on the game page made after the file was read moves its
        generation on, so the job neither writes nor hands over an icon over it.
        """
        bound = await self._loop.run_in_executor(None, self._bound_io)
        if bound is None:
            return None
        generations = {app_id: self._source.icon_generation(app_id) for app_id in bound.roms}
        return await self._loop.run_in_executor(None, self._plan_io, bound, generations)

    def _bound_io(self) -> _Bound | None:
        """Lay down the two fixed icons and read the bound ROMs with their recorded outcomes.

        ``None`` when the grid directory cannot be written. A logo icon that
        cannot be read is no reason to stop: there is then no logo icon path,
        and a game SteamGridDB has no icon for keeps its placeholder.
        """
        try:
            placeholder = self._steam_config.ensure_grid_file(PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG)
            logo = self._tender_logo()
            logo_path = self._steam_config.ensure_grid_file(LOGO_ICON_NAME, logo) if logo else None
        except SteamGridDirMissingError as e:
            self._logger.warning(f"Shortcut artwork: {e}")
            return None
        except OSError as e:
            self._logger.warning(f"Shortcut artwork: could not write Tender's icons: {e}")
            return None
        with self._uow_factory() as uow:
            roms = {rom.shortcut_app_id: rom for rom in uow.roms.iter_all() if rom.shortcut_app_id is not None}
            outcomes = {
                (rom.rom_id, slot): uow.artwork_slot_outcomes.get(rom.rom_id, slot)
                for rom in roms.values()
                for slot in (ArtworkSlot.ICON, ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO)
            }
        return _Bound(placeholder, logo_path, roms, outcomes)

    def _plan_io(self, bound: _Bound, generations: dict[int, int]) -> _Plan | None:
        """Read ``shortcuts.vdf`` and list the grid directory, and derive the two passes from them and *bound*.

        ``None`` when ``shortcuts.vdf`` cannot be read. Where the grid
        directory cannot be listed, no wide grid or logo is asked for, nor an
        icon whose path is in that directory.
        """
        icons = self._steam_config.read_shortcut_icons()
        if icons is None:
            return None
        try:
            file_names = self._steam_config.grid_file_names()
        except OSError as e:
            self._logger.warning(f"Shortcut artwork: could not list Steam's grid directory: {e}")
            file_names = None
        grid = _Grid(os.path.dirname(bound.placeholder), file_names)
        return _Plan(bound.logo_path, self._passes(bound.roms, icons, grid, bound.outcomes, generations))

    def _passes(
        self,
        roms: dict[int, Rom],
        icons: dict[int, str],
        grid: _Grid,
        outcomes: dict[tuple[int, ArtworkSlot], ArtworkSlotOutcome | None],
        generations: dict[int, int],
    ) -> tuple[list[_Item], list[_Item]]:
        """The icon and wide grid pass, then the logo pass, over *roms* keyed by app id, in ROM order."""
        missing = sum(1 for app_id in roms if app_id not in icons)
        if missing:
            self._logger.info(f"Shortcut artwork: shortcuts.vdf does not hold {missing} bound shortcut(s) yet")
        now = self._clock.time()

        def waits(app_id: int, rom: Rom, slot: ArtworkSlot) -> bool:
            recorded = outcomes[(rom.rom_id, slot)]
            if slot is ArtworkSlot.ICON:
                return app_id in icons and icon_waits(
                    icons[app_id],
                    recorded,
                    grid_dir=grid.path,
                    file_names=grid.file_names,
                    sgdb_id=rom.sgdb_id,
                    now=now,
                )
            return grid.file_names is not None and slot_waits(
                grid.file_names, app_id, slot, recorded, sgdb_id=rom.sgdb_id, now=now
            )

        first: list[_Item] = []
        second: list[_Item] = []
        for app_id, rom in sorted(roms.items(), key=lambda pair: pair[1].rom_id):
            slots = tuple(slot for slot in (ArtworkSlot.ICON, ArtworkSlot.WIDE_GRID) if waits(app_id, rom, slot))
            logo_icon = app_id in icons and logo_icon_waits(
                icons[app_id], outcomes[(rom.rom_id, ArtworkSlot.ICON)], sgdb_id=rom.sgdb_id, now=now
            )
            if slots or logo_icon:
                first.append(_Item(rom.rom_id, app_id, slots, generations[app_id], logo_icon))
            if waits(app_id, rom, ArtworkSlot.LOGO):
                second.append(_Item(rom.rom_id, app_id, (ArtworkSlot.LOGO,), generations[app_id]))
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
        if item.logo_icon:
            await self._give_logo_icon(item, batch, plan)
        for index, slot in enumerate(item.slots):
            if self._must_stop():
                return None
            if slot is ArtworkSlot.ICON:
                served = await self._serve_icon(item, batch, plan)
            else:
                served = await self._serve_slot(item, slot, batch)
            if not served:
                return _Item(item.rom_id, item.app_id, item.slots[index:], item.generation)
        return None

    async def _serve_icon(self, item: _Item, batch: list[_Finished | _SlotReady], plan: _Plan) -> bool:
        """Put *item*'s icon into the artwork cache and write it; ``False`` after a 429, so it is asked again."""
        answer = await self._loop.run_in_executor(
            None, self._source.do_fetch_slot_artwork, item.rom_id, ArtworkSlot.ICON
        )
        if answer is SlotAnswer.RATE_LIMITED:
            return False
        if answer is SlotAnswer.KEY_REFUSED:
            self._refuse_key()
        elif answer is SlotAnswer.FAILED:
            self._log_debug(
                f"Shortcut artwork: the icon fetch for rom_id={item.rom_id} failed; the next run asks again"
            )
        else:
            self._backoff = 0.0
            finished = await self._write(item, answer, plan)
            if finished is not None:
                await self._add(batch, finished)
        return True

    async def _give_logo_icon(self, item: _Item, batch: list[_Finished | _SlotReady], plan: _Plan) -> None:
        """Queue Tender's logo icon for *item*, whose recorded ``none`` was never shown, without asking SteamGridDB."""
        finished = await self._write(item, SlotAnswer.NONE, plan)
        if finished is not None:
            await self._add(batch, finished)

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
            await self._flush(batch, full_only=True)

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

    async def _write(self, item: _Item, answer: SlotAnswer, plan: _Plan) -> _Finished | None:
        return await self._shielded(self._write_unless_the_job_yields(item, answer, plan))

    async def _flush(self, batch: list[_Finished | _SlotReady], *, full_only: bool = False) -> None:
        """Hand up to a batch of what is finished to the frontend under one lease, and take it out of *batch*.

        One batch at a time: the next is taken only once the frontend has
        finished this one, its lease released, disowned or expired, so the
        renderer reading before it is taken after this one landed. That holds
        across a stop too: a run stopped while it waited leaves the batch out,
        and the next run waits for it before its first reading. With
        *full_only* nothing is taken unless a whole batch waits. Nothing is
        handed over while Steam's renderer stands at or above the session
        budget's ceiling: the run ends there, and what was finished waits for
        the next run, as :meth:`_yields_now` says.
        """
        async with self._one_batch_at_a_time:
            if not batch or (full_only and len(batch) < _BATCH_SIZE):
                return
            finished = batch[:_BATCH_SIZE]
            del batch[:_BATCH_SIZE]
            await self._wait_for_the_batch_out()
            if self._yields_now() or await self._renderer_is_full():
                return
            self._batch_out = await self._shielded(self._hand_over_unless_the_job_yields(finished))
            await self._wait_for_the_batch_out()

    async def _wait_for_the_batch_out(self) -> None:
        if self._batch_out is not None:
            await self._wait_for_the_frontend(self._batch_out)
            self._batch_out = None

    async def _wait_for_the_frontend(self, token: str) -> None:
        try:
            await asyncio.wait_for(self._rules.wait_for_lease_end(token), timeout=_BATCH_WAIT_SECONDS)
        except TimeoutError:
            self._stopped = True
            self._logger.warning(
                f"Shortcut artwork: the frontend has not finished a batch after {_BATCH_WAIT_SECONDS:.0f}s; "
                "the run ends"
            )

    async def _renderer_is_full(self) -> bool:
        if not await self._renderer_above_ceiling()():
            return False
        self._stopped = True
        self._logger.info(
            "Shortcut artwork: Steam's renderer memory is at or above the session budget's ceiling; the job "
            "runs again after the next sync, or once a panel connects after Steam restarts"
        )
        self._wait_for_a_new_panel()
        return True

    async def _shielded[T](self, work: Coroutine[Any, Any, T]) -> T:
        """Run *work* in a task of its own and count it, so :meth:`_stop` waits for it to end."""
        self._writing += 1
        self._no_write.clear()
        task = self._tasks.start_awaited(work, "shortcut artwork write")
        task.add_done_callback(self._write_done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The run that awaited it is gone, so nobody else will see how the write ends.
            task.add_done_callback(self._log_an_orphaned_failure)
            raise

    def _write_done(self, _task: asyncio.Task[Any]) -> None:
        self._writing -= 1
        if not self._writing:
            self._no_write.set()

    def _log_an_orphaned_failure(self, task: asyncio.Task[Any]) -> None:
        failure = None if task.cancelled() else task.exception()
        if failure is not None:
            self._logger.warning(f"Shortcut artwork: a write its stopped run left behind failed: {failure}")

    def _yields_now(self) -> bool:
        """Whether the run must stop here; what it wrote and has not handed over waits for the next run.

        That run finds it not set and gives it again: an image from the
        artwork cache, and the logo icon without asking SteamGridDB, since a
        ``none`` recorded for a shortcut still showing no icon does not keep
        the logo icon back (:func:`domain.artwork_slot_outcome.logo_icon_waits`).
        """
        if self._stopped or self._must_yield():
            self._stopped = True
        return self._stopped

    async def _write_unless_the_job_yields(self, item: _Item, answer: SlotAnswer, plan: _Plan) -> _Finished | None:
        """The icon *answer* calls for: SteamGridDB's, written, for ``READY``, and the logo icon for ``NONE``.

        ``NONE`` also stands for a recorded ``none`` whose logo icon the
        shortcut does not show yet. ``None`` when the job yields, when the
        write answers nothing, when there is no logo icon to give, or when the
        game page saved an icon since the item's generation was taken.
        """
        if self._yields_now():
            return None
        if answer is SlotAnswer.READY:
            written = await self._source.write_job_icon(item.app_id, item.rom_id, item.generation)
            if written is None:
                return None
            return _Finished(item.app_id, item.rom_id, written.path, item.generation, written)
        if plan.logo_path is None or self._source.icon_generation(item.app_id) != item.generation:
            return None
        return _Finished(item.app_id, item.rom_id, plan.logo_path, item.generation, None)

    async def _hand_over_unless_the_job_yields(self, finished: list[_Finished | _SlotReady]) -> str | None:
        """Emit *finished* under a lease; answer its token once a panel heard it, else ``None``."""
        if self._yields_now():
            return None
        # A pick on the game page since the job took the generation wins: for
        # the logo icon the batch would otherwise set Tender's file over the pick;
        # for SteamGridDB's icon it would record the job's image over the one
        # the page wrote.
        icons = [
            _icon_entry(icon)
            for icon in finished
            if isinstance(icon, _Finished) and self._source.icon_generation(icon.app_id) == icon.generation
        ]
        artwork = [
            {"app_id": ready.app_id, "rom_id": ready.rom_id, "slot": ready.slot.value}
            for ready in finished
            if isinstance(ready, _SlotReady)
        ]
        if not icons and not artwork:
            return None
        sent: list[str] = []

        async def emit(token: str) -> bool:
            sent.append(token)
            return await self._emit(
                "shortcut_artwork", {"icons": icons, "artwork": artwork, "prune_lease_token": token}
            )

        if await self._rules.emit_under_lease("shortcut_artwork", emit):
            return sent[0]
        self._stopped = True
        self._logger.info("Shortcut artwork: no panel heard the batch; the job runs again once one connects")
        self._wait_for_a_panel()
        return None


def _icon_entry(icon: _Finished) -> dict[str, Any]:
    """The batch's entry for *icon*; SteamGridDB's icon also names what its ``set`` record is to carry."""
    entry: dict[str, Any] = {"app_id": icon.app_id, "icon_path": icon.icon_path}
    if icon.written is not None:
        entry["record"] = {
            "rom_id": icon.rom_id,
            "sgdb_id": icon.written.sgdb_id,
            "image_id": icon.written.image_id,
            "sha256": icon.written.sha256,
        }
    return entry
