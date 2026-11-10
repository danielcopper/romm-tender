"""The download records one platform's page offers, read for the status answer and the delete alike."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.firmware_platforms import records_of_platform

if TYPE_CHECKING:
    import logging

    from domain.bios_file import BiosFile
    from services.protocols import ListingRefiler, UnitOfWorkFactory


def platform_records(
    uow_factory: UnitOfWorkFactory,
    refiling: ListingRefiler,
    logger: logging.Logger,
    platform_folder: str,
    platform_slug: str,
) -> list[BiosFile]:
    """The records :func:`~domain.firmware_platforms.records_of_platform` gives the platform, in one short read UoW.

    Whether its slug is a listed ``fs_slug`` is the listing the refiling last
    read, so this page and the refiling judge a record by the same listing;
    a record under the slug held back because no listing has been read yet is
    logged at debug level. Its read UoW is closed before it answers. Blocking.
    """
    with uow_factory() as uow:
        records = list(uow.bios_files.iter_all())
    listed = refiling.listed_folders()
    if listed is None and platform_slug != platform_folder:
        held = sorted(record.file_name for record in records if record.platform_folder == platform_slug)
        if held:
            logger.debug(
                f"Not offering the BIOS records under {platform_slug} on {platform_folder}'s page "
                f"until RomM's platform listing is read: {', '.join(held)}"
            )
    return records_of_platform(records, platform_folder, platform_slug, listed)
