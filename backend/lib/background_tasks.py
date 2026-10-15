"""The tasks an owner starts and does not await: held from their start until they end.

The event loop keeps only a weak reference to a task, so a task nothing else
holds can be collected before it ends (the Python documentation of
``asyncio.create_task``, "Save a reference to the result of this function").
Every task the backend starts outside ``host/`` is started here;
``scripts/check_background_tasks.py`` fails on one created anywhere else.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine, Iterator


class BackgroundTasks:
    """The tasks one owner started, each held until it ends, and stopped together at the owner's shutdown."""

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
        task = asyncio.get_running_loop().create_task(coroutine, name=name)
        self._held[task] = None
        task.add_done_callback(self._ended)
        return task

    async def shutdown(self) -> None:
        """Cancel every task still held and wait until each has ended."""
        tasks = tuple(self._held)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _ended(self, task: asyncio.Task[Any]) -> None:
        self._held.pop(task, None)
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None:
            self._logger.warning(f"Background task {task.get_name()} failed: {failure!r}", exc_info=failure)
