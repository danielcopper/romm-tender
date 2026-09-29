"""Installing a newer release from the panel: everything about it that is pure.

Contract: the names an install from the panel goes by — the transient unit the
installer runs in, the environment it is handed, where the downloaded release is
put — and the vocabulary its answers are given in: why a press has to wait, the
steps an attempt passes through, and how one fails. Downloading, hashing,
unpacking and starting the installer stay in adapters; the attempt itself stays
in the service.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from domain.app_directories import (
    ENV_BIN_DIR,
    ENV_CACHE_DIR,
    ENV_CODE_DIR,
    ENV_CONFIG_DIR,
    ENV_DATA_DIR,
    ENV_STATE_DIR,
    XDG_CONFIG_HOME,
)
from domain.update_release import ENV_RELEASE_API

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from domain.app_directories import AppDirectories

# The transient unit the installer runs in. Not the service's own unit: the
# installer stops that one, and would stop itself with it.
INSTALLER_UNIT = "romm-tender-update"

# Under the cache root: nothing in it is needed once the attempt is over.
UPDATE_DIR_NAME = "update"

# Where ``scripts/package.sh`` puts the installer inside the tarball, and the
# name it is extracted under.
INSTALLER_MEMBER = "romm-tender/install.sh"
INSTALLER_NAME = "install.sh"

# ``install.sh``'s own names for its test seams and its interpreter. The
# interpreter is handed over because the installer writes it into the unit's
# ``ExecStart`` and falls back to ``/usr/bin/python3`` without it.
ENV_PYTHON = "TENDER_PYTHON"
ENV_DOWNLOAD_BASE = "TENDER_DOWNLOAD_BASE"
ENV_UPDATE_WAIT = "TENDER_UPDATE_WAIT"

# Handed on only where this process has them. ``XDG_CONFIG_HOME`` decides where
# the installer writes the unit file, which no ``TENDER_*`` variable does.
_PASSED_ON_WHEN_SET = (XDG_CONFIG_HOME, ENV_RELEASE_API, ENV_DOWNLOAD_BASE, ENV_UPDATE_WAIT)


class WaitReason(StrEnum):
    """Why an install may not start yet; the panel words each one."""

    APP_RUNNING = "app_running"
    RUNNING_APPS_UNKNOWN = "running_apps_unknown"
    LIBRARY_SYNC = "library_sync"
    ROM_DOWNLOADS = "rom_downloads"
    SAVE_SYNC = "save_sync"
    FIRMWARE_DOWNLOADS = "firmware_downloads"
    SAVE_DIRECTORY_MOVE = "save_directory_move"
    REMOVED_GAMES_CLEANUP = "removed_games_cleanup"
    RETRODECK_MIGRATION = "retrodeck_migration"
    OTHER_WORK = "other_work"
    INTERFACE_RELOAD_LIMIT = "interface_reload_limit"


# The claims on the prune conflicts — an operation named after its endpoint, or
# a lease named after its key — whose work one of the reasons above already
# names. Every other claim held is ``other_work``. The save operations outside
# the device gate count as the save sync, the end of a session among them,
# since its work is the post-exit sync.
_CLAIMS_NAMED_BY_A_REASON: Mapping[str, WaitReason] = MappingProxyType(
    {
        **dict.fromkeys(
            ("start_sync", "sync_preview", "sync_apply_delta", "report_unit_results", "sync_complete", "sync_stale"),
            WaitReason.LIBRARY_SYNC,
        ),
        **dict.fromkeys(("start_download", "resume_download", "download_complete"), WaitReason.ROM_DOWNLOADS),
        **dict.fromkeys(
            (
                "pre_launch_sync",
                "sync_rom_saves",
                "sync_all_saves",
                "resolve_sync_conflict",
                "switch_slot",
                "confirm_slot_choice",
                "delete_slot",
                "saves_rollback_to_version",
                "copy_save_to_slot",
                "delete_local_saves",
                "delete_platform_saves",
                "finalize_game_session",
            ),
            WaitReason.SAVE_SYNC,
        ),
        "prune_complete": WaitReason.REMOVED_GAMES_CLEANUP,
        **dict.fromkeys(("migrate_retrodeck_files", "migration_relaunch_options"), WaitReason.RETRODECK_MIGRATION),
    }
)


def claim_reasons(claims: Iterable[str]) -> frozenset[WaitReason]:
    """The reasons the held *claims* stand for: the one that names each, and ``other_work`` for any none names."""
    return frozenset(_CLAIMS_NAMED_BY_A_REASON.get(claim, WaitReason.OTHER_WORK) for claim in claims)


def claims_named_by(reason: WaitReason) -> frozenset[str]:
    """Every claim *reason* already names."""
    return frozenset(claim for claim, named in _CLAIMS_NAMED_BY_A_REASON.items() if named is reason)


class InstallStep(StrEnum):
    """Where an attempt is. ``installer_started`` is the last one this process sees succeed."""

    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLER_STARTED = "installer_started"
    FAILED = "failed"


class InstallFailure(StrEnum):
    """How an attempt ended before the installer stopped this process."""

    DOWNLOAD_FAILED = "download_failed"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    INSTALLER_NOT_STARTED = "installer_not_started"
    INSTALLER_STOPPED = "installer_stopped"
    # Asked again right before the installer starts: a game started during the
    # download, or no reading of whether one had, ends the attempt there.
    GAME_STARTED = "game_started"
    RUNNING_APPS_UNKNOWN = "running_apps_unknown"


@dataclass(frozen=True)
class Wait:
    """One reason a press waits.

    ``apps`` names what is running, for ``app_running`` alone; ``frees_at`` is
    the epoch second ``interface_reload_limit`` ends at, for that one alone.
    """

    reason: WaitReason
    apps: tuple[str, ...] = ()
    frees_at: float | None = None

    def to_wire(self) -> dict[str, Any]:
        """The JSON shape: ``reason`` always, ``apps`` and ``frees_at`` only on the reason they belong to."""
        wire: dict[str, Any] = {"reason": self.reason.value}
        if self.reason is WaitReason.APP_RUNNING:
            wire["apps"] = list(self.apps)
        if self.reason is WaitReason.INTERFACE_RELOAD_LIMIT:
            wire["frees_at"] = self.frees_at
        return wire


@dataclass(frozen=True)
class InstallAttempt:
    """One press, as far as it got. ``failure`` is set exactly when ``step`` is ``failed``."""

    version: str
    step: InstallStep
    bytes_done: int = 0
    bytes_total: int | None = None
    failure: InstallFailure | None = None

    def to_wire(self) -> dict[str, Any]:
        """The JSON shape the state answer and the progress event both carry."""
        return {
            "version": self.version,
            "step": self.step.value,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "failure": self.failure.value if self.failure is not None else None,
        }


def installer_command(installer: str, tarball: str) -> tuple[str, ...]:
    """The installer's command line: an update from the local *tarball*, asking nothing.

    Run through ``/bin/bash`` rather than executed, so the file needs no mode
    of its own.
    """
    return ("/bin/bash", installer, "--from", tarball, "--yes")


def installer_environment(
    environ: Mapping[str, str], directories: AppDirectories, python: str
) -> tuple[tuple[str, str], ...]:
    """The variables the installer is started with, as ``(name, value)`` pairs.

    The six directories as this process resolved them — the installer writes
    them into the unit, and a transient unit starts from the user manager's
    environment rather than this process's — then *python*, then each
    pass-through variable *environ* sets to something non-empty.
    """
    pairs = [
        (ENV_CODE_DIR, directories.code_dir),
        (ENV_CONFIG_DIR, directories.config_dir),
        (ENV_DATA_DIR, directories.data_dir),
        (ENV_CACHE_DIR, directories.cache_dir),
        (ENV_STATE_DIR, directories.state_dir),
        (ENV_BIN_DIR, directories.bin_dir),
        (ENV_PYTHON, python),
    ]
    pairs.extend((name, environ[name]) for name in _PASSED_ON_WHEN_SET if environ.get(name))
    return tuple(pairs)
