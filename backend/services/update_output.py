"""UpdateOutputService — what the installer said about an update that failed, read back from the journal.

Owns answering which run of the installer belongs to the failure the panel
shows — the attempt of this process's, or the installer's record — and, after
a rollback, which run of this program was the failed version trying to start.
Which lines of a run are shown, and how, is ``domain/update_output.py``'s; the
journal itself is behind a seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.update_install import INSTALLER_UNIT
from domain.update_outcome import UpdateFailureKind, standing_update_failure
from domain.update_output import (
    REPLACED_RUN_LINES,
    RUN_SPAN_SECONDS,
    SERVICE_UNIT,
    OutputGap,
    first_run_from,
    installer_section,
    last_invocation,
    output_section,
    run_around,
    runs_other_than,
    utc_stamp_seconds,
)

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.update_outcome import UpdateFailure
    from domain.update_output import JournalEntry
    from services.protocols import FailedInstallerStartFn, JournalEntriesFn, UpdateFailureFn


@dataclass(frozen=True)
class UpdateOutputServiceConfig:
    """Frozen wiring bundle handed to ``UpdateOutputService.__init__``.

    Carries the running version, the installer's record and the latest
    attempt's installer start as the two failures the output belongs to, the
    journal seam, and the runtime infrastructure.
    """

    current_version: str
    read_update_failure: UpdateFailureFn
    failed_installer_started_at: FailedInstallerStartFn
    journal: JournalEntriesFn
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class UpdateOutputService:
    """Reads what the installer, and after a rollback the version it tried, printed for one failed update."""

    def __init__(self, *, config: UpdateOutputServiceConfig) -> None:
        self._current_version = config.current_version
        self._read_update_failure = config.read_update_failure
        self._failed_installer_started_at = config.failed_installer_started_at
        self._journal = config.journal
        self._loop = config.loop
        self._logger = config.logger

    async def get_update_output(self, rolled_back_at: object) -> dict[str, Any]:
        """What the installer printed for one failed update, read from the journal.

        *rolled_back_at* names the installer's record by its stamp; ``None``
        names this process's latest attempt, where it failed after its
        installer ran. The run shown for a record is the installer's run that
        was going on at its stamp, and for an attempt the first that began at
        or after its installer was started — never merely the latest run.

        Returns ``{"success": True, "ran_at", "installer", "new_version",
        "missing"}``. ``installer`` is ``{"lines", "earlier"}`` — the last
        lines of that run with the admission token hidden and each of its rows
        only in the last state it printed, and how many of the lines that
        folding kept are left out before them — and ``ran_at`` when the run
        began, in epoch seconds. After a rollback ``new_version`` is the same
        shape, unfolded, over this program's own journal from the installer's
        start to the record's stamp, the lines of the run the installer found
        already going left out; it is ``None`` for any other failure or where
        no such line is left. Where the journal holds no such run,
        ``installer`` and ``ran_at`` are ``None`` and ``missing`` says why:
        ``"rotated"`` where the journal no longer reaches back that far, and
        where it does, ``"terminal"`` for a record — the installer was run by
        hand — and ``"empty"`` for an attempt, whose installer this program
        started as a unit. The canonical failure shape answers ``not_found``
        where no such failure stands, ``invalid_value`` for an argument of
        another type, and ``journal_unreadable`` where the journal could not be
        read; the first two are logged, since a panel asks only about a failure
        it shows.
        """
        if rolled_back_at is not None and not isinstance(rolled_back_at, str):
            self._logger.warning(f"update: the installer's output was asked for with {rolled_back_at!r}")
            return {"success": False, "reason": "invalid_value", "message": "Invalid record"}
        try:
            if rolled_back_at is None:
                started_at = self._failed_installer_started_at()
                at = utc_stamp_seconds(started_at) if started_at is not None else None
                if at is None:
                    return self._nothing_to_show("the latest attempt did not fail after its installer ran")
                return await self._loop.run_in_executor(None, self._attempt_output_io, at)
            return await self._loop.run_in_executor(None, self._record_output_io, rolled_back_at)
        except OSError as e:
            self._logger.warning(f"update: the journal could not be read for the installer's output: {e!r}")
            return {"success": False, "reason": "journal_unreadable", "message": "The journal could not be read"}

    def _attempt_output_io(self, started_at: float) -> dict[str, Any]:
        """The installer's output for an attempt whose installer this program started at *started_at*.

        It was started as a unit, so where the journal reaches back that far
        and holds no run of it, the unit left nothing there.
        """
        run = first_run_from(self._journal(INSTALLER_UNIT, since=started_at), started_at)
        if run is None:
            return _missing(OutputGap.EMPTY if self._reaches_back_to_io(started_at) else OutputGap.ROTATED)
        return _found(run, None)

    def _record_output_io(self, rolled_back_at: str) -> dict[str, Any]:
        failure = standing_update_failure(self._read_update_failure(), self._current_version)
        at = utc_stamp_seconds(rolled_back_at)
        if failure is None or failure.rolled_back_at != rolled_back_at or at is None:
            return self._nothing_to_show(f"no standing record is stamped {rolled_back_at}")
        run = run_around(self._journal(INSTALLER_UNIT, since=at - RUN_SPAN_SECONDS, until=at + RUN_SPAN_SECONDS), at)
        if run is None:
            return _missing(OutputGap.TERMINAL if self._reaches_back_to_io(at) else OutputGap.ROTATED)
        return _found(run, self._failed_version_io(failure, run[0].at, at))

    def _reaches_back_to_io(self, at: float) -> bool:
        """Whether the journal holds any line at or before *at*, so a run there would still be in it."""
        return bool(self._journal(None, until=at, last=1))

    def _nothing_to_show(self, why: str) -> dict[str, Any]:
        self._logger.info(f"update: there is no failed update to show the installer's output of: {why}")
        return {"success": False, "reason": "not_found", "message": "No failed update to show the output of"}

    def _failed_version_io(self, failure: UpdateFailure, began: float, at: float) -> tuple[JournalEntry, ...] | None:
        """This program's own lines from *began*, the installer's start, to *at*, the rollback, after a rollback.

        The run already going when the installer started is the version it
        replaced, which it stops in that window; its lines are left out.
        """
        if failure.kind is not UpdateFailureKind.ROLLBACK:
            return None
        replaced = last_invocation(self._journal(SERVICE_UNIT, until=began, last=REPLACED_RUN_LINES))
        tried = runs_other_than(self._journal(SERVICE_UNIT, since=began, until=at), replaced)
        return tried or None


def _found(run: tuple[JournalEntry, ...], new_version: tuple[JournalEntry, ...] | None) -> dict[str, Any]:
    return {
        "success": True,
        "ran_at": run[0].at,
        "installer": installer_section(run).to_wire(),
        "new_version": output_section(new_version).to_wire() if new_version is not None else None,
        "missing": None,
    }


def _missing(gap: OutputGap) -> dict[str, Any]:
    return {"success": True, "ran_at": None, "installer": None, "new_version": None, "missing": gap.value}
