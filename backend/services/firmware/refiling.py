"""Moving Tender's older firmware entries to the platform RomM states for them.

Download records and emulator picks Tender filed under a server folder name
before a firmware file's platform came from ``platform_id`` are moved to that
platform here, each time a fresh platform list is read. Once an entry is where
it belongs it is not moved again, so every later reading moves nothing; the
rules are ``domain/firmware_platforms.py``'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_platforms import folder_platforms, refiled_picks, refiled_record_platform

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping, Sequence

    from domain.bios_file import BiosFile
    from services.protocols import SettingsPersister, UnitOfWorkFactory

# Where the per-platform emulator picks are stored in ``settings.json``.
_PICKS_SETTING = "platform_cores"


@dataclass(frozen=True)
class FirmwareRefilingConfig:
    """Frozen wiring bundle handed to ``FirmwareRefiling.__init__``.

    Holds the Unit-of-Work factory the download records are read and moved
    through, the live settings dict the picks sit in and its persister, and the
    logger.
    """

    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    logger: logging.Logger


class FirmwareRefiling:
    """Moves download records and picks filed under a folder name to the platform they belong to."""

    def __init__(self, *, config: FirmwareRefilingConfig) -> None:
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._logger = config.logger

    def do_refile(self, platforms: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> None:
        """Move every entry *platforms* and their firmware *rows* place elsewhere. Blocking.

        *rows* are :func:`domain.firmware_platforms.library_firmware` over
        *platforms*.
        """
        folders = folder_platforms(platforms)
        self._refile_records(rows, folders)
        self._refile_picks(folders)

    def _refile_records(self, rows: Sequence[Mapping[str, Any]], folders: Mapping[str, str]) -> None:
        """Move each download record to its platform, in one write UoW.

        A record whose new identity is already taken is moved only where the
        record there names the same file, and is then dropped as its duplicate;
        one naming another file stays where it is, so neither record's delete
        authority is lost.
        """
        with self._uow_factory() as uow:
            for record in list(uow.bios_files.iter_all()):
                target = refiled_record_platform(
                    record.platform_slug, record.file_name, record.firmware_id, rows, folders
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

    def _refile_picks(self, folders: Mapping[str, str]) -> None:
        picks = self._settings.get(_PICKS_SETTING)
        if not isinstance(picks, dict):
            return
        refiled = refiled_picks(picks, folders)
        if refiled is None:
            return
        self._settings[_PICKS_SETTING] = refiled
        self._settings_persister.save_settings()
        self._logger.info("Moved emulator picks filed under a firmware folder name to their platform")
