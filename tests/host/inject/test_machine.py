"""The two readings taken from the machine rather than from the page."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from host.inject.machine import decky_loader_is_serving, read_steam_build
from tests.host.conftest import free_port

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
            server.close()
            await server.wait_closed()

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
