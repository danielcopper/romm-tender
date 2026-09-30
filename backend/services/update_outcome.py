"""UpdateOutcomeService — what the last update did, told as a toast once and a card until dismissed, or a card alone.

Owns the two outcomes a start can find: a version that moved — an update that
went through, or a return to an earlier release — which the panel raises as one
toast per process and shows as a card until the user dismisses it, and an update
that did not go through — rolled back by the installer, or refused by its
pre-install check before anything was replaced — which the panel shows until the user dismisses
that record or the installer removes it. What is announced, how the installer's
record is read, and whether it still stands live in ``domain/update_outcome.py``;
the record itself is behind a seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.update_install import INSTALLER_UNIT
from domain.update_outcome import UpdateFailureKind, announced_update, standing_update_failure

if TYPE_CHECKING:
    import asyncio
    import logging
    from typing import Any

    from domain.update_outcome import UpdateAnnouncement, UpdateFailure
    from services.protocols import SettingsPersister, UnitOfWorkFactory, UpdateFailureFn

# The version the previous start ran as, in kv_config: observed state kept only
# as a last-seen marker, which is what a start compares its own version with.
LAST_RUN_KEY = "last_run_version"

# The ``rolled_back_at`` of the record whose card the user waved away. User
# intent, so settings.json rather than kv_config (CONTEXT.md, Persistence
# boundary), and no default entry: absent already means nothing dismissed. It
# holds the record's stamp rather than a flag, so the next rollback raises the
# card again on its own.
FAILURE_DISMISSED_KEY = "update_failure_dismissed_at"


@dataclass(frozen=True)
class UpdateOutcomeServiceConfig:
    """Frozen wiring bundle handed to ``UpdateOutcomeService.__init__``.

    Carries the running version, the seam the installer's record is read
    through, the unit-of-work factory the last-run version is stored through,
    the live settings dict plus its persister for the dismissal, and the
    runtime infrastructure.
    """

    current_version: str
    read_update_failure: UpdateFailureFn
    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class UpdateOutcomeService:
    """Tells the user what the last update did: a toast once and a card until dismissed, or a card alone."""

    def __init__(self, *, config: UpdateOutcomeServiceConfig) -> None:
        self._current_version = config.current_version
        self._read_update_failure = config.read_update_failure
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._loop = config.loop
        self._logger = config.logger
        # For this process only: a Steam restart reloads the panel, and a panel that
        # asks again must not raise the same toast a second time, while the next start
        # compares against the version recorded below and owes nothing — so a backend
        # restart on the same version takes the card down too.
        self._announcement: UpdateAnnouncement | None = None
        self._toast_owed = False

    def note_start(self) -> None:
        """Compare the running version with the one the previous start recorded, and record it.

        Runs once, at start. A version that moved — to a later release, or back
        to an earlier one — is logged at INFO and owed to the panel as one
        announcement, its toast not yet raised; the installer's record of an
        update that did not go through is logged at WARNING whether or not it
        has been dismissed, because the log is where the reason is looked for —
        but only while it stands, and in the words of its kind. The first start
        that records a version announces nothing.
        """
        failure = self._standing_failure_io()
        with self._uow_factory() as uow:
            last_run = uow.kv_config.get(LAST_RUN_KEY)
            if last_run != self._current_version:
                uow.kv_config.set(LAST_RUN_KEY, self._current_version)
        self._announcement = announced_update(last_run, self._current_version, failure)
        self._toast_owed = self._announcement is not None
        if self._announcement is not None:
            self._logger.info(
                f"updated from {last_run} to {self._current_version}"
                if self._announcement.direction == "updated"
                else f"back on {self._current_version} after {last_run}"
            )
        if failure is not None:
            self._log_failure(failure)

    def _log_failure(self, failure: UpdateFailure) -> None:
        if failure.kind is UpdateFailureKind.CHECK:
            self._logger.warning(
                f"the pre-install check refused {failure.attempted_version} at {failure.rolled_back_at}: it does not "
                f"start, so nothing was changed and Tender is still on {failure.restored_version} — what the check "
                f"said is in journalctl --user -u {INSTALLER_UNIT}, or in the terminal the installer ran in"
            )
        elif failure.kind is UpdateFailureKind.UNKNOWN:
            self._logger.warning(
                f"the update to {failure.attempted_version} did not go through at {failure.rolled_back_at}; Tender is "
                f"still on {failure.restored_version} — what the installer said is in journalctl --user -u "
                f"{INSTALLER_UNIT}, or in the terminal the installer ran in"
            )
        else:
            self._logger.warning(
                f"the update to {failure.attempted_version} was rolled back at {failure.rolled_back_at}; "
                f"back on {failure.restored_version} — what {failure.attempted_version} logged when it tried to "
                "start is earlier in this log, or in journalctl --user -u romm-tender if it failed before logging"
            )

    async def get_update_outcome(self) -> dict[str, Any]:
        """Report what the panel owes the user about the last update.

        Returns ``{"announce_version", "announce_direction", "toast_owed",
        "failure", "failure_dismissed"}``. ``announce_version`` is the version
        this process moved to, until the user dismissed its card, ``None``
        otherwise, and ``announce_direction`` which way it moved — ``"updated"``
        or ``"back"``, ``None`` exactly when ``announce_version`` is.
        ``toast_owed`` says its toast has not been raised yet, and is ``False``
        whenever ``announce_version`` is ``None``. ``failure`` is the
        installer's record of an update that did not go through as
        ``{"attempted_version", "restored_version", "rolled_back_at", "kind"}``
        — ``kind`` ``"rollback"``, ``"check"`` or ``"unknown"`` — read afresh on every call so
        it goes when the installer removes it, and ``None`` where there is none
        or it no longer stands.
        ``failure_dismissed`` says the user waved away that exact record.
        """
        failure = await self._loop.run_in_executor(None, self._standing_failure_io)
        return {
            "announce_version": self._announcement.version if self._announcement is not None else None,
            "announce_direction": self._announcement.direction if self._announcement is not None else None,
            "toast_owed": self._announcement is not None and self._toast_owed,
            "failure": failure.to_wire() if failure is not None else None,
            "failure_dismissed": failure is not None and failure.rolled_back_at == self._dismissed_at(),
        }

    def acknowledge_update_toast(self) -> dict[str, Any]:
        """Record that the panel raised the announcement's toast, so a reloaded panel does not raise it again.

        Leaves the card standing. Idempotent. Returns ``{"success": True}``.
        """
        self._toast_owed = False
        return {"success": True}

    def dismiss_update_announcement(self) -> dict[str, Any]:
        """Record that the user waved away the announcement's card, for the rest of this process.

        Owes no toast afterwards either. Idempotent. Returns ``{"success": True}``.
        """
        self._announcement = None
        return {"success": True}

    def dismiss_update_failure(self, rolled_back_at: object) -> dict[str, Any]:
        """Record that the user waved away the card for the record stamped *rolled_back_at*.

        Per record and never global, so a later rollback raises the card again.
        Idempotent. Returns ``{"success": True}``, or the canonical failure shape
        for a stamp that is not a non-empty string.
        """
        if not isinstance(rolled_back_at, str) or not rolled_back_at:
            return {"success": False, "reason": "invalid_value", "message": "Invalid record"}
        self._settings[FAILURE_DISMISSED_KEY] = rolled_back_at
        self._settings_persister.save_settings()
        return {"success": True}

    def _dismissed_at(self) -> str | None:
        dismissed = self._settings.get(FAILURE_DISMISSED_KEY)
        return dismissed if isinstance(dismissed, str) else None

    def _standing_failure_io(self) -> UpdateFailure | None:
        """Ask the record seam for a record that still stands, degrading a raising seam to no record.

        The seam's contract is that it never raises. This guard is here because
        what is promised — a start that records its version and a panel read
        that answers, record or no record — is this service's, and the next
        implementation of a Protocol need not share the adapter's discipline.
        """
        try:
            failure = self._read_update_failure()
        except Exception as e:
            self._logger.warning(f"the installer's update record could not be read: {e!r}")
            return None
        return standing_update_failure(failure, self._current_version)
