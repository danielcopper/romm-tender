"""How often this machine's backends may take Steam's interface down.

Contract: the one piece of state that outlives a backend process in replacing a
stranded panel — when this machine last had Steam reload its JS context or
terminated its web helper for that — and the answer whether one more is allowed.
It takes nothing down itself.

**Why it has to outlive the process.** The once-rule in ``recovery.py`` holds
per stranded panel, and every backend start makes a new one: a backend that
crashes after loading its panel, under a service manager that starts it again,
leaves a fresh stranded panel behind each time, and each new process would
reload Steam's interface once for it. Only a record the next process reads can
see that it is the third in a row.

**Two in ten minutes.** That lets a deliberate restart or a reinstall through,
with one more right after it, and stops a loop by its third turn.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

RELOAD_LIMIT_FILENAME = "reload-guard.json"

RELOAD_LIMIT = 2
RELOAD_WINDOW_SECONDS = 600.0


class ReloadLimit:
    """The times this machine's backends took Steam's interface down, and the limit on them."""

    def __init__(
        self,
        path: str,
        *,
        clock: Callable[[], float] = time.time,
        limit: int = RELOAD_LIMIT,
        window: float = RELOAD_WINDOW_SECONDS,
    ) -> None:
        self._path = path
        self._clock = clock
        self._limit = limit
        self._window = window

    @property
    def limit(self) -> int:
        """How many takedowns the window holds."""
        return self._limit

    @property
    def window(self) -> float:
        """The window, in seconds."""
        return self._window

    def allows(self) -> bool:
        """May the interface be taken down once more now?"""
        return len(self._recent(self._clock())) < self._limit

    def record(self) -> None:
        """Note one takedown, now."""
        now = self._clock()
        self._write([*self._recent(now), now])

    def _recent(self, now: float) -> list[float]:
        """The recorded takedowns inside the window ending *now*.

        A time after *now* is dropped rather than counted: it can only come from
        a clock that was set back, and counting it would hold the limit shut for
        as long as the clock takes to catch up.
        """
        return [at for at in self._read() if now - self._window < at <= now]

    def _read(self) -> list[float]:
        """The recorded times, or none when there is nothing readable."""
        try:
            with open(self._path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return []
        times = stored.get("takedowns") if isinstance(stored, dict) else None
        if not isinstance(times, list):
            return []
        return [float(at) for at in times if isinstance(at, int | float) and not isinstance(at, bool)]

    def _write(self, times: list[float]) -> None:
        """Replace the record. Written through a temporary file, like the watchdog's."""
        temporary = f"{self._path}.{os.getpid()}.tmp"
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"takedowns": times}, handle)
            os.replace(temporary, self._path)
        except OSError:
            # A record that cannot be written leaves the limit unable to count,
            # which is the watchdog's lenient direction for the watchdog's
            # reason: a read-only state directory is no reason to leave a panel
            # stranded.
            return
