"""Tests for ``lib.background_tasks`` — every task held until it ends, and stopped at shutdown."""

from __future__ import annotations

import asyncio
import gc
import logging
import weakref

from lib.background_tasks import BackgroundTasks


def _tasks(logger: logging.Logger) -> BackgroundTasks:
    return BackgroundTasks(logger=logger)


class TestHolding:
    async def test_a_task_nothing_else_holds_survives_a_collection(self, logger):
        """The loop holds a task only weakly: one waiting on a future nobody else holds is garbage unless held."""
        tasks = _tasks(logger)

        async def orphan() -> None:
            await asyncio.get_running_loop().create_future()

        task = weakref.ref(tasks.spawn(orphan(), "orphan"))
        await asyncio.sleep(0)
        gc.collect()

        assert task() is not None
        await tasks.shutdown()

    async def test_a_task_is_held_until_it_ends_and_let_go_after(self, logger):
        tasks = _tasks(logger)
        release = asyncio.Event()

        task = tasks.spawn(release.wait(), "waiting")

        assert list(tasks) == [task]
        release.set()
        await task
        assert len(tasks) == 0

    async def test_the_task_carries_the_name_it_was_started_with(self, logger):
        tasks = _tasks(logger)

        task = tasks.spawn(asyncio.Event().wait(), "refresh_save_status")

        assert task.get_name() == "refresh_save_status"
        await tasks.shutdown()


class TestHowATaskEnds:
    async def test_an_exception_is_logged_as_a_warning_under_the_tasks_name(self, logger, caplog):
        tasks = _tasks(logger)

        async def fails() -> None:
            raise ValueError("the payload is not JSON")

        with caplog.at_level(logging.WARNING, logger=logger.name):
            await asyncio.wait([tasks.spawn(fails(), "sync_progress error frame")])

        records = [record for record in caplog.records if record.name == logger.name]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "sync_progress error frame" in records[0].getMessage()
        assert "the payload is not JSON" in records[0].getMessage()
        assert len(tasks) == 0

    async def test_a_cancellation_is_not_logged(self, logger, caplog):
        tasks = _tasks(logger)
        task = tasks.spawn(asyncio.Event().wait(), "waiting")
        await asyncio.sleep(0)

        with caplog.at_level(logging.DEBUG, logger=logger.name):
            task.cancel()
            await asyncio.wait([task])

        assert task.cancelled()
        assert [record for record in caplog.records if record.name == logger.name] == []
        assert len(tasks) == 0

    async def test_a_task_that_ends_normally_is_not_logged(self, logger, caplog):
        tasks = _tasks(logger)

        async def answers() -> int:
            return 7

        with caplog.at_level(logging.DEBUG, logger=logger.name):
            assert await tasks.spawn(answers(), "answers") == 7

        assert [record for record in caplog.records if record.name == logger.name] == []


class TestShutdown:
    async def test_every_task_still_held_is_cancelled_and_awaited_in_the_order_it_was_started(self, logger):
        tasks = _tasks(logger)
        ended: list[str] = []

        async def waits(name: str) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                ended.append(name)
                raise

        first = tasks.spawn(waits("first"), "first")
        second = tasks.spawn(waits("second"), "second")
        await asyncio.sleep(0)

        await tasks.shutdown()

        assert first.cancelled()
        assert second.cancelled()
        assert ended == ["first", "second"]
        assert len(tasks) == 0

    async def test_a_task_that_ends_by_itself_while_shutting_down_is_awaited_too(self, logger):
        tasks = _tasks(logger)
        ended: list[str] = []

        async def finishes_its_write() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                ended.append("written")

        task = tasks.spawn(finishes_its_write(), "write")
        await asyncio.sleep(0)

        await tasks.shutdown()

        assert task.done()
        assert ended == ["written"]

    async def test_nothing_held_returns_at_once(self, logger):
        await asyncio.wait_for(_tasks(logger).shutdown(), 1)
