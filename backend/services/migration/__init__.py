"""RetroDECK home-path migration.

A **home migration** relocates ROMs, BIOS and saves after the RetroDECK home
path changes. :class:`MigrationService` is the whole public surface.
"""

from services.migration.service import MigrationService, MigrationServiceConfig

__all__ = ["MigrationService", "MigrationServiceConfig"]
