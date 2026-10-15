"""The tasks an owner starts, held from their start until they end.

The event loop keeps only a weak reference to a task, so a task nothing else
holds can be collected before it ends (the Python documentation of
``asyncio.create_task``, "Save a reference to the result of this function").
Outside ``host/``, ``create_task``, ``ensure_future`` and ``asyncio.Task(...)``
are called only here (``scripts/check_background_tasks.py``): ``spawn`` for a
task nobody awaits, ``start_awaited`` for one its starter awaits itself. A task
``gather`` or ``shield`` makes from a coroutine its caller awaits on the spot is
not one of them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine, Iterator


class BackgroundTasks:
    """The tasks one owner started, each held until it ends; :meth:`shutdown` cancels and awaits them when called."""

    def __init__(self, *, logger: logging.Logger) -> None:
        self._logger = logger
        # A dict rather than a set, so shutdown cancels in the order the tasks were started.
        self._held: dict[asyncio.Task[Any], None] = {}

    def __len__(self) -> int:
        return len(self._held)

    def __iter__(self) -> Iterator[asyncio.Task[Any]]:
        return iter(tuple(self._held))

    def spawn[T](self, coroutine: Coroutine[Any, Any, T], name: str) -> asyncio.Task[T]:
        """Start *coroutine* on the running loop as a task named *name*, and hold it until it ends.

        Called on the loop's own thread. A task that ends with an exception is
        logged as a warning under *name*; one that is cancelled is not logged.
        """
        task = self._start(coroutine, name)
        task.add_done_callback(self._log_failure)
        return task

    def start_awaited[T](self, coroutine: Coroutine[Any, Any, T], name: str) -> asyncio.Task[T]:
        """Start *coroutine* as a task named *name* that its starter awaits itself, and hold it until it ends.

        The starter handles the task's failure where it awaits it, so nothing
        is logged here — including where it stops awaiting before the task
        ends, as one awaiting through ``asyncio.shield`` can when it is
        cancelled. Called on the loop's own thread.
        """
        return self._start(coroutine, name)

    async def shutdown(self) -> None:
        """Cancel every task still held and wait until each has ended, a task started meanwhile included."""
        while self._held:
            tasks = tuple(self._held)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start[T](self, coroutine: Coroutine[Any, Any, T], name: str) -> asyncio.Task[T]:
        task = asyncio.get_running_loop().create_task(coroutine, name=name)
        self._held[task] = None
        task.add_done_callback(self._let_go)
        return task

    def _let_go(self, task: asyncio.Task[Any]) -> None:
        self._held.pop(task, None)

    def _log_failure(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None:
            self._logger.warning(f"Background task {task.get_name()} failed: {failure!r}", exc_info=failure)
