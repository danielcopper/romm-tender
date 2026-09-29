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
    stopped_attempt,
)
from domain.update_outcome import standing_update_failure
from domain.version import is_newer_version

if TYPE_CHECKING:
    import logging

    from domain.update_install import UpdateAttemptRecord
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
        UpdateAttemptStore,
        UpdateFailureFn,
        UpdateStagingStore,
        WorkInFlightFn,
    )

# At most one progress frame per this many seconds while bytes arrive, as the
# ROM downloads do; the last one of a download always goes out.
_PROGRESS_EMIT_SECONDS = 0.5

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
    Steam interface reader the host fills in, the installer's record of a
    rolled-back update and this program's own record of an attempt, the
    download, staging and unit seams with the environment the installer starts
    with, and the runtime infrastructure.
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
        # Whether this attempt's record was written, so a failure this process
        # reports itself takes it away again.
        self._record_written = False
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
        again, and kept for the notice on Main until it is dismissed, a new
        attempt starts, or the running version changes. Any other record — an
        update that went through, one the installer rolled back, a version
        that moved since — is removed.
        """
        record = self._attempts.read()
        if record is None:
            return
        failure = standing_update_failure(self._read_update_failure(), self._current_version)
        stopped = stopped_attempt(record, self._current_version, failure)
        if stopped is None:
            self._remove_record_io()
            return
        self._logger.warning(
            f"update: the installer for {stopped.attempted_version} started at {stopped.started_at} stopped without "
            f"updating; still on {self._current_version} — what it said is in journalctl --user -u {INSTALLER_UNIT}"
        )
        self._stopped = stopped
        self._attempt = InstallAttempt(
            version=stopped.attempted_version, step=InstallStep.FAILED, failure=InstallFailure.INSTALLER_STOPPED
        )

    def get_stopped_update_attempt(self) -> dict[str, Any] | None:
        """The attempt an earlier start's installer stopped without updating, until dismissed or superseded.

        ``{"attempted_version", "from_version", "started_at"}``, or ``None``.
        Judged by :meth:`note_start`; the notice on Main shows it.
        """
        stopped = self._stopped
        if stopped is None:
            return None
        return {
            "attempted_version": stopped.attempted_version,
            "from_version": stopped.from_version,
            "started_at": stopped.started_at,
        }

    async def dismiss_stopped_attempt(self) -> dict[str, Any]:
        """Wave away the notice of an installer that stopped without updating, and remove its record.

        Returns ``{"success": True}``. A record that could not be removed is
        logged and is judged again at the next start — a notice shown twice
        rather than one lost.
        """
        self._stopped = None
        await self._loop.run_in_executor(None, self._remove_record_io)
        return {"success": True}

    async def shutdown(self) -> None:
        """Stop the attempt's task and a download still running on its thread; the hold ends with the process."""
        self._stopping = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def get_update_install_state(self) -> dict[str, Any]:
        """Report whether an install is offered, what it waits for, and how the latest attempt went.

        Returns ``{"offered", "version", "wait_reasons", "paused_downloads",
        "attempt", "try_again"}``. ``offered`` holds on the installed program
        with the check switched on and a stored release newer than the running
        version, which ``version`` names (``None`` where nothing is offered).
        ``wait_reasons`` lists every reason a press would be refused now, each
        ``{"reason"}`` plus ``apps`` for ``app_running`` and ``frees_at`` for
        ``interface_reload_limit``; empty where nothing is offered or an
        attempt holds the rule. ``paused_downloads`` counts the paused ROM
        downloads a restart would lose. ``attempt`` is the latest attempt's
        ``{"version", "step", "bytes_done", "bytes_total", "failure"}``, or
        ``None``. ``try_again`` says the offered version already failed once —
        an attempt in this process, or an update the installer rolled back.
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
        if self._holding:
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
        apps = await self._steam.running_apps()
        frees_at = await self._steam.reload_frees_at()
        # Asked again after the readings: a second press may have started an
        # attempt while this one waited for them.
        if self._holding:
            return self._in_progress_refusal()
        waits = [*_app_waits(apps), *self._work_waits(frees_at)]
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
        apps = await self._steam.running_apps()
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
        that failed. Where it cannot say either, the attempt goes on as started
        and the watch finds out, rather than giving the rule back while the
        installer may run.
        """
        self._logger.warning(
            f"update: starting the installer for {version} gave no answer ({no_answer}); asking whether it runs"
        )
        active, _why = await self._ask_unit()
        return repr(no_answer) if active is False else None

    async def _watch_installer(self, version: str) -> None:
        """Wait for the installer to stop this process; it ending first is the attempt failing.

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
        if not self._installed_program or not self._releases.is_check_enabled():
            return None
        release = await self._releases.last_seen_release()
        if release is None or release.tarball is None:
            return None
        return release if is_newer_version(release.version, self._current_version) else None

    async def _waits(self) -> list[Wait]:
        apps = await self._steam.running_apps()
        frees_at = await self._steam.reload_frees_at()
        return [*_app_waits(apps), *self._work_waits(frees_at)]

    def _work_waits(self, frees_at: float | None) -> list[Wait]:
        """Every reason but Steam's two readings, read from memory in one loop turn, then the reload limit.

        A claim held on the prune conflicts counts towards the reason that
        names its work, and every other one is ``other_work``: any work of
        this process a restart would cut short makes a press wait.
        """
        claimed = claim_reasons(self._held_claims())
        waits = [
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
        if frees_at is not None:
            waits.append(Wait(WaitReason.INTERFACE_RELOAD_LIMIT, frees_at=frees_at))
        return waits

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
    """What an attempt that failed in an unforeseen way is reported as, by the step it had reached."""
    if attempt is not None and attempt.step is InstallStep.DOWNLOADING:
        return InstallFailure.DOWNLOAD_FAILED
    return InstallFailure.INSTALLER_NOT_STARTED


def _app_waits(apps: tuple[str, ...] | None) -> list[Wait]:
    if apps is None:
        return [Wait(WaitReason.RUNNING_APPS_UNKNOWN)]
    if apps:
        return [Wait(WaitReason.APP_RUNNING, apps=apps)]
    return []
