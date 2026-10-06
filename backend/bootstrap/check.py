"""What a pre-install check builds on: copies of the live data, under the check's own roots.

Contract: :func:`copy_live_data` copies the database and the settings of the
install the check stands beside into the directories the check was told to use,
so that the build which follows migrates the copies and never the originals.
Where the live data is, and where the copies go, are both handed in: the first
by the installer, the second by the entry point's reading of the environment.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from adapters.live_data_copy import copy_database, copy_file
from adapters.persistence import SETTINGS_FILENAME

from .adapters import DB_FILENAME, LEGACY_DB_FILENAME

if TYPE_CHECKING:
    import logging

    from domain.app_directories import AppDirectories


def copy_live_data(*, data_from: str, config_from: str, directories: AppDirectories, logger: logging.Logger) -> None:
    """Copy the live database under *data_from*, and the live settings under *config_from*.

    The copies go into *directories*. Either one missing is nothing to copy — a
    first install has neither — and the build then starts from what is there,
    as a start would. Raises what the copy raises for a file that is there and
    cannot be read.

    The database copied is the one a start would open — the current name where
    a file has it, the old name otherwise — under the name it has, so the build
    renames the copy as a start renames the original.
    """
    database = DB_FILENAME if os.path.isfile(os.path.join(data_from, DB_FILENAME)) else LEGACY_DB_FILENAME
    if copy_database(os.path.join(data_from, database), os.path.join(directories.data_dir, database)):
        logger.info(f"check: copied the database from {data_from}")
    if copy_file(os.path.join(config_from, SETTINGS_FILENAME), os.path.join(directories.config_dir, SETTINGS_FILENAME)):
        logger.info(f"check: copied the settings from {config_from}")
