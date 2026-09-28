"""LeftoverTmpCleanupService — the start-up removal of partial transfer files.

A download writes its bytes to a ``.tmp`` (or ``.zip.tmp``) beside its target
and renames it into place once it is whole, and a BIOS download does the same
under the BIOS directory. A backend that stopped mid-transfer leaves that
partial behind; this service removes every one it finds under the ROM and BIOS
directories when the backend starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.rom_files import TMP_EXT, ZIP_TMP_EXT

if TYPE_CHECKING:
    import logging

    from services.protocols import DownloadFileStore, RetroDeckPaths


@dataclass(frozen=True)
class LeftoverTmpCleanupServiceConfig:
    """Frozen wiring bundle handed to ``LeftoverTmpCleanupService.__init__``.

    Holds the logger, the file store the partials are listed and removed
    through, and the RetroDECK paths that name the ROM and BIOS directories.
    """

    logger: logging.Logger
    download_file_store: DownloadFileStore
    retrodeck_paths: RetroDeckPaths


class LeftoverTmpCleanupService:
    """Removes the partial transfer files a stopped backend left under the ROM and BIOS directories."""

    def __init__(self, *, config: LeftoverTmpCleanupServiceConfig) -> None:
        self._logger = config.logger
        self._download_file_store = config.download_file_store
        self._retrodeck_paths = config.retrodeck_paths

    def _remove_tmp_files(self, paths: list[str]) -> int:
        """Remove each path in *paths*, logging a warning on per-file failure.

        Returns the count of successful removals. Mirrors the
        SteamGridService cache-prune pattern: service owns the loop +
        ``try``/``except`` + ``logger.warning`` so the operational
        signal on each failure is preserved instead of being swallowed
        inside the adapter.
        """
        removed = 0
        for path in paths:
            try:
                self._download_file_store.remove_file(path)
                removed += 1
            except OSError as e:
                self._logger.warning(f"Failed to remove tmp file {path}: {e}")
        return removed

    def _clean_rom_tmp_files(self):
        """Remove leftover .tmp and .zip.tmp files from ROM directories."""
        roms_base = self._retrodeck_paths.roms_path()
        if not roms_base:
            return 0
        paths = self._download_file_store.walk_files_matching_suffixes(roms_base, (TMP_EXT, ZIP_TMP_EXT))
        return self._remove_tmp_files(paths)

    def _clean_bios_tmp_files(self):
        """Remove leftover .tmp files from BIOS directory."""
        bios_base = self._retrodeck_paths.bios_path()
        if not bios_base:
            return 0
        paths = self._download_file_store.walk_files_matching_suffixes(bios_base, (TMP_EXT,))
        return self._remove_tmp_files(paths)

    def cleanup_leftover_tmp_files(self):
        """Remove leftover .tmp and .zip.tmp files from ROM and BIOS directories on startup.

        v1 note: this also deletes the ``.tmp`` of a download paused before a
        plugin reload. That is acceptable — the in-memory download queue does not
        survive a reload either, so a paused download could not have been resumed
        across one regardless; the next download restarts from scratch.
        """
        cleaned = self._clean_rom_tmp_files() + self._clean_bios_tmp_files()
        if cleaned:
            self._logger.info(f"Cleaned {cleaned} leftover tmp file(s)")
