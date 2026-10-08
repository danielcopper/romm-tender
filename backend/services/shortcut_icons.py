"""The shortcut icon job: every bound shortcut that waits for its SteamGridDB icon gets one, in the background.

Contract: docs/architecture/steam-non-steam-shortcuts.md, "Shortcut icons". The
job derives its worklist from ``shortcuts.vdf`` each time it runs
(:func:`domain.shortcut_icon.icon_worklist`) and stores nothing, so a run that is
stopped leaves exactly the rest for the next one. It fetches four icons at a
time, writes each into Steam's grid directory, and hands the finished ones to
the frontend in batches as the ``shortcut_icons`` event.

It yields to a sync and to a removed-game cleanup. A sync is seen between icons
and the run's end asks for the job again; a cleanup stops it through
:meth:`ShortcutIconService.stop_for_cleanup` before it reserves its start, and
asks for it again when it ends. The job is no endpoint, so it takes no ``hold``:
it asks whether a cleanup runs before each write and each hand-over instead.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from models.shortcut_icon import IconAnswer, IconFetch

from domain.shortcut_icon import (
    LOGO_ICON_NAME,
    PLACEHOLDER_ICON_NAME,
    PLACEHOLDER_ICON_PNG,
    icon_worklist,
)
from lib.errors import SteamGridDirMissingError

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Coroutine

    from services.protocols import (
        ConflictRules,
        DebugLogger,
        EventEmitter,
        ShortcutIconSource,
        Sleeper,
        SteamConfigStore,
        TenderLogoFn,
        UnitOfWorkFactory,
        WorkInFlightFn,
    )

_WORKERS = 4
_BATCH_SIZE = 25
# How long a run after a sync waits for Steam to rewrite shortcuts.vdf with the
# shortcuts it created (docs/architecture/steam-non-steam-shortcuts.md,
# "Shortcut icons").
_SETTLE_SECONDS = 10.0
_FIRST_BACKOFF_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 15 * 60.0
_PANEL_POLL_SECONDS = 5.0


@dataclass(frozen=True)
class ShortcutIconServiceConfig:
    """Frozen wiring bundle handed to ``ShortcutIconService.__init__``.

    Holds the Steam config store the worklist is read from and the two fixed
    icons are written through, the SteamGridDB service's icon seam, the
    Unit-of-Work factory the bound shortcuts are read with, the live settings
    (the API key), the conflict rules and the emitter a batch goes out under,
    the sleeper every wait goes through, the three readings the job asks before
    it acts (a sync in flight, a removed-game cleanup running, a panel
    connected), the shipped logo's reader, and runtime infrastructure.
    """

    steam_config: SteamConfigStore
    icons: ShortcutIconSource
    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    conflict_rules: ConflictRules
    emit: EventEmitter
    sleeper: Sleeper
    sync_in_flight: Callable[[], bool]
    cleanup_running: WorkInFlightFn
    panel_connected: Callable[[], bool]
    tender_logo: TenderLogoFn
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger


@dataclass(frozen=True)
class _Plan:
    """One run's worklist and the two fixed icon paths it compares and writes."""

    placeholder_path: str
    logo_path: str | None
    worklist: list[tuple[int, int]]


@dataclass(frozen=True)
class _Finished:
    """An icon written for *app_id*, and the game-page generation the job read before it fetched."""

    app_id: int
    icon_path: str
    generation: int


class ShortcutIconService:
    """Runs the shortcut icon job, one run at a time, and answers a sync's and a cleanup's start and end."""

    def __init__(self, *, config: ShortcutIconServiceConfig) -> None:
        self._steam_config = config.steam_config
        self._icons = config.icons
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._rules = config.conflict_rules
        self._emit = config.emit
        self._sleeper = config.sleeper
        self._sync_in_flight = config.sync_in_flight
        self._cleanup_running = config.cleanup_running
        self._panel_connected = config.panel_connected
        self._tender_logo = config.tender_logo
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._task: asyncio.Task[None] | None = None
        self._again = False
        self._settle = False
        self._stopped = False
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
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._stopped = False
        self._task = self._loop.create_task(self._run())

    async def run_when_a_panel_connects(self) -> None:
        """Start a run once a panel is there to hear its batches; the backend's start-up trigger."""
        while not self._panel_connected():
            await self._sleeper.sleep(_PANEL_POLL_SECONDS)
        self.request_run()

    async def placeholder_path(self) -> str | None:
        """Implements ``services.protocols.ShortcutIconJob.placeholder_path``.

        Where it answers ``None`` the sync gives the shortcuts it creates no
        icon at all.
        """
        try:
            return await self._loop.run_in_executor(
                None, self._steam_config.ensure_grid_file, PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG
            )
        except (SteamGridDirMissingError, OSError) as e:
            self._logger.warning(f"Shortcut icons: no placeholder to give new shortcuts: {e}")
            return None

    async def stop_for_cleanup(self) -> None:
        """Stop the run under way, and return once no icon is being written or handed over.

        A fetch in flight is abandoned; a write or a hand-over already under
        way lands first, so none of the job's lands after the cleanup that
        called this reserves its start.
        """
        await self._stop()

    async def shutdown(self) -> None:
        """Stop for good: no later trigger starts a run."""
        self._closed = True
        await self._stop()

    async def _stop(self) -> None:
        self._stopped = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._no_write.wait()

    # -- one run ---------------------------------------------------------------

    async def _run(self) -> None:
        try:
            while True:
                self._again = False
                if self._settle:
                    self._settle = False
                    await self._sleeper.sleep(_SETTLE_SECONDS)
                await self._run_once()
                if not self._again or self._stopped:
                    return
        except Exception:
            self._logger.exception("Shortcut icons: the run stopped on an unexpected error")

    async def _run_once(self) -> None:
        if not self._settings.get("steamgriddb_api_key"):
            self._log_debug("Shortcut icons: no SteamGridDB API key, nothing to do")
            return
        if self._sync_in_flight():
            self._log_debug("Shortcut icons: a sync is running; its end asks again")
            return
        plan = await self._loop.run_in_executor(None, self._plan_io)
        if plan is None or not plan.worklist:
            return
        self._logger.info(f"Shortcut icons: {len(plan.worklist)} shortcut(s) wait for an icon")
        queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()
        for item in plan.worklist:
            queue.put_nowait(item)
        batch: list[_Finished] = []
        workers = [self._loop.create_task(self._work(queue, batch, plan)) for _ in range(_WORKERS)]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
        if not self._stopped:
            await self._flush(batch)

    def _plan_io(self) -> _Plan | None:
        """Lay down the two fixed icons and derive the worklist.

        ``None`` when the grid directory cannot be written or ``shortcuts.vdf``
        cannot be read. A logo that cannot be read is no reason to stop: the
        plan then has no logo path, and a game SteamGridDB has no icon for keeps
        its placeholder.
        """
        try:
            placeholder_path = self._steam_config.ensure_grid_file(PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG)
            logo = self._tender_logo()
            logo_path = self._steam_config.ensure_grid_file(LOGO_ICON_NAME, logo) if logo else None
        except SteamGridDirMissingError as e:
            self._logger.warning(f"Shortcut icons: {e}")
            return None
        except OSError as e:
            self._logger.warning(f"Shortcut icons: could not write Tender's icons: {e}")
            return None
        icons = self._steam_config.read_shortcut_icons()
        if icons is None:
            return None
        with self._uow_factory() as uow:
            bound = {rom.shortcut_app_id: rom.rom_id for rom in uow.roms.iter_all() if rom.shortcut_app_id is not None}
        return _Plan(placeholder_path, logo_path, icon_worklist(bound, icons, placeholder_path))

    async def _work(self, queue: asyncio.Queue[tuple[int, int]], batch: list[_Finished], plan: _Plan) -> None:
        while not queue.empty():
            await self._not_paused.wait()
            if self._stopped or self._sync_in_flight():
                return
            rom_id, app_id = queue.get_nowait()
            generation = self._icons.icon_generation(app_id)
            fetch = await self._loop.run_in_executor(None, self._icons.fetch_shortcut_icon_io, rom_id)
            if fetch.answer is IconAnswer.RATE_LIMITED:
                queue.put_nowait((rom_id, app_id))
                await self._wait_out_rate_limit()
                continue
            if fetch.answer is IconAnswer.FAILED:
                self._log_debug(f"Shortcut icons: no answer for rom_id={rom_id}; the next run asks again")
                continue
            self._backoff = 0.0
            if self._stopped or self._sync_in_flight():
                return
            path = await self._write(app_id, fetch, generation, plan)
            if path is not None:
                batch.append(_Finished(app_id, path, generation))
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
        self._logger.info(f"Shortcut icons: SteamGridDB answered 429, waiting {self._backoff:.0f}s")
        self._not_paused.clear()
        try:
            await self._sleeper.sleep(self._backoff)
        finally:
            self._not_paused.set()

    async def _write(self, app_id: int, fetch: IconFetch, generation: int, plan: _Plan) -> str | None:
        return await self._shielded(self._write_unless_a_cleanup_runs(app_id, fetch, generation, plan))

    async def _flush(self, batch: list[_Finished]) -> None:
        """Hand the finished icons to the frontend under one lease, and empty *batch*."""
        if not batch:
            return
        finished = list(batch)
        batch.clear()
        await self._shielded(self._hand_over_unless_a_cleanup_runs(finished))

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

    async def _write_unless_a_cleanup_runs(
        self, app_id: int, fetch: IconFetch, generation: int, plan: _Plan
    ) -> str | None:
        if self._cleanup_running():
            self._stopped = True
            return None
        if fetch.answer is IconAnswer.ICON and fetch.data is not None:
            return await self._icons.write_job_icon(app_id, fetch.data, generation)
        if plan.logo_path is None or self._icons.icon_generation(app_id) != generation:
            return None
        return plan.logo_path

    async def _hand_over_unless_a_cleanup_runs(self, finished: list[_Finished]) -> None:
        if self._cleanup_running():
            self._stopped = True
            return
        # A pick on the game page since the job read the generation wins: for
        # the logo the batch would otherwise set Tender's file over the pick.
        icons = [
            {"app_id": icon.app_id, "icon_path": icon.icon_path}
            for icon in finished
            if self._icons.icon_generation(icon.app_id) == icon.generation
        ]
        if not icons:
            return
        await self._rules.emit_under_lease(
            "shortcut_icons",
            lambda token: self._emit("shortcut_icons", {"icons": icons, "prune_lease_token": token}),
        )
