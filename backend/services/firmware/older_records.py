"""The download records one platform's page offers, read for the status answer and the delete alike."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.firmware_platforms import records_of_platform

if TYPE_CHECKING:
    from domain.bios_file import BiosFile
    from services.protocols import ListingRefiler, UnitOfWorkFactory


def platform_records(
    uow_factory: UnitOfWorkFactory, refiling: ListingRefiler, platform_folder: str, platform_slug: str
) -> list[BiosFile]:
    """The records :func:`~domain.firmware_platforms.records_of_platform` gives the platform, in one short read UoW.

    Whether its slug is a listed ``fs_slug`` is the listing the refiling last
    read, so this page and the refiling judge a record by the same listing.
    Blocking.
    """
    with uow_factory() as uow:
        records = list(uow.bios_files.iter_all())
    return records_of_platform(records, platform_folder, platform_slug, refiling.listed_folders())
