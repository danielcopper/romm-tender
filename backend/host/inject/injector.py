"""Loading the panel into Steam, and keeping it there.

Contract: the whole of what this backend does to Steam's renderer — find it,
attach to it, put the panel in it once, put it back when the context is rebuilt,
and stop for good if doing so is taking the interface down. It owns the order and
the timing; the decisions it takes are each answered by a module of its own.

The loop is event-driven once it is attached. Polling is confined to the two
places a question has no event: finding the target while Steam is still naming it,
and waiting for the module registry the panel needs. After that the only thing
that brings us back is ``Page.domContentEventFired``, which a device run measured
as arriving 0.19-0.6 s after a JS-context rebuild — and a rebuild is the one
thing that wipes our marker.

**Nothing here writes to Decky's objects or reads its presence from the window.**
Which bundles to load is ``bundles.choose_bundles``, answered from the machine
(``machine.decky_loader_is_serving``); the one window read that mentions Decky is
the readiness wait, which is a wait for something the machine has already said is
coming.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from host.inject.bootstrap import (
    MARKER,
    STOP_BINDING,
    STOP_PAYLOAD,
    build_bootstrap,
    build_facts,
    marker_present_expression,
)
from host.inject.bundles import bundle_digest, choose_bundles
from host.inject.cdp import (
    DEBUGGER_PORT,
    CdpConnection,
    CdpConnectionLost,
    CdpUnavailableError,
    Target,
    find_shared_context,
    list_targets,
    pages_besides,
    unnamed_targets,
)
from host.inject.machine import DECKY_LOADER_PORT, decky_loader_is_serving, read_steam_build
from host.inject.watchdog import (
    INJECT_ENV,
    INJECT_FORCE,
    INJECT_OFF,
    WATCHDOG_FILENAME,
    CrashWatchdog,
    Fingerprint,
)
from host.logging_setup import LOG_FILENAME

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Mapping

# How long to wait before attaching again after the debugger could not be
# reached. Steam not running is the ordinary reason, and it may not be running
# for hours.
RECONNECT_SECONDS = 5.0

# Measured from the debugger port answering: the target appears at +0.20 s with
# an empty title and is renamed ``SharedJSContext`` at +0.6 s, so a missing name
# is a moment rather than an answer. The window is far wider than that reading
# because waiting costs nothing and giving up costs the panel until the next
# attachment.
DISCOVERY_POLL_SECONDS = 0.25
DISCOVERY_WINDOW_SECONDS = 15.0

# How long the page is given to become something the panel can load into. The
# longer of the two conditions is the one beside Decky Loader — its copy of the
# package — and measured from the debugger port answering, ``DFL`` was still
# absent at +4.65 s and Decky had finished at +10.6 s with ten plugins. This is
# roughly six times that.
READY_POLL_SECONDS = 0.25
READY_WINDOW_SECONDS = 60.0

# The panel's own start-up runs inside the import this awaits, so the budget is
# the panel's rather than the protocol's. Every other command is a question the
# debugger answers out of its own state, so it gets the short one.
EVALUATE_TIMEOUT_SECONDS = 60.0
COMMAND_TIMEOUT_SECONDS = 10.0

# How long after an injection the interface is asked whether it is still there.
#
# Derived from the one measurement of the failure it watches for: the
# ``Runtime.evaluate`` returned successfully and the page targets vanished 3.04 s
# later. Ten seconds is a little over three times that; what makes it cheap to be
# generous rather than tight is that waiting longer costs only a record staying
# open a few seconds more, while waiting too little would record a crash as a
# survival and let the next start walk into it again.
ALIVE_AFTER_SECONDS = 10.0


_EVALUATE = "Runtime.evaluate"

# The two subscriptions one attachment watches, named so a waiter can say which
# of them answered.
_REBUILT = "rebuilt"
_PRESSED = "pressed"


class _Waits:
    """The two queues an attachment watches, each with its getter held open.

    The getters are made once and only the one that answered is renewed, so a
    getter is never cancelled with an item already handed to it — which is what
    a ``Queue.get`` cancelled and remade on every turn of a loop risks.
    """

    def __init__(
        self,
        rebuilt: asyncio.Queue[dict[str, Any] | None],
        pressed: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        self._queues = {_REBUILT: rebuilt, _PRESSED: pressed}
        self._waiting = {name: asyncio.ensure_future(queue.get()) for name, queue in self._queues.items()}

    async def next(self) -> tuple[str, dict[str, Any] | None]:
        """The next event off either queue, with the name of the queue it came from."""
        done, _ = await asyncio.wait(self._waiting.values(), return_when=asyncio.FIRST_COMPLETED)
        for name, waiting in self._waiting.items():
            if waiting in done:
                self._waiting[name] = asyncio.ensure_future(self._queues[name].get())
                return name, waiting.result()
        raise CdpConnectionLost("waited on two queues and neither answered")

    async def cancel(self) -> None:
        """Drop both getters; the attachment is over."""
        for waiting in self._waiting.values():
            waiting.cancel()
        await asyncio.gather(*self._waiting.values(), return_exceptions=True)


@dataclass(frozen=True)
class InjectionSetup:
    """Everything the injector needs that is known before the port is bound."""

    static_root: str
    state_dir: str
    user_home: str
    version: str
    override: str = ""
    debugger_port: int = DEBUGGER_PORT
    decky_port: int = DECKY_LOADER_PORT

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
        *,
        static_root: str,
        state_dir: str,
        user_home: str,
        version: str,
    ) -> InjectionSetup:
        """Build the setup, reading the one switch this feature has.

        The environment is handed in rather than read here, so every rung is
        checkable against a table — the same shape ``domain/app_directories.py``
        resolves the directories with. A value that is neither of the two words
        is no switch at all: the alternative is refusing to start the backend
        over a typo in a unit file.
        """
        asked = environ.get(INJECT_ENV, "").strip().lower()
        return cls(
            static_root=static_root,
            state_dir=state_dir,
            user_home=user_home,
            version=version,
            override=asked if asked in (INJECT_OFF, INJECT_FORCE) else "",
        )


class PanelInjector:
    """Puts the panel into Steam's renderer and keeps it there for this process."""

    def __init__(
        self,
        *,
        setup: InjectionSetup,
        asset_url: Callable[[str], str],
        token: str,
        logger: logging.Logger,
    ) -> None:
        self._setup = setup
        self._asset_url = asset_url
        self._token = token
        self._logger = logger
        self._watchdog = CrashWatchdog(os.path.join(setup.state_dir, WATCHDOG_FILENAME), override=setup.override)
        self._log_path = os.path.join(setup.state_dir, LOG_FILENAME)
        self._alive_check: asyncio.Task[None] | None = None
        self._stopped = False

    async def run(self) -> None:
        """Attach, inject, and stay attached until cancelled or stopped for cause."""
        if self._setup.override == INJECT_OFF:
            self._logger.info(f"inject: not loading the panel into Steam, {INJECT_ENV}={INJECT_OFF} is set")
            return
        try:
            while not self._stopped:
                try:
                    await self._attached_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # The loop outlives one attachment on purpose: this task is
                    # awaited only at shutdown, so an exception escaping here
                    # would stop the panel being loaded for the rest of the
                    # process with nothing said anywhere.
                    self._logger.exception("inject: unexpected failure; will attach again")
                if self._stopped:
                    return
                await asyncio.sleep(RECONNECT_SECONDS)
        finally:
            await self._abandon_alive_check()

    # -- one attachment --------------------------------------------------------

    async def _attached_once(self) -> None:
        """Attach to the renderer and serve it until the connection ends."""
        target = await self._find_target()
        if target is None:
            return

        try:
            connection = await CdpConnection.connect(target.websocket_url, logger=self._logger)
        except CdpUnavailableError as exc:
            self._logger.info(f"inject: cannot attach to {target.title}: {exc}")
            return

        self._logger.info(f"inject: attached to {target.title} ({target.id})")
        # Subscribed before the domains are enabled, so nothing that happens
        # while we are still setting up is missed.
        waits = _Waits(connection.subscribe("Page.domContentEventFired"), connection.subscribe("Runtime.bindingCalled"))
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
                await connection.call("Page.enable")
            while not self._stopped:
                if not await self._already_carries_the_panel(connection) and not await self._inject(connection, target):
                    return
                if not await self._wait_for_something_to_do(waits):
                    return
        except (CdpConnectionLost, CdpUnavailableError, TimeoutError) as exc:
            self._logger.info(f"inject: the renderer connection failed ({type(exc).__name__}: {exc})")
        finally:
            await waits.cancel()
            await connection.close()

    async def _wait_for_something_to_do(self, waits: _Waits) -> bool:
        """Wait until there is a reason to act; ``False`` ends this attachment.

        Two things can arrive, and they are read as two subscriptions rather than
        one poll: Steam rebuilt its JS context and wiped the marker, or the user
        pressed the load-failure card's one button. A connection that ends puts
        ``None`` on both, which is the third answer.
        """
        while True:
            name, event = await waits.next()
            if event is None:
                self._logger.info("inject: the renderer connection ended; will attach again")
                return False
            if name == _REBUILT:
                self._logger.info("inject: Steam rebuilt its JS context; loading the panel again")
                return True
            if _is_the_stop_press(event):
                self._logger.warning(
                    "inject: the load-failure card was pressed; loading nothing more into Steam until this backend "
                    f"is restarted. Start it again, or set {INJECT_ENV}={INJECT_OFF} to stop it asking."
                )
                self._stopped = True
                return False

    async def _find_target(self) -> Target | None:
        """Wait for Steam to name ``SharedJSContext``, or answer ``None``.

        A miss here is never "Steam has nothing open" — see
        ``cdp.find_shared_context`` — so the unnamed targets are reported by
        count rather than as an absence.
        """
        targets: tuple[Target, ...] = ()
        try:
            async with asyncio.timeout(DISCOVERY_WINDOW_SECONDS):
                while True:
                    targets = await list_targets(self._setup.debugger_port)
                    found = find_shared_context(targets)
                    if found is not None:
                        return found
                    await asyncio.sleep(DISCOVERY_POLL_SECONDS)
        except CdpUnavailableError as exc:
            self._logger.debug(f"inject: {exc}")
            return None
        except TimeoutError:
            self._logger.info(
                f"inject: the debugger answers but has not named its renderer within "
                f"{DISCOVERY_WINDOW_SECONDS:.0f}s ({len(targets)} target(s), "
                f"{len(unnamed_targets(targets))} still unnamed)"
            )
            return None

    async def _already_carries_the_panel(self, connection: CdpConnection) -> bool:
        """Is the marker on this context? An injected panel leaves one behind."""
        async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
            answer = await connection.call(
                _EVALUATE,
                {"expression": marker_present_expression(MARKER), "returnByValue": True},
            )
        return bool(_value_of(answer))

    # -- one injection ---------------------------------------------------------

    async def _inject(self, connection: CdpConnection, target: Target) -> bool:
        """Load the panel into the attached context once; answer whether to stay.

        ``False`` gives this attachment up rather than waiting on it: the caller
        then returns, and the run loop attaches again after its own pause. The
        two ways that happens are a page that never became ready, where
        attaching again is the only thing left to try, and a watchdog that has
        stopped the injection, where there is nothing left to try at all.
        """
        choice = choose_bundles(decky_is_serving=await decky_loader_is_serving(self._setup.decky_port))
        if not await self._wait_until_ready(connection, choice.ready_when):
            # The expression rather than a summary of it: on a machine where
            # something that is not Decky Loader answers on its port, this line
            # naming ``DFL`` is the only thing that says why no panel appears.
            self._logger.warning(
                f"inject: Steam's interface was not ready within {READY_WINDOW_SECONDS:.0f}s "
                f"(waiting for {choice.ready_when}); will attach again"
            )
            return False

        # Before the record is read, never after: this process may hold one it
        # armed and never answered for, and ``judge`` reads an open record as a
        # crash that already happened. A JS-context rebuild inside the alive
        # window is the ordinary way that arrives — it is what a Steam restart
        # produces — and a real crash is unaffected, because the check that saw
        # it marks its reading taken and the record stays open for this call.
        #
        # Below the readiness wait rather than above it, for the same reason in
        # the other direction: that wait can end in a return, and answering
        # above it would throw the last injection's pending verdict away for an
        # injection that never happened.
        await self._abandon_alive_check()

        fingerprint = await self._fingerprint(choice.files)
        verdict = self._watchdog.judge(fingerprint)
        if not verdict.may_inject:
            self._logger.error(f"inject: {verdict.line}")
            self._stopped = True
            return False
        if verdict.line:
            self._logger.warning(f"inject: {verdict.line}")

        witness = len(pages_besides(await self._targets_now(), target.id))
        self._watchdog.arm(fingerprint)

        binding = await self._install_the_cards_callback(connection)
        self._logger.info(f"inject: loading {', '.join(choice.files)} — {choice.because}")
        async with asyncio.timeout(EVALUATE_TIMEOUT_SECONDS):
            answer = await connection.call(
                _EVALUATE,
                {
                    "expression": build_bootstrap(
                        build_facts(
                            kind=choice.kind,
                            version=self._setup.version,
                            steam_build=fingerprint.steam,
                            log_path=self._log_path,
                            urls=tuple(self._asset_url(name) for name in choice.files),
                            token=self._token,
                            binding=binding,
                        )
                    ),
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
        if self._report(answer):
            await self._listen_for_a_press(connection)
        self._alive_check = asyncio.create_task(self._check_still_alive(target.id, witness))
        return True

    async def _install_the_cards_callback(self, connection: CdpConnection) -> str:
        """Put the card's one callback on the page; answer its name, or ``""``.

        Installed BEFORE the source that may draw the card is evaluated, so the
        button is wired from the moment it exists — and re-installed on every
        injection rather than once per attachment, because whether a binding
        survives a JS-context rebuild is not established here. Adding one that
        survived costs a round trip; missing one costs the card its only button.
        """
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
                await connection.call("Runtime.addBinding", {"name": STOP_BINDING})
        except (CdpUnavailableError, TimeoutError) as exc:
            self._logger.info(f"inject: the debugger refused the card's callback ({exc}); it will draw no button")
            return ""
        return STOP_BINDING

    async def _listen_for_a_press(self, connection: CdpConnection) -> None:
        """Turn on the domain whose event carries a press — only once a card is up.

        ``Runtime.bindingCalled`` is a Runtime event, so it arrives only while
        that domain is enabled, and enabling it turns on every other Runtime
        event for this connection as well. That cost is paid where it is worth
        paying: after a load that failed, where the card is the only thing left
        to act on, and never on the path where the panel came up.
        """
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
                await connection.call("Runtime.enable")
        except (CdpUnavailableError, TimeoutError) as exc:
            # The one state this design calls worse than no button: it is drawn,
            # because the binding went on, and a press reaches nothing. Taking
            # the button back is POSSIBLE — ``Runtime.evaluate`` needs no enabled
            # domain, which is why every other evaluate here works without one —
            # and it is not worth doing: a second round trip on a connection
            # this very refusal suggests is going, to reach a card that keeps no
            # handle for one to find. So the log says it instead, and names the
            # way out, which is not inside Steam either.
            self._logger.warning(
                f"inject: the load-failure card is up and its button cannot reach this backend ({exc}); pressing it "
                f"will only take the card off the screen. To stop the attempts, restart this backend with "
                f"{INJECT_ENV}={INJECT_OFF}."
            )

    async def _wait_until_ready(self, connection: CdpConnection, expression: str) -> bool:
        """Poll *expression* until the page says it is ready, or the window closes."""
        try:
            async with asyncio.timeout(READY_WINDOW_SECONDS):
                while True:
                    answer = await connection.call(_EVALUATE, {"expression": expression, "returnByValue": True})
                    if bool(_value_of(answer)):
                        return True
                    await asyncio.sleep(READY_POLL_SECONDS)
        except TimeoutError:
            return False

    async def _fingerprint(self, files: tuple[str, ...]) -> Fingerprint:
        """The three readings whose change lets a stopped injector start again."""
        loop = asyncio.get_running_loop()
        digest = await loop.run_in_executor(None, bundle_digest, self._setup.static_root, files)
        steam = await loop.run_in_executor(None, read_steam_build, self._setup.user_home)
        return Fingerprint(tender=self._setup.version, bundle=digest, steam=steam or "")

    def _report(self, answer: dict[str, Any]) -> bool:
        """Say what the evaluated bootstrap answered; is a card now on screen?

        Its own failure path draws the card that explains the state to the user,
        so what is wanted here is the sentence for the log — and the bootstrap
        has already taken the token out of it.
        """
        if "exceptionDetails" in answer:
            self._logger.error(f"inject: the bootstrap could not be evaluated: {answer['exceptionDetails']}")
            return False
        value = _value_of(answer)
        if not isinstance(value, dict):
            self._logger.error(f"inject: the bootstrap answered something unreadable: {value!r}")
            return False
        if value.get("ok"):
            if value.get("already"):
                self._logger.info("inject: this context already carries the panel")
            else:
                self._logger.info("inject: the panel is loaded")
            return False
        shown = (
            "the load-failure card is up"
            if value.get("shown")
            else "and the load-failure card could not be drawn either"
        )
        self._logger.error(f"inject: the panel did not load ({shown}): {value.get('reason')}")
        return bool(value.get("shown"))

    # -- did the interface survive it? ----------------------------------------

    async def _check_still_alive(self, target_id: str, witness: int) -> None:
        """After a pause, decide whether the interface survived this injection.

        Three answers, and two of them close the record. The interface is there:
        clear it. The debugger cannot be asked any more, or there was nothing to
        lose in the first place: close it without counting, because the crash
        this guards against is specific — every other page target goes at once
        while the debugger keeps answering — and a run in which that could not be
        observed says nothing. Otherwise the record stays open, and the next
        attempt reads it as the crash it is.
        """
        await asyncio.sleep(ALIVE_AFTER_SECONDS)
        try:
            targets = await self._targets_now()
        except CdpUnavailableError:
            self._logger.info("inject: the debugger stopped answering; this injection is not judged")
            self._watchdog.inconclusive()
            return
        if witness == 0:
            self._logger.info(
                "inject: nothing but the renderer was open when the panel was loaded; this injection is not judged"
            )
            self._watchdog.inconclusive()
            return
        if pages_besides(targets, target_id):
            self._watchdog.survived()
            return
        self._watchdog.stays_open()
        self._logger.error(
            f"inject: Steam's interface is gone — {witness} page(s) were open when the panel was loaded and none "
            f"are now. The record stays open; two in a row and Tender stops loading the panel."
        )

    async def _targets_now(self) -> tuple[Target, ...]:
        """The debugger's current target list."""
        return await list_targets(self._setup.debugger_port)

    async def _abandon_alive_check(self) -> None:
        """Drop a pending alive check and close a record nobody answered for.

        The record rather than the task is what decides: an injection can be
        interrupted after the record is armed and before the check that answers
        for it exists, and a record left open there would be read as a crash at
        the next start. Nothing was observed, so nothing is claimed — and a
        reading that was taken is left exactly as it left it.
        """
        check, self._alive_check = self._alive_check, None
        if check is not None and not check.done():
            check.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await check
        if self._watchdog.armed:
            self._watchdog.inconclusive()


def _is_the_stop_press(event: dict[str, Any]) -> bool:
    """Is this ``Runtime.bindingCalled`` the card's button and nothing else?

    The connection carries every Runtime event once the domain is on, so the
    name AND the word the button sends are both checked: a page that happened to
    call a function of the same name with something else is not this press.
    """
    params = event.get("params")
    if not isinstance(params, dict):
        return False
    return params.get("name") == STOP_BINDING and params.get("payload") == STOP_PAYLOAD


def _value_of(answer: dict[str, Any]) -> Any:
    """The value a ``Runtime.evaluate`` answered with, or ``None``."""
    result = answer.get("result")
    return result.get("value") if isinstance(result, dict) else None
