"""PlatformPicks — the one writer of the per-platform emulator picks in ``settings.json``.

Why the picks are replaced whole rather than changed in place:
``docs/architecture/core-emulator-selection.md``, "Per-platform (``CoreService.set_system_core``)".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from contextlib import AbstractContextManager

    from services.protocols import SettingsPersister

# Where the per-platform emulator picks are stored in ``settings.json``.
_PICKS_SETTING = "platform_cores"


@dataclass(frozen=True)
class PlatformPicksConfig:
    """Frozen wiring bundle handed to ``PlatformPicks.__init__``.

    Holds the live settings dict the picks sit in, its persister, and the lock
    every change of the picks is made under — their writers run in executor
    threads of their own.
    """

    settings: dict[str, Any]
    settings_persister: SettingsPersister
    lock: AbstractContextManager[object]


class PlatformPicks:
    """Replaces the per-platform picks with a changed copy and saves them, or puts the old ones back."""

    def __init__(self, *, config: PlatformPicksConfig) -> None:
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._lock = config.lock
        # Present before any writer runs, so a replace never adds a key to the
        # settings a save on another thread may be walking.
        self._settings.setdefault(_PICKS_SETTING, {})

    def replace(self, build: Callable[[Mapping[str, Any]], dict[str, Any] | None]) -> bool:
        """Put *build*'s map of the current picks in their place and save it; ``False`` where it built none.

        Under the lock, *build* is handed the picks as they stand and answers
        the whole new map, or ``None`` to change nothing. Where the save raises,
        the old map is put back and the exception propagates. Blocking.
        """
        with self._lock:
            current = self._settings.get(_PICKS_SETTING)
            previous: dict[str, Any] = current if isinstance(current, dict) else {}
            changed = build(previous)
            if changed is None:
                return False
            self._settings[_PICKS_SETTING] = changed
            try:
                self._settings_persister.save_settings()
            except BaseException:
                self._settings[_PICKS_SETTING] = previous
                raise
            return True
