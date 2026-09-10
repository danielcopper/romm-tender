"""Tests for the start-up install that keeps the launcher out of the plugin folder.

Every case runs against a real file under ``tmp_path``: what the adapter is for
is what it does to a filesystem — the mode it lands, the inode it leaves alone,
the staging file it must not leave behind — and a faked one would prove none of
it.
"""

from __future__ import annotations

import logging
import os
import stat

from adapters.launcher_install import LauncherInstallAdapter

_SHIPPED = b'#!/bin/bash\nexec "$@"\n'


def _make(tmp_path, *, source: str | None = None, destination: str | None = None) -> LauncherInstallAdapter:
    return LauncherInstallAdapter(
        source=source if source is not None else str(tmp_path / "plugin" / "bin" / "rom-launcher"),
        destination=destination if destination is not None else str(tmp_path / "data" / "bin" / "rom-launcher"),
        logger=logging.getLogger("test"),
    )


def _ship(tmp_path, content: bytes = _SHIPPED) -> None:
    shipped = tmp_path / "plugin" / "bin" / "rom-launcher"
    shipped.parent.mkdir(parents=True, exist_ok=True)
    shipped.write_bytes(content)
    shipped.chmod(0o755)


class TestInstallingIt:
    def test_writes_the_launcher_where_nothing_is_yet(self, tmp_path):
        _ship(tmp_path)

        assert _make(tmp_path).install() is True

        installed = tmp_path / "data" / "bin" / "rom-launcher"
        assert installed.read_bytes() == _SHIPPED

    def test_the_installed_launcher_is_executable_by_its_owner_and_nobody_else(self, tmp_path):
        """Steam runs it as the shortcut's exe, and no other account has any business with it.

        Under the user's own data root, executed by the account that owns that
        root — so group and other are granted nothing (python:S2612).
        """
        _ship(tmp_path)

        _make(tmp_path).install()

        mode = stat.S_IMODE((tmp_path / "data" / "bin" / "rom-launcher").stat().st_mode)
        assert mode == 0o700

    def test_the_directory_it_creates_is_owner_only_too(self, tmp_path):
        _ship(tmp_path)

        _make(tmp_path).install()

        assert stat.S_IMODE((tmp_path / "data" / "bin").stat().st_mode) == 0o700

    def test_the_staging_file_is_never_wider_than_the_launcher_it_becomes(self, tmp_path, monkeypatch):
        """There is no window in which the launcher is readable by anyone the final one is not."""
        _ship(tmp_path)
        seen: list[int] = []
        real_replace = os.replace

        def capture(source, destination):
            seen.append(stat.S_IMODE(os.stat(source).st_mode))
            real_replace(source, destination)

        monkeypatch.setattr(os, "replace", capture)

        _make(tmp_path).install()

        assert seen == [0o700]

    def test_a_launcher_an_older_release_left_wider_is_narrowed(self, tmp_path):
        """Same bytes, wrong mode: the ordinary write is the only path that narrows it."""
        _ship(tmp_path)
        installed = tmp_path / "data" / "bin" / "rom-launcher"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(_SHIPPED)
        installed.chmod(0o755)

        assert _make(tmp_path).install() is True

        assert stat.S_IMODE(installed.stat().st_mode) == 0o700

    def test_it_leaves_no_staging_file_behind(self, tmp_path):
        _ship(tmp_path)

        _make(tmp_path).install()

        assert sorted(os.listdir(tmp_path / "data" / "bin")) == ["rom-launcher"]

    def test_it_replaces_a_launcher_whose_content_differs(self, tmp_path):
        """Every release brings its own, so an older one is overwritten rather than kept."""
        _ship(tmp_path)
        installed = tmp_path / "data" / "bin" / "rom-launcher"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(b'#!/bin/bash\n# an older release\nexec "$@"\n')
        installed.chmod(0o755)

        assert _make(tmp_path).install() is True

        assert installed.read_bytes() == _SHIPPED

    def test_it_replaces_the_launcher_by_renaming_a_new_file_onto_it(self, tmp_path):
        """A game running right now is executing that file; it must keep its own inode.

        Written in place instead, the replacement is read out from under the
        interpreter mid-script — bash reads a script as it runs it.
        """
        _ship(tmp_path)
        installed = tmp_path / "data" / "bin" / "rom-launcher"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(b'#!/bin/bash\n# an older release\nexec "$@"\n')
        installed.chmod(0o755)
        running = installed.stat().st_ino

        _make(tmp_path).install()

        assert installed.stat().st_ino != running

    def test_it_rewrites_a_launcher_that_lost_its_executable_bit(self, tmp_path):
        """Right bytes, no execute fails every game exactly as a missing file does."""
        _ship(tmp_path)
        installed = tmp_path / "data" / "bin" / "rom-launcher"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(_SHIPPED)
        installed.chmod(0o644)

        assert _make(tmp_path).install() is True

        assert stat.S_IMODE(installed.stat().st_mode) == 0o700


class TestLeavingItAlone:
    def test_an_identical_launcher_keeps_its_inode(self, tmp_path):
        """The launcher a game is executing right now is this one — nothing may touch it."""
        _ship(tmp_path)
        adapter = _make(tmp_path)
        adapter.install()
        installed = tmp_path / "data" / "bin" / "rom-launcher"
        untouched = installed.stat().st_ino

        assert adapter.install() is True

        assert installed.stat().st_ino == untouched


class TestWhenItCannotBeDone:
    def test_an_unwritable_destination_is_reported_not_raised(self, tmp_path):
        """A start that cannot place the launcher still starts — it just says so."""
        _ship(tmp_path)
        blocked = tmp_path / "data"
        blocked.mkdir()
        blocked.chmod(0o500)
        try:
            assert _make(tmp_path).install() is False
        finally:
            blocked.chmod(0o700)

    def test_a_missing_shipped_launcher_is_reported_not_raised(self, tmp_path):
        assert _make(tmp_path).install() is False

    def test_a_failed_write_leaves_no_half_written_launcher_at_the_path(self, tmp_path):
        """The rename is what publishes it, so a failure never shows a partial file."""
        _ship(tmp_path)
        destination = tmp_path / "data" / "bin" / "rom-launcher"
        destination.parent.mkdir(parents=True)
        destination.mkdir()

        assert _make(tmp_path).install() is False

        assert destination.is_dir()
        assert sorted(os.listdir(destination.parent)) == ["rom-launcher"]
