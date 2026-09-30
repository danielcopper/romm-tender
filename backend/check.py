"""The pre-install check: build the application on copies of the live data, and answer whether it could be built.

What the installer runs on the version it unpacked, before it stops or replaces
anything (``check_the_new_version`` in ``install.sh``). It is an entry of its
own rather than a flag on ``main.py`` because the installer runs it on the NEW
version: a version whose ``main.py`` predated the flag would ignore it and start
a whole backend beside the running one, where a version without this file is
simply one the installer cannot check.

The database and the save-sync state under ``--data-from`` and the settings
under ``--config-from`` are copied into the roots the environment names, and
the build migrates the copies. Nothing else a start does happens here: no lock,
no port, no port file, no ``backend.log``, no start-up repair, no network, no
Steam.

The exit status is the answer, and ``install.sh`` reads it:

- 0: the application was built.
- 1: it could not be built, or ``main.py`` could not be imported — the
  traceback is on stderr. An import of this file's own that fails answers 1
  too, which is Python's status for an exception nothing caught.
- 2: the check was not tried — its arguments were wrong (argparse's own
  status), a root it would build under was not one it may write, or the live
  data could not be copied.

A process a signal ends answers none of these: the installer sees 128 plus
the signal's number, a crash in the native library among them.
"""

import argparse
import asyncio
import importlib
import logging
import os
import sys

backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

_THIS_TREE = os.path.dirname(backend_dir)

from bootstrap import build_application, copy_live_data

from domain.app_directories import (
    ENV_BIN_DIR,
    ENV_CACHE_DIR,
    ENV_CODE_DIR,
    ENV_CONFIG_DIR,
    ENV_DATA_DIR,
    ENV_STATE_DIR,
    AppDirectories,
    resolve_directories,
)
from domain.identity import VERSION
from domain.update_install import installer_environment
from domain.update_release import resolve_update_source
from host import EventSink, HostStatus, configure_stderr_logging

BUILT = 0
NOT_BUILT = 1
NOT_TRIED = 2

# The six roots a check builds under. Each one the environment leaves unset
# falls back to where the installed program keeps its own, and the build writes
# there: the launcher into the bin root, the settings into the config root.
_CHECK_ROOTS = (ENV_CODE_DIR, ENV_CONFIG_DIR, ENV_DATA_DIR, ENV_CACHE_DIR, ENV_STATE_DIR, ENV_BIN_DIR)


def check(argv: list[str]) -> int:
    """Copy the live data under the check's roots, build the application on the copies, and answer how that went."""
    parser = argparse.ArgumentParser(prog="check.py")
    parser.add_argument("--data-from", required=True)
    parser.add_argument("--config-from", required=True)
    arguments = parser.parse_args(argv)
    logger = configure_stderr_logging()
    unset = [name for name in _CHECK_ROOTS if not os.environ.get(name, "").strip()]
    if unset:
        logger.error(f"check: refused, {', '.join(unset)} not set")
        return NOT_TRIED
    user_home = os.path.expanduser("~")
    directories = resolve_directories(os.environ, user_home, _THIS_TREE)
    refusal = _refusal(directories, arguments.data_from, arguments.config_from)
    if refusal is not None:
        logger.error(f"check: refused, {refusal}")
        return NOT_TRIED
    try:
        copy_live_data(
            data_from=arguments.data_from,
            config_from=arguments.config_from,
            directories=directories,
            logger=logger,
        )
    except Exception:
        logger.exception("check: the live data could not be copied")
        return NOT_TRIED
    try:
        # The backend's own entry, which the build alone never reaches; importing
        # it runs nothing, since its start sits behind ``__name__ == "__main__"``.
        importlib.import_module("main")
        asyncio.run(_build_on_copies(directories, user_home, logger))
    except Exception:
        logger.exception(f"check: {VERSION} could not be built")
        return NOT_BUILT
    logger.info(f"check: {VERSION} was built")
    return BUILT


def _refusal(directories: AppDirectories, data_from: str, config_from: str) -> str | None:
    """Why the check may not build under *directories*, or ``None`` where it may.

    The code root has to be the tree this file is in, so the build reads the
    version that is being checked. Every other root — the runtime directory
    among them, which holds the running backend's port note — has to be absent
    or empty, so nothing the build writes lands beside what the running version
    keeps; and neither copy may land where it is copied from.
    """
    if os.path.realpath(directories.code_dir) != os.path.realpath(_THIS_TREE):
        return f"{ENV_CODE_DIR} is {directories.code_dir}, not the tree being checked ({_THIS_TREE})"
    for root, source in ((directories.data_dir, data_from), (directories.config_dir, config_from)):
        if os.path.realpath(root) == os.path.realpath(source):
            return f"{root} is where the live data is copied from"
    for root in _written_roots(directories):
        if os.path.lexists(root) and (not os.path.isdir(root) or os.listdir(root)):
            return f"{root} already exists and is not an empty directory"
    return None


def _written_roots(directories: AppDirectories) -> tuple[str, ...]:
    return (
        directories.config_dir,
        directories.data_dir,
        directories.cache_dir,
        directories.state_dir,
        directories.runtime_dir,
        directories.bin_dir,
    )


async def _build_on_copies(directories: AppDirectories, user_home: str, logger: logging.Logger) -> None:
    """Build the application exactly as ``build_backend`` in ``main.py`` does, and run nothing of it."""
    build_application(
        directories=directories,
        update_source=resolve_update_source(os.environ, _THIS_TREE),
        installer_environment=installer_environment(os.environ, directories, sys.executable),
        user_home=user_home,
        logger=logger,
        loop=asyncio.get_running_loop(),
        emit=EventSink(logger).emit,
        steam=HostStatus().steam,
    )


if __name__ == "__main__":
    sys.exit(check(sys.argv[1:]))
