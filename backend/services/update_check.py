"""UpdateCheckService — whether a newer release exists, and whether to say so.

Owns the question and everything the answer needs a decision about: the
once-a-day throttle, the user's switch, which release they have already waved
away, which one they have already been told about, and the stored answer
itself — the install reads the last seen release through here rather than from
the row. The answer is one-sided — it either has
something to say or stays silent, and a check that reached nothing is silence
rather than a failure. The release read itself is a seam; what a release is
called and how the stored answer is spelled live in
``domain/update_release.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.update_release import UpdateCheck, decode_update_check, encode_update_check
from domain.version import is_newer_version

if TYPE_CHECKING:
    import logging
    from typing import Any

    from domain.update_release import LatestRelease
    from services.protocols import (
        Clock,
        DebugLogger,
        EventEmitter,
        LatestReleaseFn,
        SettingsPersister,
        Sleeper,
        UnitOfWorkFactory,
    )

# How long an answer stands before the next release read. The check exists to
# tell a user about a release eventually, not promptly, and GitHub's
# unauthenticated budget is 60 requests an hour per IP.
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# How often the running backend asks whether a check is due. Far shorter than
# the interval, so a backend that runs for days checks within an hour of the
# stamp going stale; the stamp, not this, decides whether GitHub is asked.
_DUE_POLL_SECONDS = 60 * 60

# The version whose card the user waved away. User intent, so settings.json
# rather than kv_config (GLOSSARY.md, Persistence boundary), and no default entry:
# absent already means nothing dismissed. It holds a VERSION rather than a flag,
# so the next release raises the card again on its own.
DISMISSED_KEY = "update_notice_dismissed_version"

# The user's switch. Absent means on, the default, so an install that never
# touches the switch never grows the key.
ENABLED_KEY = "update_check_enabled"

# What the checks have seen, as one JSON object in kv_config: observed state from
# an external source, kept only as a last-seen marker.
LAST_CHECK_KEY = "update_check_last_seen"

# The version the user has been told about — by the panel's toast, or by a Check
# now that found it — in kv_config: observed state, like the last-seen marker. It
# holds a version rather than a flag, so the next release owes its own toast, and
# it lives in the database rather than in memory because the toast is owed once
# per version across every restart.
TOASTED_KEY = "update_available_toasted_version"


@dataclass(frozen=True)
class UpdateCheckServiceConfig:
    """Frozen wiring bundle handed to ``UpdateCheckService.__init__``.

    Carries the GitHub release seam, the running version, whether this process
    is the installed program, the clock the throttle is measured on, the
    unit-of-work factory the last-seen marker is stored through, the live
    settings dict plus its persister for the two user-intent keys, and the
    runtime infrastructure — the sleeper the running check waits on, the emit
    it tells the panel through, and the logger a failed round is warned on.
    """

    latest_release: LatestReleaseFn
    current_version: str
    installed_program: bool
    clock: Clock
    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    loop: asyncio.AbstractEventLoop
    sleeper: Sleeper
    emit: EventEmitter
    logger: logging.Logger
    log_debug: DebugLogger


class UpdateCheckService:
    """Reports whether a newer Tender release is out, at most once a day unless asked."""

    def __init__(self, *, config: UpdateCheckServiceConfig) -> None:
        self._latest_release = config.latest_release
        self._current_version = config.current_version
        self._installed_program = config.installed_program
        self._clock = config.clock
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._loop = config.loop
        self._sleeper = config.sleeper
        self._emit = config.emit
        self._logger = config.logger
        self._log_debug = config.log_debug
        # One check at a time, from reading the stored answer to recording the
        # new one: the panel-load read and a Check now can overlap, and the one
        # that finishes last would otherwise record an answer built on a stored
        # one the other had already replaced — a slow failed read stamping a
        # stale release over a fresh one.
        self._check_lock = asyncio.Lock()

    async def get_update_notice(self) -> dict[str, Any]:
        """Report the last available release a check saw, and whether the card should say so.

        Returns ``{"available", "newer", "latest_version", "current_version",
        "enabled", "installed_program", "toast_owed"}``. ``latest_version`` is
        the last available release a check saw — the release GitHub called
        latest, with its tarball and checksum file attached — ``None`` where
        none was established. ``newer`` says it is strictly newer than the
        running version. ``available`` is the card: newer, and not the
        dismissed version. ``enabled`` is the switch. ``toast_owed`` says the
        panel owes a toast for ``latest_version``: available, the switch on,
        and that version not yet told — by an acknowledged toast or by a Check
        now that found it.

        Reads GitHub at most once a day: inside that window the answer comes
        from the stored marker, so a reload shows the card again without a
        request. A check that reached nothing is silent — the previous answer
        stands and the next attempt is a day out, so an offline machine neither
        waits on a timeout at every start nor spends the request budget.

        The switch decides only whether this read asks GitHub: with it off the
        stored answer is reported however old it is.
        """
        async with self._check_lock:
            check = await self._loop.run_in_executor(None, self._read_last_check_io)
            # Asked under the lock: the switch may have gone off while this waited.
            if self.is_check_enabled() and self._is_due(check):
                check, _ = await self._check_now(check)
        toasted = await self._loop.run_in_executor(None, self._read_toasted_io)
        return self._notice(check, toasted)

    async def check_for_update_now(self) -> dict[str, Any]:
        """Read the release now — past the throttle, past a dismissal, and whatever the switch says.

        The answer :meth:`get_update_notice` returns plus ``reached``, which says
        whether the release read answered at all: the automatic check renders a
        read that reached nothing as silence, and a button that says *check now*
        owes the reader the difference between "nothing newer" and "nothing
        found out".

        The dismissal is forgotten because the button's second job is bringing
        a waved-away card back. The switch governs only the reads this program
        makes by itself; a press is the user asking. A newer release a read that
        answered shows is recorded as told: the user has just read it, so no
        toast follows for it.
        """
        # The dismissal this press is undoing is the one standing when it was
        # made; a Dismiss pressed while it waited for the lock is newer intent.
        dismissed_at_press = self._settings.get(DISMISSED_KEY)
        async with self._check_lock:
            self._forget_dismissal(dismissed_at_press)
            previous = await self._loop.run_in_executor(None, self._read_last_check_io)
            check, reached = await self._check_now(previous)
            toasted = await self._loop.run_in_executor(None, self._read_toasted_io)
            found = check.release.version if check.release is not None else None
            if reached and found is not None and is_newer_version(found, self._current_version):
                await self._loop.run_in_executor(None, self._record_toasted_io, found)
                # This answer tells it, whether or not the record above was kept.
                toasted = found
        return {**self._notice(check, toasted), "reached": reached}

    async def run_due_checks(self) -> None:
        """Ask for the notice whenever a check may be due, for as long as this runs; tell the panel when it changes.

        The throttle stands as it does for the panel's own read: the stored
        stamp decides whether GitHub is asked, so this asks at most once a day
        and a check that reached nothing is silent. A notice different from the
        one this loop saw last is emitted as ``update_notice``, carrying what
        :meth:`get_update_notice` answers, unless the switch went off while it
        was worked out: switched off, this program says nothing by itself.
        Runs until cancelled; a round that raises, its emit included, is logged
        and the next one comes as usual, pushing again what did not go out.
        """
        last: dict[str, Any] | None = None
        while True:
            await self._sleeper.sleep(_DUE_POLL_SECONDS)
            try:
                if not self.is_check_enabled():
                    continue
                notice = await self.get_update_notice()
                if notice == last or not self.is_check_enabled():
                    continue
                await self._emit("update_notice", notice)
                last = notice
            except Exception as e:
                self._logger.warning(f"update: the running release check failed: {e!r}")

    async def last_seen_release(self) -> LatestRelease | None:
        """The last available release a check stored, whatever the switch says; asks GitHub nothing."""
        check = await self._loop.run_in_executor(None, self._read_last_check_io)
        return check.release if check is not None else None

    def dismiss_update_notice(self, version: object) -> dict[str, Any]:
        """Record that the user waved away the card for *version*.

        Per version and never global, so one Dismiss cannot end the card for
        every later release. Idempotent. Returns ``{"success": True}``, or the
        canonical failure shape for a version that is not a non-empty string.
        """
        if not isinstance(version, str) or not version:
            return {"success": False, "reason": "invalid_value", "message": "Invalid version"}
        self._settings[DISMISSED_KEY] = version
        self._settings_persister.save_settings()
        return {"success": True}

    async def acknowledge_update_available_toast(self, version: object) -> dict[str, Any]:
        """Record that the panel raised the toast for *version*, for every later start.

        Per version, so the next release owes its own. Idempotent. Returns
        ``{"success": True}``, or the canonical failure shape: ``invalid_value``
        for a version that is not a non-empty string, and ``version_changed``
        where *version* is not the release the last check stored — a toast
        acknowledged after a newer release replaced it must not take the newer
        one's toast with it.
        """
        if not isinstance(version, str) or not version:
            return {"success": False, "reason": "invalid_value", "message": "Invalid version"}
        if not await self._loop.run_in_executor(None, self._acknowledge_toast_io, version):
            return {"success": False, "reason": "version_changed", "message": "Not the last seen release"}
        return {"success": True}

    def set_update_check_enabled(self, enabled: object) -> dict[str, Any]:
        """Persist whether this program may ask GitHub about newer releases.

        With it off :meth:`get_update_notice` and the running check make no
        request at all; :meth:`check_for_update_now` still does. Returns
        ``{"success": True}``, or the canonical failure shape for a non-boolean
        value off the untrusted frontend wire.
        """
        if not isinstance(enabled, bool):
            return {"success": False, "reason": "invalid_value", "message": "Invalid value"}
        self._settings[ENABLED_KEY] = enabled
        self._settings_persister.save_settings()
        return {"success": True}

    def is_check_enabled(self) -> bool:
        """Whether the user lets this program ask GitHub about newer releases by itself."""
        return bool(self._settings.get(ENABLED_KEY, True))

    def _is_due(self, check: UpdateCheck | None) -> bool:
        """Answer whether a fresh release read is owed.

        A stamp dated in the future is due at once rather than never: a clock
        set back by hand or by NTP would otherwise hold the check off for as
        long as the jump was large.
        """
        if check is None:
            return True
        elapsed = self._clock.time() - check.checked_at
        return not 0 <= elapsed < _CHECK_INTERVAL_SECONDS

    async def _check_now(self, previous: UpdateCheck | None) -> tuple[UpdateCheck, bool]:
        """Read the latest release and stamp the result; the second value says whether the read answered.

        The stamp cannot carry that itself: a read that reached nothing, and one
        that found a release whose tarball is not attached yet, both stamp the
        previous release forward, so a stamp naming a release is no evidence
        about this read.
        """
        latest = await self._loop.run_in_executor(None, self._read_latest_release_io)
        kept = previous.release if previous is not None else None
        if latest is not None and latest.tarball is not None:
            kept = latest
        stamped = UpdateCheck(checked_at=self._clock.time(), release=kept)
        await self._loop.run_in_executor(None, self._record_check_io, stamped)
        return stamped, latest is not None

    def _notice(self, check: UpdateCheck | None, toasted: str | None) -> dict[str, Any]:
        release = check.release if check is not None else None
        latest = release.version if release is not None else None
        newer = is_newer_version(latest, self._current_version)
        available = newer and latest != self._dismissed()
        enabled = self.is_check_enabled()
        return {
            "available": available,
            "newer": newer,
            "latest_version": latest,
            "current_version": self._current_version,
            "enabled": enabled,
            "installed_program": self._installed_program,
            "toast_owed": available and enabled and latest != toasted,
        }

    def _dismissed(self) -> str | None:
        dismissed = self._settings.get(DISMISSED_KEY)
        return dismissed if isinstance(dismissed, str) else None

    def _forget_dismissal(self, dismissed_at_press: object) -> None:
        """Drop the dismissed version if it is still the one *dismissed_at_press*, and persist that.

        Nothing is written where nothing was held, or where the held value has
        changed since the press. The key is removed rather than emptied,
        because absent is what an install that never dismissed anything
        carries. The press this serves may be repeated, and a settings write
        per press would be a file write per press.
        """
        if dismissed_at_press is None or self._settings.get(DISMISSED_KEY) != dismissed_at_press:
            return
        del self._settings[DISMISSED_KEY]
        self._settings_persister.save_settings()

    def _read_latest_release_io(self) -> LatestRelease | None:
        """Ask the release seam, degrading a raising one to no answer.

        The seam's contract is that it never raises. This guard is here because
        the promise being kept — silence, never an error the user has to read —
        is this service's, and the next implementation of a Protocol need not
        share the adapter's discipline.
        """
        try:
            return self._latest_release()
        except Exception as e:
            self._log_debug(f"[update] latest-release seam raised: {e!r}")
            return None

    def _read_last_check_io(self) -> UpdateCheck | None:
        with self._uow_factory() as uow:
            return decode_update_check(uow.kv_config.get(LAST_CHECK_KEY))

    def _record_check_io(self, check: UpdateCheck) -> None:
        with self._uow_factory() as uow:
            uow.kv_config.set(LAST_CHECK_KEY, encode_update_check(check))

    def _read_toasted_io(self) -> str | None:
        """The version told about; one that cannot be read is none.

        So the toast is owed once more — a repeat rather than a loss.
        """
        try:
            with self._uow_factory() as uow:
                return uow.kv_config.get(TOASTED_KEY)
        except Exception as e:
            self._logger.warning(f"update: which release was told about could not be read: {e!r}")
            return None

    def _record_toasted_io(self, version: str) -> None:
        """Record *version* as told about; a write that fails is a warning.

        A later start may then owe its toast once more — a repeat rather than a loss.
        """
        try:
            with self._uow_factory() as uow:
                uow.kv_config.set(TOASTED_KEY, version)
        except Exception as e:
            self._logger.warning(f"update: that {version} was told about could not be recorded: {e!r}")

    def _acknowledge_toast_io(self, version: str) -> bool:
        """Record *version* as told if it is the stored release, in the one unit of work that read it."""
        with self._uow_factory() as uow:
            check = decode_update_check(uow.kv_config.get(LAST_CHECK_KEY))
            if check is None or check.release is None or check.release.version != version:
                return False
            uow.kv_config.set(TOASTED_KEY, version)
            return True
