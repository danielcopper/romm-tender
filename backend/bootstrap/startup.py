"""Running the start-up routines so one failing repair cannot take the backend down.

Contract: the wrapper the start-up REPAIRS are called through, and nothing else.
Eight of the nine steps go through it; the ninth, ``migrate_legacy_credentials``,
does not — it runs after the port is announced (``host/runtime.py``) and
swallows its own failures. It belongs to the composition root rather than to the services, so the
distinction it draws — this step is a repair, not a prerequisite — is visible at
the call site rather than buried in each service.

The distinction is not decorative. Six of the nine routines contain no exception
handling at all. Under the plugin loader that cost nothing, because the lifecycle
hook was a detached task nobody awaited; hosting the backend ourselves, an
unhandled failure in an artwork sweep ends the process, and a service manager's
restart policy turns that into a loop.

**The result is returned because one pair of steps has an edge between them.**
``prune_stale_installed_roms`` may run only after ``detect_retrodeck_path_change``
has *succeeded*: the prune reads the pending homes the detection writes into
``kv_config``, and if the detection broke off, the prune takes every install
under the home RetroDECK has just left for orphaned and deletes its rows. The
guard the prune carries covers "the home is missing", not "the detection
failed". The other seven steps have no such edge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable


class StartupSteps:
    """Runs start-up routines, reporting a failure instead of raising it."""

    def __init__(self, logger: logging.Logger, report_failure: Callable[[str], None]) -> None:
        self._logger = logger
        self._report_failure = report_failure

    def run(self, name: str, step: Callable[[], Any]) -> bool:
        """Run *step*, and answer whether it finished.

        A failure is logged with its traceback and reported to the caller's
        recorder, so a routine that fails on every start is a number the panel
        shows rather than a line only a log reader would ever find.
        """
        try:
            step()
        except Exception:
            self._logger.exception(f"start-up step {name!r} failed; continuing without it")
            self._report_failure(name)
            return False
        return True
