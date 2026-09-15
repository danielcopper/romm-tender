"""Single owner of putting the shortcut launcher where the shortcuts point at it.

Everything the launcher's installation touches on the filesystem lives here:
reading the copy this release ships, deciding whether the one already at the
destination is it, and writing a replacement through a staging file that is
renamed into place. Where the destination IS is not decided here —
``domain.user_data_location.launcher_path`` owns that, and the composition root
hands the answer in.
"""

from __future__ import annotations

import contextlib
import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging

# Suffix of the file the replacement is written to before it is renamed onto the
# destination. It sits beside the destination so the rename stays within one
# filesystem, which is what makes it atomic.
_STAGING_SUFFIX = ".installing"

# Owner-only, and executable because Steam runs it as the shortcut's exe.
# Nothing outside the owner has any business with it: it sits under the user's
# own data root and is executed by the account that owns that root, so group and
# other are granted nothing (python:S2612).
_LAUNCHER_MODE = 0o700


class LauncherInstallAdapter:
    """Keeps this release's shortcut launcher at its home outside the plugin folder.

    Never the thing that brings the data root into existence: the composition
    root calls this only once the start-up migration's data half stands at that
    root, because the migration reads any entry in it as a finished move. The
    ordering, and what it costs to lose it, is stated where it is enforced —
    ``bootstrap/adapters.py``, at the call.
    """

    def __init__(self, *, source: str, destination: str, logger: logging.Logger) -> None:
        self._source = source
        self._destination = destination
        self._logger = logger

    def install(self) -> bool:
        """Put this release's launcher at its destination, and say whether it is there.

        Never raises. ``False`` means no shortcut may be pointed at the
        destination yet: the answer is what stands between a shortcut and an
        ``exe`` naming a file nothing put there, so anything short of "this
        release's launcher is at that path, executable" is reported as not
        installed rather than assumed.

        A launcher already matching the shipped copy is left alone, and the
        reason is only that there is nothing to gain from writing it again: a
        running game is protected by the staging rename in :meth:`_write`, not
        by this skip. What the skip does keep is the inode, which is worth
        having but is not what makes a replacement safe.
        """
        try:
            shipped = self._read(self._source)
        except OSError as e:
            self._logger.warning(f"Could not read the launcher this release ships at {self._source}: {e}")
            return False
        if self._already_in_place(shipped):
            return True
        try:
            self._write(shipped)
        except OSError as e:
            self._logger.warning(f"Could not install the launcher at {self._destination}: {e}")
            return False
        self._logger.info(f"Installed this release's launcher at {self._destination}")
        return True

    @staticmethod
    def _read(path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    def _already_in_place(self, shipped: bytes) -> bool:
        """Whether the destination already holds this release's launcher, at the mode it wants.

        The mode is compared exactly, not just for the executable bit, and it
        answers two different questions in one. A launcher whose content is
        right but which is not executable fails every game the same way a
        missing one does. A launcher left WIDER than :data:`_LAUNCHER_MODE` —
        every install written before this file was narrowed to owner-only — is
        not broken, and is rewritten anyway, because the ordinary write is the
        only path that narrows it and it costs the same read either way.

        Deliberately not ``os.access(..., X_OK)``: that answers for the account
        this plugin runs as, and the account that matters is the one Steam
        launches under. Where those differ no mode would help, and where they
        are the same the mode comparison already says it.
        """
        try:
            with open(self._destination, "rb") as handle:
                current = handle.read()
            mode = stat.S_IMODE(os.stat(self._destination).st_mode)
        except OSError:
            return False
        return current == shipped and mode == _LAUNCHER_MODE

    def _write(self, content: bytes) -> None:
        """Write *content* beside the destination, then rename it on.

        The mode is set on the staging file rather than after the rename, so the
        launcher is never visible at its own path in a state a shortcut could
        catch it in — it arrives complete and at its mode or not at all. The
        staging file is never wider than the file it becomes, so there is no
        window in which the launcher is readable by anyone the final one is not.
        ``os.open``'s mode argument is masked by the process umask, which is not
        ours to assume, so the mode is set explicitly afterwards.

        The directory is created owner-only for the same reason. An existing one
        keeps whatever mode it has — ``exist_ok`` does not restate it, and this
        adapter owns the launcher rather than the tree around it.
        """
        staging = self._destination + _STAGING_SUFFIX
        os.makedirs(os.path.dirname(self._destination), mode=_LAUNCHER_MODE, exist_ok=True)
        try:
            handle = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _LAUNCHER_MODE)
            with os.fdopen(handle, "wb") as launcher:
                launcher.write(content)
            os.chmod(staging, _LAUNCHER_MODE)
            os.replace(staging, self._destination)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(staging)
            raise
