"""This program's record of an update attempt whose installer it started, in the state directory.

Owns ``update-attempt.json``: reading it, writing it, removing it. The backend
is its only writer — the installer never touches it — and the record is what
lets the next start tell an installer that stopped without updating from an
update that went through or was rolled back.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

from domain.update_install import UPDATE_ATTEMPT_FILENAME, decode_attempt_record, encode_attempt_record

if TYPE_CHECKING:
    from collections.abc import Callable

    from domain.update_install import UpdateAttemptRecord


class UpdateAttemptFileAdapter:
    """Reads, writes and removes the attempt record under *state_dir*.

    Parameters
    ----------
    state_dir:
        The state root from ``AppDirectories``, which every start keeps.
    log_debug:
        Debug sink for a record that is there and cannot be read: it is read
        as no record.
    """

    def __init__(self, *, state_dir: str, log_debug: Callable[[str], None]) -> None:
        self._state_dir = state_dir
        self._path = os.path.join(state_dir, UPDATE_ATTEMPT_FILENAME)
        self._log_debug = log_debug

    def read(self) -> UpdateAttemptRecord | None:
        """Return the record, or ``None`` where there is none or it cannot be read. Never raises."""
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as e:
            self._log_debug(f"[update] {self._path} could not be read: {e!r}")
            return None
        record = decode_attempt_record(raw)
        if record is None:
            self._log_debug(f"[update] {self._path} is not a record of an update attempt")
        return record

    def write(self, record: UpdateAttemptRecord) -> None:
        """Replace the record with *record*, through a temporary file so no reader sees half of one.

        Raises ``OSError`` where it could not be written.
        """
        os.makedirs(self._state_dir, exist_ok=True)
        temporary = f"{self._path}.{os.getpid()}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as f:
                f.write(encode_attempt_record(record))
            os.replace(temporary, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(temporary)
            raise

    def remove(self) -> None:
        """Remove the record; none there is nothing to do. Raises ``OSError`` for one that stays."""
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._path)
