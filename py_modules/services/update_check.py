"""UpdateCheckService — whether a newer release exists, and whether to say so.

Tender is not in Decky's plugin catalogue and cannot be: Decky's own update
detection finds an installed plugin by looking its name up in that catalogue,
and with no entry there it never fires. This service is therefore the only place
a user can learn that a new release exists at all, which is why the answer is
one-sided — it either has something to say or stays silent, and a check that
reached nothing is silence rather than a failure.

Owns the question and everything the answer needs a decision about: the
once-a-day throttle, the user's switch, and which release they have already
waved away. The release read itself is a seam, and the address the user is sent
to lives in ``domain/update_release.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.update_release import (
    DOWNLOAD_URL,
    UpdateCheck,
    decode_update_check,
    encode_update_check,
)
from domain.version import is_newer_version

if TYPE_CHECKING:
    import asyncio
    import logging
    from typing import Any

    from domain.update_release import LatestRelease
    from services.protocols import (
        Clock,
        LatestReleaseFn,
        PluginMetadataReader,
        SettingsPersister,
        UnitOfWorkFactory,
    )

# How long an answer stands before the next release read. The check exists to
# tell a user about a release eventually, not promptly, and GitHub's
# unauthenticated budget is 60 requests an hour per IP.
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# The version whose card the user waved away, if any. User intent, so
# ``settings.json`` rather than ``kv_config`` (CONTEXT.md, persistence
# boundary) — and no ``DEFAULT_SETTINGS`` entry or schema bump, because an
# absent key already means the only sensible default: nothing dismissed. It
# holds a VERSION and not a flag, so the next release asks again on its own.
DISMISSED_KEY = "update_notice_dismissed_version"

# The user's switch. Absent means on, which is the default, so this key too
# needs no ``DEFAULT_SETTINGS`` entry — and an install that never touches the
# switch never grows the key.
ENABLED_KEY = "update_check_enabled"

# What the last check saw, as one JSON object in the ``kv_config`` table.
# Observed state read from an external source, persisted only as a last-seen
# marker — bucket 2 of the persistence boundary (ADR-0003), the same shape
# ``platform_names`` has.
LAST_CHECK_KEY = "update_check_last_seen"


@dataclass(frozen=True)
class UpdateCheckServiceConfig:
    """Frozen wiring bundle handed to ``UpdateCheckService.__init__``.

    Carries the GitHub release seam, the plugin's own manifests (read once at
    construction for the running version and the name Decky knows this plugin
    by), the clock the throttle is measured on, the unit-of-work factory the
    last-seen marker is stored through, the live settings dict plus its
    persister for the two user-intent keys, and the runtime infrastructure
    (event loop, logger).
    """

    latest_release: LatestReleaseFn
    plugin_metadata: PluginMetadataReader
    plugin_dir: str
    clock: Clock
    uow_factory: UnitOfWorkFactory
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class UpdateCheckService:
    """Reports whether a newer Tender release is out, at most once a day."""

    def __init__(self, *, config: UpdateCheckServiceConfig) -> None:
        self._latest_release = config.latest_release
        self._clock = config.clock
        self._uow_factory = config.uow_factory
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._loop = config.loop
        self._logger = config.logger
        self._current_version = config.plugin_metadata.read_version(config.plugin_dir)
        # ``read_decky_name``, never ``read_name``: what Decky matches an
        # existing installation against is plugin.json's name, and package.json
        # holds a different one. The adapter states what each read answers.
        self._plugin_name = config.plugin_metadata.read_decky_name(config.plugin_dir)

    async def get_update_notice(self) -> dict[str, Any]:
        """Report the newer release, if there is one the user still wants to hear about.

        Returns ``{"available", "latest_version", "current_version",
        "download_url", "install_url", "plugin_name", "digest", "enabled"}``.
        ``available`` is the notice, and it is the conjunction of three separate
        answers: a release newer than the running one exists, it is not the
        version the user dismissed, and the check is switched on.

        The rest is what an install needs, and it carries TWO addresses because
        they answer two different questions. ``install_url`` names one release
        and is the one to fetch: it belongs with ``digest``, which was read off
        that same release's asset. ``download_url`` is the fixed
        ``releases/latest`` address a reader is shown and could type by hand;
        handing it to an install that also passes the checksum is the way this
        breaks, because it starts resolving to a newer release the moment one
        lands. ``plugin_name`` is the name Decky matches the existing
        installation by.

        Reads GitHub at most once a day: an answer inside that window comes from
        the stored marker, so a plugin reload or a Steam restart shows the card
        again without asking GitHub again. A check that reached nothing is
        silent — ``available`` is False, the previous answer stands whole, and
        the next attempt is a day out, so an offline Deck neither pays a request
        timeout on every plugin load nor spends the request budget.

        With the switch off nothing is fetched and nothing is read: the answer
        carries no version, no digest and no install address, because the plugin
        is not looking.
        """
        enabled = self._enabled()
        if not enabled:
            return self._notice(None, enabled=False)
        check = await self._loop.run_in_executor(None, self._read_last_check_io)
        if self._is_due(check):
            check = await self._check_now(check)
        return self._notice(check, enabled=True)

    def dismiss_update_notice(self, version: object) -> dict[str, Any]:
        """Record that the user has waved away the card for *version*.

        Per version and never global: dismissing 0.33.0 leaves 0.34.0 free to
        raise the card again, which is what keeps a single Dismiss from ending
        the one channel this plugin has for telling anyone about a release.

        Idempotent — a second call re-writes the same value. Returns
        ``{"success": True}``, or the canonical failure shape for a version that
        is not a non-empty string.
        """
        if not isinstance(version, str) or not version:
            return {"success": False, "reason": "invalid_value", "message": "Invalid version"}
        self._settings[DISMISSED_KEY] = version
        self._settings_persister.save_settings()
        return {"success": True}

    def set_update_check_enabled(self, enabled: object) -> dict[str, Any]:
        """Persist whether this plugin may ask GitHub about newer releases.

        On by default. It is the plugin's only outgoing request that goes
        neither to the user's own RomM server nor to SteamGridDB, so it is the
        one a user may reasonably want to switch off; with it off
        :meth:`get_update_notice` makes no request at all.

        Returns ``{"success": True}``, or the canonical failure shape for a
        non-boolean value off the untrusted frontend wire.
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

        A stamp dated in the future is due immediately rather than never: an NTP
        correction or a hand-set clock moves :meth:`Clock.time` backwards — a
        time-zone change does not, it is Unix seconds — and a window measured
        from a future instant would hold the check off for as long as the jump
        was large.
        """
        if check is None:
            return True
        elapsed = self._clock.time() - check.checked_at
        return not 0 <= elapsed < _CHECK_INTERVAL_SECONDS

    async def _check_now(self, previous: UpdateCheck | None) -> UpdateCheck:
        """Read the latest release and stamp the result, keeping the previous answer on failure."""
        release = await self._loop.run_in_executor(None, self._read_latest_release_io)
        checked_at = self._clock.time()
        if release is None:
            stamped = UpdateCheck(
                checked_at=checked_at,
                version=previous.version if previous is not None else None,
                digest=previous.digest if previous is not None else None,
                install_url=previous.install_url if previous is not None else "",
            )
        else:
            stamped = UpdateCheck(
                checked_at=checked_at,
                version=release.version,
                digest=release.digest,
                install_url=release.install_url,
            )
        await self._loop.run_in_executor(None, self._record_check_io, stamped)
        return stamped

    def _notice(self, check: UpdateCheck | None, *, enabled: bool) -> dict[str, Any]:
        latest = check.version if check is not None else None
        return {
            "available": enabled and is_newer_version(latest, self._current_version) and latest != self._dismissed(),
            "latest_version": latest,
            "current_version": self._current_version,
            "download_url": DOWNLOAD_URL,
            "install_url": check.install_url if check is not None else "",
            "plugin_name": self._plugin_name,
            "digest": check.digest if check is not None else None,
            "enabled": enabled,
        }

    def _dismissed(self) -> str | None:
        dismissed = self._settings.get(DISMISSED_KEY)
        return dismissed if isinstance(dismissed, str) else None

    def _read_latest_release_io(self) -> LatestRelease | None:
        """Ask the release seam, degrading a raising one to no answer.

        The seam's own contract is that it never raises, and the production
        adapter holds to it. This guard is here because the promise being kept
        is THIS service's — silence, never an error the user has to read — and
        the seam is a Protocol whose next implementation need not share the
        adapter's discipline.
        """
        try:
            return self._latest_release()
        except Exception as e:
            self._logger.warning(f"Update check could not read the latest release: {e}")
            return None

    def _read_last_check_io(self) -> UpdateCheck | None:
        with self._uow_factory() as uow:
            return decode_update_check(uow.kv_config.get(LAST_CHECK_KEY))

    def _record_check_io(self, check: UpdateCheck) -> None:
        with self._uow_factory() as uow:
            uow.kv_config.set(LAST_CHECK_KEY, encode_update_check(check))
