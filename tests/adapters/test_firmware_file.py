"""Tests for FirmwareFileAdapter — raw filesystem ops for firmware/BIOS files."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat

import pytest

from adapters.firmware_file import FirmwareFileAdapter
from domain.firmware_places import PlaceLook


@pytest.fixture
def fw_files() -> FirmwareFileAdapter:
    return FirmwareFileAdapter()


class TestExists:
    def test_true_for_existing_file(self, fw_files, tmp_path):
        f = tmp_path / "bios.bin"
        f.write_bytes(b"x")
        assert fw_files.exists(str(f)) is True

    def test_true_for_directory(self, fw_files, tmp_path):
        assert fw_files.exists(str(tmp_path)) is True

    def test_false_for_missing(self, fw_files, tmp_path):
        assert fw_files.exists(str(tmp_path / "missing.bin")) is False


class TestRemove:
    def test_removes_existing(self, fw_files, tmp_path):
        f = tmp_path / "bios.bin"
        f.write_bytes(b"x")
        fw_files.remove_file(str(f))
        assert not f.exists()

    def test_missing_is_noop(self, fw_files, tmp_path):
        # idempotent: must not raise
        fw_files.remove_file(str(tmp_path / "missing.bin"))

    def test_propagates_non_filenotfound_errors(self, fw_files, tmp_path):
        # Removing a non-empty directory raises IsADirectoryError or OSError —
        # anything other than FileNotFoundError must surface.
        with pytest.raises(OSError):
            fw_files.remove_file(str(tmp_path))


class TestRename:
    def test_renames_file(self, fw_files, tmp_path):
        src = tmp_path / "a.bin.tmp"
        dst = tmp_path / "a.bin"
        src.write_bytes(b"payload")
        fw_files.rename(str(src), str(dst))
        assert not src.exists()
        assert dst.read_bytes() == b"payload"

    def test_overwrites_existing_destination(self, fw_files, tmp_path):
        src = tmp_path / "new.tmp"
        dst = tmp_path / "old.bin"
        src.write_bytes(b"new")
        dst.write_bytes(b"old")
        fw_files.rename(str(src), str(dst))
        assert dst.read_bytes() == b"new"
        assert not src.exists()

    def test_missing_source_raises(self, fw_files, tmp_path):
        with pytest.raises(FileNotFoundError):
            fw_files.rename(str(tmp_path / "missing"), str(tmp_path / "dst"))


class TestMakeDirs:
    def test_creates_dir(self, fw_files, tmp_path):
        target = tmp_path / "bios"
        fw_files.make_dirs(str(target))
        assert target.is_dir()

    def test_creates_parents(self, fw_files, tmp_path):
        target = tmp_path / "retrodeck" / "bios" / "dc"
        fw_files.make_dirs(str(target))
        assert target.is_dir()

    def test_idempotent_when_dir_exists(self, fw_files, tmp_path):
        target = tmp_path / "bios"
        target.mkdir()
        # Must not raise
        fw_files.make_dirs(str(target))
        assert target.is_dir()


class TestChecksumMd5:
    def test_matches_hashlib(self, fw_files, tmp_path):
        f = tmp_path / "bios.bin"
        payload = b"firmware payload bytes"
        f.write_bytes(payload)
        expected = hashlib.md5(payload).hexdigest()
        assert fw_files.checksum_md5(str(f)) == expected

    def test_returns_hex_digest_format(self, fw_files, tmp_path):
        f = tmp_path / "bios.bin"
        f.write_bytes(b"x")
        digest = fw_files.checksum_md5(str(f))
        # 32-char lowercase hex string
        assert re.fullmatch(r"[0-9a-f]{32}", digest)

    def test_empty_file(self, fw_files, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert fw_files.checksum_md5(str(f)) == hashlib.md5(b"").hexdigest()

    def test_streams_large_file(self, fw_files, tmp_path):
        """Files larger than one chunk are hashed correctly."""
        f = tmp_path / "large.bin"
        # 25 KiB — well above the 8 KiB chunk size
        payload = b"A" * (25 * 1024)
        f.write_bytes(payload)
        assert fw_files.checksum_md5(str(f)) == hashlib.md5(payload).hexdigest()

    def test_missing_file_raises(self, fw_files, tmp_path):
        with pytest.raises(FileNotFoundError):
            fw_files.checksum_md5(str(tmp_path / "missing.bin"))


class TestReadBytes:
    def test_roundtrip(self, fw_files, tmp_path):
        f = tmp_path / "bios.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        assert fw_files.read_bytes(str(f)) == b"\x00\x01\x02\x03"

    def test_empty_file(self, fw_files, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert fw_files.read_bytes(str(f)) == b""

    def test_missing_raises(self, fw_files, tmp_path):
        with pytest.raises(FileNotFoundError):
            fw_files.read_bytes(str(tmp_path / "missing.bin"))


class TestLookingAtAPlace:
    def test_nothing_there_says_whether_its_folder_is(self, fw_files, tmp_path):
        assert fw_files.look(str(tmp_path / "x.bin")) == PlaceLook(lexists=False, folder_exists=True)
        assert fw_files.look(str(tmp_path / "missing" / "x.bin")) == PlaceLook(lexists=False, folder_exists=False)

    def test_a_link_is_not_followed_and_names_its_target(self, fw_files, tmp_path):
        place = tmp_path / "x.bin"
        place.symlink_to(tmp_path / "gone.bin")

        assert fw_files.look(str(place)) == PlaceLook(lexists=True, link_target=str(tmp_path / "gone.bin"))

    def test_a_relative_link_names_its_target_absolute(self, fw_files, tmp_path):
        (tmp_path / "store").mkdir()
        place = tmp_path / "x.bin"
        place.symlink_to("store/y.bin")

        assert fw_files.look(str(place)).link_target == str(tmp_path / "store" / "y.bin")

    def test_a_file_and_a_folder(self, fw_files, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"x")
        (tmp_path / "d").mkdir()

        assert fw_files.look(str(tmp_path / "f.bin")) == PlaceLook(lexists=True)
        assert fw_files.look(str(tmp_path / "d")) == PlaceLook(lexists=True, is_dir=True)


class TestLinks:
    def test_a_link_is_placed_where_the_name_is_free_and_never_over_anything(self, fw_files, tmp_path):
        target = tmp_path / "store.bin"
        target.write_bytes(b"x")
        taken = tmp_path / "taken.bin"
        taken.write_bytes(b"mine")

        fw_files.symlink(str(target), str(tmp_path / "free.bin"))
        with pytest.raises(FileExistsError):
            fw_files.symlink(str(target), str(taken))

        assert os.readlink(tmp_path / "free.bin") == str(target)
        assert taken.read_bytes() == b"mine"

    def test_removing_a_link_leaves_its_target_and_a_file_alone(self, fw_files, tmp_path):
        target = tmp_path / "store.bin"
        target.write_bytes(b"x")
        link = tmp_path / "link.bin"
        link.symlink_to(target)
        plain = tmp_path / "plain.bin"
        plain.write_bytes(b"y")

        fw_files.remove_link(str(link))
        fw_files.remove_link(str(plain))

        assert not link.is_symlink()
        assert target.exists()
        assert plain.read_bytes() == b"y"

    def test_moving_aside_never_moves_over_anything_and_leaves_both_where_they_are(self, fw_files, tmp_path):
        (tmp_path / "old.bin").write_bytes(b"old")
        (tmp_path / "place.bin").write_bytes(b"someone else's")
        (tmp_path / "old.lnk").symlink_to(tmp_path / "elsewhere.bin")

        with pytest.raises(FileExistsError):
            fw_files.move_aside(str(tmp_path / "old.bin"), str(tmp_path / "place.bin"))
        with pytest.raises(FileExistsError):
            fw_files.move_aside(str(tmp_path / "old.lnk"), str(tmp_path / "place.bin"))

        assert (tmp_path / "old.bin").read_bytes() == b"old"
        assert (tmp_path / "old.lnk").is_symlink()
        assert (tmp_path / "place.bin").read_bytes() == b"someone else's"

    def test_moving_aside_keeps_the_mode_and_leaves_nothing_where_the_copy_failed(
        self, fw_files, tmp_path, monkeypatch
    ):
        (tmp_path / "old.bin").write_bytes(b"old")
        (tmp_path / "old.bin").chmod(0o640)

        fw_files.move_aside(str(tmp_path / "old.bin"), str(tmp_path / "moved.bin"))
        assert stat.S_IMODE((tmp_path / "moved.bin").stat().st_mode) == 0o640

        def full(_source, _out):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(shutil, "copyfileobj", full)
        with pytest.raises(OSError):
            fw_files.move_aside(str(tmp_path / "moved.bin"), str(tmp_path / "again.bin"))
        assert (tmp_path / "moved.bin").read_bytes() == b"old"
        assert not (tmp_path / "again.bin").exists()

    def test_moving_aside_creates_the_folders_and_moves_a_link_as_itself(self, fw_files, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"wrong")
        (tmp_path / "l.bin").symlink_to(tmp_path / "elsewhere.bin")

        fw_files.move_aside(str(tmp_path / "f.bin"), str(tmp_path / "backup" / "a" / "f.bin"))
        fw_files.move_aside(str(tmp_path / "l.bin"), str(tmp_path / "backup" / "a" / "l.bin"))

        assert (tmp_path / "backup" / "a" / "f.bin").read_bytes() == b"wrong"
        assert os.readlink(tmp_path / "backup" / "a" / "l.bin") == str(tmp_path / "elsewhere.bin")
        assert not (tmp_path / "f.bin").exists()
