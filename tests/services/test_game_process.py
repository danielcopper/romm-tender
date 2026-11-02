"""Unit tests for ``GameProcessService`` — the stop-game escalation ladder.

Two safety rules are guarded here and neither may be weakened.

A still-alive process is NEVER asked to stop a second time (emulators skip their
save flush on the repeat and destroy the save file); the only escalation is the
force kill after the grace window. That rule has two halves — no retry inside
one call, and no second concurrent call — and
:class:`TestNeverRepeatsTheStopRequest` guards both.

And only the tree Steam started for the ROM's shortcut is ever signalled:
several games can run at once, in the same emulator too, so signalling another
tree ends another game mid-save. :class:`TestTargetsOnlyThisGamesTree` guards
that, asserting the OTHER tree's pids reached neither signal.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from fakes.fake_game_process_control import DEFAULT_APP_ID, FakeGameProcessControlAdapter
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeSleeper

from domain.rom import Rom
from lib.errors import Refused
from services.game_process import (
    _GRACE_POLL_SECONDS,
    _GRACE_POLLS,
    GameProcessService,
    GameProcessServiceConfig,
)

APP_ID = DEFAULT_APP_ID
ROM_ID = 42


class _YieldingSleeper:
    """``Sleeper`` that hands control back to the event loop without real delay.

    ``FakeSleeper`` returns without ever suspending, so two coroutines driven
    through it never interleave — a concurrency test built on it would run the
    two calls strictly one after the other and pass vacuously. This one awaits a
    zero-delay ``asyncio.sleep``, so a second call genuinely arrives while the
    first is parked inside its grace window.
    """

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        await asyncio.sleep(0)


def _bound_roms(bindings: dict[int, int | None]) -> FakeUnitOfWork:
    """A Unit of Work holding one ROM per *bindings* entry, bound to the shortcut appId it maps to."""
    uow = FakeUnitOfWork()
    for rom_id, app_id in bindings.items():
        uow.roms.save(
            Rom(
                rom_id=rom_id,
                platform_slug="psx",
                name=f"Game {rom_id}",
                fs_name=f"game_{rom_id}.chd",
                shortcut_app_id=app_id,
                last_synced_at="2025-01-01T00:00:00",
            )
        )
    return uow


def _make_service(
    control: FakeGameProcessControlAdapter,
    sleeper: FakeSleeper | _YieldingSleeper | None = None,
    uow: FakeUnitOfWork | None = None,
) -> GameProcessService:
    """Build the service. By default ``ROM_ID`` is bound to the fake's own appId.

    The single-tree shorthand (``FakeGameProcessControlAdapter(pids=…)``) runs
    its tree under :data:`DEFAULT_APP_ID`, so the default binding here makes
    that tree the ROM's and the ladder runs — which is what every test that is
    about the LADDER rather than the choice of tree wants.
    """
    return GameProcessService(
        config=GameProcessServiceConfig(
            game_process=control,
            uow_factory=FakeUnitOfWorkFactory(uow=uow or _bound_roms({ROM_ID: APP_ID})),
            sleeper=sleeper or FakeSleeper(),
            logger=logging.getLogger("test_game_process"),
            log_debug=MagicMock(),
        ),
    )


class TestNothingRunning:
    @pytest.mark.asyncio
    async def test_refuses_with_not_running(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[])
        service = _make_service(control)

        with pytest.raises(Refused) as refused:
            await service.stop_running_game(ROM_ID)

        assert refused.value.reason == "not_running"
        assert refused.value.message == "No running game was found to stop."

    @pytest.mark.asyncio
    async def test_signals_nothing_at_all(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[])
        service = _make_service(control)

        with pytest.raises(Refused):
            await service.stop_running_game(ROM_ID)

        assert control.stop_calls == []
        assert control.kill_calls == []

    @pytest.mark.asyncio
    async def test_looks_the_game_up_by_the_roms_shortcut_app_id(self) -> None:
        # The service must ask about the ROM's own shortcut — a tree under any
        # other appId is not this game.
        control = FakeGameProcessControlAdapter(pids=[101])
        service = _make_service(control, uow=_bound_roms({ROM_ID: 555}))

        with pytest.raises(Refused) as refused:
            await service.stop_running_game(ROM_ID)

        assert control.find_calls == [555]
        assert refused.value.reason == "not_running"


class TestStopRequestAlone:
    @pytest.mark.asyncio
    async def test_a_cooperative_process_is_never_force_killed(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[101, 102])

        result = await _make_service(control).stop_running_game(ROM_ID)

        assert result == {"success": True, "stopped": 2, "force_killed": 0}
        assert control.stop_calls == [101, 102]
        assert control.kill_calls == []

    @pytest.mark.asyncio
    async def test_the_ladder_stops_polling_as_soon_as_everything_has_exited(self) -> None:
        # A well-behaved emulator costs one poll interval, not the whole window.
        control = FakeGameProcessControlAdapter(pids=[101])
        sleeper = FakeSleeper()

        await _make_service(control, sleeper).stop_running_game(ROM_ID)

        assert sleeper.calls == [_GRACE_POLL_SECONDS]

    @pytest.mark.asyncio
    async def test_processes_are_signalled_in_the_order_the_adapter_reports_them(self) -> None:
        # Deepest-first is the adapter's contract; the service must not reorder
        # it — the emulator has to be asked before its shell wrappers die.
        control = FakeGameProcessControlAdapter(pids=[103, 102, 101])

        await _make_service(control).stop_running_game(ROM_ID)

        assert control.stop_calls == [103, 102, 101]


class TestEscalationToForceKill:
    @pytest.mark.asyncio
    async def test_a_process_that_ignores_the_stop_request_is_force_killed(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[101])
        control.survive_stop = {101}

        result = await _make_service(control).stop_running_game(ROM_ID)

        assert result == {"success": True, "stopped": 1, "force_killed": 1}
        assert control.kill_calls == [101]

    @pytest.mark.asyncio
    async def test_only_the_survivors_are_force_killed(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[101, 102, 103])
        control.survive_stop = {102}

        result = await _make_service(control).stop_running_game(ROM_ID)

        assert control.kill_calls == [102]
        assert result["force_killed"] == 1

    @pytest.mark.asyncio
    async def test_the_full_grace_window_is_waited_out_before_forcing(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[101])
        control.survive_stop = {101}
        sleeper = FakeSleeper()

        await _make_service(control, sleeper).stop_running_game(ROM_ID)

        assert sleeper.calls == [_GRACE_POLL_SECONDS] * _GRACE_POLLS

    @pytest.mark.asyncio
    async def test_a_process_that_exits_late_in_the_window_is_not_force_killed(self) -> None:
        # Alive for the first few polls, gone before the window expires — a slow
        # save flush must never be force-killed.
        control = FakeGameProcessControlAdapter(pids=[101])
        control.survive_stop = {101}
        real_is_alive = control.is_alive

        def _alive_for_three_polls(pid: int) -> bool:
            still = real_is_alive(pid)
            if len(control.alive_calls) >= 3:
                control.alive.discard(pid)
            return still

        control.is_alive = _alive_for_three_polls  # type: ignore[method-assign]

        result = await _make_service(control).stop_running_game(ROM_ID)

        assert control.kill_calls == []
        assert result["force_killed"] == 0

    @pytest.mark.asyncio
    async def test_a_force_kill_that_cannot_be_delivered_is_not_counted(self) -> None:
        # The process took the stop request, survived the grace window, and then
        # vanished before the force kill landed (or belongs to another user).
        # ``unkillable`` — not ``unsignalable`` — so the stop request still
        # succeeds and the pid actually reaches the force rung.
        control = FakeGameProcessControlAdapter(pids=[101])
        control.survive_stop = {101}
        control.unkillable = {101}

        result = await _make_service(control).stop_running_game(ROM_ID)

        # The force kill was genuinely attempted and genuinely reported False.
        assert control.kill_calls == [101]
        assert result == {"success": True, "stopped": 1, "force_killed": 0}


class TestNeverRepeatsTheStopRequest:
    """Guard for the save-safety invariant: one stop request per process, ever.

    A second request makes RetroArch (``unix_sighandler_quit == 2`` → ``exit(1)``
    with no ``atexit``), DuckStation/PCSX2 (``quick_exit``) and Dolphin
    (``SA_RESETHAND``) skip the save flush the first one started, destroying the
    save file. Only the force kill may escalate.

    "Ever" is literal and covers both halves: no retry loop inside a single
    ladder, AND no second ladder running concurrently with the first. The
    in-call half alone is not the guarantee — the grace window yields the event
    loop for seconds, which is ample time for a second endpoint call to arrive.
    """

    @pytest.mark.asyncio
    async def test_a_surviving_process_is_asked_exactly_once_across_the_whole_window(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[101])
        control.survive_stop = {101}

        await _make_service(control).stop_running_game(ROM_ID)

        # Exactly once — not once per poll, not a retry ladder. The process was
        # alive for every one of the polls, which is precisely the state a naive
        # retry loop would react to.
        assert control.stop_calls == [101]
        assert control.alive_calls  # it really did stay alive through the window
        assert control.kill_calls == [101]

    @pytest.mark.asyncio
    async def test_every_process_is_asked_exactly_once_even_when_all_survive(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[101, 102, 103])
        control.survive_stop = {101, 102, 103}

        await _make_service(control).stop_running_game(ROM_ID)

        assert control.stop_calls == [101, 102, 103]
        assert sorted(control.kill_calls) == [101, 102, 103]

    @pytest.mark.asyncio
    async def test_a_concurrent_second_call_never_re_asks_the_same_process(self) -> None:
        """The cross-CALL half of the guarantee — the save-destroying scenario.

        The user presses Stop, the emulator starts flushing a large memory card,
        the UI shows nothing changing for seconds, and the user presses Stop
        again. Without the single-flight claim the second call rediscovers the
        same still-alive pids and sends a second stop request, which is exactly
        what makes the emulator abandon the flush.
        """
        control = FakeGameProcessControlAdapter(pids=[101])
        control.survive_stop = {101}
        # A yielding sleeper, so the second call really does land while the first
        # is parked in its grace window (see _YieldingSleeper).
        service = _make_service(control, _YieldingSleeper())

        first, second = await asyncio.gather(
            service.stop_running_game(ROM_ID),
            service.stop_running_game(ROM_ID),
            return_exceptions=True,
        )

        # THE assertion: one stop request for that pid across BOTH calls.
        assert control.stop_calls == [101]
        # The loser is refused rather than silently no-oping, so the frontend
        # can say something true about it.
        refusals = [outcome for outcome in (first, second) if isinstance(outcome, Refused)]
        answers = [outcome for outcome in (first, second) if isinstance(outcome, dict)]
        assert len(refusals) == 1
        assert answers == [{"success": True, "stopped": 1, "force_killed": 1}]
        assert refusals[0].reason == "already_stopping"
        assert refusals[0].message == "The game is already being stopped."
        # The refusal never touched the process table at all.
        assert control.find_calls == [APP_ID]

    @pytest.mark.asyncio
    async def test_the_claim_is_released_so_a_later_stop_still_works(self) -> None:
        # The guard is single-FLIGHT, not once-per-session: once the ladder ends,
        # a fresh Stop for a new session must be admitted normally.
        control = FakeGameProcessControlAdapter(pids=[101])
        service = _make_service(control)

        assert (await service.stop_running_game(ROM_ID))["success"] is True

        control.pids = [202]
        control.alive = {202}
        second = await service.stop_running_game(ROM_ID)

        assert second == {"success": True, "stopped": 1, "force_killed": 0}
        assert control.stop_calls == [101, 202]

    @pytest.mark.asyncio
    async def test_the_claim_is_released_when_the_ladder_raises(self) -> None:
        # A leaked flag would make Stop Game permanently unavailable for the rest
        # of the session, so the release must survive an exception.
        control = FakeGameProcessControlAdapter(pids=[101])
        service = _make_service(control)
        real_find = control.find_game_processes
        find_count = 0

        def _boom_on_first_call(app_id: int) -> list[int]:
            nonlocal find_count
            find_count += 1
            if find_count == 1:
                raise RuntimeError("proc exploded")
            return real_find(app_id)

        control.find_game_processes = _boom_on_first_call  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="proc exploded"):
            await service.stop_running_game(ROM_ID)

        # The next call is admitted normally, not refused with already_stopping.
        assert (await service.stop_running_game(ROM_ID))["success"] is True
        assert control.stop_calls == [101]


class TestTargetsOnlyThisGamesTree:
    """Guard for the second safety invariant: never signal another game's tree.

    Steam starts every shortcut under its own process tree, and several can
    run at once — two games in the same emulator are two trees. Each test here
    asserts the OTHER tree's pids appear in NEITHER ``stop_calls`` nor
    ``kill_calls``: asserting only that the right pids were signalled would
    pass just as happily if both were.
    """

    THEIRS = 2157604804

    def _two_trees(self) -> FakeGameProcessControlAdapter:
        control = FakeGameProcessControlAdapter()
        control.add_tree(self.THEIRS, [201, 202])
        control.add_tree(APP_ID, [101, 102])
        return control

    @pytest.mark.asyncio
    async def test_only_this_games_tree_is_signalled(self) -> None:
        control = self._two_trees()
        service = _make_service(control)

        result = await service.stop_running_game(ROM_ID)

        assert result == {"success": True, "stopped": 2, "force_killed": 0}
        assert control.stop_calls == [101, 102]
        # THE assertion: the other game was never touched, on either rung.
        assert 201 not in control.stop_calls
        assert 202 not in control.stop_calls
        assert control.kill_calls == []

    @pytest.mark.asyncio
    async def test_another_games_tree_is_not_force_killed_either(self) -> None:
        # Everything survives the stop request, so the force rung is reached for
        # real — and it too must stay inside this game's tree.
        control = self._two_trees()
        control.survive_stop = {101, 102, 201, 202}
        service = _make_service(control)

        result = await service.stop_running_game(ROM_ID)

        assert result == {"success": True, "stopped": 2, "force_killed": 2}
        assert sorted(control.kill_calls) == [101, 102]
        assert control.stop_calls == [101, 102]

    @pytest.mark.asyncio
    async def test_only_another_games_tree_alive_signals_nothing_and_refuses_with_not_running(self) -> None:
        control = FakeGameProcessControlAdapter()
        control.add_tree(self.THEIRS, [201, 202])
        service = _make_service(control)

        with pytest.raises(Refused) as refused:
            await service.stop_running_game(ROM_ID)

        assert refused.value.reason == "not_running"
        assert refused.value.message == "No running game was found to stop."
        # Killing the wrong game is worse than refusing: nothing was signalled.
        assert control.stop_calls == []
        assert control.kill_calls == []

    @pytest.mark.asyncio
    async def test_a_rom_bound_to_no_shortcut_signals_nothing(self) -> None:
        # No bound shortcut → nothing Steam runs is this ROM's, and "stop
        # whatever runs" is the bug the ROM id exists to prevent.
        control = self._two_trees()
        service = _make_service(control, uow=_bound_roms({ROM_ID: None}))

        with pytest.raises(Refused) as refused:
            await service.stop_running_game(ROM_ID)

        assert refused.value.reason == "not_running"
        assert control.find_calls == []
        assert control.stop_calls == []
        assert control.kill_calls == []

    @pytest.mark.asyncio
    async def test_the_shortcut_is_looked_up_for_the_rom_that_was_passed(self) -> None:
        control = self._two_trees()
        service = _make_service(control, uow=_bound_roms({7: APP_ID, ROM_ID: self.THEIRS}))

        result = await service.stop_running_game(7)

        assert control.find_calls == [APP_ID]
        assert result["success"] is True
        assert control.stop_calls == [101, 102]


class TestRacesDuringTheLadder:
    @pytest.mark.asyncio
    async def test_a_process_that_vanished_before_the_stop_request_is_not_polled_or_killed(self) -> None:
        control = FakeGameProcessControlAdapter(pids=[101, 102])
        control.unsignalable = {101}

        result = await _make_service(control).stop_running_game(ROM_ID)

        # 101 was still asked once (that IS the discovery of its death) but is
        # carried no further; 102 is the only one counted.
        assert control.stop_calls == [101, 102]
        assert 101 not in control.alive_calls
        assert control.kill_calls == []
        assert result == {"success": True, "stopped": 1, "force_killed": 0}

    @pytest.mark.asyncio
    async def test_every_process_vanishing_between_discovery_and_signal_still_succeeds(self) -> None:
        # The requested end state (nothing running) is already true, so this is a
        # success with a zero count — not a failure the user has to act on.
        control = FakeGameProcessControlAdapter(pids=[101, 102])
        control.unsignalable = {101, 102}
        sleeper = FakeSleeper()

        result = await _make_service(control, sleeper).stop_running_game(ROM_ID)

        assert result == {"success": True, "stopped": 0, "force_killed": 0}
        assert control.kill_calls == []
        # With nothing left to wait for, the grace window is skipped entirely.
        assert sleeper.calls == []
