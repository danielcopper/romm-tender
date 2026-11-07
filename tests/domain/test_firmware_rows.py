"""Unit tests for which of RomM's firmware a platform draws on (#2223 D48)."""

from __future__ import annotations

from domain.firmware_rows import platform_library


def _file(firmware_id: int, folder: str | None, slug: str = "ngc") -> dict[str, object]:
    row: dict[str, object] = {"id": firmware_id, "file_name": "IPL.bin", "platform_slug": slug}
    if folder is not None:
        row["platform_folder"] = folder
    return row


class TestPlatformLibrary:
    def test_a_file_under_the_platforms_own_folder_is_kept(self):
        own = _file(1, "gc")

        assert platform_library([own], "gc") == [own]

    def test_a_file_under_another_folder_is_dropped_also_where_the_two_share_a_slug(self):
        own, other = _file(1, "gc"), _file(2, "gamecube")

        assert platform_library([own, other], "gc") == [own]

    def test_a_file_without_a_folder_is_in_no_platforms_folder(self):
        assert platform_library([_file(1, None), _file(2, "")], "gc") == []
