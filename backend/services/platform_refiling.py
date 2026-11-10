"""Moving Tender's older per-platform entries to the platforms RomM's listing names.

Download records and per-platform emulator picks are kept under a platform's
``fs_slug`` (#2452 D6, D7). Each read of RomM's platform listing hands what it
read here, and an entry kept under any other key — a slug, as both were before,
or a server folder name — is moved to its platform. The rules are
``domain/firmware_platforms.py``'s; an entry already under a listed ``fs_slug``
is never moved, so every later reading moves nothing.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_platforms import library_firmware, pick_moves, refiled_record_folder

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping, Sequence

    from domain.bios_file import BiosFile
    from services.protocols import SettingsPersister, UnitOfWorkFactory

# Where the per-platform emulator picks are stored in ``settings.json``.
_PICKS_SETTING = "platform_cores"


@dataclass(frozen=True)
class PlatformRefilingConfig:
    """Frozen wiring bundle handed to ``PlatformRefiling.__init__``.

    Holds the Unit-of-Work factory the download records are read and moved
    through, the live settings dict the per-platform picks sit in and its
    persister, and the logger.
    """

    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    logger: logging.Logger


class PlatformRefiling:
    """Moves download records and per-platform picks kept under another key to their platform's ``fs_slug``."""

    def __init__(self, *, config: PlatformRefilingConfig) -> None:
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._logger = config.logger

    def do_refile(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        """Move every entry the read *platforms* place elsewhere. Blocking.

        Raises ``OSError`` where the settings could not be saved, once the picks
        in memory are given back.
        """
        self._refile_records(platforms)
        self._refile_picks(platforms)

    def _refile_records(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        """Move each download record to its platform, in one write UoW.

        Where the record's new identity is already taken, a record there naming
        the same file makes the moving one its duplicate, which is dropped; one
        naming another file leaves the moving record where it is, so neither
        record's delete authority is lost.
        """
        rows = library_firmware(platforms)
        with self._uow_factory() as uow:
            # Read whole first: iter_all yields from a live cursor over the table this loop deletes from and saves to.
            records = list(uow.bios_files.iter_all())
            for record in records:
                target = refiled_record_folder(
                    record.platform_folder, record.file_name, record.firmware_id, rows, platforms
                )
                if target is None or target == record.platform_folder:
                    continue
                taken = uow.bios_files.get(target, record.file_name)
                if taken is not None and taken.file_path != record.file_path:
                    self._logger.warning(
                        f"Kept the BIOS record {record.file_name} under {record.platform_folder}: "
                        f"{target} already records another file of that name"
                    )
                    continue
                self._refile(uow, record, target, keep_existing=taken is not None)

    @staticmethod
    def _refile(uow: Any, record: BiosFile, target: str, *, keep_existing: bool) -> None:
        uow.bios_files.delete(record.platform_folder, record.file_name)
        if not keep_existing:
            uow.bios_files.save(record.refiled_under(target))

    def _refile_picks(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        """Move each pick in the live dict itself, so a choice stored meanwhile for another platform stays.

        Where the settings cannot be saved, every move is given back, so memory
        and file agree, and the ``OSError`` propagates.
        """
        picks = self._settings.get(_PICKS_SETTING)
        if not isinstance(picks, dict):
            return
        moves = pick_moves(picks, platforms)
        if not moves:
            return
        moved = {key: picks.pop(key) for key in moves}
        for key, folders in moves.items():
            for folder in folders:
                picks[folder] = copy.deepcopy(moved[key])
        try:
            self._settings_persister.save_settings()
        except OSError:
            for key, folders in moves.items():
                for folder in folders:
                    picks.pop(folder, None)
                picks[key] = moved[key]
            raise
        self._logger.info("Moved per-platform emulator picks kept under a slug to the platforms' fs_slug")
