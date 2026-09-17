"""The loop: attach, load the panel once, load it again when the context is rebuilt."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import Any

import pytest

import host.inject.injector as injector_module
from host.inject.bootstrap import MARKER, STOP_BINDING, STOP_PAYLOAD, marker_present_expression
from host.inject.bundles import COEXISTENCE_PANEL, GLOBALS_BUNDLE, STANDALONE_PANEL, choose_bundles
from host.inject.injector import InjectionSetup, PanelInjector
from host.inject.watchdog import INJECT_FORCE, INJECT_OFF, WATCHDOG_FILENAME, CrashWatchdog, Fingerprint
from tests.host.conftest import free_port
from tests.host.inject.fake_debugger import FakeDebugger, FakePage, FakeTarget, refuse

LOGGER = logging.getLogger("test_injector")
TOKEN = "an-admission-token"
RENDERER = "renderer-target"


@pytest.fixture(autouse=True)
def quick_timings(monkeypatch):
    """The waits this loop is built around, shrunk to test scale."""
    monkeypatch.setattr(injector_module, "RECONNECT_SECONDS", 0.05)
    monkeypatch.setattr(injector_module, "DISCOVERY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(injector_module, "DISCOVERY_WINDOW_SECONDS", 1.0)
    monkeypatch.setattr(injector_module, "READY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(injector_module, "READY_WINDOW_SECONDS", 1.0)
    monkeypatch.setattr(injector_module, "ALIVE_AFTER_SECONDS", 0.1)


def a_page(*, decky_is_serving: bool = False) -> FakePage:
    return FakePage(
        marker_expression=marker_present_expression(MARKER),
        ready_expression=choose_bundles(decky_is_serving=decky_is_serving).ready_when,
    )


async def wait_until(predicate, *, timeout: float = 5.0) -> None:
    """Wait for *predicate*, or fail the test saying it never came true."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


class Running:
    """A started injector and everything a test asserts against."""

    def __init__(
        self,
        debugger: FakeDebugger,
        page: FakePage,
        setup: InjectionSetup,
        task: asyncio.Task[None],
    ) -> None:
        self.debugger = debugger
        self.page = page
        self.setup = setup
        self.task = task

    def watchdog_record(self) -> dict[str, object]:
        """The record as it stands, or an empty one before it is first written."""
        try:
            with open(f"{self.setup.state_dir}/{WATCHDOG_FILENAME}", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}


@pytest.fixture
async def injecting(tmp_path):
    """Start a real injector against a fake debugger; stop it after the test."""
    started: list[Running] = []

    async def start(
        *,
        page: FakePage | None = None,
        targets: list[FakeTarget] | None = None,
        decky_port: int | None = None,
        override: str = "",
        handlers: dict[str, Any] | None = None,
        hold: str = "",
    ) -> Running:
        used_page = page if page is not None else a_page()
        debugger = FakeDebugger(used_page)
        debugger.handlers.update(handlers or {})
        debugger.hold_method = hold
        await debugger.start()
        debugger.targets = targets if targets is not None else [FakeTarget(id=RENDERER, title="SharedJSContext")]
        debugger.handlers["Page.enable"] = lambda _params: {}

        static_root = tmp_path / "dist"
        static_root.mkdir(exist_ok=True)
        for name in (GLOBALS_BUNDLE, STANDALONE_PANEL, COEXISTENCE_PANEL):
            (static_root / name).write_text(f"// {name}\n", encoding="utf-8")
        state_dir = tmp_path / "state"
        state_dir.mkdir(exist_ok=True)

        setup = InjectionSetup(
            static_root=str(static_root),
            state_dir=str(state_dir),
            user_home=str(tmp_path / "home"),
            version="1.2.3",
            override=override,
            debugger_port=debugger.port,
            decky_port=decky_port if decky_port is not None else free_port(),
        )
        injector = PanelInjector(
            setup=setup,
            asset_url=lambda name: f"http://127.0.0.1:27737/{name}?token={TOKEN}",
            token=TOKEN,
            logger=LOGGER,
        )
        running = Running(debugger, used_page, setup, asyncio.ensure_future(injector.run()))
        started.append(running)
        return running

    try:
        yield start
    finally:
        for running in started:
            running.debugger.held.set()
            running.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running.task
            await running.debugger.stop()


class TestLoadingThePanel:
    async def test_it_loads_the_panel_into_the_renderer(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.marker)
        assert len(running.page.bootstraps) == 1

    async def test_it_loads_the_two_standalone_bundles_in_order(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)
        carried = running.page.bootstraps[0]
        assert carried.index(GLOBALS_BUNDLE) < carried.index(STANDALONE_PANEL)
        assert COEXISTENCE_PANEL not in carried

    async def test_the_addresses_it_loads_from_carry_the_admission_token(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)
        assert f"{STANDALONE_PANEL}?token={TOKEN}" in running.page.bootstraps[0]

    async def test_it_waits_for_steams_module_registry_before_loading(self, injecting):
        page = a_page()
        page.ready = False
        await injecting(page=page)

        await wait_until(lambda: page.evaluated.count(page.ready_expression) >= 2)
        assert page.bootstraps == []

        page.ready = True
        await wait_until(lambda: page.bootstraps)

    async def test_a_page_that_never_becomes_ready_is_given_up_and_attached_to_again(
        self, injecting, monkeypatch, caplog
    ):
        """Waiting on one attachment for ever would never see Steam change."""
        monkeypatch.setattr(injector_module, "READY_WINDOW_SECONDS", 0.2)
        page = a_page()
        page.ready = False
        with caplog.at_level(logging.WARNING, logger="test_injector"):
            running = await injecting(page=page)
            await wait_until(lambda: sum(1 for method, _ in running.debugger.calls if method == "Page.enable") >= 2)
        assert page.bootstraps == []
        line = next(record.message for record in caplog.records if "was not ready" in record.message)
        # The condition, not a summary of it: on a machine where something that
        # is not Decky Loader answers on its port, this is the only line that
        # says why no panel ever appears.
        assert "DFL" not in line
        assert "webpackChunksteamui" in line

    async def test_a_context_that_already_carries_the_panel_is_left_alone(self, injecting):
        page = a_page()
        page.marker = True
        running = await injecting(page=page)

        await wait_until(lambda: page.marker_expression in page.evaluated)
        await asyncio.sleep(0.15)
        assert page.bootstraps == []
        assert running.debugger.connections == 1

    async def test_it_waits_for_steam_to_name_the_renderer(self, injecting):
        running = await injecting(targets=[FakeTarget(id=RENDERER, title="")])
        await asyncio.sleep(0.1)
        assert running.page.bootstraps == []

        running.debugger.targets = [FakeTarget(id=RENDERER, title="SharedJSContext")]
        await wait_until(lambda: running.page.bootstraps)

    async def test_a_debugger_that_never_names_a_renderer_is_reported_by_count(self, injecting, caplog):
        with caplog.at_level(logging.INFO, logger="test_injector"):
            await injecting(targets=[FakeTarget(id="a", title=""), FakeTarget(id="b", title="")])
            await wait_until(lambda: any("has not named its renderer" in r.message for r in caplog.records))
        line = next(r.message for r in caplog.records if "has not named its renderer" in r.message)
        assert "2 target(s), 2 still unnamed" in line


def carried_facts(bootstrap: str) -> dict[str, Any]:
    """The one JSON object the evaluated source was built around."""
    found = re.search(r"const T = (\{.*?\});", bootstrap, re.DOTALL)
    assert found is not None
    return json.loads(found.group(1))


class TestTheCardsOneButton:
    async def test_its_callback_is_installed_before_the_card_can_exist(self, injecting):
        """The card is drawn by the evaluate, so the button is wired before it runs."""
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError", "shown": True}
        running = await injecting(page=page)
        await wait_until(lambda: page.bootstraps)

        methods = [method for method, _ in running.debugger.calls]
        drew_the_card = next(
            index
            for index, (method, params) in enumerate(running.debugger.calls)
            if method == "Runtime.evaluate" and params.get("expression") == page.bootstraps[0]
        )
        assert methods.index("Runtime.addBinding") < drew_the_card
        assert carried_facts(page.bootstraps[0])["binding"] == STOP_BINDING

    async def test_the_press_is_listened_for_only_once_a_card_is_up(self, injecting):
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError", "shown": True}
        running = await injecting(page=page)
        await wait_until(lambda: any(method == "Runtime.enable" for method, _ in running.debugger.calls))

    async def test_a_load_that_worked_turns_no_runtime_events_on(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)
        await asyncio.sleep(0.1)

        assert "Runtime.enable" not in [method for method, _ in running.debugger.calls]

    async def test_a_press_stops_loading_anything_more_into_steam(self, injecting, caplog):
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError", "shown": True}
        running = await injecting(page=page)
        await wait_until(lambda: page.bootstraps)

        with caplog.at_level(logging.WARNING, logger="test_injector"):
            await running.debugger.emit(
                "Runtime.bindingCalled", {"name": STOP_BINDING, "payload": STOP_PAYLOAD, "executionContextId": 1}
            )
            await wait_until(lambda: running.task.done())

        assert any("until this backend is restarted" in record.message for record in caplog.records)
        assert len(page.bootstraps) == 1

    async def test_a_rebuild_after_a_press_loads_nothing(self, injecting):
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError", "shown": True}
        running = await injecting(page=page)
        await wait_until(lambda: page.bootstraps)
        await running.debugger.emit(
            "Runtime.bindingCalled", {"name": STOP_BINDING, "payload": STOP_PAYLOAD, "executionContextId": 1}
        )
        await wait_until(lambda: running.task.done())

        page.marker = False
        await running.debugger.emit("Page.domContentEventFired", {"timestamp": 1})
        await asyncio.sleep(0.15)
        assert len(page.bootstraps) == 1

    async def test_another_binding_call_is_not_that_press(self, injecting):
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError", "shown": True}
        running = await injecting(page=page)
        await wait_until(lambda: page.bootstraps)

        await running.debugger.emit("Runtime.bindingCalled", {"name": "somebody-elses", "payload": STOP_PAYLOAD})
        await running.debugger.emit("Runtime.bindingCalled", {"name": STOP_BINDING, "payload": "something-else"})
        await asyncio.sleep(0.15)
        assert not running.task.done()

    async def test_a_button_that_cannot_reach_the_backend_is_said_to_be_one(self, injecting, caplog):
        """The binding went on, the card drew its button, and the press reaches nothing.

        The card cannot be taken back — the evaluate that drew it has returned —
        so the log is the only thing that can say the button on screen is inert,
        and it names the way out, which is not inside Steam.
        """
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError", "shown": True}
        with caplog.at_level(logging.WARNING, logger="test_injector"):
            await injecting(page=page, handlers={"Runtime.enable": lambda _params: refuse("not today")})
            await wait_until(lambda: any("cannot reach this backend" in r.message for r in caplog.records))
        line = next(r.message for r in caplog.records if "cannot reach this backend" in r.message)
        assert "TENDER_INJECT=off" in line

    async def test_a_refused_callback_leaves_the_card_without_a_button(self, injecting, caplog):
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError", "shown": True}
        with caplog.at_level(logging.INFO, logger="test_injector"):
            await injecting(page=page, handlers={"Runtime.addBinding": lambda _params: refuse("no such command")})
            await wait_until(lambda: page.bootstraps)

        assert carried_facts(page.bootstraps[0])["binding"] == ""
        assert any("will draw no button" in record.message for record in caplog.records)


class TestWhenSomethingUnexpectedGoesWrong:
    async def test_the_loop_says_so_and_carries_on(self, injecting, monkeypatch, caplog):
        """This task is awaited only at shutdown, so an escaping exception is silent."""
        attempts = 0
        real = injector_module.list_targets

        async def sometimes_broken(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("something nobody thought of")
            return await real(*args, **kwargs)

        monkeypatch.setattr(injector_module, "list_targets", sometimes_broken)
        with caplog.at_level(logging.ERROR, logger="test_injector"):
            running = await injecting()
            await wait_until(lambda: running.page.bootstraps)
        assert any("unexpected failure" in record.message for record in caplog.records)


class TestBesideDeckyLoader:
    async def test_it_loads_the_coexistence_panel_and_never_the_globals(self, injecting):
        loader = await asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=0)
        port = loader.sockets[0].getsockname()[1]
        try:
            running = await injecting(page=a_page(decky_is_serving=True), decky_port=port)
            await wait_until(lambda: running.page.bootstraps)
            carried = running.page.bootstraps[0]
            assert COEXISTENCE_PANEL in carried
            assert GLOBALS_BUNDLE not in carried
        finally:
            loader.close()
            await loader.wait_closed()


class TestWhenSteamRebuildsItsContext:
    async def test_it_loads_the_panel_again(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)

        running.page.marker = False
        await running.debugger.emit("Page.domContentEventFired", {"timestamp": 1})
        await wait_until(lambda: len(running.page.bootstraps) == 2)

    async def test_a_rebuild_that_left_the_marker_standing_loads_nothing(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)

        await running.debugger.emit("Page.domContentEventFired", {"timestamp": 1})
        await asyncio.sleep(0.15)
        assert len(running.page.bootstraps) == 1

    async def test_a_lost_connection_is_attached_to_again(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)

        running.page.marker = False
        await running.debugger.drop_connections()
        await wait_until(lambda: len(running.page.bootstraps) == 2)

    async def test_a_debugger_that_goes_away_entirely_is_waited_for(self, injecting):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)

        await running.debugger.stop()
        await asyncio.sleep(0.15)
        assert not running.task.done()


class TestDidTheInterfaceSurviveIt:
    async def test_an_interface_that_is_still_there_closes_the_record(self, injecting):
        running = await injecting(
            targets=[FakeTarget(id=RENDERER, title="SharedJSContext"), FakeTarget(id="bpm", title="Big Picture")]
        )
        await wait_until(lambda: running.page.bootstraps)
        await wait_until(lambda: running.watchdog_record().get("open") is False)
        assert running.watchdog_record()["failures"] == 0

    async def test_an_interface_that_vanished_leaves_the_record_open(self, injecting, caplog):
        running = await injecting(
            targets=[FakeTarget(id=RENDERER, title="SharedJSContext"), FakeTarget(id="bpm", title="Big Picture")]
        )
        with caplog.at_level(logging.ERROR, logger="test_injector"):
            await wait_until(lambda: running.watchdog_record().get("open") is True)
            running.debugger.targets = [FakeTarget(id=RENDERER, title="SharedJSContext")]
            await wait_until(lambda: any("Steam's interface is gone" in r.message for r in caplog.records))
        assert running.watchdog_record().get("open") is True

    async def test_an_injection_with_nothing_else_open_is_not_judged(self, injecting, caplog):
        with caplog.at_level(logging.INFO, logger="test_injector"):
            running = await injecting()
            await wait_until(lambda: any("is not judged" in r.message for r in caplog.records))
        assert running.watchdog_record().get("open") is False
        assert running.watchdog_record()["failures"] == 0

    async def test_a_debugger_that_stopped_answering_is_not_judged(self, injecting, caplog):
        running = await injecting(
            targets=[FakeTarget(id=RENDERER, title="SharedJSContext"), FakeTarget(id="bpm", title="Big Picture")]
        )
        with caplog.at_level(logging.INFO, logger="test_injector"):
            await wait_until(lambda: running.watchdog_record().get("open") is True)
            await running.debugger.stop()
            await wait_until(lambda: any("stopped answering" in r.message for r in caplog.records))
        assert running.watchdog_record().get("open") is False

    async def test_a_rebuild_inside_the_check_window_is_not_a_crash(self, injecting, monkeypatch):
        """The record this process armed is answered for before the next one reads it.

        A JS-context rebuild inside the alive window starts a second injection
        while the first record is still open and unanswered — ordinary during
        start-up settle, and exactly what ``mise run dev:bpm-reset`` produces.
        Read as a crash, two of them stop the injection on a machine where
        nothing was ever wrong.
        """
        monkeypatch.setattr(injector_module, "ALIVE_AFTER_SECONDS", 5.0)
        running = await injecting(
            targets=[FakeTarget(id=RENDERER, title="SharedJSContext"), FakeTarget(id="bpm", title="Big Picture")]
        )
        await wait_until(lambda: running.page.bootstraps)

        running.page.marker = False
        await running.debugger.emit("Page.domContentEventFired", {"timestamp": 1})
        await wait_until(lambda: len(running.page.bootstraps) == 2)

        assert running.watchdog_record().get("failures") == 0

    async def test_a_connection_lost_before_the_check_exists_is_closed_by_the_next_attempt(self, injecting):
        """The record is armed, the connection goes, and no check was ever made.

        ``CdpConnectionLost`` escapes the install of the card's callback, which
        catches only the two refusals, so the attachment ends with an armed
        record and nothing to answer for it. What closes it is the reattach's own
        answer, before it reads one — and read unanswered it is a crash that
        never happened.
        """
        running = await injecting(
            targets=[FakeTarget(id=RENDERER, title="SharedJSContext"), FakeTarget(id="bpm", title="Big Picture")],
            hold="Runtime.addBinding",
        )
        await wait_until(lambda: running.watchdog_record().get("open") is True)

        await running.debugger.drop_connections()
        running.debugger.hold_method = ""
        running.debugger.held.set()

        await wait_until(lambda: running.page.bootstraps)
        assert running.watchdog_record().get("failures") == 0

    async def test_a_shutdown_before_the_check_even_exists_closes_the_record(self, injecting):
        """The record is armed a moment before the check that answers for it exists."""
        running = await injecting(
            targets=[FakeTarget(id=RENDERER, title="SharedJSContext"), FakeTarget(id="bpm", title="Big Picture")],
            hold="Runtime.addBinding",
        )
        await wait_until(lambda: running.watchdog_record().get("open") is True)
        running.task.cancel()
        running.debugger.held.set()
        with contextlib.suppress(asyncio.CancelledError):
            await running.task

        assert running.watchdog_record().get("open") is False

    async def test_shutting_the_backend_down_mid_check_closes_the_record(self, injecting):
        running = await injecting(
            targets=[FakeTarget(id=RENDERER, title="SharedJSContext"), FakeTarget(id="bpm", title="Big Picture")]
        )
        await wait_until(lambda: running.watchdog_record().get("open") is True)
        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running.task
        assert running.watchdog_record().get("open") is False


class TestWhenTheWatchdogHasHadEnough:
    def refuse_from_now_on(self, running: Running) -> None:
        record = f"{running.setup.state_dir}/{WATCHDOG_FILENAME}"
        for _ in range(2):
            watchdog = CrashWatchdog(record)
            watchdog.judge(_fingerprint_of(running))
            watchdog.arm(_fingerprint_of(running))

    async def test_it_loads_nothing_and_says_what_state_it_is_in(self, injecting, caplog):
        running = await injecting()
        await wait_until(lambda: running.page.bootstraps)
        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running.task

        self.refuse_from_now_on(running)
        page = a_page()
        with caplog.at_level(logging.ERROR, logger="test_injector"):
            await injecting(page=page)
            await wait_until(lambda: any("not loading the panel into Steam" in r.message for r in caplog.records))

    async def test_the_switch_loads_it_anyway(self, injecting, tmp_path, caplog):
        first = await injecting()
        await wait_until(lambda: first.page.bootstraps)
        first.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first.task
        self.refuse_from_now_on(first)

        page = a_page()
        with caplog.at_level(logging.WARNING, logger="test_injector"):
            await injecting(page=page, override=INJECT_FORCE)
            await wait_until(lambda: page.bootstraps)


class TestTheSwitch:
    async def test_off_loads_nothing_at_all(self, injecting, caplog):
        with caplog.at_level(logging.INFO, logger="test_injector"):
            running = await injecting(override=INJECT_OFF)
            await wait_until(lambda: running.task.done())
        assert running.page.evaluated == []
        assert running.debugger.connections == 0
        assert any("TENDER_INJECT=off" in record.message for record in caplog.records)

    def test_only_the_two_known_words_are_a_switch(self):
        read = InjectionSetup.from_environment
        common = {"static_root": "/d", "state_dir": "/s", "user_home": "/h", "version": "1"}
        assert read({"TENDER_INJECT": "off"}, **common).override == INJECT_OFF
        assert read({"TENDER_INJECT": " FORCE "}, **common).override == INJECT_FORCE
        assert read({"TENDER_INJECT": "yes please"}, **common).override == ""
        assert read({}, **common).override == ""


class TestWhatItSaysAboutAFailedLoad:
    async def test_a_bundle_that_does_not_mount_is_reported_with_its_reason(self, injecting, caplog):
        page = a_page()
        page.bootstrap_answer = {"ok": False, "reason": "TypeError: undefined", "shown": True}
        with caplog.at_level(logging.ERROR, logger="test_injector"):
            await injecting(page=page)
            await wait_until(lambda: any("the panel did not load" in r.message for r in caplog.records))
        line = next(r.message for r in caplog.records if "the panel did not load" in r.message)
        assert "TypeError: undefined" in line
        assert "the load-failure card is up" in line

    async def test_a_bootstrap_that_would_not_compile_is_reported_as_such(self, injecting, caplog):
        page = a_page()
        page.bootstrap_raises = True
        with caplog.at_level(logging.ERROR, logger="test_injector"):
            await injecting(page=page)
            await wait_until(lambda: any("could not be evaluated" in r.message for r in caplog.records))


def _fingerprint_of(running: Running) -> Fingerprint:
    from host.inject.bundles import bundle_digest

    return Fingerprint(
        tender=running.setup.version,
        bundle=bundle_digest(running.setup.static_root, (GLOBALS_BUNDLE, STANDALONE_PANEL)),
        steam="",
    )
