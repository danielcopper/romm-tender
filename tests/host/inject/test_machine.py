"""What this module asks the machine rather than the page, and the one thing it puts back."""

from __future__ import annotations

import asyncio
import logging
import os
import time

import pytest

from host.inject import machine
from host.inject.machine import decky_loader_is_serving, read_steam_build
from tests.host.conftest import close_listener, free_port

MANIFEST = '"ubuntu12"\n{\n\t"version"\t\t"1788652215"\n\t"tenfoot_images_all"\n\t{\n\t}\n}\n'


def steam_home(tmp_path, *, branch: str | None, manifests: dict[str, str]) -> str:
    package = tmp_path / ".local" / "share" / "Steam" / "package"
    package.mkdir(parents=True)
    if branch is not None:
        (package / "beta").write_text(f"{branch}\n", encoding="utf-8")
    for name, body in manifests.items():
        (package / name).write_text(body, encoding="utf-8")
    return str(tmp_path)


class TestReadingSteamsBuild:
    def test_it_reads_the_manifest_the_branch_file_names(self, tmp_path):
        home = steam_home(
            tmp_path,
            branch="steamdeck_stable",
            manifests={
                "steam_client_steamdeck_stable_ubuntu12.manifest": MANIFEST,
                "steam_client_steamdeck_publicbeta_ubuntu12.manifest": MANIFEST.replace("1788652215", "1700000000"),
            },
        )
        assert read_steam_build(home) == "1788652215"

    def test_one_manifest_answers_even_without_a_branch_file(self, tmp_path):
        home = steam_home(tmp_path, branch=None, manifests={"steam_client_ubuntu12.manifest": MANIFEST})
        assert read_steam_build(home) == "1788652215"

    def test_several_manifests_and_no_branch_answer_nothing(self, tmp_path):
        home = steam_home(
            tmp_path,
            branch=None,
            manifests={"steam_client_a_ubuntu12.manifest": MANIFEST, "steam_client_b_ubuntu12.manifest": MANIFEST},
        )
        assert read_steam_build(home) is None

    def test_a_branch_naming_no_manifest_falls_back_to_the_single_one(self, tmp_path):
        home = steam_home(tmp_path, branch="something_else", manifests={"steam_client_ubuntu12.manifest": MANIFEST})
        assert read_steam_build(home) == "1788652215"

    def test_the_other_spelling_of_the_steam_root_is_read_too(self, tmp_path):
        package = tmp_path / ".steam" / "steam" / "package"
        package.mkdir(parents=True)
        (package / "steam_client_ubuntu12.manifest").write_text(MANIFEST, encoding="utf-8")
        assert read_steam_build(str(tmp_path)) == "1788652215"

    def test_a_manifest_with_no_version_answers_nothing(self, tmp_path):
        home = steam_home(tmp_path, branch=None, manifests={"steam_client_ubuntu12.manifest": '"ubuntu12"\n{\n}\n'})
        assert read_steam_build(home) is None

    def test_no_manifest_at_all_answers_nothing(self, tmp_path):
        assert read_steam_build(steam_home(tmp_path, branch="stable", manifests={})) is None

    def test_no_steam_at_all_answers_nothing(self, tmp_path):
        assert read_steam_build(str(tmp_path)) is None

    def test_a_package_directory_it_cannot_read_answers_nothing(self, tmp_path):
        home = steam_home(tmp_path, branch=None, manifests={"steam_client_ubuntu12.manifest": MANIFEST})
        package = os.path.join(home, ".local", "share", "Steam", "package")
        os.chmod(package, 0o000)
        try:
            assert read_steam_build(home) is None
        finally:
            os.chmod(package, 0o700)


class TestAskingWhetherDeckyIsServing:
    async def test_a_port_nothing_answers_on_is_no_loader(self):
        assert await decky_loader_is_serving(free_port()) is False

    async def test_a_port_something_answers_on_is_read_as_the_loader(self):
        server = await asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        try:
            assert await decky_loader_is_serving(port) is True
        finally:
            await close_listener(server)

    async def test_the_question_does_not_block_the_event_loop(self, monkeypatch):
        """The connect is synchronous; on the loop thread it would stall everything."""
        import host.inject.machine as machine

        def slow_connect(_port, _timeout):
            time.sleep(0.2)
            return False

        monkeypatch.setattr(machine, "someone_listening", slow_connect)

        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.005)

        ticking = asyncio.ensure_future(tick())
        try:
            await asyncio.sleep(0)
            before = ticks
            assert await machine.decky_loader_is_serving(1337, connect_timeout=0.2) is False
            assert ticks - before >= 5
        finally:
            ticking.cancel()
            with pytest.raises(asyncio.CancelledError):
                await ticking


class TestEnsuringSteamsDebuggerMarker:
    """The one thing this module puts back rather than reads.

    Without the marker Steam opens no debugger, so no panel can ever be loaded.
    Why it has to be re-created on every start rather than once is
    ``docs/architecture/loading-the-panel.md``.
    """

    @staticmethod
    def _steam_root(tmp_path, parts: tuple[str, ...]):
        root = tmp_path.joinpath(*parts)
        root.mkdir(parents=True)
        return root

    def test_it_creates_the_marker_where_there_is_none(self, tmp_path, caplog):
        root = self._steam_root(tmp_path, (".local", "share", "Steam"))

        with caplog.at_level(logging.WARNING):
            answer = machine.ensure_debugger_marker(str(tmp_path), str(tmp_path / "state"), logging.getLogger("test"))

        assert answer is True
        assert (root / ".cef-enable-remote-debugging").is_file()
        assert "Steam has to be restarted once" in caplog.text

    def test_it_writes_the_note_the_uninstaller_reads(self, tmp_path):
        root = self._steam_root(tmp_path, (".local", "share", "Steam"))

        machine.ensure_debugger_marker(str(tmp_path), str(tmp_path / "state"), logging.getLogger("test"))

        note = tmp_path / "state" / machine.DEBUGGER_MARKER_NOTE
        first, second = note.read_text(encoding="utf-8").splitlines()
        assert first == str(root / ".cef-enable-remote-debugging")
        assert second.startswith("created by the backend ")

    def test_the_note_names_the_marker_that_was_created(self, tmp_path):
        """``install.sh --uninstall`` unlinks that line, so it names one file rather than a name."""
        root = self._steam_root(tmp_path, (".steam", "steam"))

        machine.ensure_debugger_marker(str(tmp_path), str(tmp_path / "state"), logging.getLogger("test"))

        note = tmp_path / "state" / machine.DEBUGGER_MARKER_NOTE
        assert note.read_text(encoding="utf-8").splitlines()[0] == str(root / ".cef-enable-remote-debugging")

    def test_a_note_that_could_not_be_written_does_not_unmake_the_marker(self, tmp_path, caplog):
        """The marker is what Steam reads; the note is only what the uninstaller reads.

        A state directory that cannot be written costs the uninstaller its
        authority over one file. Reporting the created marker as a failure
        because of it would be a worse answer than the one thing that went
        wrong — and it is the answer a single try block gives.
        """
        root = self._steam_root(tmp_path, (".local", "share", "Steam"))
        blocked = tmp_path / "state"
        blocked.mkdir()
        blocked.chmod(0o500)
        try:
            with caplog.at_level(logging.WARNING):
                answer = machine.ensure_debugger_marker(str(tmp_path), str(blocked), logging.getLogger("test"))
        finally:
            blocked.chmod(0o700)

        assert answer is True
        assert (root / ".cef-enable-remote-debugging").is_file()
        assert "could not record it at" in caplog.text
        assert "Steam has to be restarted once" in caplog.text

    def test_the_first_existing_steam_root_wins(self, tmp_path):
        """Same order as every other reading here; on this machine one is a symlink to the other."""
        first = self._steam_root(tmp_path, (".local", "share", "Steam"))
        second = self._steam_root(tmp_path, (".steam", "steam"))

        machine.ensure_debugger_marker(str(tmp_path), str(tmp_path / "state"), logging.getLogger("test"))

        assert (first / ".cef-enable-remote-debugging").exists()
        assert not (second / ".cef-enable-remote-debugging").exists()

    def test_the_other_spelling_is_used_when_it_is_the_only_one(self, tmp_path):
        root = self._steam_root(tmp_path, (".steam", "steam"))

        assert machine.ensure_debugger_marker(str(tmp_path), str(tmp_path / "state"), logging.getLogger("test"))
        assert (root / ".cef-enable-remote-debugging").is_file()

    def test_a_marker_already_there_is_left_alone_and_says_nothing(self, tmp_path, caplog):
        root = self._steam_root(tmp_path, (".local", "share", "Steam"))
        marker = root / ".cef-enable-remote-debugging"
        marker.write_text("someone else's", encoding="utf-8")

        with caplog.at_level(logging.DEBUG):
            answer = machine.ensure_debugger_marker(str(tmp_path), str(tmp_path / "state"), logging.getLogger("test"))

        assert answer is True
        assert marker.read_text(encoding="utf-8") == "someone else's"
        assert not (tmp_path / "state" / machine.DEBUGGER_MARKER_NOTE).exists()
        assert caplog.text == ""

    def test_no_steam_directory_at_all_is_reported_not_raised(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            answer = machine.ensure_debugger_marker(str(tmp_path), str(tmp_path / "state"), logging.getLogger("test"))

        assert answer is False
        assert "no Steam directory" in caplog.text

    def test_a_write_that_cannot_happen_is_logged_and_swallowed(self, tmp_path, caplog):
        """Bad path: a read-only Steam directory must not take the backend down."""
        root = self._steam_root(tmp_path, (".local", "share", "Steam"))
        root.chmod(0o500)
        try:
            with caplog.at_level(logging.WARNING):
                answer = machine.ensure_debugger_marker(
                    str(tmp_path), str(tmp_path / "state"), logging.getLogger("test")
                )
        finally:
            root.chmod(0o700)

        assert answer is False
        assert "could not create Steam's remote-debugging marker" in caplog.text
