"""LegacyInstallService — the pre-rename install standing beside this one.

Owns one question the QAM asks at startup: does a second copy of this plugin sit
under the folder name releases used before 0.31.0, and is the data the user
built still over there. Nothing here mutates either install — the answer is a
warning, and the move it warns about is not yet written.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from typing import Any

    from services.protocols import PathExistsReader, ResolvedPathFn, SettingsPersister

# The folder releases up to 0.30.1 unpack into. Decky's CLI names the package
# after the directory CI checked the repository out into
# (``FilenameSource::Directory``), so the name followed the GitHub rename to
# ``romm-tender`` at 0.31.0 — neither spelling was chosen here, and neither can
# be changed from inside the plugin. Decky derives every data location from that
# same folder name (``decky_loader/plugin/sandboxed_plugin.py``), which is why
# the runtime directory below is asked the same question.
_LEGACY_PLUGIN_FOLDER = "decky-romm-sync"

# The user's answer to the one statement this card makes that they are free to
# ignore. User intent, so ``settings.json`` rather than ``kv_config``
# (CONTEXT.md, persistence boundary) — and no ``DEFAULT_SETTINGS`` entry or
# schema bump, because an absent key already means the only sensible default.
DISMISSED_KEY = "legacy_install_notice_dismissed"


@dataclass(frozen=True)
class LegacyInstallServiceConfig:
    """Frozen wiring bundle handed to ``LegacyInstallService.__init__``.

    Carries this install's own two Decky-assigned directories (``plugin_dir``
    holds the launcher the shortcuts written from this folder point at,
    ``runtime_dir`` the database), the generic path-exists probe, the path resolver that decides
    whether two spellings name one directory, the filename bootstrap gives the
    SQLite database — threaded in rather than restated here, because a second
    spelling of it would drift silently and this detection would go quiet without
    failing — and the runtime logger a swallowed path error is reported through.
    """

    plugin_dir: str
    runtime_dir: str
    db_filename: str
    path_exists: PathExistsReader
    resolve_path: ResolvedPathFn
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    logger: logging.Logger


class LegacyInstallService:
    """Reports the pre-rename plugin folder, and whether the data is still in it."""

    def __init__(self, *, config: LegacyInstallServiceConfig) -> None:
        self._plugin_dir = config.plugin_dir
        self._runtime_dir = config.runtime_dir
        self._db_filename = config.db_filename
        self._path_exists = config.path_exists
        self._resolve_path = config.resolve_path
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._logger = config.logger

    def dismiss_legacy_install_notice(self) -> dict[str, Any]:
        """Record that the user has answered the card, and keep it down for good.

        The one thing a Dismiss is allowed to do here: it hides a card whose
        condition is STILL TRUE. Every other notice in the panel ends when its
        condition does, and this one's condition — the older install standing
        beside ours — ends only when the user removes that folder. Keeping it is
        a legitimate end state, so the answer has to outlive the session; a card
        that came back at every Steam start is exactly the standing warning the
        Dismiss exists to prevent.

        It answers the removable statement alone. The card is shown regardless
        while the shortcuts still point into the older install, because nothing
        about that statement is optional and it is not what was dismissed.

        Idempotent — a second call re-writes the same value.
        """
        self._settings[DISMISSED_KEY] = True
        self._settings_persister.save_settings()
        return {"success": True}

    def get_legacy_install_notice(self) -> dict[str, bool]:
        """Report the older install the user must not remove.

        Returns ``{"pending": bool, "legacy_data_present": bool, "dismissed": bool}``. ``pending``
        is the notice: the legacy plugin folder is on disk and is not the folder
        this plugin runs from. Every Steam shortcut's ``exe`` names a launcher
        inside the folder it was written from, so removing that install stops the
        games from starting and nothing here can put the launcher back.

        ``legacy_data_present`` is half of the second sentence — the older
        install still has a database — and is False whenever ``pending`` is. The
        other half is "and this install shows nothing", which the panel already
        knows from ``get_sync_stats``; it is joined there rather than here, so
        this answer stays a question about two directories and the warning that
        prevents the irreversible action cannot be taken down by a library read.

        ``dismissed`` is the user's own answer, and the only part of this that IS
        persisted: it says they have chosen to keep the older install, which is
        a legitimate end state the panel must stop asking about.

        The two directory answers are computed on every call and persisted
        nowhere — that condition ends when the folder does, and a marker would
        outlive it.
        """
        if not self._stands_apart(self._legacy_twin(self._plugin_dir), self._plugin_dir):
            return {"pending": False, "legacy_data_present": False, "dismissed": False}
        return {
            "pending": True,
            "legacy_data_present": self._legacy_data_present(),
            "dismissed": bool(self._settings.get(DISMISSED_KEY, False)),
        }

    def _legacy_data_present(self) -> bool:
        """Answer whether the older install's runtime directory still holds a database.

        A path error here costs the user one sentence and never the warning: the
        narrow half is a nicety, the broad half is what stands between them and a
        removal nothing can undo, and both are computed into one dict. So this
        half degrades to False rather than propagating.

        ``OSError`` names the I/O hazard, and only it: anything else reaching
        here is a defect in a seam rather than a disk that would not answer, and
        a sentence quietly dropped is the worst way to learn about one.
        ``PathExistsReader`` cannot raise as ``os.path.exists`` (it returns False
        on ``OSError`` and ``ValueError``), but it is a Protocol and the next
        implementation need not share that. A failing RESOLVER reaches here only
        outside that type — ``_stands_apart`` answers an ``OSError`` from it
        definitely rather than raising.
        """
        try:
            return self._probe_legacy_database()
        except OSError as e:
            self._logger.warning(f"Could not tell whether the older install still holds data: {e}")
            return False

    def _probe_legacy_database(self) -> bool:
        """Look for the database in the older install's runtime directory.

        Deliberately not "and ours does not": ``bootstrap`` creates our own
        database on the first boot, before the user has seen a single game, so
        that comparison is false from the first second of this install's life and
        would silence the sentence for good.

        The runtime pair is held apart on its own rather than on the plugin
        pair's verdict, because the two questions can answer differently: a data
        root reached through a symlink gives one directory two spellings, and
        without this guard our OWN database answers as the older install's — a
        fresh install told its library is still over there, pointing at the
        library it is looking at.
        """
        legacy_runtime_dir = self._legacy_twin(self._runtime_dir)
        if not self._stands_apart(legacy_runtime_dir, self._runtime_dir):
            return False
        return self._path_exists.exists(os.path.join(legacy_runtime_dir, self._db_filename))

    def _legacy_twin(self, ours: str) -> str:
        """Return the legacy install's counterpart of one of our own directories.

        Decky lays both installs out as siblings under one parent — ``plugins/``
        for the code, ``data/`` for the runtime state — so the counterpart is
        this directory's parent plus the old folder name. Taking the parent from
        our own path rather than composing it from ``DECKY_HOME`` asserts one
        layout fact less: wherever Decky put us, the other install is next door.
        """
        return os.path.join(os.path.dirname(ours), _LEGACY_PLUGIN_FOLDER)

    def _stands_apart(self, legacy: str, ours: str) -> bool:
        """Answer whether *legacy* exists and is a different directory from *ours*.

        Both sides are resolved before the comparison because an install running
        from the legacy folder is a real configuration — the maintainer's dev
        deploy targets it — and there the two paths name one directory, with
        nothing to warn about. A string comparison alone would also read a root
        reached through a symlink as two directories (#1838).

        When the resolver itself fails, the raw comparison is the answer rather
        than the failure. ``exists`` has already said the folder is there, so all
        that is missing is whether it is ours, and the strings settle that
        definitely in both directions that matter: equal for an install running
        from the legacy folder (silent), different for a real second install
        (card shown). Erring toward a card whose whole content is "leave this
        alone" is the cheap direction.

        ``OSError`` is what a failing resolution raises: ``os.path.realpath``'s
        ``_joinrealpath`` guards its ``os.lstat`` with ``except OSError`` and
        leaves the ``os.readlink`` beside it unguarded (CPython 3.11
        ``posixpath``), so whatever that ``readlink`` raises comes straight out.
        Two routes reach it, both measured. A symlink the process may lstat but
        may not read: ``/proc/<pid>/root`` is ``lrwxrwxrwx`` and readable through
        its parent, while reading it needs ptrace permission, so
        ``os.lstat("/proc/1/root")`` succeeds where ``os.readlink("/proc/1/root")``
        raises ``PermissionError``. And a link that changes or vanishes between
        the two calls, which is an ordinary race rather than an exotic one —
        ``FileNotFoundError`` escaped 19,851 times in 263,602 calls against a
        thread churning the link. Its one non-``OSError`` exit,
        ``ValueError: embedded null byte``, cannot arise here: both paths are
        built from Decky's own environment variables.
        """
        if not self._path_exists.exists(legacy):
            return False
        try:
            return self._resolve_path(legacy) != self._resolve_path(ours)
        except OSError as e:
            self._logger.warning(f"Could not resolve {legacy}; comparing paths as written: {e}")
            return legacy != ours
