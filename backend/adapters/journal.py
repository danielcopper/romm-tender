"""This user's systemd journal, read through ``journalctl --user``.

Owns the one command that reads it, and reads only: this adapter writes no
journal entry of its own.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from adapters.bounded_run import run_bounded
from domain.update_output import JournalEntry, decode_journal_entry

if TYPE_CHECKING:
    from collections.abc import Callable

# ``journalctl`` reads files on this machine and answers at once; a bound this
# wide is only ever reached by a journal it cannot get through.
_TIMEOUT_SECONDS = 10

# ``__REALTIME_TIMESTAMP`` comes with every entry whatever is asked for
# (``journalctl(1)``, ``--output-fields``).
_FIELDS = "MESSAGE,_SYSTEMD_INVOCATION_ID,USER_INVOCATION_ID"


class JournalctlAdapter:
    """Reads entries of this user's journal with ``journalctl``.

    Parameters
    ----------
    log_debug:
        Debug sink for what ``journalctl`` wrote to stderr on a run that
        succeeded — a journal file it skipped, for one.
    """

    def __init__(self, *, log_debug: Callable[[str], None]) -> None:
        self._log_debug = log_debug

    def __call__(
        self,
        unit: str | None,
        *,
        since: float | None = None,
        until: float | None = None,
        last: int | None = None,
    ) -> tuple[JournalEntry, ...]:
        """The entries of *unit* — of every unit where it is ``None`` — oldest first.

        *since* and *until* bound them in epoch seconds, both ends included;
        *last* keeps only that many of the newest. Raises ``OSError`` where
        ``journalctl`` is missing, could not be run, gave no answer in time
        (``TimeoutError``) or failed.
        """
        # ``--all``: without it the JSON output answers a field over 4096 bytes
        # as ``null`` (``journalctl(1)``, ``--output=json``), which would read
        # as an empty line.
        argv = [
            "journalctl",
            "--user",
            "--output=json",
            f"--output-fields={_FIELDS}",
            "--all",
            "--quiet",
            "--no-pager",
        ]
        if unit is not None:
            argv.append(f"--unit={unit}")
        if since is not None:
            argv.append(f"--since=@{since:.6f}")
        if until is not None:
            argv.append(f"--until=@{until:.6f}")
        if last is not None:
            argv.append(f"--lines={last}")
        try:
            done = run_bounded(argv, timeout=_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(f"journalctl did not answer: {e}") from e
        said = done.stderr.strip()
        if done.returncode != 0:
            raise OSError(f"journalctl exited with status {done.returncode}{f': {said}' if said else ''}")
        if said:
            self._log_debug(f"[update] journalctl said: {said}")
        return tuple(entry for line in done.stdout.splitlines() if (entry := decode_journal_entry(line)) is not None)
