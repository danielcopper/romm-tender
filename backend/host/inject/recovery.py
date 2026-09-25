"""Replacing a panel an earlier backend left in Steam.

Contract: what happens when the context this backend attaches to already carries
a panel another backend process loaded. That panel holds the other process's
token, so this server refuses every connection it makes, and the injector loads
nothing over its marker — the context has to be rebuilt before this backend's
panel can go in. This module decides when to ask Steam for that, asks, and falls
back once if asking did not work. It holds no connection of its own: the injector hands
it a way to evaluate into whichever context is attached right now.

**Only a definite "nothing is running" lets it act.** A reload takes Steam's
interface away, so it waits for every app to exit first, and a reading that
could not be taken is a reason to wait rather than an answer. The
same gate stands in front of the fallback.

**At most once per stranded panel.** One reload, and one fallback if the
earlier panel is still there after the reload; after that it says it is giving
up and does nothing more for that panel. Across backend starts, ``ReloadLimit``
caps how often this machine does either at all. Everything that takes the
interface down is announced to the injector first, so the crash watchdog never
reads it as a crash of its own making.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from host.inject.bootstrap import marker_owner_expression, read_panel_marker
from host.inject.cdp import CdpConnectionLost, CdpUnavailableError

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from host.inject.bootstrap import PanelMarker
    from host.inject.reload_limit import ReloadLimit

# How often Steam is asked whether an app is still running. The wait is for a
# person to finish a game, so there is nothing to gain from asking more often.
APP_POLL_SECONDS = 5.0

# How long this backend's panel is given to connect after the context was
# reloaded, and after the fallback.
PANEL_BACK_AFTER_RELOAD_SECONDS = 20.0
PANEL_BACK_AFTER_RESTART_SECONDS = 60.0

# Evaluated directly, the call answers "Cannot find default execution context"
# — the answer that led ADR-0024 to rule the call out. Scheduled with
# ``setTimeout``, as Decky Loader schedules the same call
# (backend/decky_loader/helpers.py), the evaluation returns first and the
# rebuild follows.
RELOAD_DELAY_MS = 200

# ``SteamUIStore.RunningApps`` is the source ``frontend/src/utils/runningApps.ts``
# reads. Unlike that reader, every shape it cannot read answers ``null`` rather
# than an empty list, because here an empty list is the go-ahead.
RUNNING_APPS_EXPRESSION = (
    "(() => {"
    ' if (typeof SteamUIStore === "undefined" || SteamUIStore === null) { return null; }'
    " const listed = SteamUIStore.RunningApps;"
    ' if (listed === null || typeof listed === "undefined") { return null; }'
    ' if (!Array.isArray(listed) && typeof listed[Symbol.iterator] !== "function") { return null; }'
    " return Array.from(listed, (app) =>"
    ' String((app && (app.display_name || app.strDisplayName || app.appid)) || "an app"));'
    " })()"
)

RELOAD_EXPRESSION = (
    "(() => {"
    " const browser = window.SteamClient && window.SteamClient.Browser;"
    ' if (!browser || typeof browser.RestartJSContext !== "function") { return false; }'
    f" setTimeout(() => browser.RestartJSContext(), {RELOAD_DELAY_MS});"
    " return true;"
    " })()"
)

_RELOADING = "reloading Steam's JS context"
_RESTARTING = "terminating steamwebhelper"


class EvaluateFn(Protocol):
    """Evaluate one expression in the attached context and answer its value.

    Raises :class:`CdpConnectionLost`, :class:`CdpUnavailableError` or
    ``TimeoutError`` when there is no answer to be had — no context attached,
    the page threw, or the debugger did not reply in time.
    """

    async def __call__(self, expression: str) -> Any: ...


class PanelPresence(Protocol):
    """Whether this backend's panel is connected — ``host.server.HostServer`` answers it."""

    @property
    def connected(self) -> bool:
        """Is a panel connected right now?"""
        ...

    async def wait_connected(self) -> None:
        """Return once a panel is connected."""
        ...


@dataclass(frozen=True)
class _OwnerReading:
    """Whose panel the context carries (``marker`` ``None``: none), or why it could not be asked."""

    answered: bool
    marker: PanelMarker | None = None
    why: str = ""


@dataclass(frozen=True)
class _AppsReading:
    """Steam's running apps by name, or why they could not be read (``names`` is ``None``)."""

    names: tuple[str, ...] | None
    why: str = ""


class StrandedPanelRecovery:
    """Reloads Steam's JS context once for a panel an earlier backend left there."""

    def __init__(
        self,
        *,
        evaluate: EvaluateFn,
        panel: PanelPresence,
        terminate_webhelper: Callable[[], int],
        before_takedown: Callable[[], None],
        limit: ReloadLimit,
        logger: logging.Logger,
    ) -> None:
        self._evaluate = evaluate
        self._panel = panel
        self._terminate_webhelper = terminate_webhelper
        self._before_takedown = before_takedown
        self._limit = limit
        self._logger = logger
        self._stranded: PanelMarker | None = None
        self._reloaded_for: str | None = None
        self._said_not_again = False
        self._task: asyncio.Task[None] | None = None

    def seen(self, marker: PanelMarker) -> None:
        """The attached context carries *marker*, which is not this backend's."""
        self._stranded = marker
        if self._task is not None and not self._task.done():
            return
        if self._panel.connected:
            return
        if marker.instance == self._reloaded_for:
            if not self._said_not_again:
                self._said_not_again = True
                self._logger.warning(
                    "inject: Steam still carries the earlier backend's panel after one attempt to replace it; not "
                    "trying again. Restart Steam to load this backend's panel."
                )
            return
        self._task = asyncio.create_task(self._recover_reporting(marker))

    def cleared(self) -> None:
        """The attached context carries no panel but this backend's, or none at all."""
        self._stranded = None

    async def close(self) -> None:
        """Stop whatever is under way; the backend is going."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- the recovery ------------------------------------------------------------

    async def _recover_reporting(self, marker: PanelMarker) -> None:
        """Run the recovery, and say so when it fails in a way nobody planned for.

        Nothing awaits this task until shutdown, so an exception left to escape
        would end the recovery with nothing said anywhere.
        """
        try:
            await self._recover(marker)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("inject: replacing the earlier backend's panel failed unexpectedly; giving up")

    async def _recover(self, marker: PanelMarker) -> None:
        """Reload once no app runs; fall back once if no panel follows."""
        earlier = f"Tender {marker.version}" if marker.version else "an older Tender"
        self._logger.info(
            f"inject: Steam still carries a panel an earlier backend loaded ({earlier}); it cannot reach this "
            f"backend, so Steam's JS context will be reloaded to replace it once no app is running"
        )
        if not await self._clear_to_act(marker, _RELOADING):
            self._logger.info("inject: the earlier backend's panel is gone without a reload; nothing to replace")
            return

        self._reloaded_for = marker.instance
        if not self._may_take_the_interface_down():
            return
        self._logger.info(f"inject: no app is running; {_RELOADING} to replace the earlier backend's panel")
        self._before_takedown()
        if await self._request_reload():
            if await self._panel_back(PANEL_BACK_AFTER_RELOAD_SECONDS, _RELOADING):
                return
            if not self._still_stranded(marker):
                # The context was rebuilt and the earlier panel went with it, so
                # the reload did what it could; restarting the web helper would
                # only take the interface away again from a load already under way.
                self._logger.info(
                    f"inject: the earlier backend's panel is gone, but no panel of this backend connected within "
                    f"{PANEL_BACK_AFTER_RELOAD_SECONDS:.0f}s of asking Steam to reload its JS context; the context was "
                    f"rebuilt, so the ordinary load takes it from here — its inject: lines say how it went"
                )
                return
            self._logger.info(
                f"inject: no panel of this backend connected within {PANEL_BACK_AFTER_RELOAD_SECONDS:.0f}s of asking "
                f"Steam to reload its JS context; falling back to {_RESTARTING}, which Steam starts again"
            )
        else:
            self._logger.info(f"inject: falling back to {_RESTARTING}, which Steam starts again")
        if not await self._clear_to_act(marker, _RESTARTING):
            self._logger.info(f"inject: the earlier backend's panel is gone; not {_RESTARTING}")
            return

        if not self._may_take_the_interface_down():
            return
        self._before_takedown()
        loop = asyncio.get_running_loop()
        signalled = await loop.run_in_executor(None, self._terminate_webhelper)
        if not signalled:
            self._logger.warning(
                "inject: found no steamwebhelper process of this user to terminate; giving up. Restart Steam to load "
                "this backend's panel."
            )
            return
        self._logger.info(f"inject: sent SIGTERM to {signalled} steamwebhelper process(es)")
        if await self._panel_back(PANEL_BACK_AFTER_RESTART_SECONDS, _RESTARTING):
            return
        self._logger.warning(
            f"inject: no panel of this backend connected within {PANEL_BACK_AFTER_RESTART_SECONDS:.0f}s of "
            f"{_RESTARTING} either; giving up. Restart Steam to load it."
        )

    def _may_take_the_interface_down(self) -> bool:
        """Ask the limit, and record the takedown it allows; say so, once, when it refuses."""
        if not self._limit.allows():
            self._said_not_again = True
            self._logger.warning(
                f"inject: Tender has already taken Steam's interface down {self._limit.limit} times in the last "
                f"{self._limit.window / 60:.0f} minutes; not doing it again for the earlier backend's panel. Restart "
                f"Steam to load this backend's panel."
            )
            return False
        self._limit.record()
        return True

    def _still_stranded(self, marker: PanelMarker) -> bool:
        """Is *marker* still what the attached context carries, with no panel of ours connected?"""
        return self._stranded is not None and self._stranded.instance == marker.instance and not self._panel.connected

    async def _clear_to_act(self, marker: PanelMarker, before: str) -> bool:
        """Wait until it is safe to take the interface down over *marker*.

        Safe is two readings one poll apart that say no app is running — a
        freshly rebuilt context can list none for a few seconds while a game is
        still up (``frontend/src/utils/runningApps.ts``) — and then the context,
        asked there and then rather than remembered, still carrying *marker*.
        ``False`` once it no longer does, or a panel of this backend connected.
        """
        said = ""
        empty_before = False
        while self._still_stranded(marker):
            reading = await self._running_apps()
            if not self._still_stranded(marker):
                return False
            if reading.names == () and empty_before:
                owner = await self._owner_now()
                if owner.answered:
                    self._stranded = owner.marker
                    return self._still_stranded(marker)
                line = f"inject: cannot tell whose panel Steam carries ({owner.why}); waiting before {before}"
            elif reading.names == ():
                empty_before = True
                line = f"inject: no app is running; asking once more before {before}"
            elif reading.names is None:
                empty_before = False
                line = f"inject: cannot tell whether an app is running ({reading.why}); waiting before {before}"
            else:
                empty_before = False
                line = f"inject: waiting for {', '.join(reading.names)} to exit before {before}"
            if line != said:
                said = line
                self._logger.info(line)
            await asyncio.sleep(APP_POLL_SECONDS)
        return False

    async def _owner_now(self) -> _OwnerReading:
        """Ask the attached context whose panel it carries."""
        try:
            value = await self._evaluate(marker_owner_expression())
        except (CdpConnectionLost, CdpUnavailableError, TimeoutError) as exc:
            return _OwnerReading(answered=False, why=f"{type(exc).__name__}: {exc}")
        return _OwnerReading(answered=True, marker=read_panel_marker(value))

    async def _running_apps(self) -> _AppsReading:
        """Ask Steam which apps are running."""
        try:
            value = await self._evaluate(RUNNING_APPS_EXPRESSION)
        except (CdpConnectionLost, CdpUnavailableError, TimeoutError) as exc:
            return _AppsReading(None, f"{type(exc).__name__}: {exc}")
        if not isinstance(value, list):
            return _AppsReading(None, "Steam's list of running apps could not be read")
        return _AppsReading(tuple(str(name) for name in value))

    async def _request_reload(self) -> bool:
        """Ask Steam to rebuild its JS context; ``False`` where it said it cannot."""
        try:
            accepted = await self._evaluate(RELOAD_EXPRESSION)
        except (CdpConnectionLost, CdpUnavailableError, TimeoutError) as exc:
            self._logger.info(
                f"inject: the request to reload got no answer ({type(exc).__name__}: {exc}); waiting to see whether "
                f"it happened anyway"
            )
            return True
        if accepted is not True:
            self._logger.warning("inject: Steam offers no SteamClient.Browser.RestartJSContext here; nothing reloaded")
            return False
        return True

    async def _panel_back(self, window: float, after: str) -> bool:
        """Watch for this backend's panel connecting within *window* seconds."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(window):
                await self._panel.wait_connected()
        except TimeoutError:
            return False
        self._logger.info(f"inject: the panel is back, {loop.time() - started:.0f}s after {after}")
        return True
