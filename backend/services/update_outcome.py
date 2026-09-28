"""UpdateOutcomeService — what the last update did, told once where the user will see it.

Owns the two outcomes a start can find: an update that went through, which the
panel announces once per process, and an update the installer rolled back,
which the panel shows until the user dismisses that record or the installer
removes it. Which version is announced, and how the installer's record is read,
live in ``domain/update_outcome.py``; the record itself is behind a seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.update_outcome import announced_update

if TYPE_CHECKING:
    import asyncio
    import logging
    from typing import Any

    from domain.update_outcome import UpdateFailure
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
    """Tells the user what the last update did: announced once, or shown until dismissed."""

    def __init__(self, *, config: UpdateOutcomeServiceConfig) -> None:
        self._current_version = config.current_version
        self._read_update_failure = config.read_update_failure
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._loop = config.loop
        self._logger = config.logger
        # For this process only: a Steam restart reloads the panel, and a panel
        # that asks again must not announce the same update a second time, while
        # the next start compares against the version recorded below and owes
        # nothing.
        self._announcement: str | None = None

    def note_start(self) -> None:
        """Compare the running version with the one the previous start recorded, and record it.

        Runs once, at start. An update that went through is logged at INFO and
        owed to the panel as one announcement; a record of a rolled-back update
        is logged at WARNING whether or not it has been dismissed, because the
        log is where the reason is looked for. The first start that records a
        version announces nothing.
        """
        failure = self._read_failure_io()
        with self._uow_factory() as uow:
            last_run = uow.kv_config.get(LAST_RUN_KEY)
            if last_run != self._current_version:
                uow.kv_config.set(LAST_RUN_KEY, self._current_version)
        self._announcement = announced_update(last_run, self._current_version, failure)
        if self._announcement is not None:
            self._logger.info(f"updated from {last_run} to {self._current_version}")
        if failure is not None:
            self._logger.warning(
                f"the update to {failure.attempted_version} was rolled back at {failure.rolled_back_at}; "
                f"back on {failure.restored_version} — the installer's output says why"
            )

    async def get_update_outcome(self) -> dict[str, Any]:
        """Report what the panel owes the user about the last update.

        Returns ``{"announce_version", "failure", "failure_dismissed"}``.
        ``announce_version`` is the version this process was updated to and has
        not yet been acknowledged for, ``None`` otherwise. ``failure`` is the
        installer's record of a rolled-back update as
        ``{"attempted_version", "restored_version", "rolled_back_at"}``, read
        afresh on every call so it goes when the installer removes it, and
        ``None`` where there is none. ``failure_dismissed`` says the user waved
        away that exact record.
        """
        failure = await self._loop.run_in_executor(None, self._read_failure_io)
        return {
            "announce_version": self._announcement,
            "failure": _failure_payload(failure) if failure is not None else None,
            "failure_dismissed": failure is not None and failure.rolled_back_at == self._dismissed_at(),
        }

    def acknowledge_update_announcement(self) -> dict[str, Any]:
        """Record that the panel announced the update, so this process does not announce it again.

        Idempotent. Returns ``{"success": True}``.
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

    def _read_failure_io(self) -> UpdateFailure | None:
        """Ask the record seam, degrading a raising one to no record.

        The seam's contract is that it never raises. This guard is here because
        what is promised — a start that records its version and a panel read
        that answers, record or no record — is this service's, and the next
        implementation of a Protocol need not share the adapter's discipline.
        """
        try:
            return self._read_update_failure()
        except Exception as e:
            self._logger.warning(f"the record of a rolled-back update could not be read: {e!r}")
            return None


def _failure_payload(failure: UpdateFailure) -> dict[str, str]:
    return {
        "attempted_version": failure.attempted_version,
        "restored_version": failure.restored_version,
        "rolled_back_at": failure.rolled_back_at,
    }
