"""Running a short command to its end, with a bound on every wait — the one after a timeout included.

``subprocess.run`` kills a command that overran its timeout and then waits for
it with no bound at all; a process in uninterruptible sleep does not die on
``SIGKILL``, and a child it left holding the pipes keeps them open, so either
holds the caller's thread for as long as it lasts. Here the kill is followed by
a bounded wait, and the pipes are closed rather than read.
"""

from __future__ import annotations

import contextlib
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# How long a killed command is given to go before the caller is told it timed
# out regardless; ``SIGKILL`` ends any process that can be ended within it.
_REAP_SECONDS = 2.0


def run_bounded(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run *argv* and answer what it printed, decoded as UTF-8 with undecodable bytes replaced, and how it exited.

    Raises ``OSError`` — ``FileNotFoundError`` among them — where it cannot be
    started, and ``subprocess.TimeoutExpired`` where it did not end within
    *timeout* seconds, at most :data:`_REAP_SECONDS` after that.
    """
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_REAP_SECONDS)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        raise
    return subprocess.CompletedProcess(list(argv), process.returncode, stdout, stderr)
