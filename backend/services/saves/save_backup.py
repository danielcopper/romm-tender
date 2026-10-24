"""The save backup: a local save file moved into ``.romm-backup`` before anything destroys it.

The one funnel through which every path that replaces or removes a save file
takes its recoverable copy — the sync's download, the slot steps, following a
moved save directory, the adoption renamer and the removed-game cleanup. The
names, their time stamp and which backups go are ``domain/save_backup.py``'s;
the moves are this module's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.save_backup import BACKUP_DIR_NAME, backup_name, backup_timestamp, select_backups_to_prune

if TYPE_CHECKING:
    from models.prune import MutationOutcome, SourceClaim
    from services.protocols import Clock, SaveFileStore


@dataclass(frozen=True)
class SaveBackupConfig:
    """Frozen wiring bundle handed to ``SaveBackup.__init__``.

    Holds the save-file store the moves go through and the clock a backup's
    name is stamped by.
    """

    save_file_store: SaveFileStore
    clock: Clock


class SaveBackup:
    """Backs a local save file up into the ``.romm-backup`` folder beside it.

    Holds no state and takes no lock: a caller that changes a ROM's save state
    holds that ROM's ``rom_lock`` itself.
    """

    def __init__(self, *, config: SaveBackupConfig) -> None:
        self._save_file_store = config.save_file_store
        self._clock = config.clock

    def back_up_save_file(self, saves_dir: str, filename: str, *, keep: int) -> bool:
        """Move *filename* in *saves_dir* aside into ``.romm-backup`` before it is destroyed.

        The backup lands at ``<saves_dir>/.romm-backup/<name>_<ts>[_<n>]<ext>``
        (``<ts>`` from the injected clock). A same-second collision appends a
        ``_<n>`` counter so a multi-file slot backed up within one second never
        overwrites an earlier backup, and the newest *keep* backups of the file
        are kept — older ones are removed to bound the recovery net's disk use.
        The backup written by this call is never removed in its own call, so
        under sustained same-second churn the folder may briefly hold one extra
        copy (*keep* + 1) — destroying the just-saved file to honour the cap
        would defeat the backup. Returns ``True`` when a file was moved,
        ``False`` when there was nothing at *filename* to back up. Raises
        ``ValueError`` when the backup folder is a link or lies outside
        *saves_dir*, and the store's ``OSError`` when a move fails.
        """
        local_path = os.path.join(saves_dir, filename)
        if not self._save_file_store.is_file(local_path):
            return False
        backup_dir = os.path.join(saves_dir, BACKUP_DIR_NAME)
        if self._save_file_store.is_symlink(backup_dir) or not self._save_file_store.is_within(backup_dir, saves_dir):
            raise ValueError(f"Unsafe save backup directory: {backup_dir}")
        self._save_file_store.make_dirs(backup_dir)
        existing = set(self._save_file_store.listdir(backup_dir))
        backup = self._backup_name(filename, existing)
        self._save_file_store.rename(local_path, os.path.join(backup_dir, backup))
        existing.add(backup)
        for stale in select_backups_to_prune(filename, list(existing), keep):
            if stale == backup:
                # A freed base name reused for this newer backup sorts oldest;
                # removing it would destroy the save just backed up.
                continue
            self._save_file_store.remove_file(os.path.join(backup_dir, stale))
        return True

    def back_up_claimed_save_file(
        self,
        saves_dir: str,
        filename: str,
        *,
        claim: SourceClaim,
        safe_root: str,
    ) -> MutationOutcome:
        """Durably move one exact claimed save into ``.romm-backup`` through anchored directories.

        The removed-game cleanup's backup: the folder is made through
        ``ensure_directory`` under *safe_root* and the save moves only through
        ``rename_claimed`` against *claim*, so a path that is no longer the
        claimed file is refused rather than moved. Every backup is kept — the
        cleanup bounds none. Answers the store's outcome of the move.
        """
        local_path = os.path.join(saves_dir, filename)
        backup_dir = os.path.join(saves_dir, BACKUP_DIR_NAME)
        self._save_file_store.ensure_directory(backup_dir, safe_root)
        backup = self._backup_name(filename, set(self._save_file_store.listdir(backup_dir)))
        return self._save_file_store.rename_claimed(
            local_path,
            os.path.join(backup_dir, backup),
            safe_root,
            claim,
        )

    def _backup_name(self, filename: str, existing: set[str]) -> str:
        """A name for a backup of *filename* taken now, free among *existing*."""
        return backup_name(filename, backup_timestamp(self._clock.now()), existing)
