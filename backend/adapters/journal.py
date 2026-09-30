"""This user's systemd journal, read through ``journalctl --user``.

Owns the one command that reads it. Reading only: nothing in this program writes
into the journal but its own stderr, which systemd puts there.
"""

from __future__ import annotations

import subprocess

from domain.update_output import JournalEntry, decode_journal_entry

# ``journalctl`` reads files on this machine and answers at once; a bound this
# wide is only ever reached by a journal it cannot get through.
_TIMEOUT_SECONDS = 10

# ``__REALTIME_TIMESTAMP`` comes with every entry whatever is asked for
# (``journalctl(1)``, ``--output-fields``).
_FIELDS = "MESSAGE,_SYSTEMD_INVOCATION_ID,USER_INVOCATION_ID"


class JournalctlAdapter:
    """Reads entries of this user's journal with ``journalctl``."""

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
        argv = ["journalctl", "--user", "--output=json", f"--output-fields={_FIELDS}", "--quiet", "--no-pager"]
        if unit is not None:
            argv.append(f"--unit={unit}")
        if since is not None:
            argv.append(f"--since=@{since:.6f}")
        if until is not None:
            argv.append(f"--until=@{until:.6f}")
        if last is not None:
            argv.append(f"--lines={last}")
        try:
            done = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(f"journalctl did not answer: {e}") from e
        if done.returncode != 0:
            said = done.stderr.strip()
            raise OSError(f"journalctl exited with status {done.returncode}{f': {said}' if said else ''}")
        return tuple(entry for line in done.stdout.splitlines() if (entry := decode_journal_entry(line)) is not None)
