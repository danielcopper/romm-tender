"""In-memory ``SteamInterfaceReader`` for service and contract tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeListedApp:
    """One app the fake lists, shaped as ``ListedRunningApp``."""

    name: str
    status_read: bool = True


class FakeSteamInterface:
    """Answers the running apps and the reload limit it was given, and counts the app readings.

    ``apps`` is one reading: a tuple of names, ``()`` for a definite none, or
    ``None`` for a reading that could not be taken; each is listed with its
    status read, followed by ``apps_status_unread`` listed with a status that
    could not be read. ``frees_at`` is ``None`` while the limit would let one
    more reload through. ``raises``, where set, is raised by every app reading
    instead, and ``limit_raises`` by every reading of the limit.
    """

    def __init__(
        self,
        *,
        apps: tuple[str, ...] | None = (),
        apps_status_unread: tuple[str, ...] = (),
        frees_at: float | None = None,
    ) -> None:
        self.apps = apps
        self.apps_status_unread = apps_status_unread
        self.frees_at = frees_at
        self.readings = 0
        self.raises: Exception | None = None
        self.limit_raises: Exception | None = None

    async def running_apps(self) -> tuple[FakeListedApp, ...] | None:
        self.readings += 1
        if self.raises is not None:
            raise self.raises
        if self.apps is None:
            return None
        return (
            *(FakeListedApp(name) for name in self.apps),
            *(FakeListedApp(name, status_read=False) for name in self.apps_status_unread),
        )

    async def reload_frees_at(self) -> float | None:
        if self.limit_raises is not None:
            raise self.limit_raises
        return self.frees_at
