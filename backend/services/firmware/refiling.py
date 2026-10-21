"""Moving Tender's older firmware entries to the platform RomM states for them.

Download records and per-platform emulator overrides Tender filed under a
server folder name before a firmware file's platform came from
``platform_id`` are moved to that platform here, each time a read of the
listing reaches the server. Only an entry filed under a name that is no listed
platform's slug is judged, so every later reading moves nothing; the rules are
``domain/firmware_platforms.py``'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_platforms import folder_platforms, override_moves, refiled_record_platform

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping, Sequence

    from domain.bios_file import BiosFile
    from services.protocols import SettingsPersister, UnitOfWorkFactory

# Where the per-platform emulator overrides are stored in ``settings.json``.
_OVERRIDES_SETTING = "platform_cores"


@dataclass(frozen=True)
class FirmwareRefilingConfig:
    """Frozen wiring bundle handed to ``FirmwareRefiling.__init__``.

    Holds the Unit-of-Work factory the download records are read and moved
    through, the live settings dict the per-platform emulator overrides sit in
    and its persister, and the logger.
    """

    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    logger: logging.Logger


class FirmwareRefiling:
    """Moves download records and per-platform emulator overrides filed under a folder name to their platform."""

    def __init__(self, *, config: FirmwareRefilingConfig) -> None:
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._logger = config.logger

    def do_refile(self, platforms: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> None:
        """Move every entry *platforms* and their firmware *rows* place elsewhere. Blocking.

        *rows* are :func:`domain.firmware_platforms.library_firmware` over
        *platforms*. Raises ``OSError`` where the settings could not be saved,
        once the overrides in memory are given back.
        """
        self._refile_records(platforms, rows)
        self._refile_overrides(folder_platforms(platforms))

    def _refile_records(self, platforms: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> None:
        """Move each download record to its platform, in one write UoW.

        Where the record's new identity is already taken, a record there naming
        the same file makes the moving one its duplicate, which is dropped; one
        naming another file leaves the moving record where it is, so neither
        record's delete authority is lost.
        """
        with self._uow_factory() as uow:
            # Read whole first: iter_all yields from a live cursor over the table this loop deletes from and saves to.
            records = list(uow.bios_files.iter_all())
            for record in records:
                target = refiled_record_platform(
                    record.platform_slug, record.file_name, record.firmware_id, rows, platforms
                )
                if target is None:
                    continue
                taken = uow.bios_files.get(target, record.file_name)
                if taken is not None and taken.file_path != record.file_path:
                    self._logger.warning(
                        f"Kept the BIOS record {record.file_name} under {record.platform_slug}: "
                        f"{target} already records another file of that name"
                    )
                    continue
                self._refile(uow, record, target, keep_existing=taken is not None)

    @staticmethod
    def _refile(uow: Any, record: BiosFile, target: str, *, keep_existing: bool) -> None:
        uow.bios_files.delete(record.platform_slug, record.file_name)
        if not keep_existing:
            uow.bios_files.save(record.refiled_under(target))

    def _refile_overrides(self, folders: Mapping[str, str]) -> None:
        """Move each override in the live dict itself, so a choice stored meanwhile for another platform stays.

        Where the settings cannot be saved, every move is given back, so memory
        and file agree, and the ``OSError`` propagates.
        """
        overrides = self._settings.get(_OVERRIDES_SETTING)
        if not isinstance(overrides, dict):
            return
        moves = override_moves(overrides, folders)
        if not moves:
            return
        for folder, slug in moves.items():
            overrides[slug] = overrides.pop(folder)
        try:
            self._settings_persister.save_settings()
        except OSError:
            for folder, slug in moves.items():
                overrides[folder] = overrides.pop(slug)
            raise
        self._logger.info("Moved per-platform emulator overrides filed under a firmware folder name to their platform")
