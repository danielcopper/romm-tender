"""Transient systemd user units: starting a command as one, and asking whether it still runs.

Owns the two commands that do it, ``systemd-run --user`` and ``systemctl
--user show``. A command started this way runs under the user manager rather
than as a child of this process, which is the point: it outlives this process
and is not stopped with this process's own unit.

``--collect`` is passed so that a unit which ended in failure is unloaded at
once instead of staying loaded under its name until someone runs
``reset-failed`` (systemd-run(1), ``--collect``; systemd.unit(5),
``CollectMode=inactive-or-failed``): without it, starting the same name again
after a failure would be refused.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Both commands answer at once — ``systemd-run`` without ``--wait`` returns as
# soon as the unit is started — so a bound this wide is only ever reached by a
# user manager that is not answering at all.
_TIMEOUT_SECONDS = 30

# ``ActiveState`` values (systemd.unit(5)) under which the unit's process may
# still be running. ``inactive`` is also what an unknown — here, collected —
# unit reports.
_RUNNING_STATES = frozenset({"active", "activating", "deactivating", "reloading"})
_ENDED_STATES = frozenset({"inactive", "failed"})


class SystemdRunAdapter:
    """Starts transient user units with ``systemd-run`` and reads their state with ``systemctl``."""

    def start(self, unit: str, command: Sequence[str], environment: Sequence[tuple[str, str]]) -> str | None:
        """Start *command* as the transient user unit *unit*, with *environment* set in it.

        Answers ``None`` once the unit is started, or why it was not: the
        tool's own complaint (a unit of that name still loaded among them), a
        missing ``systemd-run``, or one that could not be run. Raises
        ``TimeoutError`` where ``systemd-run`` gave no answer in time, because
        then nobody knows whether the unit started.
        """
        argv = [
            "systemd-run",
            "--user",
            "--unit",
            unit,
            "--collect",
            "--quiet",
            *(f"--setenv={name}={value}" for name, value in environment),
            "--",
            *command,
        ]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False)
        except FileNotFoundError:
            return "systemd-run is not installed"
        except OSError as e:
            return f"systemd-run could not be run: {e}"
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(f"systemd-run did not answer: {e}") from e
        if done.returncode != 0:
            said = done.stderr.strip() or done.stdout.strip()
            return said or f"systemd-run exited with status {done.returncode}"
        return None

    def is_active(self, unit: str) -> bool | None:
        """``True`` while *unit* runs, ``False`` once it has ended or is unknown, ``None`` where no answer says either.

        ``None`` covers a ``systemctl`` that could not be run, gave no answer in
        time or failed, and a state that is neither running nor ended.
        """
        argv = ["systemctl", "--user", "show", "--property=ActiveState", "--value", unit]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        state = done.stdout.strip()
        if done.returncode != 0:
            return None
        if state in _RUNNING_STATES:
            return True
        if state in _ENDED_STATES:
            return False
        return None
