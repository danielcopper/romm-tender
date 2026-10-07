"""LeftoverTmpCleanupService — the start-up removal of partial transfer files.

A single-file download writes its bytes to a ``.tmp`` beside its target and
renames it into place once it is whole, and a BIOS download does the same under
the BIOS directory; a download RomM serves as a ZIP is written to a
``.zip.tmp``, which is extracted and then removed. A backend that stopped
mid-transfer leaves that partial behind; this service removes every one it finds
under RetroDECK's ROM and BIOS folders when the backend starts — the folders the
resolver names, and none at all while RetroDECK reports that its roots are
defaults (``RetroDeckFolders``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.retrodeck_folders import FolderRefused
from domain.rom_files import TMP_EXT, ZIP_TMP_EXT

if TYPE_CHECKING:
    import logging

    from services.protocols import DownloadFileStore, RetroDeckFolders


@dataclass(frozen=True)
class LeftoverTmpCleanupServiceConfig:
    """Frozen wiring bundle handed to ``LeftoverTmpCleanupService.__init__``.

    Holds the logger, the file store the partials are listed and removed
    through, and RetroDECK's folders, which name the ROM and BIOS roots.
    """

    logger: logging.Logger
    download_file_store: DownloadFileStore
    retrodeck_folders: RetroDeckFolders


class LeftoverTmpCleanupService:
    """Removes the partial transfer files a stopped backend left under the ROM and BIOS directories."""

    def __init__(self, *, config: LeftoverTmpCleanupServiceConfig) -> None:
        self._logger = config.logger
        self._download_file_store = config.download_file_store
        self._retrodeck_folders = config.retrodeck_folders

    def _remove_tmp_files(self, paths: list[str]) -> int:
        """Remove each path in *paths*, logging a warning on per-file failure.

        Returns the count of successful removals. The loop and its
        ``logger.warning`` live here rather than in the file store, so each
        failure is logged instead of being swallowed inside the adapter.
        """
        removed = 0
        for path in paths:
            try:
                self._download_file_store.remove_file(path)
                removed += 1
            except OSError as e:
                self._logger.warning(f"Failed to remove tmp file {path}: {e}")
        return removed

    def _clean_tmp_files(self, root: str | FolderRefused | None, suffixes: tuple[str, ...]) -> int:
        """Remove the files ending in *suffixes* under *root*; nothing where it names no folder."""
        if root is None or isinstance(root, FolderRefused):
            return 0
        return self._remove_tmp_files(self._download_file_store.walk_files_matching_suffixes(root, suffixes))

    def _clean_rom_tmp_files(self):
        """Remove leftover .tmp and .zip.tmp files from ROM directories."""
        return self._clean_tmp_files(self._retrodeck_folders.rom_root(), (TMP_EXT, ZIP_TMP_EXT))

    def _clean_bios_tmp_files(self):
        """Remove leftover .tmp files from BIOS directory."""
        return self._clean_tmp_files(self._retrodeck_folders.bios_folder(), (TMP_EXT,))

    def cleanup_leftover_tmp_files(self):
        """Remove leftover .tmp and .zip.tmp files from ROM and BIOS directories on startup.

        This also deletes the ``.tmp`` of a download paused before the backend
        stopped. The in-memory download queue does not survive a restart either,
        so a paused download could not have been resumed across one regardless;
        the next download starts from scratch.
        """
        cleaned = self._clean_rom_tmp_files() + self._clean_bios_tmp_files()
        if cleaned:
            self._logger.info(f"Cleaned {cleaned} leftover tmp file(s)")
