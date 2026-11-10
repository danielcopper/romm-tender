"""Moving Tender's older per-platform entries to the platforms RomM's listing names.

Download records and per-platform emulator picks are kept under a platform's
``fs_slug`` (#2452 D6, D7). Each reader that keeps or uses RomM's platform
listing hands what it read here, and an entry kept under another key — a slug,
as both were before, or a server folder name — is moved to the platform the
listing places it with, where it places it at all. Which entries that is, and
which stay, is ``domain/firmware_platforms.py``'s; an entry already under a
listed ``fs_slug`` is never moved, so every later reading moves nothing.

This is the one place a failed move is reported: the records and the picks are
moved independently, a failure of either is logged here, and no reader of the
listing ever sees one.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_platforms import ListedPlatforms, listed_platforms, pick_moves, refiled_record_folder

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Mapping, Sequence
    from contextlib import AbstractContextManager

    from domain.bios_file import BiosFile
    from services.protocols import ListingKeeper, SettingsPersister, UnitOfWork, UnitOfWorkFactory

# Where the per-platform emulator picks are stored in ``settings.json``.
_PICKS_SETTING = "platform_cores"


@dataclass(frozen=True)
class PlatformRefilingConfig:
    """Frozen wiring bundle handed to ``PlatformRefiling.__init__``.

    Holds the Unit-of-Work factory the download records are read and moved
    through, the live settings dict the per-platform picks sit in, its
    persister, the lock every writer of those picks holds while it changes and
    saves them, the lock one refile at a time holds — the readers of the
    listing run in executor threads of their own — the keeper of the listed
    platforms (bound late: it hands its own listing reads here), and the logger.
    """

    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    picks_lock: AbstractContextManager[object]
    refile_lock: AbstractContextManager[object]
    listing_keeper: Callable[[], ListingKeeper]
    logger: logging.Logger


class PlatformRefiling:
    """Moves download records and per-platform picks kept under another key to their platform's ``fs_slug``."""

    def __init__(self, *, config: PlatformRefilingConfig) -> None:
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._picks_lock = config.picks_lock
        self._logger = config.logger
        self._lock = config.refile_lock
        self._listing_keeper = config.listing_keeper
        self._listed_folders: frozenset[str] | None = None
        self._conflicts_told: set[tuple[str, str, str]] = set()
        self._failures_told: set[tuple[str, str, str]] = set()

    def listed_folders(self) -> frozenset[str] | None:
        """The ``fs_slug`` of every platform the last listing handed here names; ``None`` before the first."""
        return self._listed_folders

    def do_refile(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        """Move every entry the read *platforms* place elsewhere. Blocking; never raises.

        The listed platforms are kept first, then the download records and the
        picks are moved: each step on its own, so a failure of one is logged,
        gives back what that step had changed, and the others still run.
        """
        with self._lock:
            listed = self._run("Failed to read where older entries belong", lambda: listed_platforms(platforms))
            if listed is None:
                return
            self._listed_folders = listed.folders
            self._run("Failed to keep the listed platforms", lambda: self._listing_keeper().keep_listing(platforms))
            self._run(
                "Failed to move older BIOS download records to their platforms", lambda: self._refile_records(listed)
            )
            self._run("Failed to move older per-platform picks to their platforms", lambda: self._refile_picks(listed))

    def _run[T](self, failed: str, step: Callable[[], T]) -> T | None:
        """*step*'s answer, or ``None`` where it raised; its traceback is logged the first time per process."""
        try:
            return step()
        except Exception as e:
            told = (failed, type(e).__name__, str(e))
            self._logger.warning(f"{failed}: {type(e).__name__}: {e}", exc_info=told not in self._failures_told)
            self._failures_told.add(told)
            return None

    def _refile_records(self, listed: ListedPlatforms) -> None:
        """Move each download record to its platform, in one write UoW, so a failure midway moves none.

        Where the record's new identity is already taken, a record there naming
        the same file makes the moving one its duplicate, which is dropped; one
        naming another file leaves the moving record where it is, so neither
        record's delete authority is lost.
        """
        moved = 0
        with self._uow_factory() as uow:
            # Read whole first: iter_all yields from a live cursor over the table this loop deletes from and saves to.
            records = list(uow.bios_files.iter_all())
            for record in records:
                target = refiled_record_folder(record.platform_folder, record.file_name, record.firmware_id, listed)
                if target is None:
                    continue
                taken = uow.bios_files.get(target, record.file_name)
                if taken is not None and taken.file_path != record.file_path:
                    self._tell_conflict(record, target)
                    continue
                self._refile(uow, record, target, keep_existing=taken is not None)
                if taken is None:
                    moved += 1
        if moved:
            self._logger.info(f"Moved {moved} older BIOS download records to their platforms")

    def _tell_conflict(self, record: BiosFile, target: str) -> None:
        """Warn once per record and target in this process; every later run says it at debug level."""
        key = (record.platform_folder, record.file_name, target)
        log = self._logger.debug if key in self._conflicts_told else self._logger.warning
        self._conflicts_told.add(key)
        log(
            f"Kept the BIOS record {record.file_name} under {record.platform_folder}: "
            f"{target} already records another file of that name"
        )

    @staticmethod
    def _refile(uow: UnitOfWork, record: BiosFile, target: str, *, keep_existing: bool) -> None:
        uow.bios_files.delete(record.platform_folder, record.file_name)
        if not keep_existing:
            uow.bios_files.save(record.refiled_under(target))

    def _refile_picks(self, listed: ListedPlatforms) -> None:
        """Replace the picks with a moved copy in one assignment, under the lock every writer of them holds.

        The dict in place is never changed, so a save of the whole settings
        that iterates it meanwhile never sees it change size, and where the
        save fails the old dict is put back whole, so memory and file agree,
        and the exception propagates.
        """
        with self._picks_lock:
            picks = self._settings.get(_PICKS_SETTING)
            if not isinstance(picks, dict):
                return
            moves = pick_moves(picks, listed)
            if not moves:
                return
            moved = dict(picks)
            for key, folders in moves.items():
                for folder in folders:
                    moved[folder] = copy.deepcopy(picks[key])
            for key in moves:
                del moved[key]
            self._settings[_PICKS_SETTING] = moved
            try:
                self._settings_persister.save_settings()
            except Exception:
                self._settings[_PICKS_SETTING] = picks
                raise
        self._log_pick_moves(moves)

    def _log_pick_moves(self, moves: Mapping[str, tuple[str, ...]]) -> None:
        moved = {key: folders for key, folders in moves.items() if folders}
        dropped = sorted(key for key, folders in moves.items() if not folders)
        if moved:
            listed = "; ".join(f"{key} → {', '.join(folders)}" for key, folders in sorted(moved.items()))
            self._logger.info(f"Moved older per-platform picks to their platforms: {listed}")
        if dropped:
            self._logger.info(
                f"Dropped older per-platform picks every platform of their slug has its own of: {', '.join(dropped)}"
            )
