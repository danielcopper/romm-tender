"""PlatformSwitches — the one writer of the per-platform sync toggles in ``settings.json``.

The toggles are kept by ``fs_slug``, and those an earlier release kept by RomM's
platform id wait under ``enabled_platform_ids`` until a read of RomM's listing
moves them (``domain/platform_prefs.py``). Why the map is replaced whole, why
the move leaves its key in place, and what a failed save keeps:
``docs/architecture/backend-architecture.md``, "The per-platform sync toggles".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.platform_prefs import stored_switches, switches_kept_by_id
from domain.state_migrations import SWITCHES_BY_ID_SETTING, SWITCHES_SETTING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from contextlib import AbstractContextManager

    from services.protocols import SettingsPersister


@dataclass(frozen=True)
class PlatformSwitchesConfig:
    """Frozen wiring bundle handed to ``PlatformSwitches.__init__``.

    Holds the live settings dict the toggles sit in, its persister, and the
    lock every change of them is made under — their writers run in executor
    threads of their own.
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

    def replace(self, build: Callable[[Mapping[str, Any]], dict[str, Any]]) -> None:
        """Put *build*'s map of the current toggles in their place and save it. Blocking.

        Where the save raises, the old map is put back and the exception
        propagates (#2452 D28).
        """
        with self._lock:
            previous = self._settings.get(SWITCHES_SETTING)
            self._settings[SWITCHES_SETTING] = build(stored_switches(self._settings))
            try:
                self._settings_persister.save_settings()
            except BaseException:
                self._settings[SWITCHES_SETTING] = previous
                raise

    def carry_over(self, build: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any] | None]) -> bool:
        """Put the toggles *build* moves from the ones kept by id in place of both, and save them. Blocking.

        *build* is handed the toggles kept by RomM's platform id and the current
        ones, and answers the whole new map, or ``None`` to change nothing yet.
        Answers whether a map was built: ``False`` too where nothing is kept by
        id. The toggles kept by id are then set to ``None``. Where the save
        raises, the moved map stays and the exception propagates (#2452 D23).
        """
        with self._lock:
            by_id = switches_kept_by_id(self._settings)
            if by_id is None:
                return False
            moved = build(by_id, stored_switches(self._settings))
            if moved is None:
                return False
            self._settings[SWITCHES_SETTING] = moved
            self._settings[SWITCHES_BY_ID_SETTING] = None
            self._settings_persister.save_settings()
            return True
