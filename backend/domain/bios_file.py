"""BiosFile — one downloaded BIOS/firmware file Tender tracks on disk.

Identified by ``(platform_folder, file_name)`` — a bare filename is unsafe because
two platforms can ship same-named BIOS. ``platform_folder`` is the ``fs_slug`` of
the RomM platform the library files the file on (``domain/firmware_platforms.py``);
a record written before records were keyed by ``fs_slug`` holds a slug or a server
folder name there until the refiling can place it. ``firmware_id`` is nullable metadata from
RomM, not part of the identity. Tracked so migrations can move the file when
the RetroDECK home path changes.
"""

from __future__ import annotations

from domain._aggregate import cosmic_aggregate


@cosmic_aggregate
class BiosFile:
    """One downloaded BIOS file, keyed by (platform_folder, file_name)."""

    platform_folder: str
    file_name: str
    file_path: str
    downloaded_at: str
    firmware_id: int | None = None

    @classmethod
    def mark_downloaded(
        cls,
        *,
        platform_folder: str,
        file_name: str,
        file_path: str,
        downloaded_at: str,
        firmware_id: int | None = None,
    ) -> BiosFile:
        """Record a freshly downloaded BIOS file at ISO timestamp ``downloaded_at``."""
        if not platform_folder or not file_name:
            raise ValueError("platform_folder and file_name are required")
        return cls(
            platform_folder=platform_folder,
            file_name=file_name,
            file_path=file_path,
            downloaded_at=downloaded_at,
            firmware_id=firmware_id,
        )

    def refiled_under(self, platform_folder: str) -> BiosFile:
        """This record filed under *platform_folder* instead, every other field kept.

        The platform is part of the identity, so the answer is a new record;
        the caller removes the old one.
        """
        return BiosFile.mark_downloaded(
            platform_folder=platform_folder,
            file_name=self.file_name,
            file_path=self.file_path,
            downloaded_at=self.downloaded_at,
            firmware_id=self.firmware_id,
        )

    def relocate(self, new_path: str) -> None:
        """Move the BIOS file to a new path (RetroDECK home migration)."""
        self.file_path = new_path
