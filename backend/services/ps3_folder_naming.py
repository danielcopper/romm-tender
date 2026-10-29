"""Ps3FolderNamingService — naming the PS3 games already installed as folders as ES-DE scans them.

A one-time step (#2195 D27): a download names a PS3 game kept as a folder
``<game>.ps3`` (D11), and the resolver answers a folder without that name as no
launch file, so one installed before that name came in is renamed once, with
its install record following, and keeps starting without a new download. The
step stays in every later release, so an update that skips releases still
gets it; it is safe to run again, because a folder already named so is never
renamed.

Its completion is stamped in ``kv_config`` only after a pass that left no
folder it could still rename: a folder that is not there — a drive not
mounted, a RetroDECK move not yet followed — is asked about again at the next
start. A folder whose new name is taken keeps its name, and counts as done:
nothing is moved over another folder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.rom_files import ps3_folder_rename

if TYPE_CHECKING:
    import logging

    from services.protocols import DownloadFileStore, UnitOfWorkFactory

# The completion stamp: a derived marker about work this install finished, in
# ``kv_config`` beside the other cross-run markers (GLOSSARY.md, persistence
# boundary).
KV_PS3_FOLDERS_NAMED = "ps3_folders_named"


@dataclass(frozen=True)
class Ps3FolderNamingServiceConfig:
    """Frozen wiring bundle handed to ``Ps3FolderNamingService.__init__``."""

    uow_factory: UnitOfWorkFactory
    file_store: DownloadFileStore
    logger: logging.Logger


class Ps3FolderNamingService:
    """Renames the PS3 folder installs not yet named ``<game>.ps3``, once."""

    def __init__(self, *, config: Ps3FolderNamingServiceConfig) -> None:
        self._uow_factory = config.uow_factory
        self._file_store = config.file_store
        self._logger = config.logger

    def name_ps3_folders(self) -> None:
        """Rename every PS3 folder install not named as ES-DE scans it, and stamp the step done when none is left.

        Each folder is moved outside any Unit of Work, and its install record is
        relocated in a short one after the move, only while it still names the
        folder that was moved.
        """
        with self._uow_factory() as uow:
            if uow.kv_config.get(KV_PS3_FOLDERS_NAMED) == "1":
                return
            installs = list(uow.rom_installs.iter_all())
        left = 0
        for install in installs:
            rename = ps3_folder_rename(install.rom_dir, install.file_path)
            if rename is None or install.rom_dir is None:
                continue
            new_rom_dir, new_file_path = rename
            if not self._file_store.exists(install.rom_dir):
                self._logger.info(
                    f"PS3 folder of rom_id={install.rom_id} is not at {install.rom_dir}; asked again later"
                )
                left += 1
                continue
            if self._file_store.exists(new_rom_dir):
                self._logger.warning(
                    f"PS3 folder rename skipped: {new_rom_dir} already exists; keeping {install.rom_dir}"
                )
                continue
            try:
                self._file_store.move_dir(install.rom_dir, new_rom_dir)
            except OSError as e:
                self._logger.warning(f"PS3 folder rename of {install.rom_dir} failed: {e}; asked again later")
                left += 1
                continue
            self._relocate(install.rom_id, install.rom_dir, new_rom_dir, new_file_path)
            self._logger.info(f"Renamed the PS3 folder of rom_id={install.rom_id} to {new_rom_dir}")
        if left == 0:
            with self._uow_factory() as uow:
                uow.kv_config.set(KV_PS3_FOLDERS_NAMED, "1")

    def _relocate(self, rom_id: int, old_rom_dir: str, new_rom_dir: str, new_file_path: str) -> None:
        with self._uow_factory() as uow:
            install = uow.rom_installs.get(rom_id)
            if install is None or install.rom_dir != old_rom_dir:
                return
            install.relocate(new_rom_dir, new_file_path)
            uow.rom_installs.save(install)
