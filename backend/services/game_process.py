"""GameProcessService — ending the game a Stop Game press was meant for.

Owns the stop-game policy: which processes are the game the user pressed Stop
on — those Steam started for the ROM's shortcut — the order they are asked to
exit in, how long they get before force, and the answer the frontend's Stop
Game action gets: the counts, or a raised refusal. The signal mechanics sit behind the
``GameProcessControl`` Protocol, so nothing here touches a syscall or a POSIX
signal number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lib.errors import Refused

if TYPE_CHECKING:
    import logging

    from services.protocols import (
        DebugLogger,
        GameProcessControl,
        Sleeper,
        UnitOfWorkFactory,
    )

# Grace window the ladder gives a stop-requested process before the force kill:
# ``_GRACE_POLLS`` polls of ``_GRACE_POLL_SECONDS`` (6 s total). Emulators write
# their save file from the stop handler, and a large memory-card image on a cold
# SD card is not instant, so the window is deliberately generous — it is only
# ever paid in full by a process that ignores the request outright, and the poll
# returns the moment everything has exited.
_GRACE_POLL_SECONDS = 0.25
_GRACE_POLLS = 24


@dataclass(frozen=True)
class GameProcessServiceConfig:
    """Frozen wiring bundle handed to ``GameProcessService.__init__``.

    Carries the process-control seam the ladder acts through, the Unit-of-Work
    factory the ROM's shortcut appId is read through, the injected ``Sleeper``
    the grace window is spent on (services own no clocks), and the runtime
    logger and debug logger.
    """

    game_process: GameProcessControl
    uow_factory: UnitOfWorkFactory
    sleeper: Sleeper
    logger: logging.Logger
    log_debug: DebugLogger


class GameProcessService:
    """Stops the running game via a one-request-then-force escalation ladder."""

    def __init__(self, *, config: GameProcessServiceConfig) -> None:
        self._game_process = config.game_process
        self._uow_factory = config.uow_factory
        self._sleeper = config.sleeper
        self._logger = config.logger
        self._log_debug = config.log_debug
        # Single-flight admission flag for the ladder — see ``stop_running_game``.
        self._stopping = False

    async def stop_running_game(self, rom_id: int) -> dict[str, Any]:
        """Stop the processes running *rom_id*, escalating only if they refuse to exit.

        Returns ``{"success": True, "stopped": int, "force_killed": int}`` once
        every process Steam started for the ROM's shortcut has been dealt with,
        where ``stopped`` counts the processes that received the stop request
        and ``force_killed`` the subset that had to be forced. Raises ``Refused``
        with one of two reasons: ``not_running`` when nothing runs under the
        ROM's shortcut — or the ROM has none — which is the honest answer when
        the frontend's running overlay has gone stale, and ``already_stopping``
        when a ladder is already in flight.

        A process that exits between discovery and the request is not an error:
        the requested end state is already true, so the run still succeeds (with
        a smaller ``stopped`` count).
        """
        # ── Single-flight admission, claimed before ANY await ──────────────────
        #
        # The exactly-once stop request is a guarantee across CALLS, not merely
        # within one. The grace window below yields the event loop for seconds
        # while the emulator flushes, during which the user sees no change and
        # may well press Stop again. A second concurrent call would rediscover
        # the same still-alive pids and send them a second stop request — the
        # exact save-destroying repeat the ladder exists to prevent. So refuse.
        #
        # A plain flag, deliberately NOT an ``asyncio.Lock``: a Lock queues the
        # second caller and runs it the moment the first releases, which fires
        # the repeat a few seconds late instead of never. The needed semantic is
        # REFUSE, not wait. Unsynchronised access is safe because the event loop
        # is single-threaded and no await sits between the check and the set —
        # the same compare-and-swap shape as ``LibrarySyncStateBox.try_begin_run``.
        #
        # The claim is GLOBAL rather than per-ROM. A shortcut's binding moves
        # between the versions of a game (ADR-0021), so a second ROM id can
        # name the same process tree while a ladder runs over it, and per-ROM
        # claims would admit both — precisely the save-destroying second request.
        # Global costs at most the grace window of a stop the user is already
        # watching (and the poll returns the moment the tree is gone, so the
        # usual cost is one 0.25 s interval, not the full 6 s) before a second
        # game can be stopped, and it cannot be wrong; the finer claim buys a
        # rare convenience for a real risk.
        if self._stopping:
            self._log_debug("GameProcessService: stop refused — a stop is already in flight")
            raise Refused("already_stopping", "The game is already being stopped.")
        self._stopping = True
        try:
            return await self._run_stop_ladder(rom_id)
        finally:
            # Released on every exit path, exceptions included — a leaked flag
            # would make Stop Game permanently unavailable for the session.
            self._stopping = False

    async def _run_stop_ladder(self, rom_id: int) -> dict[str, Any]:
        """Run the discovery → stop-request → grace → force ladder once.

        Split from :meth:`stop_running_game` so the single-flight claim and its
        release read as one unit; every caller must hold that claim.
        """
        app_id = self._shortcut_app_id(rom_id)
        pids = self._game_process.find_game_processes(app_id) if app_id is not None else []
        if not pids:
            self._log_debug(f"GameProcessService: nothing runs under the shortcut of rom_id={rom_id} (appId={app_id})")
            raise Refused("not_running", "No running game was found to stop.")

        # ── The ladder: ONE stop request per process, grace window, then force ──
        #
        # NEVER send a second stop request to a process that is still alive.
        # Emulators treat the second one as "the user means it" and skip the
        # save flush the first one started: RetroArch's handler runs
        # ``if (unix_sighandler_quit == 2) exit(1);`` and registers no
        # ``atexit``; DuckStation and PCSX2 route the repeat through
        # ``quick_exit``; Dolphin installs its handler with ``SA_RESETHAND`` so
        # the second signal takes the default terminate action. In every case
        # the repeat DESTROYS the save file the first request was in the middle
        # of writing. A "just retry the polite signal a few times" loop reads
        # like a robustness improvement and would silently corrupt saves — the
        # only escalation permitted here is the force kill below, and only once
        # the grace window is fully spent.
        #
        # "Never a second request" spans CALLS as well as this loop: a repeat
        # from a concurrent invocation is just as fatal, which is what the
        # single-flight claim in ``stop_running_game`` refuses. Both halves are
        # load-bearing; neither alone gives the guarantee.
        requested = [pid for pid in pids if self._game_process.request_stop(pid)]
        self._log_debug(
            f"GameProcessService: stop requested for {len(requested)}/{len(pids)} "
            f"process(es) of appId={app_id}: {requested}"
        )

        survivors = await self._await_exit(requested)
        forced = [pid for pid in survivors if self._game_process.force_kill(pid)]

        # The forced pids are named only when there are any — an escalation is the
        # unusual path and worth having in the log verbatim when it happens.
        forced_note = f", force-killed {forced} after the grace window" if forced else ""
        self._logger.info(
            f"Stopped running game (rom_id={rom_id}, appId={app_id}): "
            f"{len(requested)} of {len(pids)} process(es) signalled{forced_note}"
        )
        return {"success": True, "stopped": len(requested), "force_killed": len(forced)}

    def _shortcut_app_id(self, rom_id: int) -> int | None:
        """Return the appId of the Steam shortcut *rom_id* is bound to, or None when it is bound to none.

        Nothing Steam runs belongs to a ROM bound to no shortcut.
        """
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
        return rom.shortcut_app_id if rom is not None else None

    async def _await_exit(self, pids: list[int]) -> list[int]:
        """Poll *pids* across the grace window; return those still alive at the end.

        Returns as soon as every process has gone, so a well-behaved emulator
        costs one poll interval rather than the whole window. Nothing is
        signalled from here — the window is pure waiting by design (see the
        ladder note in :meth:`stop_running_game`).
        """
        if not pids:
            return []
        alive = list(pids)
        for _ in range(_GRACE_POLLS):
            await self._sleeper.sleep(_GRACE_POLL_SECONDS)
            alive = [pid for pid in alive if self._game_process.is_alive(pid)]
            if not alive:
                return []
        return alive
