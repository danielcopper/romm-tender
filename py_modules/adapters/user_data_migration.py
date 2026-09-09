"""Single owner of the move that takes the user's data out of the plugin's reach.

Everything the data-location migration does to the filesystem lives here:
probing the older locations and the new roots, copying a half through a staging
directory that is renamed into place, the note left behind in a source, and the
small file that carries a user's choice across a restart. The decision itself is
not here — ``domain.user_data_location`` owns the ladder and is handed the facts
this module gathers.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from models.data_location import UserDataLocations

from domain.iso_time import epoch_to_iso, epoch_to_local_stamp
from domain.user_data_location import DATA_HALF, SETTINGS_HALF, SourceFacts, plan_migration

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Sequence

    from models.data_location import SourceDescription

# Suffix of the directory a half is copied into before it is renamed onto its
# root. It sits beside the root so the rename stays within one filesystem, which
# is what makes it atomic: a copy interrupted by a Steam restart or a standby
# leaves this directory behind and never a target root that looks finished.
_STAGING_SUFFIX = ".migrating"

_NOTE_FILENAME = "README.txt"


class _ClockPort(Protocol):
    """Minimal wall-clock port this adapter consumes.

    Adapters must not import ``services.protocols`` (import-linter forbids
    ``adapters -> services``), so this declares the single Clock method the
    adapter needs — ``time()`` for the date in the note left in a source.
    """

    def time(self) -> float: ...


@dataclass(frozen=True)
class SourceLocation:
    """One older location's two halves, named by the folder they sit under."""

    name: str
    settings_dir: str
    data_dir: str


@dataclass(frozen=True)
class _RootProbe:
    """What asking one target root whether it already holds anything answered."""

    occupied: bool
    error: str | None

    @property
    def settled(self) -> bool:
        """The half already lives at its root — nothing to do, and safe to use."""
        return self.error is None and self.occupied

    @property
    def fillable(self) -> bool:
        """The root is empty and readable, so this start may fill this half."""
        return self.error is None and not self.occupied


class UserDataMigrationAdapter:
    """Moves the user's data to the plugin's own roots, and reports where it ended up."""

    def __init__(
        self,
        *,
        settings_root: str,
        data_root: str,
        fallback_settings_dir: str,
        fallback_data_dir: str,
        sources: Sequence[SourceLocation],
        answer_path: str,
        db_filename: str,
        settings_filename: str,
        clock: _ClockPort,
        logger: logging.Logger,
    ) -> None:
        self._settings_root = settings_root
        self._data_root = data_root
        self._fallback_settings_dir = fallback_settings_dir
        self._fallback_data_dir = fallback_data_dir
        self._sources = tuple(sources)
        # Normalised so the copy's per-directory skip can compare it as a string
        # against the paths ``copytree`` hands back.
        self._answer_path = os.path.normpath(answer_path)
        self._db_filename = db_filename
        self._settings_filename = settings_filename
        self._clock = clock
        self._logger = logger

    # ------------------------------------------------------------------
    # Start-up migration
    # ------------------------------------------------------------------

    def migrate(self) -> UserDataLocations:
        """Bring the user's data to the plugin's own roots, and say where it is now.

        Never raises: a migration that cannot finish leaves the plugin running
        from the Decky-assigned directories with the reason on the answer, which
        is the one outcome that is never mistaken for data loss. Starting
        against an empty new root while the data sits in a source would be.
        """
        try:
            return self._run()
        except Exception as e:
            self._logger.exception("Could not move the plugin's data to its own directories")
            return self._degraded(str(e) or e.__class__.__name__)

    def _run(self) -> UserDataLocations:
        # Each root is asked on its own, and a root that will not answer takes
        # only its own half down with it. Probing both under one guard would let
        # an unreadable data root un-settle a settings half that had already
        # migrated — the plugin would then read and write the pre-migration
        # settings file, and the next good start would read the new root again
        # with those edits silently gone.
        probes = {
            SETTINGS_HALF: self._probe_root(self._settings_root),
            DATA_HALF: self._probe_root(self._data_root),
        }
        recorded = self._read_answer()
        plan = plan_migration(
            settings_target_occupied=not probes[SETTINGS_HALF].fillable,
            data_target_occupied=not probes[DATA_HALF].fillable,
            recorded_answer=recorded,
            probe_sources=self._probe_sources,
        )

        done = {half for half, probe in probes.items() if probe.settled}
        failures = [error for half in (SETTINGS_HALF, DATA_HALF) if (error := probes[half].error) is not None]

        moved: list[str] = []
        if plan.choice_required:
            self._logger.info("Two older installs both hold a library; waiting for the user to pick one")
        elif plan.outstanding:
            source = self._source_named(plan.source_name)
            for half in plan.outstanding:
                try:
                    copied_from = self._fill(half, source)
                except OSError as e:
                    self._logger.warning(f"Could not move the {half} half of the plugin's data: {e}")
                    failures.append(str(e))
                    continue
                done.add(half)
                if copied_from is not None:
                    moved.append(f"{half} from {copied_from} to {self._root_for(half)}")
        if moved:
            self._logger.info(f"Moved the plugin's data to its own directories: {'; '.join(moved)}")
        # An answer is dropped only once nothing is left for it to name: while
        # any half is still outstanding the next start needs it to reach the
        # same location this one was heading for.
        if not failures and not plan.choice_required and (recorded is not None or plan.outstanding):
            self._clear_answer()
        return UserDataLocations(
            settings_dir=self._settings_root if SETTINGS_HALF in done else self._fallback_settings_dir,
            data_dir=self._data_root if DATA_HALF in done else self._fallback_data_dir,
            choice_required=plan.choice_required,
            failure="; ".join(failures) or None,
        )

    def _degraded(self, failure: str | None) -> UserDataLocations:
        """Fall back to the Decky-assigned directory for both halves.

        The answer when nothing at all could be established. A half-by-half
        answer is :meth:`_run`'s job; this is the backstop for an error that
        escaped it entirely.
        """
        return UserDataLocations(
            settings_dir=self._fallback_settings_dir,
            data_dir=self._fallback_data_dir,
            choice_required=False,
            failure=failure,
        )

    def _source_named(self, name: str | None) -> SourceLocation | None:
        return next((source for source in self._sources if source.name == name), None)

    def _root_for(self, half: str) -> str:
        return self._settings_root if half == SETTINGS_HALF else self._data_root

    def _fill(self, half: str, source: SourceLocation | None) -> str | None:
        """Put one half at its root, copying from *source* when there is one.

        Returns the directory the half was copied FROM, or ``None`` where there
        was nothing to copy and the root was simply created — which is what
        separates a real move, worth a line in the log, from a fresh install.
        """
        origin = None
        if source is not None:
            origin = source.settings_dir if half == SETTINGS_HALF else source.data_dir
        return self._copy_into_place(origin, self._root_for(half))

    def _copy_into_place(self, origin: str | None, root: str) -> str | None:
        """Copy *origin* into a staging directory beside *root*, then rename it on.

        The whole directory comes along — every backup, every cache, every file
        a later version may still want; a curated list of what travels would rot
        the moment a file was added, and copying everything instead was measured
        at 4.11 s for 268 MB on the maintainer's Steam Deck. The exceptions are
        this adapter's own two files, which are bookkeeping about the move
        rather than something the move is for.
        """
        staging = root + _STAGING_SUFFIX
        # A half whose source is simply not there still gets its root: an empty
        # one is the right answer for a fresh install, and for a chosen location
        # that never had that half.
        copied_from = origin if origin is not None and os.path.isdir(origin) else None
        os.makedirs(os.path.dirname(root), exist_ok=True)
        self._clear_staging(staging)
        if copied_from is not None:
            shutil.copytree(copied_from, staging, symlinks=True, ignore=self._ignore_our_own_files(copied_from))
        else:
            os.makedirs(staging)
        os.rename(staging, root)
        if copied_from is not None:
            self._leave_note(copied_from, root)
        return copied_from

    def _ignore_our_own_files(self, origin: str) -> Callable[[str, list[str]], set[str]]:
        """Keep the two files this adapter writes itself out of the copy of *origin*.

        The recorded answer lives in the Decky-assigned runtime directory, which
        is one of the locations being copied FROM, so without this the answer to
        a question already settled would be carried into the new data root and
        sit there for good. It is matched by full path, so only the one file
        this adapter wrote is dropped.

        The note is the same idea and worse if it travels: it says the folder
        holding it is the copy left behind and safe to delete, which is a lie
        anywhere but in a source — and a source migrated out of once already
        carries one, so the next migration out of it would carry that sentence
        into the live root. It is matched by name at the top level of *origin*,
        which is the only place :meth:`_leave_note` ever writes it.
        """
        top = os.path.normpath(origin)

        def ignore(directory: str, names: list[str]) -> set[str]:
            # A note the user wrote themselves is dropped along with ours: the
            # copy removes nothing, so their file stays where it is, and reading
            # a file's content to guess who wrote it would be worse than losing
            # a copy of a name this code writes itself.
            at_top = os.path.normpath(directory) == top
            return {
                name
                for name in names
                if os.path.normpath(os.path.join(directory, name)) == self._answer_path
                or (at_top and name == _NOTE_FILENAME)
            }

        return ignore

    @staticmethod
    def _clear_staging(staging: str) -> None:
        """Remove whatever a previous, interrupted start left at the staging path."""
        if os.path.islink(staging) or os.path.isfile(staging):
            os.unlink(staging)
            return
        shutil.rmtree(staging, ignore_errors=True)

    def _leave_note(self, origin: str, root: str) -> None:
        """Explain, in the directory the copy came from, that it is now the spare.

        Created exclusively, never opened for writing: a file of that name here
        is the user's, and this cut modifies a source nowhere else — starting
        with the one file that says the source is safe to delete would be the
        worst place to make an exception. ``O_EXCL`` also refuses a symlink of
        that name, so the note cannot be written through one.

        Best effort throughout: a note that cannot be written never fails a
        migration that has already succeeded.
        """
        text = (
            "Tender moved your data\n"
            "======================\n"
            "\n"
            "Tender used to keep this data here, because Decky names a plugin's\n"
            "folders after the plugin's own folder — so renaming the plugin moved\n"
            "your data with it.\n"
            "\n"
            f"  Copied on: {epoch_to_local_stamp(self._clock.time())}\n"
            f"  Copied to: {root}\n"
            "\n"
            "Nothing was removed: this folder is the copy left behind. Tender no\n"
            "longer reads your library or settings from here, so it is safe to delete\n"
            "once you are happy that everything is still there.\n"
        )
        path = os.path.join(origin, _NOTE_FILENAME)
        try:
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            self._logger.info(f"Left {path} alone; something is already there")
            return
        except OSError as e:
            self._logger.warning(f"Could not leave a note in {origin}: {e}")
            return
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as note:
                note.write(text)
        except OSError as e:
            self._logger.warning(f"Could not write the note in {origin}: {e}")

    # ------------------------------------------------------------------
    # Probes
    # ------------------------------------------------------------------

    def _probe_root(self, root: str) -> _RootProbe:
        """Ask one target root whether it already holds anything.

        A root that is not there holds nothing, which is the ordinary answer on
        the start that migrates. Anything else going wrong is reported rather
        than guessed: reading it as empty would copy over data, and reading it as
        occupied would point the plugin at a directory that would not answer.
        """
        try:
            with os.scandir(root) as entries:
                return _RootProbe(occupied=next(iter(entries), None) is not None, error=None)
        except FileNotFoundError:
            return _RootProbe(occupied=False, error=None)
        except OSError as e:
            self._logger.warning(f"Could not tell whether {root} already holds data: {e}")
            return _RootProbe(occupied=False, error=str(e))

    def _probe_sources(self) -> list[SourceFacts]:
        """Look at both older locations. Called only when the ladder gets that far."""
        return [self._probe(source) for source in self._sources]

    def _probe(self, source: SourceLocation) -> SourceFacts:
        return SourceFacts(
            name=source.name,
            present=os.path.isdir(source.settings_dir) or os.path.isdir(source.data_dir),
            has_library=self._has_library(source.data_dir),
            settings_mtime=self._settings_mtime(source.settings_dir),
        )

    def _has_library(self, data_dir: str) -> bool:
        """Whether this location's database holds at least one ROM.

        Opened read-only, and every way of not getting an answer reads as "no
        library": the file may not be there, may be a database from before the
        table existed, or may not open at all. The distinction that matters is
        the one against mere existence — the plugin creates an empty database on
        its first start, so a file is not evidence that a user ever built
        anything here.
        """
        db_path = os.path.join(data_dir, self._db_filename)
        if not os.path.isfile(db_path):
            return False
        uri = f"file:{urllib.parse.quote(db_path)}?mode=ro"
        try:
            with contextlib.closing(sqlite3.connect(uri, uri=True)) as connection:
                row = connection.execute("SELECT COUNT(*) FROM roms").fetchone()
        except (sqlite3.Error, OSError) as e:
            self._logger.warning(f"Could not read the library in {data_dir}: {e}")
            return False
        return bool(row) and int(row[0]) >= 1

    def _settings_mtime(self, settings_dir: str) -> float | None:
        try:
            return os.stat(os.path.join(settings_dir, self._settings_filename)).st_mtime
        except OSError:
            return None

    # ------------------------------------------------------------------
    # The choice the plugin cannot make for itself
    # ------------------------------------------------------------------

    def describe_sources(self) -> list[SourceDescription]:
        """Describe EVERY older location, for the choice modal.

        Every one, including a location that is not on disk: the modal asks which
        of two copies to keep, and a list quietly reduced to one reads as a
        question with a single answer. A location that has gone since the start
        that raised the question says so instead.

        The size and date are the data half's: that is the library the reader is
        choosing between, and the settings beside it are a few kilobytes that
        would only blur the difference. Both are absent where they could not be
        established — an approximate size is worse than none when the size is
        what the reader is choosing on.
        """
        described: list[SourceDescription] = []
        for source in self._sources:
            present = os.path.isdir(source.data_dir)
            size, newest = self._measure(source.data_dir) if present else (None, None)
            described.append(
                {
                    "source": source.name,
                    "path": source.data_dir,
                    "present": present,
                    "size_bytes": size,
                    "changed_at": self._iso_or_none(newest),
                },
            )
        return described

    @staticmethod
    def _measure(directory: str) -> tuple[int | None, float | None]:
        """Total bytes below *directory* and the newest modification time in it.

        ``(None, None)`` where the reading could not be completed. ``os.walk``
        swallows a directory it cannot open by default and simply yields less,
        so the total would come back smaller than the truth with nothing saying
        so — which is the one failure a reader choosing on size cannot detect.
        """
        incomplete = False

        def _record(_error: OSError) -> None:
            nonlocal incomplete
            incomplete = True

        try:
            newest: float | None = os.stat(directory).st_mtime
        except OSError:
            newest = None
            incomplete = True
        total = 0
        for parent, _dirs, files in os.walk(directory, onerror=_record, followlinks=False):
            for name in files:
                try:
                    stat = os.lstat(os.path.join(parent, name))
                except OSError:
                    incomplete = True
                    continue
                total += stat.st_size
                if newest is None or stat.st_mtime > newest:
                    newest = stat.st_mtime
        if incomplete:
            return None, None
        return total, newest

    @staticmethod
    def _iso_or_none(epoch: float | None) -> str | None:
        """Render an epoch as ISO-8601, or nothing at all.

        A modification time far outside the range ``datetime`` can hold is what
        a corrupt inode looks like; it is not worth failing a modal over.
        """
        if epoch is None:
            return None
        try:
            return epoch_to_iso(epoch)
        except (OSError, OverflowError, ValueError):
            return None

    def record_answer(self, name: str) -> None:
        """Record which location the next start copies from.

        The copy cannot happen while this answer is given: the plugin is running
        from one of the two candidates with its database open, and copying a live
        SQLite file risks a torn copy. So the answer is written where the next
        start will look for it — the Decky-assigned runtime directory, which is
        the directory Decky hands THIS install whichever candidate it happens to
        be running from, and so the one place a start can find the answer before
        it has decided anything. It is one of the candidates' own data
        directories, which is why the copy skips this one file by its path.
        """
        if self._source_named(name) is None:
            raise ValueError(f"{name} is not one of this plugin's older data locations")
        os.makedirs(os.path.dirname(self._answer_path), exist_ok=True)
        temporary = f"{self._answer_path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"source": name}, handle)
        os.replace(temporary, self._answer_path)

    def _read_answer(self) -> str | None:
        try:
            with open(self._answer_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return None
        recorded = payload.get("source") if isinstance(payload, dict) else None
        return recorded if isinstance(recorded, str) else None

    def _clear_answer(self) -> None:
        """Drop a recorded answer the migration it named has now carried out."""
        with contextlib.suppress(OSError):
            os.unlink(self._answer_path)
