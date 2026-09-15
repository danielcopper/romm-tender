"""Moving one file to a path something may already occupy.

The one thing the two migrations in this package share. Both walk a list of
``(label, old_path, new_path, state_updater, kind)`` items and have to answer
the same three questions per item — the source is gone, the destination is
taken, or the move is clean — while accumulating per-kind counts and per-item
errors rather than aborting the run.

What they do NOT share is how a taken destination is decided, so that stays
with each caller: the home migration is handed a ``conflict_strategy`` chosen by
the user, and the save-sort migration resolves newest-wins in place before it
ever gets here.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging

    from services.protocols import MigrationFileStore


class FileMover:
    """Performs one migration item's move and records what happened.

    Holds no migration state: the counts and errors it appends to are owned by
    the run that passes them in, so one mover serves every item of every run.
    """

    def __init__(self, *, file_store: MigrationFileStore, logger: logging.Logger) -> None:
        self._migration_file_store = file_store
        self._logger = logger

    def migrate_single_item(self, label, old_path, new_path, state_updater, kind, conflict_strategy, counts, errors):
        """Migrate a single file/directory item. Updates counts and errors in place."""
        # A moved per-ROM directory ("rom_dir") counts as one migrated ROM, same
        # as a single-file ROM ("rom") — both fold into the "rom" counter.
        count_key = "rom" if kind in ("rom", "rom_dir") else kind

        if not self._migration_file_store.exists(old_path):
            if self._migration_file_store.exists(new_path):
                state_updater()
                if count_key:
                    counts[count_key] = counts.get(count_key, 0) + 1
            else:
                # The record's file exists at no known location and no
                # destination — a lost install/BIOS, surfaced in the result
                # so a chained migration never silently reports "nothing to do"
                # while data is gone (#1042).
                counts["missing"] = counts.get("missing", 0) + 1
            return

        if self._migration_file_store.exists(new_path):
            self._migrate_conflict_item(
                label,
                old_path,
                new_path,
                state_updater,
                conflict_strategy,
                count_key,
                counts,
                errors,
            )
            return

        try:
            self._migration_file_store.make_dirs(os.path.dirname(new_path))
            self._migration_file_store.move(old_path, new_path)
            state_updater()
            if count_key:
                counts[count_key] = counts.get(count_key, 0) + 1
            self._logger.info(f"Migrated {kind}: {old_path} -> {new_path}")
        except OSError as e:
            errors.append(f"{label}: {e}")
            self._logger.error(f"Migration failed: {old_path}: {e}")

    def _migrate_conflict_item(
        self,
        label,
        old_path,
        new_path,
        state_updater,
        conflict_strategy,
        count_key,
        counts,
        errors,
    ):
        """Handle migration when destination already exists."""
        if conflict_strategy == "overwrite":
            try:
                if self._migration_file_store.is_dir(new_path):
                    self._migration_file_store.remove_tree(new_path)
                else:
                    self._migration_file_store.remove_file(new_path)
                self._migration_file_store.make_dirs(os.path.dirname(new_path))
                self._migration_file_store.move(old_path, new_path)
                state_updater()
                if count_key:
                    counts[count_key] = counts.get(count_key, 0) + 1
                self._logger.info(f"Migration overwrite: {old_path} -> {new_path}")
            except OSError as e:
                errors.append(f"{label}: {e}")
                self._logger.error(f"Migration overwrite failed: {old_path}: {e}")
        else:
            # skip — keep destination, update state
            state_updater()
            if count_key:
                counts[count_key] = counts.get(count_key, 0) + 1
            self._logger.info(f"Migration skip (exists): {new_path}")

    @staticmethod
    def build_migration_result(counts, errors):
        """Build the result dict from migration counts and errors.

        ``missing`` (records whose file was found at no known location — see
        :meth:`migrate_single_item`) is surfaced additively in both the message
        and the ``missing_count`` field so a chained migration reports lost
        files honestly instead of a bare "No files to migrate" success; it does
        not, on its own, make the migration a failure (only ``errors`` do).
        """
        parts = []
        if counts["rom"]:
            parts.append(f"{counts['rom']} ROM(s)")
        if counts["bios"]:
            parts.append(f"{counts['bios']} BIOS")
        if counts["save"]:
            parts.append(f"{counts['save']} save(s)")
        msg = f"Migrated {', '.join(parts)}" if parts else "No files to migrate"
        missing = counts.get("missing", 0)
        if missing:
            msg += f"; {missing} file(s) missing (not found at any known location)"
        if errors:
            msg += f" ({len(errors)} error(s))"
        return {
            "success": len(errors) == 0,
            "message": msg,
            "roms_moved": counts["rom"],
            "bios_moved": counts["bios"],
            "saves_moved": counts["save"],
            "missing_count": missing,
            "errors": errors,
        }
