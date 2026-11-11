"""PlatformSwitches — the one writer of the per-platform sync toggles in ``settings.json``.

The toggles are kept by ``fs_slug``, and those an earlier release kept by RomM's
platform id wait under ``enabled_platform_ids`` until a read of RomM's listing
moves them (``domain/platform_prefs.py``). Why the map is replaced whole rather
than changed in place, and why a failed save keeps what was written:
``docs/architecture/backend-architecture.md``, "The per-platform sync toggles".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.state_migrations import SWITCHES_BY_ID_SETTING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from contextlib import AbstractContextManager

    from services.protocols import SettingsPersister

# Where the per-platform sync toggles are stored in ``settings.json``.
_SWITCHES_SETTING = "enabled_platforms"


@dataclass(frozen=True)
class PlatformSwitchesConfig:
    """Frozen wiring bundle handed to ``PlatformSwitches.__init__``.

    Holds the live settings dict the toggles sit in, its persister, and the
    lock every change of them is made under — the refiling writes them from
    executor threads of its own.
    """

    settings: dict[str, Any]
    settings_persister: SettingsPersister
    lock: AbstractContextManager[object]


class PlatformSwitches:
    """Replaces the per-platform sync toggles with a changed copy and saves them."""

    def __init__(self, *, config: PlatformSwitchesConfig) -> None:
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._lock = config.lock

    def replace(self, build: Callable[[Mapping[str, bool]], dict[str, bool]]) -> None:
        """Put *build*'s map of the current toggles in their place and save it. Blocking.

        Where the save raises, the new map stays and the exception propagates.
        """
        with self._lock:
            self._settings[_SWITCHES_SETTING] = build(self._current())
            self._settings_persister.save_settings()

    def carry_over(self, build: Callable[[Mapping[str, Any], Mapping[str, bool]], dict[str, bool] | None]) -> bool:
        """Put the toggles *build* moves from the ones kept by id in place of both, and save them. Blocking.

        *build* is handed the toggles kept by RomM's platform id and the current
        ones, and answers the whole new map, or ``None`` to change nothing yet.
        Answers whether a map was built: ``False`` too where nothing is kept by
        id. The toggles kept by id are then set to ``None`` rather than their key
        removed, so the settings a save on another thread may be walking never
        change size. Where the save raises, the moved map stays and the exception
        propagates (#2452 D23).
        """
        with self._lock:
            by_id = self._settings.get(SWITCHES_BY_ID_SETTING)
            if not isinstance(by_id, dict):
                return False
            moved = build(by_id, self._current())
            if moved is None:
                return False
            self._settings[_SWITCHES_SETTING] = moved
            self._settings[SWITCHES_BY_ID_SETTING] = None
            self._settings_persister.save_settings()
            return True

    def _current(self) -> dict[str, bool]:
        current = self._settings.get(_SWITCHES_SETTING)
        return current if isinstance(current, dict) else {}
