"""UpdateInstallService — installing the last seen release from the panel, and what a press waits for.

Owns one attempt at a time, from the press until the installer stops this
process or the attempt fails: whether a press may start one, the download and
its check against the digest the release states, the installer's start as a
unit of its own, and watching that unit for as long as this process lives. It
also owns the update rule every conflicting use case asks
(``is_update_in_progress``), held from a press that starts an attempt until
that attempt fails, and the record of an attempt whose installer it started,
which tells the next start whether that installer stopped without updating.
Which release is meant is the release check's answer, read through its owner.
What the steps, reasons and record are called is in ``domain/update_install.py``;
the download, the files, the hashing and the unit are behind seams.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from domain.update_install import (
    INSTALLER_UNIT,
    InstallAttempt,
    InstallFailure,
    InstallStep,
    Wait,
    WaitReason,
    claim_reasons,
    installer_command,
    new_attempt_record,
    refused_by_the_check,
    stopped_attempt,
)
from domain.update_outcome import standing_update_failure
from domain.version import is_newer_version

if TYPE_CHECKING:
    import logging

    from domain.update_install import UpdateAttemptRecord
    from domain.update_outcome import UpdateFailure
    from domain.update_release import LatestRelease, ReleaseTarball
    from services.protocols import (
        ActiveDownloadRomIdsFn,
        Clock,
        DebugLogger,
        DownloadQueueFn,
        EventEmitter,
        HeldClaimsFn,
        LastSeenReleaseReader,
        ReleaseAssetDownloadFn,
        Sleeper,
        SteamInterfaceReader,
        TransientUnitControl,
        UnitOfWorkFactory,
        UpdateAttemptStore,
        UpdateFailureFn,
        UpdateStagingStore,
        WorkInFlightFn,
    )

# At most one progress frame per this many seconds while bytes arrive, as the
# ROM downloads do; the last one of a download always goes out.
_PROGRESS_EMIT_SECONDS = 0.5

# The ``started_at`` of the stopped attempt whose toast the panel raised, in
# kv_config: observed state. A stopped attempt is judged again at every start
# until it is dismissed or superseded, so a flag held for one process would
# raise its toast again at every start.
STOPPED_TOASTED_KEY = "update_stopped_toasted_at"

# The failures an attempt ends in once its installer has run, and has
# printed something to the journal.
_INSTALLER_RAN = frozenset({InstallFailure.INSTALLER_STOPPED, InstallFailure.NEW_VERSION_DOES_NOT_START})

# How often the installer's unit is asked whether it still runs. It stops this
# process when it gets far enough, so the watch only ever sees it end early.
_WATCH_SECONDS = 3.0


class _AttemptFailedError(Exception):
    """Ends an attempt with *failure*; raised inside :meth:`UpdateInstallService._run` alone."""

    def __init__(self, failure: InstallFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


class _ShuttingDownError(Exception):
    """Raised on the download's thread once this process is shutting down, which ends the download there."""


@dataclass(frozen=True)
class UpdateInstallServiceConfig:
    """Frozen wiring bundle handed to ``UpdateInstallService.__init__``.

    Carries the release check the release is read through, the running version
    and whether this process is the installed program, one reader per kind of
    work a restart would cut short and the claims on the prune conflicts, the
    Steam interface reader the host fills in, the installer's record of an
    update that did not go through and this program's own record of an
    attempt, the download, staging and unit seams with the environment the
    installer starts with, the unit-of-work factory the raised toast of a
    stopped attempt is stored through, and the runtime infrastructure.
    """

    releases: LastSeenReleaseReader
    current_version: str
    installed_program: bool
    steam: SteamInterfaceReader
    library_sync_in_flight: WorkInFlightFn
    rom_downloads_in_flight: ActiveDownloadRomIdsFn
    download_queue: DownloadQueueFn
    save_sync_in_flight: WorkInFlightFn
    firmware_downloads_in_flight: WorkInFlightFn
    save_directory_move_in_flight: WorkInFlightFn
    cleanup_running: WorkInFlightFn
    migration_running: WorkInFlightFn
    held_claims: HeldClaimsFn
    read_update_failure: UpdateFailureFn
    attempts: UpdateAttemptStore
    download_asset: ReleaseAssetDownloadFn
    staging: UpdateStagingStore
    units: TransientUnitControl
    installer_environment: tuple[tuple[str, str], ...]
    uow_factory: UnitOfWorkFactory
    emit: EventEmitter
    clock: Clock
    sleeper: Sleeper
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger


class UpdateInstallService:
    """Installs the last seen release when nothing a restart would cut short is under way."""

    def __init__(self, *, config: UpdateInstallServiceConfig) -> None:
        self._releases = config.releases
        self._current_version = config.current_version
        self._installed_program = config.installed_program
        self._steam = config.steam
        self._library_sync_in_flight = config.library_sync_in_flight
        self._rom_downloads_in_flight = config.rom_downloads_in_flight
        self._download_queue = config.download_queue
        self._save_sync_in_flight = config.save_sync_in_flight
        self._firmware_downloads_in_flight = config.firmware_downloads_in_flight
        self._save_directory_move_in_flight = config.save_directory_move_in_flight
        self._cleanup_running = config.cleanup_running
        self._migration_running = config.migration_running
        self._held_claims = config.held_claims
        self._read_update_failure = config.read_update_failure
        self._attempts = config.attempts
        self._download_asset = config.download_asset
        self._staging = config.staging
        self._units = config.units
        self._installer_environment = config.installer_environment
        self._uow_factory = config.uow_factory
        self._emit = config.emit
        self._clock = config.clock
        self._sleeper = config.sleeper
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._attempt: InstallAttempt | None = None
        self._task: asyncio.Task[None] | None = None
        # Set in the same loop turn as the check a press passes, and cleared
        # only by an attempt that failed while this process still runs.
        self._holding = False
        # From the moment the installer is asked to start: an attempt that
        # fails in an unforeseen way after that keeps the rule, because the
        # installer may be running.
        self._installer_may_run = False
        # The download runs on a thread that cancelling the attempt does not
        # stop; its progress callback ends it once this is set.
        self._stopping = False
        # The frames the download's thread hands the loop, held so the loop
        # cannot collect one before it is sent.
        self._frames: set[asyncio.Task[None]] = set()
        # The attempt a previous start's installer stopped without updating, as
        # this start found it; gone once dismissed or a new attempt starts.
        self._stopped: UpdateAttemptRecord | None = None
        # Whether the panel still owes that attempt's toast.
        self._stopped_toast_owed = False
        # Judges the record an earlier start's attempt left, once the
        # installer's unit has ended.
        self._judging: asyncio.Task[None] | None = None
        # Whether this attempt's record was written, so a failure this process
        # reports itself takes it away again.
        self._record_written = False
        # When this attempt's installer was started, as its record states it,
        # whether or not the record could be written.
        self._installer_started_at: str | None = None
        self._last_progress_emit: float | None = None
        # Every tick's count, throttled or not: the frames the worker hands the
        # loop can land after the download's own completion has been seen, and
        # are then dropped, so the final count is taken from here instead.
        self._bytes_seen: tuple[int, int | None] = (0, None)

    def is_update_in_progress(self) -> bool:
        """Whether an attempt holds the update rule: from its press until it fails, or this process ends."""
        return self._holding

    def remove_leftovers(self) -> None:
        """Remove whatever an earlier attempt left in the staging directory. Run once, at start."""
        self._staging.remove_all()

    def note_start(self) -> None:
        """Judge the record of the attempt whose installer an earlier start started. Run once, at start.

        An installer that stopped without updating never tells the backend it
        stopped first, and a start on the same version looks like any other.
        Where the record says that is what happened, the attempt is reported
        as ``installer_stopped`` — logged at WARNING, offered again as Try
        again, pushed to the panel as ``update_attempt_stopped``, and kept for
        the notice on Main until it is dismissed, a new attempt starts, or the
        running version changes. Any other record — an update that went
        through, one the installer rolled back or its pre-install check
        refused, a version that moved since — is removed.

        The installer starts this program itself — the new version, the one it
        rolled back to, or the same one again after it gave up — and may still
        be running when this start asks. Only once its unit reads ended is the
        record judged: while it runs, or nobody can say whether it does, the
        unit is asked again every watch interval, and a press in the meantime
        ends the question, since a new attempt ends the record itself. All of
        it runs in a task of its own, the record and the unit read off the
        loop, so a user manager slow to answer holds up no other start step.
        """
        self._judging = self._loop.create_task(self._judge_the_last_attempt())

    def _judge_io(self, record: UpdateAttemptRecord) -> UpdateAttemptRecord | None:
        """*record* where it says the installer stopped without updating; any other record is removed."""
        failure = standing_update_failure(self._read_update_failure(), self._current_version)
        stopped = stopped_attempt(record, self._current_version, failure)
        if stopped is None:
            self._remove_record_io()
        return stopped

    def _judge_the_record_io(self) -> UpdateAttemptRecord | None:
        record = self._attempts.read()
        return self._judge_io(record) if record is not None else None

    def _take_stopped(self, stopped: UpdateAttemptRecord, toasted_at: str | None) -> None:
        self._logger.warning(
            f"update: the installer for {stopped.attempted_version} started at {stopped.started_at} stopped without "
            f"updating; still on {self._current_version} — what it said is in journalctl --user -u {INSTALLER_UNIT}"
        )
        self._stopped = stopped
        self._stopped_toast_owed = toasted_at != stopped.started_at
        self._installer_started_at = stopped.started_at
        self._attempt = InstallAttempt(
            version=stopped.attempted_version, step=InstallStep.FAILED, failure=InstallFailure.INSTALLER_STOPPED
        )

    async def _judge_the_last_attempt(self) -> None:
        """Judge the record once the installer's unit reads ended, then push a stopped attempt it found."""
        try:
            stopped = await self._stopped_once_the_installer_ended()
        except Exception:
            self._logger.exception("update: the record of the last update attempt could not be judged")
            return
        if stopped is None:
            return
        try:
            await self._emit("update_attempt_stopped", _stopped_wire(stopped, self._stopped_toast_owed))
        except Exception:
            self._logger.exception(
                f"update: the panel could not be told that the installer for {stopped.attempted_version} stopped "
                "without updating; the notice on Main shows it once the panel loads again"
            )

    async def _stopped_once_the_installer_ended(self) -> UpdateAttemptRecord | None:
        """The stopped attempt the record names, once the unit reads ended; ``None`` where a press came first.

        A press is looked for after every answer the unit gives and after the
        record is judged, since a new attempt ends the record itself.
        """
        record = await self._loop.run_in_executor(None, self._attempts.read)
        if record is None:
            return None
        told = False
        while True:
            active, _why = await self._ask_unit()
            if self._attempt is not None:
                return None
            if active is False:
                break
            if not told:
                told = True
                self._logger.info(
                    f"update: the installer for {record.attempted_version} "
                    f"{'still runs' if active else 'may still run'}; its record is judged once it has ended"
                )
            await self._sleeper.sleep(_WATCH_SECONDS)
        stopped = await self._loop.run_in_executor(None, self._judge_the_record_io)
        if stopped is None:
            return None
        toasted_at = await self._loop.run_in_executor(None, self._toasted_at_io)
        if self._attempt is not None:
            return None
        self._take_stopped(stopped, toasted_at)
        return stopped

    def _toasted_at_io(self) -> str | None:
        with self._uow_factory() as uow:
            return uow.kv_config.get(STOPPED_TOASTED_KEY)

    def get_stopped_update_attempt(self) -> dict[str, Any] | None:
        """The attempt an earlier start's installer stopped without updating, until dismissed or superseded.

        ``{"attempted_version", "from_version", "started_at", "toast_owed"}``,
        or ``None``; ``toast_owed`` says the panel has not raised its toast yet.
        Judged by :meth:`note_start`, whose late judgement pushes the same
        shape as ``update_attempt_stopped``; the notice on Main shows it.
        """
        stopped = self._stopped
        return _stopped_wire(stopped, self._stopped_toast_owed) if stopped is not None else None

    async def acknowledge_stopped_attempt_toast(self, started_at: object) -> dict[str, Any]:
        """Record that the panel raised the toast for the stopped attempt started at *started_at*, for good.

        Per attempt, so a later one owes its own toast. Idempotent. Returns
        ``{"success": True}``, or the canonical failure shape for a stamp that
        is not a non-empty string.
        """
        if not isinstance(started_at, str) or not started_at:
            return {"success": False, "reason": "invalid_value", "message": "Invalid attempt"}
        await self._loop.run_in_executor(None, self._record_toast_io, started_at)
        if self._stopped is not None and self._stopped.started_at == started_at:
            self._stopped_toast_owed = False
        return {"success": True}

    def _record_toast_io(self, started_at: str) -> None:
        with self._uow_factory() as uow:
            uow.kv_config.set(STOPPED_TOASTED_KEY, started_at)

    def failed_installer_started_at(self) -> str | None:
        """When the latest attempt's installer was started, where that attempt failed after its installer ran.

        ISO-8601 UTC text, or ``None`` where the latest attempt failed before
        its installer ran, is still under way, or there is none. An attempt a
        previous start's installer stopped counts, and keeps counting after its
        notice is dismissed, for as long as the Updates settings show it.
        """
        attempt = self._attempt
        if attempt is None or attempt.failure not in _INSTALLER_RAN:
            return None
        return self._installer_started_at

    async def dismiss_stopped_attempt(self) -> dict[str, Any]:
        """Wave away the notice of an installer that stopped without updating, and remove its record.

        Returns ``{"success": True}``. Does nothing unless such an attempt
        stands — and none does once a press has started a new one — since a
        card left on screen from before that press would otherwise take the
        new attempt's record away. A record that could not be removed is
        logged and is judged again at the next start — a notice shown twice
        rather than one lost.
        """
        if self._stopped is None:
            return {"success": True}
        self._stopped = None
        await self._loop.run_in_executor(None, self._remove_record_io)
        return {"success": True}

    async def shutdown(self) -> None:
        """Stop the attempt, a download on its thread and a judgement still waiting; the hold ends with the process."""
        self._stopping = True
        for task in (self._task, self._judging):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def get_update_install_state(self) -> dict[str, Any]:
        """Report whether an install is offered, what it waits for, and how the latest attempt went.

        Returns ``{"offered", "version", "wait_reasons", "paused_downloads",
        "attempt", "try_again"}``. ``offered`` holds on the installed program
        with a stored release newer than the running version, which ``version``
        names (``None`` where nothing is offered), whatever the check's switch
        says. ``wait_reasons`` lists every reason a press would be refused
        now, each ``{"reason"}`` plus ``apps`` for ``app_running`` and
        ``frees_at`` for ``interface_reload_limit``; empty where nothing is
        offered or an attempt holds the rule. ``paused_downloads`` counts the
        paused ROM downloads a restart would lose. ``attempt`` is the latest
        attempt's ``{"version", "step", "bytes_done", "bytes_total",
        "failure"}``, or ``None``. ``try_again`` says the offered version
        already failed once — an attempt in this process, or an update the
        installer rolled back or its pre-install check refused.
        """
        release = await self._offered_release()
        waits = await self._waits() if release is not None and not self._holding else []
        return {
            "offered": release is not None,
            "version": release.version if release is not None else None,
            "wait_reasons": [wait.to_wire() for wait in waits],
            "paused_downloads": self._paused_downloads(),
            "attempt": self._attempt.to_wire() if self._attempt is not None else None,
            "try_again": release is not None and await self._failed_before(release.version),
        }

    async def install_update(self, version: object) -> dict[str, Any]:
        """Start installing *version*, the stored release; answer once the attempt is under way.

        Refused, in the canonical failure shape, with ``reason``
        ``update_in_progress`` while an attempt holds the rule, ``not_offered``
        where :meth:`get_update_install_state` offers nothing,
        ``version_changed`` where *version* is not the stored release, and
        ``update_waiting`` — carrying ``wait_reasons`` as that method words
        them — while anything a restart would cut short is under way. Every
        reason is asked again here, with one reading of Steam's running apps.
        Otherwise the update rule is held from this moment, the attempt runs on
        by itself and reports through ``update_install_progress``, and the
        answer is ``{"success": True}``.
        """
        if self.is_update_in_progress():
            return self._in_progress_refusal()
        release = await self._offered_release()
        if release is None or release.tarball is None:
            return {"success": False, "reason": "not_offered", "message": "No newer release is offered to install"}
        if version != release.version:
            return {
                "success": False,
                "reason": "version_changed",
                "message": f"The release offered is now {release.version}",
            }
        apps = await self._running_apps()
        limit = await self._reload_limit_waits()
        # Asked again after the readings: a second press may have started an
        # attempt while this one waited for them.
        if self.is_update_in_progress():
            return self._in_progress_refusal()
        waits = [*_app_waits(apps), *self._work_waits(), *limit]
        if waits:
            return {
                "success": False,
                "reason": "update_waiting",
                "message": "Something that an update would interrupt is still under way",
                "wait_reasons": [wait.to_wire() for wait in waits],
            }
        self._holding = True
        self._installer_may_run = False
        self._stopped = None
        self._record_written = False
        self._installer_started_at = None
        self._last_progress_emit = None
        self._bytes_seen = (0, None)
        self._attempt = InstallAttempt(version=release.version, step=InstallStep.DOWNLOADING)
        self._task = self._loop.create_task(self._run(release.version, release.tarball))
        await self._emit_attempt()
        return {"success": True}

    async def _run(self, version: str, tarball: ReleaseTarball) -> None:
        try:
            # A new attempt ends the record of an earlier one.
            await self._loop.run_in_executor(None, self._remove_record_io)
            installer, path = await self._download_and_verify(version, tarball)
            await self._start_installer(version, installer, path)
            await self._watch_installer(version)
        except _AttemptFailedError as failed:
            await self._fail(version, failed.failure)
        except Exception:
            if self._installer_may_run:
                self._logger.exception(
                    f"update: the attempt at {version} failed after the installer was asked to start; "
                    "the update rule stays held while it may run"
                )
                return
            self._logger.exception(f"update: the attempt at {version} failed before the installer was started")
            await self._fail(version, _failure_at(self._attempt))

    async def _download_and_verify(self, version: str, tarball: ReleaseTarball) -> tuple[str, str]:
        """Download the tarball and its checksum file, check the digest, and unpack the installer."""
        path = self._staging.tarball_path(version)
        self._logger.info(f"update: downloading {version}")
        try:
            await self._loop.run_in_executor(None, self._staging.prepare)
            await self._loop.run_in_executor(None, self._download_asset, tarball.url, path, self._on_progress)
        except Exception as e:
            self._logger.warning(f"update: downloading {version} failed: {e!r}")
            raise _AttemptFailedError(InstallFailure.DOWNLOAD_FAILED) from e
        await self._report_downloaded(version)
        await self._advance(version, InstallStep.VERIFYING)
        try:
            digest = await self._loop.run_in_executor(None, self._staging.sha256_of, path)
        except Exception as e:
            self._logger.warning(f"update: the downloaded {version} could not be read: {e!r}")
            raise _AttemptFailedError(InstallFailure.DOWNLOAD_FAILED) from e
        if digest != tarball.digest:
            self._logger.warning(f"update: {version} does not match the digest its release states; nothing changed")
            raise _AttemptFailedError(InstallFailure.CHECKSUM_MISMATCH)
        try:
            await self._loop.run_in_executor(None, self._download_asset, tarball.checksum_url, f"{path}.sha256", None)
        except Exception as e:
            self._logger.warning(f"update: downloading {version}'s checksum file failed: {e!r}")
            raise _AttemptFailedError(InstallFailure.DOWNLOAD_FAILED) from e
        try:
            installer = await self._loop.run_in_executor(None, self._staging.extract_installer, path)
        except Exception as e:
            self._logger.warning(f"update: {version} carries no installer that could be unpacked: {e!r}")
            raise _AttemptFailedError(InstallFailure.INSTALLER_NOT_STARTED) from e
        return installer, path

    async def _start_installer(self, version: str, installer: str, tarball_path: str) -> None:
        apps = await self._running_apps()
        if apps is None:
            self._logger.warning(
                f"update: whether a game runs could not be read before the installer for {version}; nothing changed"
            )
            raise _AttemptFailedError(InstallFailure.RUNNING_APPS_UNKNOWN)
        if apps:
            self._logger.warning(f"update: {', '.join(apps)} started during the download of {version}; nothing changed")
            raise _AttemptFailedError(InstallFailure.GAME_STARTED)
        await self._write_record(version)
        self._logger.info(
            f"update: starting the installer for {version}; follow it with journalctl --user -u {INSTALLER_UNIT}"
        )
        self._installer_may_run = True
        try:
            why = await self._loop.run_in_executor(
                None,
                self._units.start,
                INSTALLER_UNIT,
                installer_command(installer, tarball_path),
                self._installer_environment,
            )
        except TimeoutError as e:
            why = await self._started_after_all(version, e)
        except Exception as e:
            why = repr(e)
        if why is not None:
            self._installer_may_run = False
            self._logger.warning(f"update: the installer for {version} could not be started: {why}")
            raise _AttemptFailedError(InstallFailure.INSTALLER_NOT_STARTED)
        await self._advance(version, InstallStep.INSTALLER_STARTED)

    async def _write_record(self, version: str) -> None:
        """Record the attempt before its installer starts, so the next start can tell whether it stopped."""
        record = new_attempt_record(version, self._current_version, self._clock.time())
        self._installer_started_at = record.started_at
        try:
            await self._loop.run_in_executor(None, self._attempts.write, record)
        except OSError as e:
            self._logger.warning(
                f"update: the record of the attempt at {version} could not be written ({e!r}); "
                "should its installer stop without updating, the next start cannot say so"
            )
            return
        self._record_written = True

    def _remove_record_io(self) -> None:
        """Remove the attempt record, logging rather than raising where it stays."""
        try:
            self._attempts.remove()
        except OSError as e:
            self._logger.warning(f"update: the record of an update attempt could not be removed: {e!r}")

    async def _started_after_all(self, version: str, no_answer: TimeoutError) -> str | None:
        """Why a start that gave no answer did not start the unit, or ``None`` where it may have.

        Only a user manager that says the unit is not running makes it a start
        that failed, and only when it still says so one watch interval later: a
        ``systemd-run`` that gave no answer may not have created the unit yet,
        and an unknown unit reads as ended. Where it cannot say either, the
        attempt goes on as started and the watch finds out, rather than giving
        the rule back while the installer may run.
        """
        self._logger.warning(
            f"update: starting the installer for {version} gave no answer ({no_answer}); asking whether it runs"
        )
        active, _why = await self._ask_unit()
        if active is False:
            await self._sleeper.sleep(_WATCH_SECONDS)
            active, _why = await self._ask_unit()
        return repr(no_answer) if active is False else None

    async def _watch_installer(self, version: str) -> None:
        """Wait for the installer to stop this process; it ending first is the attempt failing.

        It ends first as ``new_version_does_not_start`` where the installer's
        record says its pre-install check refused this attempt — and that
        record is pushed to the panel as ``update_failure_recorded``, in the
        shape ``get_update_outcome`` answers it in, so the notice on Main shows
        it without waiting for the next read — and as ``installer_stopped``
        otherwise.

        A user manager that cannot be asked is not an answer, and neither is a
        seam that raised, so the watch goes on rather than calling the
        installer stopped and giving the rule back while it may still run. The
        first such reading is a warning, since a watch that can no longer see
        the installer holds the rule for as long as this process lives.
        """
        told = False
        while True:
            await self._sleeper.sleep(_WATCH_SECONDS)
            active, why = await self._ask_unit()
            refusal = await self._check_refusal(version) if active is False else None
            if refusal is not None:
                self._logger.warning(
                    f"update: the pre-install check refused {version}: it does not start, so nothing was changed; "
                    f"what the check said is in journalctl --user -u {INSTALLER_UNIT}"
                )
                await self._emit("update_failure_recorded", refusal.to_wire())
                raise _AttemptFailedError(InstallFailure.NEW_VERSION_DOES_NOT_START)
            if active is False:
                self._logger.warning(
                    f"update: the installer for {version} stopped without updating; "
                    f"what it said is in journalctl --user -u {INSTALLER_UNIT}"
                )
                raise _AttemptFailedError(InstallFailure.INSTALLER_STOPPED)
            if active is None and not told:
                told = True
                self._logger.warning(
                    f"update: whether the installer for {version} still runs could not be read ({why}); "
                    "still watching, and the update rule stays held while it may run — "
                    f"journalctl --user -u {INSTALLER_UNIT}"
                )
            elif active is None:
                self._log_debug(f"[update] the installer's unit could not be asked about ({why})")

    async def _check_refusal(self, version: str) -> UpdateFailure | None:
        """The installer's record that its pre-install check refused this attempt, or ``None``.

        ``None`` too where the record cannot be read: the attempt then ends as
        an installer that stopped, which is what it looks like from here.
        """
        started_at = self._installer_started_at
        if started_at is None:
            return None
        try:
            failure = await self._loop.run_in_executor(None, self._read_update_failure)
        except Exception as e:
            self._logger.warning(f"update: the installer's record could not be read: {e!r}")
            return None
        return failure if refused_by_the_check(failure, version, self._current_version, started_at) else None

    async def _ask_unit(self) -> tuple[bool | None, str]:
        """The installer's unit state, and what stood in the way where there is none."""
        try:
            active = await self._loop.run_in_executor(None, self._units.is_active, INSTALLER_UNIT)
        except Exception as e:
            return None, repr(e)
        return active, "" if active is not None else "the user manager gave no state"

    async def _report_downloaded(self, version: str) -> None:
        """Report the download's final count, unless a frame already carried it."""
        done, total = self._bytes_seen
        attempt = self._attempt
        if attempt is not None and (attempt.bytes_done, attempt.bytes_total) == (done, total):
            return
        self._attempt = InstallAttempt(
            version=version, step=InstallStep.DOWNLOADING, bytes_done=done, bytes_total=total
        )
        await self._emit_attempt()

    async def _advance(self, version: str, step: InstallStep) -> None:
        self._attempt = InstallAttempt(version=version, step=step)
        await self._emit_attempt()

    async def _fail(self, version: str, failure: InstallFailure) -> None:
        """End the attempt as *failure*: drop what it staged, give the rule back, and say so."""
        try:
            await self._loop.run_in_executor(None, self._staging.remove_all)
        except Exception:
            self._logger.exception("update: what the failed attempt staged could not be removed")
        # This process reports the failure itself, so the record would tell the
        # next start of something already shown.
        if self._record_written:
            self._record_written = False
            await self._loop.run_in_executor(None, self._remove_record_io)
        self._attempt = InstallAttempt(version=version, step=InstallStep.FAILED, failure=failure)
        self._holding = False
        await self._emit_attempt()

    def _on_progress(self, done: int, total: int | None) -> None:
        """The download's byte count, from the thread it runs on; throttled, then handed to the loop.

        Raises once this process is shutting down, which is what ends the
        download on its thread.
        """
        if self._stopping:
            raise _ShuttingDownError("the backend is shutting down")
        self._bytes_seen = (done, total)
        now = self._clock.monotonic()
        final = total is not None and done >= total
        last = self._last_progress_emit
        if not final and last is not None and now - last < _PROGRESS_EMIT_SECONDS:
            return
        self._last_progress_emit = now
        self._loop.call_soon_threadsafe(self._apply_progress, done, total)

    def _apply_progress(self, done: int, total: int | None) -> None:
        attempt = self._attempt
        if attempt is None or attempt.step is not InstallStep.DOWNLOADING:
            return
        self._attempt = replace(attempt, bytes_done=done, bytes_total=total)
        frame = self._loop.create_task(self._emit_frame(self._attempt))
        self._frames.add(frame)
        frame.add_done_callback(self._frames.discard)

    async def _emit_attempt(self) -> None:
        if self._attempt is not None:
            await self._emit_frame(self._attempt)

    async def _emit_frame(self, attempt: InstallAttempt) -> None:
        """Emit *attempt* as it stood when the frame was made, however much has changed since."""
        await self._emit("update_install_progress", attempt.to_wire())

    async def _offered_release(self) -> LatestRelease | None:
        if not self._installed_program:
            return None
        release = await self._releases.last_seen_release()
        if release is None or release.tarball is None:
            return None
        return release if is_newer_version(release.version, self._current_version) else None

    async def _running_apps(self) -> tuple[str, ...] | None:
        """One reading of Steam's running apps; a reader that raised took none, which is never "nothing runs"."""
        try:
            return await self._steam.running_apps()
        except Exception as e:
            self._log_debug(f"[update] Steam's running apps could not be read: {e!r}")
            return None

    async def _reload_limit_waits(self) -> list[Wait]:
        """The reload limit's reason, where it holds; a reader that raised took no reading, never "one more is allowed".

        Its own reason rather than the limit's, which carries a time: the
        restart's panel is replaced only where the limit lets it, so a press
        waits rather than guess.
        """
        try:
            frees_at = await self._steam.reload_frees_at()
        except Exception as e:
            self._log_debug(f"[update] Steam's interface reload limit could not be read: {e!r}")
            return [Wait(WaitReason.INTERFACE_RELOAD_LIMIT_UNKNOWN)]
        return [Wait(WaitReason.INTERFACE_RELOAD_LIMIT, frees_at=frees_at)] if frees_at is not None else []

    async def _waits(self) -> list[Wait]:
        apps = await self._running_apps()
        limit = await self._reload_limit_waits()
        return [*_app_waits(apps), *self._work_waits(), *limit]

    def _work_waits(self) -> list[Wait]:
        """Every reason but Steam's two readings: the work in flight, read from memory in one loop turn.

        A claim held on the prune conflicts counts towards the reason that
        names its work, a read-only one towards none, and one nothing
        classified towards ``other_work``: any work of this process a restart
        would cut short makes a press wait.
        """
        claimed = claim_reasons(self._held_claims())
        return [
            Wait(reason)
            for reason, busy in (
                (WaitReason.LIBRARY_SYNC, self._library_sync_in_flight()),
                (WaitReason.ROM_DOWNLOADS, bool(self._rom_downloads_in_flight())),
                (WaitReason.SAVE_SYNC, self._save_sync_in_flight()),
                (WaitReason.FIRMWARE_DOWNLOADS, self._firmware_downloads_in_flight()),
                (WaitReason.SAVE_DIRECTORY_MOVE, self._save_directory_move_in_flight()),
                (WaitReason.REMOVED_GAMES_CLEANUP, self._cleanup_running()),
                (WaitReason.RETRODECK_MIGRATION, self._migration_running()),
                (WaitReason.OTHER_WORK, False),
            )
            if busy or reason in claimed
        ]

    def _paused_downloads(self) -> int:
        downloads = self._download_queue().get("downloads", [])
        return sum(1 for entry in downloads if entry.get("status") == "paused")

    async def _failed_before(self, version: str) -> bool:
        attempt = self._attempt
        if attempt is not None and attempt.version == version and attempt.step is InstallStep.FAILED:
            return True
        record = await self._loop.run_in_executor(None, self._read_update_failure)
        standing = standing_update_failure(record, self._current_version)
        return standing is not None and standing.attempted_version == version

    @staticmethod
    def _in_progress_refusal() -> dict[str, Any]:
        return {"success": False, "reason": "update_in_progress", "message": "An update is already being installed"}


def _failure_at(attempt: InstallAttempt | None) -> InstallFailure:
    """What an attempt that failed in an unforeseen way is reported as, by the step it had reached.

    Past the download every check of it reports its own failure, and a Steam
    reading that raised is one that could not be taken, so what is left is the
    way to the installer's start.
    """
    if attempt is not None and attempt.step is InstallStep.DOWNLOADING:
        return InstallFailure.DOWNLOAD_FAILED
    return InstallFailure.INSTALLER_NOT_STARTED


def _stopped_wire(stopped: UpdateAttemptRecord, toast_owed: bool) -> dict[str, Any]:
    return {
        "attempted_version": stopped.attempted_version,
        "from_version": stopped.from_version,
        "started_at": stopped.started_at,
        "toast_owed": toast_owed,
    }


def _app_waits(apps: tuple[str, ...] | None) -> list[Wait]:
    if apps is None:
        return [Wait(WaitReason.RUNNING_APPS_UNKNOWN)]
    if apps:
        return [Wait(WaitReason.APP_RUNNING, apps=apps)]
    return []
