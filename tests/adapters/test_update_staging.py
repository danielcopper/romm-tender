"""Tests for adapters.update_staging — the directory an update is staged in before its installer runs."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile

import pytest

from adapters.update_staging import UpdateStagingAdapter

_INSTALLER = b"#!/bin/bash\necho install\n"


def _tarball(path, members: list[tarfile.TarInfo], bodies: dict[str, bytes] | None = None) -> str:
    bodies = bodies or {}
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            body = bodies.get(member.name)
            archive.addfile(member, io.BytesIO(body) if body is not None else None)
    return str(path)


def _file(name: str, body: bytes) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(body)
    return member, body


@pytest.fixture
def staging(tmp_path) -> UpdateStagingAdapter:
    return UpdateStagingAdapter(directory=str(tmp_path / "cache" / "update"))


class TestTheDirectory:
    def test_prepare_starts_it_empty_even_over_an_earlier_attempt(self, staging, tmp_path):
        directory = tmp_path / "cache" / "update"
        directory.mkdir(parents=True)
        (directory / "romm-tender-0.9.0.tar.gz").write_bytes(b"old")

        staging.prepare()

        assert directory.is_dir()
        assert list(directory.iterdir()) == []

    def test_prepare_gives_a_file_an_earlier_installer_still_reads_a_new_name(self, staging, tmp_path):
        """A running installer keeps reading the file it opened; a new one must not be written into it."""
        staging.prepare()
        old = tmp_path / "cache" / "update" / "install.sh"
        old.write_bytes(b"old installer")
        with open(old, "rb") as held:
            staging.prepare()
            (tmp_path / "cache" / "update" / "install.sh").write_bytes(b"new installer")
            assert held.read() == b"old installer"

    def test_remove_all_takes_the_directory_and_everything_in_it(self, staging, tmp_path):
        staging.prepare()
        (tmp_path / "cache" / "update" / "nested").mkdir()
        (tmp_path / "cache" / "update" / "nested" / "f").write_bytes(b"x")

        staging.remove_all()

        assert not (tmp_path / "cache" / "update").exists()
        assert (tmp_path / "cache").is_dir()

    def test_remove_all_with_nothing_there_is_nothing_to_do(self, staging, tmp_path):
        staging.remove_all()

        assert not (tmp_path / "cache" / "update").exists()

    def test_remove_all_removes_a_link_under_the_name_and_never_what_it_points_at(self, staging, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep").write_bytes(b"keep")
        (tmp_path / "cache").mkdir()
        os.symlink(elsewhere, tmp_path / "cache" / "update")

        staging.remove_all()

        assert not os.path.lexists(tmp_path / "cache" / "update")
        assert (elsewhere / "keep").read_bytes() == b"keep"

    def test_the_tarball_goes_under_the_asset_name_its_checksum_file_names(self, staging, tmp_path):
        assert staging.tarball_path("1.1.0") == str(tmp_path / "cache" / "update" / "romm-tender-1.1.0.tar.gz")


class TestTheDigest:
    def test_it_is_the_lowercase_sha256_hex_of_the_file(self, staging, tmp_path):
        target = tmp_path / "f"
        body = os.urandom(3 * 1024 * 1024 + 7)
        target.write_bytes(body)

        assert staging.sha256_of(str(target)) == hashlib.sha256(body).hexdigest()

    def test_a_missing_file_raises(self, staging, tmp_path):
        with pytest.raises(FileNotFoundError):
            staging.sha256_of(str(tmp_path / "absent"))


class TestTheInstaller:
    def test_only_the_installer_is_unpacked_into_the_directory(self, staging, tmp_path):
        staging.prepare()
        installer, installer_body = _file("romm-tender/install.sh", _INSTALLER)
        main, main_body = _file("romm-tender/backend/main.py", b"print()")
        tarball = _tarball(
            tmp_path / "t.tar.gz", [installer, main], {installer.name: installer_body, main.name: main_body}
        )

        path = staging.extract_installer(tarball)

        assert path == str(tmp_path / "cache" / "update" / "install.sh")
        assert (tmp_path / "cache" / "update" / "install.sh").read_bytes() == _INSTALLER
        assert sorted(p.name for p in (tmp_path / "cache" / "update").iterdir()) == ["install.sh"]

    def test_a_tarball_without_the_installer_raises(self, staging, tmp_path):
        staging.prepare()
        main, body = _file("romm-tender/backend/main.py", b"print()")
        tarball = _tarball(tmp_path / "t.tar.gz", [main], {main.name: body})

        with pytest.raises(KeyError):
            staging.extract_installer(tarball)

    @pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE])
    def test_an_installer_that_is_not_a_regular_file_is_refused(self, staging, tmp_path, kind):
        staging.prepare()
        member = tarfile.TarInfo("romm-tender/install.sh")
        member.type = kind
        member.linkname = "/etc/passwd"
        tarball = _tarball(tmp_path / "t.tar.gz", [member])

        with pytest.raises(ValueError, match="not a regular file"):
            staging.extract_installer(tarball)

        assert not (tmp_path / "cache" / "update" / "install.sh").exists()

    def test_a_member_named_to_escape_is_never_what_is_read(self, staging, tmp_path):
        """Only the one exact name is asked for, and where it lands is chosen here."""
        staging.prepare()
        escaping, escaping_body = _file("../../escape.sh", b"evil")
        installer, installer_body = _file("romm-tender/install.sh", _INSTALLER)
        tarball = _tarball(
            tmp_path / "t.tar.gz",
            [escaping, installer],
            {escaping.name: escaping_body, installer.name: installer_body},
        )

        staging.extract_installer(tarball)

        assert not (tmp_path / "escape.sh").exists()
        assert not (tmp_path.parent / "escape.sh").exists()

    def test_a_file_that_is_not_a_tarball_raises(self, staging, tmp_path):
        staging.prepare()
        bogus = tmp_path / "t.tar.gz"
        bogus.write_bytes(b"not gzip")

        with pytest.raises(tarfile.TarError):
            staging.extract_installer(str(bogus))
