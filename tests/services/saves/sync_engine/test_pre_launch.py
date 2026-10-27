"""Tests for the sync before a start's own state and stop (``sync_engine/_pre_launch.py``)."""

import asyncio

import pytest

from services.saves._sync_stop import SyncStopped
from services.saves.sync_engine._gate import GateHolder
from services.saves.sync_engine._pre_launch import PreLaunchRun, PreLaunchRuns, PreLaunchState


class TestWhatTheLatestCallIsDoing:
    def test_no_call_is_idle(self):
        assert PreLaunchRuns().state(42, GateHolder.SYNC_ALL) == {"state": "idle"}

    def test_a_call_waiting_for_the_gate_names_its_holder(self):
        runs = PreLaunchRuns()
        with runs.track(42):
            assert runs.state(42, GateHolder.POST_EXIT) == {"state": "waiting_for_sync", "holder": "post_exit"}

    def test_a_call_waiting_while_nothing_holds_the_gate_is_handed_it_and_syncing(self):
        runs = PreLaunchRuns()
        with runs.track(42):
            assert runs.state(42, None) == {"state": "syncing"}

    def test_the_latest_call_of_the_rom_answers(self):
        runs = PreLaunchRuns()
        with runs.track(42) as earlier, runs.track(42):
            earlier.state = PreLaunchState.SYNCING
            assert runs.state(42, GateHolder.PRE_LAUNCH) == {"state": "waiting_for_sync", "holder": "pre_launch"}

    def test_a_call_that_ended_leaves_the_rom_idle(self):
        runs = PreLaunchRuns()
        with runs.track(42):
            pass
        assert runs.state(42, None) == {"state": "idle"}

    def test_another_rom_s_call_is_not_this_rom_s(self):
        runs = PreLaunchRuns()
        with runs.track(7):
            assert runs.state(42, GateHolder.PRE_LAUNCH) == {"state": "idle"}
            assert runs.stop(42) is False


class TestTheWait:
    @pytest.mark.asyncio
    async def test_a_stop_ends_the_wait(self):
        run = PreLaunchRun()
        never = asyncio.Event()
        waiting = asyncio.ensure_future(run.wait(PreLaunchState.WAITING_FOR_SYNC, never.wait()))
        await asyncio.sleep(0.01)

        run.request_stop()

        with pytest.raises(SyncStopped):
            await asyncio.wait_for(waiting, 1)
        assert run.stop.stopped_the_sync() is True

    @pytest.mark.asyncio
    async def test_a_stop_asked_before_the_wait_starts_none(self):
        run = PreLaunchRun()
        run.request_stop()
        started: list[bool] = []

        async def acquire() -> None:
            started.append(True)

        waited = acquire()
        with pytest.raises(SyncStopped):
            await run.wait(PreLaunchState.WAITING_FOR_ROM, waited)
        waited.close()
        assert started == []

    @pytest.mark.asyncio
    async def test_a_wait_answers_what_it_waited_for_and_a_later_stop_waits_for_the_next_check(self):
        run = PreLaunchRun()

        async def acquire() -> str:
            return "held"

        assert await run.wait(PreLaunchState.WAITING_FOR_ROM, acquire()) == "held"
        assert run.state is PreLaunchState.WAITING_FOR_ROM

        run.request_stop()
        assert run.stop.stopped_the_sync() is False
        assert run.stop.stops_here() is True

    @pytest.mark.asyncio
    async def test_a_timeout_the_wait_did_not_set_is_not_a_stop(self):
        run = PreLaunchRun()

        async def acquire() -> None:
            raise TimeoutError

        with pytest.raises(TimeoutError):
            await run.wait(PreLaunchState.WAITING_FOR_SYNC, acquire())
        assert run.stop.stopped_the_sync() is False
