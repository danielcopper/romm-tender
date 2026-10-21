"""Tests for adapters/tender_folder.py — the place read live, the folder's state, its creation and the drives."""

from __future__ import annotations

import os
from typing import Any

import pytest

from adapters.tender_folder import TenderFolderAdapter, drives_root_for_current_user
from domain.tender_folder import DRIVE_NOT_MOUNTED, FOLDER_MISSING, FOLDER_NOT_WRITABLE, Drive, TenderPlace


def _adapter(tmp_path, settings: dict[str, Any] | None = None, mounts: set[str] | None = None) -> TenderFolderAdapter:
    known = mounts if mounts is not None else set()
    return TenderFolderAdapter(
        settings=settings if settings is not None else {},
        user_home=str(tmp_path / "home"),
        drives_root=str(tmp_path / "media"),
        is_mount=lambda path: path in known,
    )


class TestThePlace:
    def test_no_setting_is_the_home_folder(self, tmp_path):
        adapter = _adapter(tmp_path)

        assert adapter.place() == TenderPlace(place=str(tmp_path / "home"), mount_point=None, default=True)
        assert adapter.folder() == str(tmp_path / "home" / "Tender")

    def test_a_setting_written_after_construction_is_read_on_the_next_ask(self, tmp_path):
        settings: dict[str, Any] = {}
        adapter = _adapter(tmp_path, settings)
        drive = str(tmp_path / "media" / "SD")

        settings["tender_folder"] = {"place": drive, "mount_point": drive}

        assert adapter.folder() == os.path.join(drive, "Tender")

    def test_without_a_drives_root_the_current_users_folder_under_run_media_is_used(self, tmp_path):
        adapter = TenderFolderAdapter(settings={}, user_home=str(tmp_path))

        assert adapter.drives_root() == drives_root_for_current_user()
        assert os.path.dirname(adapter.drives_root()) == "/run/media"


class TestTheCondition:
    def test_a_folder_that_exists_and_can_be_written_has_none(self, tmp_path):
        adapter = _adapter(tmp_path)
        (tmp_path / "home" / "Tender").mkdir(parents=True)

        assert adapter.condition(adapter.place()) is None

    def test_a_missing_folder_says_so(self, tmp_path):
        adapter = _adapter(tmp_path)

        assert adapter.condition(adapter.place()) == FOLDER_MISSING

    def test_a_folder_that_cannot_be_written_says_so(self, tmp_path):
        adapter = _adapter(tmp_path)
        folder = tmp_path / "home" / "Tender"
        folder.mkdir(parents=True)
        folder.chmod(0o500)
        try:
            assert adapter.condition(adapter.place()) == FOLDER_NOT_WRITABLE
        finally:
            folder.chmod(0o700)

    def test_a_drive_that_is_gone_is_told_by_its_mount_point_not_by_the_missing_folder(self, tmp_path):
        drive = str(tmp_path / "media" / "SD")
        adapter = _adapter(tmp_path, {"tender_folder": {"place": drive, "mount_point": drive}})

        assert adapter.condition(adapter.place()) == DRIVE_NOT_MOUNTED

    def test_a_folder_left_behind_on_the_internal_drive_does_not_stand_in_for_a_drive_that_is_gone(self, tmp_path):
        drive = tmp_path / "media" / "SD"
        (drive / "Tender").mkdir(parents=True)
        adapter = _adapter(tmp_path, {"tender_folder": {"place": str(drive), "mount_point": str(drive)}})

        assert adapter.condition(adapter.place()) == DRIVE_NOT_MOUNTED

    def test_a_mounted_drive_is_judged_by_its_folder(self, tmp_path):
        drive = tmp_path / "media" / "SD"
        drive.mkdir(parents=True)
        adapter = _adapter(
            tmp_path, {"tender_folder": {"place": str(drive), "mount_point": str(drive)}}, mounts={str(drive)}
        )

        assert adapter.condition(adapter.place()) == FOLDER_MISSING


class TestCreatingAndRemoving:
    def test_a_created_folder_carries_the_id_file(self, tmp_path):
        adapter = _adapter(tmp_path)
        place = tmp_path / "games" / "library"

        created = adapter.create_folder(str(place), str(place / "Tender"), "folder-id")

        assert created is True
        assert (place / "Tender" / ".tender-folder").read_text() == "folder-id\n"

    def test_a_folder_that_is_there_already_is_left_as_it_is(self, tmp_path):
        adapter = _adapter(tmp_path)
        folder = tmp_path / "Tender"
        folder.mkdir()
        (folder / "roms").mkdir()

        created = adapter.create_folder(str(tmp_path), str(folder), "folder-id")

        assert created is False
        assert sorted(os.listdir(folder)) == ["roms"]

    def test_a_place_that_cannot_be_written_raises(self, tmp_path):
        adapter = _adapter(tmp_path)
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            with pytest.raises(OSError):
                adapter.create_folder(str(locked), str(locked / "Tender"), "folder-id")
        finally:
            locked.chmod(0o700)

    def test_a_folder_holding_nothing_but_its_id_file_is_removed(self, tmp_path):
        adapter = _adapter(tmp_path)
        adapter.create_folder(str(tmp_path), str(tmp_path / "Tender"), "folder-id")

        assert adapter.remove_if_only_id(str(tmp_path / "Tender")) is True
        assert not (tmp_path / "Tender").exists()

    def test_a_folder_holding_anything_else_is_kept(self, tmp_path):
        adapter = _adapter(tmp_path)
        adapter.create_folder(str(tmp_path), str(tmp_path / "Tender"), "folder-id")
        (tmp_path / "Tender" / "recovery").mkdir()

        assert adapter.remove_if_only_id(str(tmp_path / "Tender")) is False
        assert (tmp_path / "Tender" / ".tender-folder").exists()

    def test_an_empty_folder_without_an_id_file_is_kept(self, tmp_path):
        adapter = _adapter(tmp_path)
        (tmp_path / "Tender").mkdir()

        assert adapter.remove_if_only_id(str(tmp_path / "Tender")) is False
        assert (tmp_path / "Tender").exists()

    def test_a_missing_folder_is_nothing_to_remove(self, tmp_path):
        assert _adapter(tmp_path).remove_if_only_id(str(tmp_path / "Tender")) is False


class TestPaths:
    def test_the_deepest_existing_folder_of_a_path(self, tmp_path):
        (tmp_path / "a").mkdir()

        assert _adapter(tmp_path).deepest_existing(str(tmp_path / "a" / "b" / "c")) == str(tmp_path / "a")

    def test_the_mount_point_is_the_nearest_mount_above_the_deepest_existing_folder(self, tmp_path):
        drive = tmp_path / "media" / "SD"
        (drive / "Games").mkdir(parents=True)
        adapter = _adapter(tmp_path, mounts={str(drive)})

        assert adapter.mount_point_of(str(drive / "Games" / "new")) == str(drive)


class TestTheDrives:
    def test_every_writable_mount_under_the_drives_root_is_a_drive(self, tmp_path):
        media = tmp_path / "media"
        for name in ("SD", "BIG", "stale"):
            (media / name).mkdir(parents=True)
        adapter = _adapter(tmp_path, mounts={str(media / "SD"), str(media / "BIG")})

        drives = adapter.drives()

        assert [drive.name for drive in drives] == ["BIG", "SD"]
        assert drives[0].mount_point == str(media / "BIG")
        assert all(drive.free_bytes > 0 for drive in drives)

    def test_a_drive_that_cannot_be_written_is_not_offered(self, tmp_path):
        drive = tmp_path / "media" / "RO"
        drive.mkdir(parents=True)
        drive.chmod(0o500)
        try:
            assert _adapter(tmp_path, mounts={str(drive)}).drives() == ()
        finally:
            drive.chmod(0o700)

    def test_no_drives_root_is_no_drive(self, tmp_path):
        assert _adapter(tmp_path).drives() == ()

    def test_a_drive_is_its_name_mount_point_and_free_bytes(self, tmp_path):
        drive = tmp_path / "media" / "SD"
        drive.mkdir(parents=True)

        (only,) = _adapter(tmp_path, mounts={str(drive)}).drives()

        assert isinstance(only, Drive)
        assert (only.name, only.mount_point) == ("SD", str(drive))


class TestTheDrivesKind:
    """The kind is read off the device behind the mount: ``/sys/dev/block/<major>:<minor>`` and where it leads."""

    @staticmethod
    def _device_at(tmp_path, mount_point, device_path: str) -> str:
        number = os.stat(mount_point).st_dev
        devices = tmp_path / "sys-dev-block"
        devices.mkdir(exist_ok=True)
        target = tmp_path / "sys" / device_path
        target.mkdir(parents=True)
        (devices / f"{os.major(number)}:{os.minor(number)}").symlink_to(target)
        return str(devices)

    @pytest.mark.parametrize(
        ("device_path", "kind"),
        [
            ("devices/pci0000:00/0000:02:00.0/mmc_host/mmc0/mmc0:aaaa/block/mmcblk0/mmcblk0p1", "sd_card"),
            ("devices/pci0000:00/0000:00:08.1/usb3/3-1/3-1:1.0/host0/target0:0:0/0:0:0:0/block/sda/sda1", "usb_drive"),
            ("devices/pci0000:00/0000:00:01.2/nvme/nvme1/nvme1n1/nvme1n1p1", "drive"),
        ],
    )
    def test_a_drive_is_an_sd_card_a_usb_drive_or_a_drive_by_the_device_behind_it(self, tmp_path, device_path, kind):
        drive = tmp_path / "media" / "Emulation"
        drive.mkdir(parents=True)
        devices = self._device_at(tmp_path, drive, device_path)
        adapter = TenderFolderAdapter(
            settings={},
            user_home=str(tmp_path / "home"),
            drives_root=str(tmp_path / "media"),
            is_mount=lambda path: path == str(drive),
            devices=devices,
        )

        (only,) = adapter.drives()

        assert only.kind == kind

    def test_a_device_that_cannot_be_read_is_a_drive(self, tmp_path):
        adapter = TenderFolderAdapter(settings={}, user_home=str(tmp_path), devices=str(tmp_path / "no-such-dir"))

        assert adapter.drive_kind(str(tmp_path)) == "drive"
        assert adapter.drive_kind(str(tmp_path / "gone")) == "drive"


class TestFreeSpace:
    def test_the_space_free_where_a_path_lies(self, tmp_path):
        assert _adapter(tmp_path).free_bytes(str(tmp_path)) > 0

    def test_a_path_that_cannot_be_read_has_none(self, tmp_path):
        assert _adapter(tmp_path).free_bytes(str(tmp_path / "gone")) == 0
