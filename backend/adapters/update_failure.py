"""The installer's record of an update that did not go through, read from the state directory.

Owns reading ``update-failure.json`` and nothing else: the installer writes it
and removes it, and this program never does either, so a record that is here is
the installer's statement and one that is gone is the installer's too.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from domain.update_outcome import UPDATE_FAILURE_FILENAME, decode_update_failure

if TYPE_CHECKING:
    from collections.abc import Callable

    from domain.update_outcome import UpdateFailure


class UpdateFailureFileAdapter:
    """Reads the installer's record of an update that did not go through, under *state_dir*.

    Parameters
    ----------
    state_dir:
        The state root from ``AppDirectories`` — the directory the installer
        resolved into the unit and writes the record into.
    log_debug:
        Debug sink for a record that is there and cannot be read: it is shown as
        no record, and only someone who turned debug logging on sees why.
    """

    def __init__(self, *, state_dir: str, log_debug: Callable[[str], None]) -> None:
        self._path = os.path.join(state_dir, UPDATE_FAILURE_FILENAME)
        self._log_debug = log_debug

    def read_update_failure(self) -> UpdateFailure | None:
        """Return the record, or ``None`` where there is none or it cannot be read. Never raises."""
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as e:
            self._log_debug(f"[update] {self._path} could not be read: {e!r}")
            return None
        failure = decode_update_failure(raw)
        if failure is None:
            self._log_debug(f"[update] {self._path} is not a record of a rolled-back update")
        return failure
