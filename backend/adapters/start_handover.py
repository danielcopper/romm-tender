"""Single owner of handing one start's command to the shortcut launcher.

The start check writes the command into ``<runtime dir>/start/<appId>`` just
before Steam starts the shortcut, and ``bin/tender-rom-launcher`` claims that
file, removes it and runs what it holds (#2221 D15). The runtime directory is
in memory and the user's alone, and emptied at logout; the folder here is
owner-only and every file in it readable by the user only. A file is written
whole: into a staging file beside it, then renamed onto its name, so the
launcher never reads one half written.

The record is the folder the command starts in, then the program and its
arguments, each ended by a NUL — the one byte no path and no argument can hold.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# The hand-over folder and every file in it are the user's alone.
_FOLDER_MODE = 0o700
_FILE_MODE = 0o600

_END = "\0"


class StartHandoverAdapter:
    """Writes one start's command where the shortcut launcher looks for it."""

    def __init__(self, *, folder: str) -> None:
        self._folder = folder

    def hand_over(self, app_id: int, folder: str, argv: Sequence[str], *, create_folder: bool) -> None:
        """Hand *argv*, started in *folder*, to the launcher for the next start of *app_id*.

        *create_folder* creates *folder* where it is missing. A hand-over left
        from an earlier press is replaced. Raises ``OSError`` where the folder
        or the file cannot be written, and ``ValueError`` for a value the
        record cannot hold.
        """
        words = (folder, *argv)
        if any(_END in word for word in words) or not argv:
            raise ValueError("a start's folder and command must be text without NUL, and name a program")
        if create_folder:
            os.makedirs(folder, exist_ok=True)
        os.makedirs(self._folder, mode=_FOLDER_MODE, exist_ok=True)
        os.chmod(self._folder, _FOLDER_MODE)
        handle, staging = tempfile.mkstemp(dir=self._folder, prefix=f".{app_id}.", suffix=".staging")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", errors="surrogateescape", newline="") as record:
                record.write("".join(word + _END for word in words))
            os.chmod(staging, _FILE_MODE)
            os.replace(staging, os.path.join(self._folder, str(app_id)))
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(staging)
            raise
