"""UpdateCheckService — whether a newer release exists, and whether to say so.

Owns the question and everything the answer needs a decision about: the
once-a-day throttle, the user's switch, and which release they have already
waved away. The answer is one-sided — it either has something to say or stays
silent, and a check that reached nothing is silence rather than a failure. The
release read itself is a seam; what a release is called and how the stored
answer is spelled live in ``domain/update_release.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.update_release import UpdateCheck, decode_update_check, encode_update_check
from domain.version import is_newer_version

if TYPE_CHECKING:
    from typing import Any

    from domain.update_release import LatestRelease
    from services.protocols import (
        Clock,
        DebugLogger,
        LatestReleaseFn,
        SettingsPersister,
        UnitOfWorkFactory,
    )

# How long an answer stands before the next release read. The check exists to
# tell a user about a release eventually, not promptly, and GitHub's
# unauthenticated budget is 60 requests an hour per IP.
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# The version whose card the user waved away. User intent, so settings.json
# rather than kv_config (CONTEXT.md, Persistence boundary), and no default entry:
# absent already means nothing dismissed. It holds a VERSION rather than a flag,
# so the next release raises the card again on its own.
DISMISSED_KEY = "update_notice_dismissed_version"

# The user's switch. Absent means on, the default, so an install that never
# touches the switch never grows the key.
ENABLED_KEY = "update_check_enabled"

# What the checks have seen, as one JSON object in kv_config: observed state from
# an external source, kept only as a last-seen marker.
LAST_CHECK_KEY = "update_check_last_seen"


@dataclass(frozen=True)
class UpdateCheckServiceConfig:
    """Frozen wiring bundle handed to ``UpdateCheckService.__init__``.

    Carries the GitHub release seam, the running version, whether this process
    is the installed program, the clock the throttle is measured on, the
    unit-of-work factory the last-seen marker is stored through, the live
    settings dict plus its persister for the two user-intent keys, and the
    runtime infrastructure.
    """

    latest_release: LatestReleaseFn
    current_version: str
    installed_program: bool
    clock: Clock
    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    loop: asyncio.AbstractEventLoop
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
        "enabled", "installed_program"}``. ``latest_version`` is the last
        available release a check saw — the release GitHub called latest, with
        its tarball attached — ``None`` where none was established. ``newer`` says it is strictly newer than the running
        version. ``available`` is the card: newer, not the dismissed version,
        and the check switched on.

        Reads GitHub at most once a day: inside that window the answer comes
        from the stored marker, so a reload shows the card again without a
        request. A check that reached nothing is silent — the previous answer
        stands and the next attempt is a day out, so an offline machine neither
        waits on a timeout at every start nor spends the request budget.

        With the switch off nothing is fetched and nothing is read: the answer
        names no version, because the program is not looking.
        """
        if not self._enabled():
            return self._notice(None, enabled=False)
        async with self._check_lock:
            check = await self._loop.run_in_executor(None, self._read_last_check_io)
            if self._is_due(check):
                check, _ = await self._check_now(check)
        return self._notice(check, enabled=True)

    async def check_for_update_now(self) -> dict[str, Any]:
        """Read the release now — past the throttle, and past a dismissal.

        The answer :meth:`get_update_notice` returns plus ``reached``, which says
        whether the release read answered at all: the automatic check renders a
        read that reached nothing as silence, and a button that says *check now*
        owes the reader the difference between "nothing newer" and "nothing
        found out".

        The dismissal is forgotten because the button's second job is bringing
        a waved-away card back. With the switch off nothing is read and nothing
        is forgotten — a button is not consent the switch withheld — and the
        answer carries ``enabled: False`` with ``reached: False``.
        """
        if not self._enabled():
            return {**self._notice(None, enabled=False), "reached": False}
        self._forget_dismissal()
        async with self._check_lock:
            previous = await self._loop.run_in_executor(None, self._read_last_check_io)
            check, reached = await self._check_now(previous)
        return {**self._notice(check, enabled=True), "reached": reached}

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

    def set_update_check_enabled(self, enabled: object) -> dict[str, Any]:
        """Persist whether this program may ask GitHub about newer releases.

        With it off :meth:`get_update_notice` makes no request at all. Returns
        ``{"success": True}``, or the canonical failure shape for a non-boolean
        value off the untrusted frontend wire.
        """
        if not isinstance(enabled, bool):
            return {"success": False, "reason": "invalid_value", "message": "Invalid value"}
        self._settings[ENABLED_KEY] = enabled
        self._settings_persister.save_settings()
        return {"success": True}

    def _enabled(self) -> bool:
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

    def _notice(self, check: UpdateCheck | None, *, enabled: bool) -> dict[str, Any]:
        release = check.release if check is not None else None
        latest = release.version if release is not None else None
        newer = is_newer_version(latest, self._current_version)
        return {
            "available": enabled and newer and latest != self._dismissed(),
            "newer": newer,
            "latest_version": latest,
            "current_version": self._current_version,
            "enabled": enabled,
            "installed_program": self._installed_program,
        }

    def _dismissed(self) -> str | None:
        dismissed = self._settings.get(DISMISSED_KEY)
        return dismissed if isinstance(dismissed, str) else None

    def _forget_dismissal(self) -> None:
        """Drop the dismissed version, and persist that only where one was held.

        The key is removed rather than emptied, because absent is what an install
        that never dismissed anything carries. The press this serves may be
        repeated, and a settings write per press would be a file write per
        press.
        """
        if self._settings.pop(DISMISSED_KEY, None) is None:
            return
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
