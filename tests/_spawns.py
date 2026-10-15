"""The names a test's code started tasks under through ``BackgroundTasks.spawn``, in the order it started them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lib.background_tasks import BackgroundTasks

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Coroutine

    import pytest


def record_spawns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the name of every task started through any ``BackgroundTasks``, which still starts it."""
    names: list[str] = []
    spawn = BackgroundTasks.spawn

    def recording_spawn(self: BackgroundTasks, coroutine: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        names.append(name)
        return spawn(self, coroutine, name)

    monkeypatch.setattr(BackgroundTasks, "spawn", recording_spawn)
    return names
