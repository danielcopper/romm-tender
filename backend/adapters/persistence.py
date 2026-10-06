"""Persistence adapter — pure I/O for ``settings.json``.

Handles atomic writes, file locking, and schema version stamping for
``settings.json``. Migration logic lives in ``domain/state_migrations.py``.
"""

import contextlib
import copy
import fcntl
import json
import logging
import os
from typing import Any, Protocol

from adapters.system_clock import SystemClock
from domain.state_migrations import unreadable_settings

_SETTINGS_VERSION = 13
_LOCK_EXT = ".lock"

# The settings file's name, and the only spelling of it in the codebase.
# ``scripts/check_settings_owner.py`` confines the literal here as a PROXY for
# "every write goes through the owner" — name-confinement, not dataflow: a
# module handed an already-built path is not caught, as that gate's own
# docstring says. It is public for the pre-install check, which copies the live
# file (``bootstrap/check.py``); the gate carries its own copy of the literal
# rather than importing this one.
SETTINGS_FILENAME = "settings.json"


class _ClockPort(Protocol):
    """Minimal wall-clock port the persistence adapter consumes.

    Adapters must not import ``services.protocols`` (import-linter forbids
    ``adapters -> services``), so this declares the single Clock method the
    adapter needs — ``time()`` for the corrupt-file backup stamp. The
    production ``adapters.system_clock.SystemClock`` and the test
    ``FakeClock`` both satisfy it structurally.
    """

    def time(self) -> float:
        """Return the current Unix timestamp in seconds since the epoch."""
        ...


DEFAULT_SETTINGS: dict[str, Any] = {
    "romm_url": "",
    "romm_api_token": None,
    "romm_api_token_id": None,
    "romm_api_token_origin": None,
    "romm_api_token_source": None,
    # The signed-in RomM user's own id (``/api/users/me`` ``id``), or ``None``
    # when identity is not yet known. Bound to the token like the device id —
    # stamped at sign-in, backfilled lazily on a connection check, cleared on
    # sign-out. Drives the collection owner scope; ``None`` makes the ``own``
    # scope hide nothing (the non-breaking fallback).
    "romm_user_id": None,
    # Extra HTTP headers every request to the configured RomM origin carries, for
    # a server behind an authenticating reverse proxy. A LIST of
    # ``{"name", "value"}`` rather than a map, so the modal shows the rows back in
    # the order they were entered. Values are proxy credentials: never sent to the
    # frontend, never logged.
    "romm_custom_headers": [],
    "enabled_platforms": {},
    "enabled_collections": {"standard": {}, "smart": {}, "virtual": {}},
    "collection_create_platform_groups": False,
    # QAM collection owner-scope: ``"all"`` (default — every collection the
    # server lists) or ``"own"`` (only the signed-in user's own collections;
    # virtual collections have no owner and always survive).
    "collection_owner_scope": "all",
    # Steam-collection naming mode: ``"merge"`` (default) or ``"by_label"``, as
    # docs/architecture/steam-non-steam-shortcuts.md § Collection naming mode
    # describes. Applies on the next normal sync via the reporter key +
    # complete-set reconcile.
    "collection_naming_mode": "merge",
    "preferred_region": "auto",
    # User intent for the sync button: apply without being asked first. Stored
    # for a reader to choose between a preview and a run; no backend sync path
    # consults it.
    "skip_preview": False,
    "steam_input_mode": "default",
    "steamgriddb_api_key": "",
    "romm_allow_insecure_ssl": False,
    "log_level": "warn",
    "save_sync_enabled": False,
    "sync_before_launch": True,
    "sync_after_exit": True,
    "default_slot": "autosave",
    "autocleanup_limit": 10,
    "device_name": None,
    "platform_cores": {},
    # The emulator sources' order and the switched-off ones, both lists of the
    # resolver's installation kinds and never folders, so a source that moves
    # keeps its place and its switch. Empty order: the resolver's own probe
    # order. Switched-off is a deny-list, so a source seen for the first time is
    # on. ``domain/emulator_sources.py`` reads both.
    "emulator_source_order": [],
    "emulator_sources_off": [],
}


class PersistenceAdapter:
    """Thin I/O layer for ``settings.json``.

    Parameters
    ----------
    settings_dir:
        Absolute path to the directory that holds ``settings.json`` — the
        config root this run was told about.
    logger:
        A standard-library ``logging.Logger`` instance.
    clock:
        Wall-clock source for the corrupt-file backup stamp. Keyword-only;
        defaults to a real :class:`adapters.system_clock.SystemClock`.
        Bootstrap passes the shared instance so the whole composition root
        reads one clock.
    """

    def __init__(
        self,
        settings_dir: str,
        logger: logging.Logger,
        *,
        clock: _ClockPort | None = None,
    ) -> None:
        self._settings_dir = settings_dir
        self._logger = logger
        self._clock: _ClockPort = clock if clock is not None else SystemClock()
        # Transient load-time signal of a corrupt-settings reset on the last
        # ``load_settings``. ``None`` means no reset; otherwise
        # ``{"backed_up_to": <basename>}``. Bootstrap reads this after migration
        # and folds it into the settings dict as the persistent
        # ``_settings_reset_notice`` marker (which survives a backend restart
        # and is cleared only by its dismissal); this attribute itself is never
        # written to disk.
        self._corrupt_reset: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _locked_write(self, path: str, data: dict[str, Any]) -> None:
        """Crash-safe atomic write of *data* to *path* under an exclusive lock.

        Uses the standard write-tmp → fsync(tmp) → rename → fsync(dir) recipe so
        a power loss (a real hazard on the Steam Deck) can never leave a
        truncated or empty ``settings.json``: the tmp file's bytes are forced to
        disk *before* the rename, and the directory entry the rename creates is
        forced to disk *after* it. The directory fsync is best-effort — on the
        rare filesystem where it raises, the error is logged and swallowed so it
        never fails an otherwise-successful write.
        """
        dirname = os.path.dirname(path)
        os.makedirs(dirname, exist_ok=True)
        tmp_path = path + ".tmp"
        lock_fd = os.open(path + _LOCK_EXT, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
            # fsync the directory so the rename's directory-entry update is
            # durable. Best-effort: some filesystems reject a dir fsync.
            try:
                dir_fd = os.open(dirname, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as exc:
                self._logger.debug("directory fsync after settings write failed (non-fatal): %s", exc)
        finally:
            os.close(lock_fd)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def load_settings(self) -> dict[str, Any]:
        """Read ``settings.json`` and apply defaults.

        Migration logic (e.g. renaming old keys) is intentionally NOT
        included here — that belongs in ``domain/state_migrations.py``.

        A missing file is a legitimate first run — defaults are returned
        silently, at the current version, with no backup and no reset flag. A
        file ``unreadable_settings`` objects to — older than the oldest version
        this release reads, without a usable version, or not a JSON object —
        is not converted: a warning names what was found and the defaults are
        returned, which the immediate bootstrap save writes over the file with
        no backup, since everything such a file held can be entered again. An
        *unparseable* file is the data-loss hazard: rather than silently
        returning defaults (which the immediate bootstrap save would then write
        over the corrupt file, destroying the user's server URL, token, and
        selections), the unparseable file is backed up to
        ``settings.json.corrupt-<ts>`` and a transient :attr:`_corrupt_reset`
        flag is set so bootstrap can persist the reset marker. The error is logged loudly. If the backup rename
        itself fails, the error is logged and defaults are still returned so
        boot never crashes.
        """
        settings_path = os.path.join(self._settings_dir, SETTINGS_FILENAME)
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except FileNotFoundError:
            # Legitimate first run: no file, no backup, no reset flag.
            settings = {}
        except json.JSONDecodeError:
            self._logger.error("settings.json is corrupt/unparseable — backing up and resetting to defaults")
            self._quarantine_corrupt_settings(settings_path)
            settings = {}
        else:
            unreadable = unreadable_settings(settings)
            if unreadable is not None:
                self._logger.warning("settings.json is not read, because %s — starting on the defaults", unreadable)
                settings = {}

        for key, default in DEFAULT_SETTINGS.items():
            # deepcopy so a mutable default (e.g. the ``platform_cores`` / enabled-*
            # maps) is never aliased to the shared DEFAULT_SETTINGS object — a later
            # in-place mutation (a per-platform core write) would otherwise leak back
            # into the module-level template and contaminate the next load.
            settings.setdefault(key, copy.deepcopy(default))

        settings.setdefault("version", _SETTINGS_VERSION)
        return settings

    def _quarantine_corrupt_settings(self, settings_path: str) -> None:
        """Rename an unparseable ``settings.json`` aside and record the reset.

        The backup name is ``settings.json.corrupt-<ts>`` where ``<ts>`` is the
        injected clock's epoch seconds (an int — filesystem-safe, no ``:``), so
        the original bytes survive for manual recovery while the immediate
        bootstrap save writes fresh defaults to ``settings.json``. If that name
        is already taken (two corruptions in the same wall-clock second, or a
        clock rollback onto an existing stamp) a ``-<n>`` suffix disambiguates
        so an older backup is never clobbered. Sets :attr:`_corrupt_reset` so
        bootstrap can persist the reset marker. The backup is restricted to
        ``0600`` like the live file, since it still holds the credentials. A
        failed rename or chmod (e.g. perms) is logged and swallowed — boot must
        not crash; after a failed chmod the backup and the reset still stand.
        """
        stamp = int(self._clock.time())
        backup_name = f"settings.json.corrupt-{stamp}"
        backup_path = os.path.join(self._settings_dir, backup_name)
        n = 1
        while os.path.exists(backup_path):
            backup_name = f"settings.json.corrupt-{stamp}-{n}"
            backup_path = os.path.join(self._settings_dir, backup_name)
            n += 1
        try:
            os.replace(settings_path, backup_path)
        except OSError as exc:
            self._logger.error("could not back up corrupt settings.json to %s: %s", backup_name, exc)
            return
        try:
            os.chmod(backup_path, 0o600)
        except OSError as exc:
            self._logger.error("could not restrict %s to 0600: %s", backup_name, exc)
        self._corrupt_reset = {"backed_up_to": backup_name}

    @property
    def corrupt_reset(self) -> dict[str, Any] | None:
        """The corrupt-settings reset recorded by the last ``load_settings``.

        ``None`` when the file parsed cleanly (or was a legitimate first run);
        otherwise ``{"backed_up_to": <basename>}`` naming the quarantine backup.
        Bootstrap reads this once after migration to fold the reset into the
        persistent ``_settings_reset_notice`` settings marker.
        """
        return self._corrupt_reset

    def save_settings(self, data: dict[str, Any]) -> None:
        """Atomic write of *data* to ``settings.json`` with flock, stamping version.

        The version stamp never *down*-stamps: ``data["version"]`` is set to
        ``max(stored_version, _SETTINGS_VERSION)``. A file written by a newer
        version of Tender (stored version > current) is preserved as-is so a later
        re-upgrade does not re-run migrations against down-stamped data;
        an absent or older version is stamped up to ``_SETTINGS_VERSION``.
        A non-numeric stored version (e.g. a hand-edited ``"abc"``) coerces to
        ``0`` rather than raising — the stamp must never crash the boot-time save.
        """
        try:
            stored = int(data.get("version", 0) or 0)
        except (TypeError, ValueError):
            stored = 0
        data["version"] = max(stored, _SETTINGS_VERSION)
        settings_path = os.path.join(self._settings_dir, SETTINGS_FILENAME)
        self._locked_write(settings_path, data)


class SettingsPersisterAdapter:
    """Adapter view exposing the ``SettingsPersister`` Protocol.

    Binds a :class:`PersistenceAdapter` and the live ``settings`` dict
    so services receive a zero-arg ``save_settings()`` seam. Lives in
    the adapters layer so services depend only on the Protocol, never
    on this class.
    """

    def __init__(self, persistence: PersistenceAdapter, settings: dict[str, Any]) -> None:
        self._persistence = persistence
        self._settings = settings

    def save_settings(self) -> None:
        self._persistence.save_settings(self._settings)


class PlatformCoreReaderAdapter:
    """Adapter view exposing the ``PlatformCoreReader`` Protocol over settings.

    Binds the live ``settings`` dict so the per-platform core selection is
    always read from ``settings["platform_cores"]`` as it stands at call time.
    The bound reference is the same dict every writer mutates, so a fan-out
    that resolves a freshly-written platform core sees the new value rather
    than a stale snapshot.
    """

    def __init__(self, settings: dict[str, Any]) -> None:
        self._settings = settings

    def get_platform_core(self, platform_slug: str) -> str | None:
        return self._settings.get("platform_cores", {}).get(platform_slug)
