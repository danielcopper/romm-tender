"""UpdateInstallService — installing the last seen release from the panel, and what a press waits for.

Owns one attempt at a time, from the press until the installer stops this
process or the attempt fails: whether a press may start one, the download and
its check against the digest the release states, the installer's start as a
unit of its own, and watching that unit for as long as this process lives. It
also owns the update rule every conflicting use case asks
(``is_update_in_progress``), held from a press that starts an attempt until
that attempt fails. Which release is meant is the release check's answer, read
through its owner. What the steps and reasons are called is in
``domain/update_install.py``; the download, the files, the hashing and the unit
are behind seams.
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
    installer_command,
)
from domain.update_outcome import standing_update_failure
from domain.version import is_newer_version

if TYPE_CHECKING:
    import logging

    from domain.update_release import LatestRelease, ReleaseTarball
    from services.protocols import (
        ActiveDownloadRomIdsFn,
        Clock,
        DownloadQueueFn,
        EventEmitter,
        LastSeenReleaseReader,
        ReleaseAssetDownloadFn,
        Sleeper,
        SteamInterfaceReader,
        TransientUnitRunner,
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


@dataclass(frozen=True)
class UpdateInstallServiceConfig:
    """Frozen wiring bundle handed to ``UpdateInstallService.__init__``.

    Carries the release check the release is read through, the running version
    and whether this process is the installed program, one reader per kind of
    work a restart would cut short, the Steam interface reader the host fills
    in, the installer's record of a rolled-back update, the download, staging
    and unit seams with the environment the installer starts with, and the
    runtime infrastructure.
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
    read_update_failure: UpdateFailureFn
    download_asset: ReleaseAssetDownloadFn
    staging: UpdateStagingStore
    units: TransientUnitRunner
    installer_environment: tuple[tuple[str, str], ...]
    emit: EventEmitter
    clock: Clock
    sleeper: Sleeper
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


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
        self._read_update_failure = config.read_update_failure
        self._download_asset = config.download_asset
        self._staging = config.staging
        self._units = config.units
        self._installer_environment = config.installer_environment
        self._emit = config.emit
        self._clock = config.clock
        self._sleeper = config.sleeper
        self._loop = config.loop
        self._logger = config.logger
        self._attempt: InstallAttempt | None = None
        self._task: asyncio.Task[None] | None = None
        # Set in the same loop turn as the check a press passes, and cleared
        # only by an attempt that failed while this process still runs.
        self._holding = False
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

    async def shutdown(self) -> None:
        """Stop the attempt's task, if one runs; the hold ends with the process."""
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
        # Asked again after the reading: a second press may have started an
        # attempt while this one waited for Steam.
        if self._holding:
            return self._in_progress_refusal()
        waits = [*_app_waits(apps), *self._work_waits()]
        if waits:
            return {
                "success": False,
                "reason": "update_waiting",
                "message": "Something that an update would interrupt is still under way",
                "wait_reasons": [wait.to_wire() for wait in waits],
            }
        self._holding = True
        self._last_progress_emit = None
        self._bytes_seen = (0, None)
        self._attempt = InstallAttempt(version=release.version, step=InstallStep.DOWNLOADING)
        self._task = self._loop.create_task(self._run(release.version, release.tarball))
        await self._emit_attempt()
        return {"success": True}

    async def _run(self, version: str, tarball: ReleaseTarball) -> None:
        try:
            installer, path = await self._download_and_verify(version, tarball)
            await self._start_installer(version, installer, path)
            await self._watch_installer(version)
        except _AttemptFailedError as failed:
            await self._fail(version, failed.failure)

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
        self._logger.info(
            f"update: starting the installer for {version}; follow it with journalctl --user -u {INSTALLER_UNIT}"
        )
        try:
            why = await self._loop.run_in_executor(
                None,
                self._units.start,
                INSTALLER_UNIT,
                installer_command(installer, tarball_path),
                self._installer_environment,
            )
        except Exception as e:
            why = repr(e)
        if why is not None:
            self._logger.warning(f"update: the installer for {version} could not be started: {why}")
            raise _AttemptFailedError(InstallFailure.INSTALLER_NOT_STARTED)
        await self._advance(version, InstallStep.INSTALLER_STARTED)

    async def _watch_installer(self, version: str) -> None:
        """Wait for the installer to stop this process; it ending first is the attempt failing.

        A user manager that cannot be asked is not an answer, and neither is a
        seam that raised, so the watch goes on rather than calling the
        installer stopped and giving the rule back while it may still run.
        """
        while True:
            await self._sleeper.sleep(_WATCH_SECONDS)
            try:
                active = await self._loop.run_in_executor(None, self._units.is_active, INSTALLER_UNIT)
            except Exception as e:
                self._logger.debug(f"update: the installer's unit could not be asked about: {e!r}")
                continue
            if active is False:
                self._logger.warning(
                    f"update: the installer for {version} stopped without updating; "
                    f"what it said is in journalctl --user -u {INSTALLER_UNIT}"
                )
                raise _AttemptFailedError(InstallFailure.INSTALLER_STOPPED)

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
        self._attempt = InstallAttempt(version=version, step=InstallStep.FAILED, failure=failure)
        self._holding = False
        await self._emit_attempt()

    def _on_progress(self, done: int, total: int | None) -> None:
        """The download's byte count, from the thread it runs on; throttled, then handed to the loop."""
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
        self._loop.create_task(self._emit_frame(self._attempt))

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
        return [*_app_waits(await self._steam.running_apps()), *self._work_waits()]

    def _work_waits(self) -> list[Wait]:
        """Every reason other than Steam's running apps, read from memory in one loop turn."""
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
            )
            if busy
        ]
        frees_at = self._steam.reload_frees_at()
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


def _app_waits(apps: tuple[str, ...] | None) -> list[Wait]:
    if apps is None:
        return [Wait(WaitReason.RUNNING_APPS_UNKNOWN)]
    if apps:
        return [Wait(WaitReason.APP_RUNNING, apps=apps)]
    return []
