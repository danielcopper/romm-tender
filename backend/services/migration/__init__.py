"""RetroDECK path and save-sort migration.

Two migrations live here, and they share only the mechanics of moving a file
that may already exist at its destination (:mod:`_moves`). A **home migration**
relocates ROMs, BIOS and saves after the RetroDECK home path changes; a
**save-sort migration** relocates save files after RetroArch's
``sort_savefiles_*`` flags change, and answers which files a ROM's save consists
of by asking the emulator rather than guessing extensions.

:class:`MigrationService` is the whole public surface — the save-sort half is
reached through it, not around it.
"""

from services.migration.service import MigrationService, MigrationServiceConfig

__all__ = ["MigrationService", "MigrationServiceConfig"]
