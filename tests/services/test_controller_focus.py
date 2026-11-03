"""The focus watcher's service: when it watches, what it sends on a focus change, and what it sends at stop.

Every seam is a fake. The X connection is a fake whose socket is a real pipe,
so the service's own reader registration on the loop is what wakes it; the
sleeper is gated, so each reading of Steam happens exactly when a test lets it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import TYPE_CHECKING

import pytest

from domain.controller_focus import FocusedWindow, RunningSteam, SteamIdentity, XDisplay
from services.controller_focus import (
    POLL_SECONDS,
    ControllerFocusSeams,
    ControllerFocusService,
    ControllerFocusServiceConfig,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

GAME_APP_ID = 2510548680
OTHER_GAME_APP_ID = 1663850
GAME_PID = 7001
OTHER_GAME_PID = 7002
STEAM_64 = "/home/deck/.local/share/Steam/steamrt64/steam"
STEAM_32 = "/home/deck/.local/share/Steam/ubuntu12_32/steam"
DISPLAY = XDisplay(name=":0", authority_path="/run/user/1000/xauth_abc")

BIG_PICTURE = FocusedWindow(host_pid=6000, steam_game=769, steam_input_focus=True)
DESKTOP_CLIENT = FocusedWindow(host_pid=6000, steam_game=769, steam_input_focus=False)
GAME_WINDOW = FocusedWindow(host_pid=GAME_PID, steam_game=None, steam_input_focus=False)
OTHER_GAME_WINDOW = FocusedWindow(host_pid=OTHER_GAME_PID, steam_game=None, steam_input_focus=False)
EDITOR = FocusedWindow(host_pid=6500, steam_game=None, steam_input_focus=False)


def _url(input_id: int) -> str:
    return f"steam://forceinputappid/{input_id}"


def _steam(
    *, exe: str = STEAM_64, under_gamescope: bool = False, start_time: int = 1, display: XDisplay | None = DISPLAY
) -> RunningSteam:
    return RunningSteam(
        identity=SteamIdentity(pid=4242, start_time=start_time),
        exe=exe,
        under_gamescope=under_gamescope,
        display=display,
    )


class GatedSleeper:
    """A ``Sleeper`` whose every sleep waits until the test lets the next one through."""

    def __init__(self) -> None:
        self.durations: list[float] = []
        self._gate = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.durations.append(seconds)
        gate = self._gate
        await gate.wait()

    def tick(self) -> None:
        gate, self._gate = self._gate, asyncio.Event()
        gate.set()


class FakeRunningSteam:
    def __init__(self, steam: RunningSteam | None) -> None:
        self.steam = steam
        self.reads = 0
        self.raises_once: Exception | None = None

    def read(self) -> RunningSteam | None:
        self.reads += 1
        if self.raises_once is not None:
            error, self.raises_once = self.raises_once, None
            raise error
        return self.steam


class FakeX:
    """A ``FocusedWindowReader`` whose socket is a pipe the test writes a byte into for each focus change."""

    def __init__(self) -> None:
        self.window: FocusedWindow | None = None
        self.refusal: str | None = None
        self.connected_to: list[XDisplay] = []
        self.closes = 0
        self.is_lost = False
        self.moves_during_next_question: FocusedWindow | None = None
        self._queued_change = False
        self._read_end: int | None = None
        self._write_end: int | None = None

    @property
    def is_open(self) -> bool:
        return self._read_end is not None

    def connect(self, display: XDisplay) -> str | None:
        self.close()
        self.closes -= 1
        self.connected_to.append(display)
        if self.refusal is not None:
            return self.refusal
        self._read_end, self._write_end = os.pipe()
        os.set_blocking(self._read_end, False)
        return None

    def fileno(self) -> int:
        return self._read_end if self._read_end is not None else -1

    def take_focus_changes(self) -> bool:
        if self._read_end is None:
            return False
        queued, self._queued_change = self._queued_change, False
        try:
            return bool(os.read(self._read_end, 4096)) or queued
        except BlockingIOError:
            return queued

    def focused_window(self) -> FocusedWindow | None:
        """The window focused when asked; a move set up for this question happens while it is answered.

        Such a move is what xcb does with an event that arrives with a reply:
        it is read off the socket into xcb's queue, so the socket never becomes
        readable for it, and only the next reading of the events finds it.
        """
        answer = self.window
        if self.moves_during_next_question is not None:
            self.window, self.moves_during_next_question = self.moves_during_next_question, None
            self._queued_change = True
        return answer

    def lost(self) -> bool:
        return self.is_open and self.is_lost

    def close(self) -> None:
        self.closes += 1
        for end in (self._read_end, self._write_end):
            if end is not None:
                os.close(end)
        self._read_end = self._write_end = None
        self.is_lost = False

    def focus(self, window: FocusedWindow | None) -> None:
        """The focus moves to *window*, and the server says so."""
        self.window = window
        if self._write_end is not None:
            os.write(self._write_end, b"x")

    def die(self) -> None:
        """The server goes away: the connection is lost and its socket is readable for good."""
        self.is_lost = True
        if self._write_end is not None:
            os.write(self._write_end, b"x")


class FakeSteamLaunch:
    def __init__(self) -> None:
        self.app_ids = {GAME_PID: GAME_APP_ID, OTHER_GAME_PID: OTHER_GAME_APP_ID}
        self.raises_once: Exception | None = None

    def launched_app_id(self, pid: int) -> int | None:
        if self.raises_once is not None:
            error, self.raises_once = self.raises_once, None
            raise error
        return self.app_ids.get(pid)


class FakeSteamUrls:
    """Records every URL handed to Steam; *answer* is what each hand-over answers."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.answer = True
        self.hold: asyncio.Event | None = None

    async def execute_steam_url(self, url: str) -> bool:
        self.urls.append(url)
        if self.hold is not None:
            await self.hold.wait()
        return self.answer


async def _until(condition: Callable[[], bool], what: str) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.005)
    assert condition(), what


async def _quiet() -> None:
    """Long enough for anything the service would still do on its own to have happened."""
    await asyncio.sleep(0.1)


class Watcher:
    def __init__(self, *, watch: bool, steam: RunningSteam | None, logger: logging.Logger) -> None:
        self.sleeper = GatedSleeper()
        self.steam = FakeRunningSteam(steam)
        self.x = FakeX()
        self.launch = FakeSteamLaunch()
        self.urls = FakeSteamUrls()
        self.debug: list[str] = []
        self.service = ControllerFocusService(
            config=ControllerFocusServiceConfig(
                seams=ControllerFocusSeams(
                    running_steam=self.steam,
                    focused_window=self.x,
                    steam_launch=self.launch,
                    steam_urls=self.urls,
                )
                if watch
                else None,
                loop=asyncio.get_running_loop(),
                sleeper=self.sleeper,
                logger=logger,
                log_debug=self._debug,
            )
        )
        self.task: asyncio.Task[None] | None = None

    def _debug(self, msg: str) -> None:
        self.debug.append(msg)

    async def start(self) -> None:
        self.task = asyncio.ensure_future(self.service.run())
        await self.rounds(1)

    async def rounds(self, count: int) -> None:
        """Wait until Steam has been read *count* times in all, and then a little."""
        await _until(lambda: len(self.sleeper.durations) >= count, f"{count} readings of Steam")
        await _quiet()

    async def tick(self) -> None:
        """Let the next reading of Steam happen, and wait until it has."""
        done = len(self.sleeper.durations)
        self.sleeper.tick()
        await self.rounds(done + 1)

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task


@pytest.fixture
def make_watcher(logger: logging.Logger) -> Callable[..., AsyncIterator[Watcher]]:
    @contextlib.asynccontextmanager
    async def make(*, watch: bool = True, steam: RunningSteam | None = None) -> AsyncIterator[Watcher]:
        watcher = Watcher(watch=watch, steam=steam if steam is not None else _steam(), logger=logger)
        try:
            yield watcher
        finally:
            await watcher.stop()
            watcher.x.close()

    return make  # pyright: ignore[reportReturnType]


class TestSwitchedOff:
    async def test_it_reads_nothing_opens_nothing_and_sends_nothing(self, make_watcher) -> None:
        async with make_watcher(watch=False) as watcher:
            await asyncio.wait_for(watcher.service.run(), 5)
            await watcher.service.release_pin()

            assert watcher.steam.reads == 0
            assert watcher.x.connected_to == []
            assert watcher.urls.urls == []


class TestWhereSteamAttributesWindowsItself:
    @pytest.mark.parametrize(
        "steam",
        [
            pytest.param(_steam(exe=STEAM_32), id="32-bit-client"),
            pytest.param(_steam(under_gamescope=True), id="gaming-mode"),
            pytest.param(_steam(exe=STEAM_32, under_gamescope=True), id="32-bit-gaming-mode"),
        ],
    )
    async def test_it_opens_nothing_and_sends_nothing(self, make_watcher, steam: RunningSteam) -> None:
        async with make_watcher(steam=steam) as watcher:
            await watcher.start()
            await watcher.tick()
            await watcher.tick()
            await watcher.service.release_pin()

            assert watcher.x.connected_to == []
            assert watcher.urls.urls == []

    async def test_no_steam_opens_nothing_and_sends_nothing(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            watcher.steam.steam = None
            await watcher.start()
            await watcher.tick()

            assert watcher.x.connected_to == []
            assert watcher.urls.urls == []

    async def test_steam_is_read_every_few_seconds(self, make_watcher) -> None:
        async with make_watcher(steam=_steam(exe=STEAM_32)) as watcher:
            await watcher.start()
            await watcher.tick()

            assert watcher.sleeper.durations == [POLL_SECONDS, POLL_SECONDS]


class TestWatching:
    async def test_it_sends_the_focused_window_s_id_at_once(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            watcher.x.window = BIG_PICTURE
            await watcher.start()

            assert watcher.x.connected_to == [DISPLAY]
            assert watcher.urls.urls == [_url(769)]

    async def test_with_nothing_focused_it_sends_the_desktop_s_id(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()

            assert watcher.urls.urls == [_url(413080)]

    async def test_each_focus_change_sends_the_window_s_id(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()

            for window in (GAME_WINDOW, BIG_PICTURE, DESKTOP_CLIENT, OTHER_GAME_WINDOW, EDITOR, None):
                sent = len(watcher.urls.urls)
                watcher.x.focus(window)
                await _until(lambda sent=sent: len(watcher.urls.urls) > sent, "a send for the focus change")
                await _quiet()

            assert watcher.urls.urls == [
                _url(413080),
                _url(GAME_APP_ID),
                _url(769),
                _url(413080),
                _url(OTHER_GAME_APP_ID),
                _url(413080),
                _url(413080),
            ]

    async def test_an_unchanged_id_is_sent_again(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            watcher.x.window = GAME_WINDOW
            await watcher.start()

            watcher.x.focus(GAME_WINDOW)
            await _until(lambda: len(watcher.urls.urls) == 2, "the second send")
            await _quiet()

            assert watcher.urls.urls == [_url(GAME_APP_ID), _url(GAME_APP_ID)]

    async def test_nothing_is_sent_without_a_focus_change(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            await watcher.tick()
            await watcher.tick()

            assert watcher.urls.urls == [_url(413080)]

    async def test_a_change_taken_off_the_socket_with_a_reply_is_still_answered(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.x.moves_during_next_question = BIG_PICTURE

            watcher.x.focus(GAME_WINDOW)
            await _until(lambda: len(watcher.urls.urls) >= 3, "the send for the window focused during the question")
            await _quiet()

            assert watcher.urls.urls == [_url(413080), _url(GAME_APP_ID), _url(769)]

    async def test_changes_arriving_during_a_send_are_folded_into_the_latest(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.urls.hold = asyncio.Event()
            watcher.x.focus(GAME_WINDOW)
            await _until(lambda: len(watcher.urls.urls) == 2, "the send in flight")

            watcher.x.focus(BIG_PICTURE)
            watcher.x.focus(OTHER_GAME_WINDOW)
            await _quiet()
            watcher.urls.hold.set()
            await _until(lambda: len(watcher.urls.urls) == 3, "the send after it")
            await _quiet()

            assert watcher.urls.urls == [_url(413080), _url(GAME_APP_ID), _url(OTHER_GAME_APP_ID)]


class TestSteamRestarts:
    async def test_into_the_32_bit_client_closes_the_connection_and_sends_nothing(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.steam.steam = _steam(exe=STEAM_32, start_time=2)
            await watcher.tick()
            watcher.x.focus(GAME_WINDOW)
            await watcher.tick()

            assert watcher.x.is_open is False
            assert watcher.urls.urls == [_url(413080)]

    async def test_into_gaming_mode_closes_the_connection_and_sends_nothing(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.steam.steam = _steam(under_gamescope=True, start_time=2)
            await watcher.tick()

            assert watcher.x.is_open is False
            assert watcher.urls.urls == [_url(413080)]

    async def test_a_new_64_bit_run_connects_again_and_sends_at_once(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.x.window = BIG_PICTURE
            watcher.steam.steam = _steam(start_time=2)
            await watcher.tick()

            assert watcher.x.connected_to == [DISPLAY, DISPLAY]
            assert watcher.urls.urls == [_url(413080), _url(769)]

    async def test_steam_gone_closes_the_connection(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.steam.steam = None
            await watcher.tick()

            assert watcher.x.is_open is False


class TestWhenTheDisplayCannotBeReached:
    @pytest.mark.parametrize(
        "refusal",
        [
            "the X libraries could not be loaded (libxcb-res.so.0: cannot open shared object file)",
            "the X display :0 could not be reached",
        ],
    )
    async def test_it_says_so_once_and_tries_again_each_reading(
        self, make_watcher, caplog: pytest.LogCaptureFixture, refusal: str
    ) -> None:
        caplog.set_level(logging.WARNING)
        async with make_watcher() as watcher:
            watcher.x.refusal = refusal
            await watcher.start()
            await watcher.tick()
            await watcher.tick()

            assert len(watcher.x.connected_to) == 3
            assert [r.getMessage() for r in caplog.records] == [
                f"controller focus: not watching, {refusal}; trying again"
            ]
            assert watcher.urls.urls == []

            watcher.x.refusal = None
            await watcher.tick()

            assert watcher.urls.urls == [_url(413080)]

    async def test_it_says_so_again_for_another_steam_run(self, make_watcher, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        async with make_watcher() as watcher:
            watcher.x.refusal = "the X display :0 could not be reached"
            await watcher.start()
            watcher.steam.steam = _steam(start_time=2)
            await watcher.tick()

            assert len(caplog.records) == 2

    async def test_no_display_named_by_steam_s_session_is_said_once(
        self, make_watcher, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        async with make_watcher(steam=_steam(display=None)) as watcher:
            await watcher.start()
            await watcher.tick()

            assert watcher.x.connected_to == []
            assert [r.getMessage() for r in caplog.records] == [
                "controller focus: not watching, Steam's desktop session names no X display; trying again"
            ]


class TestWhenASendCannotGoOut:
    async def test_it_is_tried_again_at_the_next_reading_with_the_window_focused_then(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            watcher.urls.answer = False
            await watcher.start()

            assert watcher.urls.urls == [_url(413080)]

            watcher.x.window = BIG_PICTURE
            watcher.urls.answer = True
            await watcher.tick()

            assert watcher.urls.urls == [_url(413080), _url(769)]

    async def test_it_is_not_tried_again_before_the_next_reading(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            watcher.urls.answer = False
            await watcher.start()
            await _quiet()

            assert watcher.urls.urls == [_url(413080)]

    async def test_it_is_said_once(self, make_watcher, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        async with make_watcher() as watcher:
            watcher.urls.answer = False
            await watcher.start()
            await watcher.tick()
            await watcher.tick()

            assert len([r for r in caplog.records if "could not be handed the input id" in r.getMessage()]) == 1


class TestALostConnection:
    async def test_it_is_closed_and_opened_again_at_the_next_reading(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.x.window = GAME_WINDOW
            watcher.x.die()
            await _until(lambda: not watcher.x.is_open, "the lost connection closed")

            await watcher.tick()

            assert watcher.x.connected_to == [DISPLAY, DISPLAY]
            assert watcher.urls.urls == [_url(413080), _url(GAME_APP_ID)]

    async def test_one_found_lost_at_a_reading_of_steam_is_opened_again(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.x.is_lost = True

            await watcher.tick()

            assert watcher.x.connected_to == [DISPLAY, DISPLAY]
            assert watcher.urls.urls == [_url(413080), _url(413080)]


class TestReleasingThePin:
    async def test_zero_is_sent_once_after_a_pin(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            watcher.x.window = GAME_WINDOW
            await watcher.start()

            await watcher.service.release_pin()
            await watcher.service.release_pin()

            assert watcher.urls.urls == [_url(GAME_APP_ID), _url(0)]

    async def test_nothing_is_sent_where_nothing_was_pinned(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            watcher.urls.answer = False
            await watcher.start()
            watcher.urls.urls.clear()

            await watcher.service.release_pin()

            assert watcher.urls.urls == []

    async def test_nothing_is_sent_when_steam_is_gone(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.steam.steam = None

            await watcher.service.release_pin()

            assert watcher.urls.urls == [_url(413080)]

    async def test_nothing_is_sent_to_a_steam_started_since(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.steam.steam = _steam(start_time=2)

            await watcher.service.release_pin()

            assert watcher.urls.urls == [_url(413080)]

    async def test_no_focus_change_pins_again_after_it(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            await watcher.service.release_pin()

            watcher.x.focus(GAME_WINDOW)
            await _quiet()
            await watcher.tick()

            assert watcher.urls.urls == [_url(413080), _url(0)]

    async def test_it_waits_for_a_send_in_flight(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.urls.hold = asyncio.Event()
            watcher.x.focus(GAME_WINDOW)
            await _until(lambda: len(watcher.urls.urls) == 2, "the send in flight")

            release = asyncio.ensure_future(watcher.service.release_pin())
            await _quiet()
            assert watcher.urls.urls == [_url(413080), _url(GAME_APP_ID)]
            watcher.urls.hold.set()
            await asyncio.wait_for(release, 5)

            assert watcher.urls.urls == [_url(413080), _url(GAME_APP_ID), _url(0)]


class TestAPassThatRaises:
    async def test_a_reading_of_steam_that_raises_is_logged_and_the_next_one_goes_on(
        self, make_watcher, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        async with make_watcher() as watcher:
            watcher.steam.raises_once = OSError("proc went away")
            await watcher.start()

            assert watcher.urls.urls == []
            assert [r.getMessage() for r in caplog.records] == [
                "controller focus: reading Steam failed: OSError('proc went away')"
            ]

            await watcher.tick()

            assert watcher.urls.urls == [_url(413080)]

    async def test_an_answer_that_raises_is_logged_and_the_next_change_is_answered(
        self, make_watcher, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        async with make_watcher() as watcher:
            await watcher.start()
            watcher.launch.raises_once = OSError("proc went away")

            watcher.x.focus(GAME_WINDOW)
            await _until(lambda: len(caplog.records) == 1, "the failed answer logged")
            watcher.x.focus(OTHER_GAME_WINDOW)
            await _until(lambda: len(watcher.urls.urls) == 2, "the next change answered")
            await _quiet()

            assert [r.getMessage() for r in caplog.records] == [
                "controller focus: answering a focus change failed: OSError('proc went away')"
            ]
            assert watcher.urls.urls == [_url(413080), _url(OTHER_GAME_APP_ID)]


class TestCancelled:
    async def test_the_connection_is_closed(self, make_watcher) -> None:
        async with make_watcher() as watcher:
            await watcher.start()
            await watcher.stop()

            assert watcher.x.is_open is False
