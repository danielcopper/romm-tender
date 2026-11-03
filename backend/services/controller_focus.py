"""The focus watcher: tells Steam which controller layout the focused window gets.

Contract: while Steam's 64-bit client runs outside gamescope, every change of
the focused window is answered with ``steam://forceinputappid/<id>`` — the id of
the Steam launch the window's process runs under, Big Picture's, or the
desktop's — and the pin it leaves is released with ``0`` once, at stop, through
:meth:`ControllerFocusService.release_pin`, which the host calls before it lets
go of Steam's debugger. Anywhere else, and with the switch off, it sends nothing.
The rules are ``domain/controller_focus.py``'s; why the watcher exists, and what
each id does inside Steam, is docs/architecture/controller-focus.md.

Two loops share one X connection: one follows Steam — whether it runs, which
client, restarted or not — every few seconds, and opens or closes the
connection to match; the other wakes when the connection's socket becomes
readable and answers the focus change. A change arriving while a send is in
flight is not queued: the next pass reads the window focused by then.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.controller_focus import (
    RELEASED_INPUT_ID,
    force_input_url,
    input_id_for,
    watcher_applies,
)

if TYPE_CHECKING:
    import logging

    from domain.controller_focus import FocusedWindow, RunningSteam, SteamIdentity
    from services.protocols import (
        DebugLogger,
        FocusedWindowReader,
        RunningSteamReader,
        Sleeper,
        SteamLaunchReader,
        SteamUrlControl,
    )

# How often Steam is read: a restart, a switch of client and a lost connection
# are noticed within this, and a send that could not go out is tried again.
POLL_SECONDS = 3.0


@dataclass(frozen=True)
class ControllerFocusSeams:
    """What the watcher reads and sends through; a watcher switched off has none.

    The readers are the running Steam, the X connection and the walk from a
    process to its Steam launch; *steam_urls* is how a URL reaches Steam.
    """

    running_steam: RunningSteamReader
    focused_window: FocusedWindowReader
    steam_launch: SteamLaunchReader
    steam_urls: SteamUrlControl


@dataclass(frozen=True)
class ControllerFocusServiceConfig:
    """Frozen wiring bundle handed to ``ControllerFocusService.__init__``.

    *seams* is ``None`` where the entry point switched the watcher off; then
    the service opens nothing and sends nothing. The loop runs the readers off
    itself and watches the X socket; the sleeper paces the reading of Steam.
    """

    seams: ControllerFocusSeams | None
    loop: asyncio.AbstractEventLoop
    sleeper: Sleeper
    logger: logging.Logger
    log_debug: DebugLogger


class ControllerFocusService:
    """Keeps Steam's controller layout on the focused window where Steam cannot attribute it itself."""

    def __init__(self, *, config: ControllerFocusServiceConfig) -> None:
        self._watch = (
            _FocusWatch(
                seams=config.seams,
                loop=config.loop,
                sleeper=config.sleeper,
                logger=config.logger,
                log_debug=config.log_debug,
            )
            if config.seams is not None
            else None
        )

    async def run(self) -> None:
        """Follow Steam and the focused window until cancelled; switched off, return at once."""
        if self._watch is not None:
            await self._watch.run()

    async def release_pin(self) -> None:
        """Hand the input choice back to Steam, once: ``0``, only where this run pinned and that Steam still runs.

        Every send after this is dropped, so nothing pins again behind the
        release. Called by the host before it detaches from Steam's debugger,
        which the send needs. Switched off, it sends nothing.
        """
        if self._watch is not None:
            await self._watch.release_pin()


class _FocusWatch:
    """The watcher itself, which exists only where it is switched on."""

    def __init__(
        self,
        *,
        seams: ControllerFocusSeams,
        loop: asyncio.AbstractEventLoop,
        sleeper: Sleeper,
        logger: logging.Logger,
        log_debug: DebugLogger,
    ) -> None:
        self._running_steam = seams.running_steam
        self._source = seams.focused_window
        self._steam_launch = seams.steam_launch
        self._steam_urls = seams.steam_urls
        self._loop = loop
        self._sleeper = sleeper
        self._logger = logger
        self._log_debug = log_debug
        self._wake = asyncio.Event()
        # Every use of the X connection, opening and closing it included.
        self._source_lock = asyncio.Lock()
        # A send, and the release that must come after every send.
        self._send_lock = asyncio.Lock()
        self._steam: SteamIdentity | None = None
        self._fd: int | None = None
        self._send_due = False
        self._pinned_for: SteamIdentity | None = None
        self._unreachable_said_for: SteamIdentity | None = None
        self._send_failure_said = False
        self._released = False

    async def run(self) -> None:
        try:
            await asyncio.gather(self._follow_steam(), self._follow_focus())
        finally:
            async with self._source_lock:
                await self._close_source()

    async def release_pin(self) -> None:
        async with self._send_lock:
            self._released = True
            pinned, self._pinned_for = self._pinned_for, None
            if pinned is None:
                return
            steam = await self._loop.run_in_executor(None, self._running_steam.read)
            if steam is None or steam.identity != pinned:
                self._log_debug("controller focus: the Steam that carried the pin is gone; nothing to release")
                return
            if await self._steam_urls.execute_steam_url(force_input_url(RELEASED_INPUT_ID)):
                self._logger.info("controller focus: handed the controller layout back to Steam")
            else:
                self._logger.warning("controller focus: could not hand the controller layout back to Steam")

    # -- following Steam -------------------------------------------------------

    async def _follow_steam(self) -> None:
        while True:
            try:
                await self._look_at_steam()
            except Exception as exc:
                self._logger.warning(f"controller focus: reading Steam failed: {exc!r}")
            await self._sleeper.sleep(POLL_SECONDS)

    async def _look_at_steam(self) -> None:
        steam = await self._loop.run_in_executor(None, self._running_steam.read)
        applies = watcher_applies(steam)
        async with self._source_lock:
            identity = steam.identity if steam is not None else None
            if identity != self._steam:
                self._steam = identity
                await self._close_source()
                if applies:
                    self._logger.info("controller focus: Steam's 64-bit client runs outside gamescope; watching")
                else:
                    self._log_debug("controller focus: Steam attributes windows itself here; not watching")
            if steam is None or not applies:
                return
            if self._fd is not None and await self._loop.run_in_executor(None, self._source.lost):
                self._logger.info("controller focus: the X connection was lost; connecting again")
                await self._close_source()
            if self._fd is None and not await self._open_source(steam):
                return
        if self._send_due:
            self._wake.set()

    async def _open_source(self, steam: RunningSteam) -> bool:
        if steam.display is None:
            reason = "Steam's desktop session names no X display"
        else:
            reason = await self._loop.run_in_executor(None, self._source.connect, steam.display)
        if reason is not None:
            if self._unreachable_said_for != steam.identity:
                self._unreachable_said_for = steam.identity
                self._logger.warning(f"controller focus: not watching, {reason}; trying again")
            else:
                self._log_debug(f"controller focus: still not watching, {reason}")
            return False
        fd = self._source.fileno()
        self._fd = fd
        self._loop.add_reader(fd, self._readable, fd)
        self._send_due = True
        return True

    async def _close_source(self) -> None:
        if self._fd is not None:
            self._loop.remove_reader(self._fd)
            self._fd = None
        self._send_due = False
        await self._loop.run_in_executor(None, self._source.close)

    def _readable(self, fd: int) -> None:
        # Unregistered until the pass that reads the socket is through:
        # registered, an unread socket would call back on every loop turn.
        self._loop.remove_reader(fd)
        self._wake.set()

    # -- following the focus ---------------------------------------------------

    async def _follow_focus(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self._answer_the_focus()
            except Exception as exc:
                self._logger.warning(f"controller focus: answering a focus change failed: {exc!r}")

    async def _answer_the_focus(self) -> None:
        async with self._source_lock:
            if self._fd is None:
                return
            changed = await self._loop.run_in_executor(None, self._source.take_focus_changes)
            if await self._loop.run_in_executor(None, self._source.lost):
                self._logger.info("controller focus: the X connection was lost; connecting again")
                await self._close_source()
                return
            self._loop.add_reader(self._fd, self._readable, self._fd)
            if not (changed or self._send_due):
                return
            self._send_due = False
            window = await self._loop.run_in_executor(None, self._source.focused_window)
            steam = self._steam
        input_id = input_id_for(window, await self._launched_app_id(window))
        if await self._send(input_id, steam):
            # Reading the window may have taken a later change off the socket
            # with the replies; one more pass looks. Not after a send that
            # failed, which the next reading of Steam tries again.
            self._wake.set()

    async def _launched_app_id(self, window: FocusedWindow | None) -> int | None:
        if window is None or window.host_pid is None:
            return None
        return await self._loop.run_in_executor(None, self._steam_launch.launched_app_id, window.host_pid)

    async def _send(self, input_id: int, steam: SteamIdentity | None) -> bool:
        """Hand Steam *input_id* for the run *steam*; answer whether it went out."""
        async with self._send_lock:
            if self._released:
                return False
            if await self._steam_urls.execute_steam_url(force_input_url(input_id)):
                self._pinned_for = steam
                self._send_failure_said = False
                self._log_debug(f"controller focus: input id {input_id}")
                return True
            self._send_due = True
            if not self._send_failure_said:
                self._send_failure_said = True
                self._logger.info("controller focus: Steam could not be handed the input id yet; trying again")
            return False
