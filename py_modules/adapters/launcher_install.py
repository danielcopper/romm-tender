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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging

# Suffix of the file the replacement is written to before it is renamed onto the
# destination. It sits beside the destination so the rename stays within one
# filesystem, which is what makes it atomic.
_STAGING_SUFFIX = ".installing"

_EXECUTABLE_MODE = 0o755


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

        A launcher already matching the shipped copy is left alone — untouched
        down to its inode, because a game running right now is executing that
        file and a replacement written over it would be read out from under the
        interpreter mid-line.
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
        """Whether the destination already holds this release's launcher, runnable.

        The executable bit is asked as well as the bytes: a launcher whose
        content is right but whose mode is not fails every game the same way a
        missing one does, and rewriting is the same cheap write either way.
        """
        try:
            with open(self._destination, "rb") as handle:
                current = handle.read()
        except OSError:
            return False
        return current == shipped and os.access(self._destination, os.X_OK)

    def _write(self, content: bytes) -> None:
        """Write *content* beside the destination, then rename it on.

        The mode is set on the staging file rather than after the rename, so the
        launcher is never visible at its own path in a state a shortcut could
        catch it in — it arrives complete and executable or not at all.
        ``os.open``'s mode argument is masked by the process umask, which is
        root's here and not ours to assume, so the bit is set explicitly.
        """
        staging = self._destination + _STAGING_SUFFIX
        os.makedirs(os.path.dirname(self._destination), exist_ok=True)
        try:
            handle = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _EXECUTABLE_MODE)
            with os.fdopen(handle, "wb") as launcher:
                launcher.write(content)
            os.chmod(staging, _EXECUTABLE_MODE)
            os.replace(staging, self._destination)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(staging)
            raise
