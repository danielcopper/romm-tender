"""Unit tests for ``RunningSteamAdapter`` — which Steam runs, and how, read from its pid file and ``/proc``.

Drives a fake ``/proc`` under ``tmp_path`` by pointing the adapter's ``_PROC``
module constant at it, and the test's own home holding ``.steam/steam.pid``.
The 64-bit client's chain is the one measured on a Deck: ``steam`` and
``pv-adverb`` inside the container's user namespace, then ``srt-bwrap`` on the
host still naming the container's authority file, then ``srt-client-cont``
naming the session's, then the host's ``steam`` launch script and its shell.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from adapters import running_steam
from adapters.running_steam import RunningSteamAdapter
from domain.controller_focus import RunningSteam, SteamIdentity, XDisplay

if TYPE_CHECKING:
    from pathlib import Path

STEAM_PID = 5000
STEAM_64 = "/home/deck/.local/share/Steam/steamrt64/steam"
STEAM_32 = "/home/deck/.local/share/Steam/ubuntu12_32/steam"
HOST_NAMESPACE = "user:[4026531837]"
CONTAINER_NAMESPACE = "user:[4026532901]"
GAMESCOPE = {"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}


@pytest.fixture
def proc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proc"
    (root / "self" / "ns").mkdir(parents=True)
    (root / "self" / "ns" / "user").symlink_to(HOST_NAMESPACE)
    monkeypatch.setattr(running_steam, "_PROC", str(root))
    return root


@pytest.fixture
def session_authority(tmp_path: Path) -> str:
    """The desktop session's authority file, which the host can read."""
    path = tmp_path / "run" / "xauth_QkUzSn"
    path.parent.mkdir()
    path.write_bytes(b"\x01\x00")
    return str(path)


@pytest.fixture
def container_authority(tmp_path: Path) -> str:
    """The authority file the container names, which does not exist on the host."""
    return str(tmp_path / "pressure-vessel" / "Xauthority")


def _write_pid_file(home: Path, text: str) -> None:
    (home / ".steam").mkdir(exist_ok=True)
    (home / ".steam" / "steam.pid").write_text(text, encoding="utf-8")


def _make_process(
    proc_root: Path,
    pid: int,
    *,
    comm: str,
    parent: int,
    namespace: str,
    environ: dict[str, str] | None,
    exe: str | None = None,
    start_time: int = 1234567,
) -> None:
    """Write a fake ``/proc/<pid>``; *environ* ``None`` leaves the environment unreadable."""
    entry = proc_root / str(pid)
    (entry / "ns").mkdir(parents=True)
    (entry / "comm").write_text(f"{comm}\n", encoding="utf-8")
    (entry / "status").write_text(f"Name:\t{comm}\nState:\tS (sleeping)\nPPid:\t{parent}\n", encoding="utf-8")
    fields = ["S", str(parent)] + ["0"] * 17 + [str(start_time), "0", "0"]
    (entry / "stat").write_text(f"{pid} ({comm}) {' '.join(fields)}\n", encoding="utf-8")
    (entry / "ns" / "user").symlink_to(namespace)
    if exe is not None:
        (entry / "exe").symlink_to(exe)
    if environ is not None:
        (entry / "environ").write_bytes(b"".join(f"{name}={value}\0".encode() for name, value in environ.items()))


def _steam_in_its_container(
    proc_root: Path,
    home: Path,
    *,
    session_authority: str,
    container_authority: str,
    steam_environ: dict[str, str] | None = None,
    launcher_environ: dict[str, str] | None = None,
) -> None:
    """The 64-bit client in its container, under the chain measured on a Deck."""
    container_x = {"DISPLAY": ":0", "XAUTHORITY": container_authority}
    session_x = {"DISPLAY": ":0", "XAUTHORITY": session_authority}
    _make_process(proc_root, 3000, comm="bash", parent=1, namespace=HOST_NAMESPACE, environ=session_x)
    _make_process(
        proc_root,
        3500,
        comm="steam",
        parent=3000,
        namespace=HOST_NAMESPACE,
        environ={**session_x, **(launcher_environ or {})},
    )
    _make_process(proc_root, 4000, comm="srt-client-cont", parent=3500, namespace=HOST_NAMESPACE, environ=session_x)
    _make_process(proc_root, 4200, comm="srt-bwrap", parent=4000, namespace=HOST_NAMESPACE, environ=container_x)
    _make_process(proc_root, 4500, comm="pv-adverb", parent=4200, namespace=CONTAINER_NAMESPACE, environ=container_x)
    _make_process(
        proc_root,
        STEAM_PID,
        comm="steam",
        parent=4500,
        namespace=CONTAINER_NAMESPACE,
        environ=steam_environ if steam_environ is not None else container_x,
        exe=STEAM_64,
    )
    _write_pid_file(home, f"{STEAM_PID}\n")


def _read(home: Path) -> RunningSteam | None:
    return RunningSteamAdapter(user_home=str(home)).read()


class TestTheSteamThatRuns:
    def test_the_64_bit_client_in_its_container(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        _steam_in_its_container(
            proc_root, home, session_authority=session_authority, container_authority=container_authority
        )

        assert _read(home) == RunningSteam(
            identity=SteamIdentity(pid=STEAM_PID, start_time=1234567),
            exe=STEAM_64,
            under_gamescope=False,
            display=XDisplay(name=":0", authority_path=session_authority),
        )

    def test_the_32_bit_client_on_the_host_names_its_own_display(
        self, proc_root: Path, home: Path, session_authority: str
    ) -> None:
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=1,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":1", "XAUTHORITY": session_authority},
            exe=STEAM_32,
        )
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.exe == STEAM_32
        assert steam.display == XDisplay(name=":1", authority_path=session_authority)

    def test_a_command_with_a_parenthesis_still_yields_its_start_time(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        _steam_in_its_container(
            proc_root, home, session_authority=session_authority, container_authority=container_authority
        )
        stat = proc_root / str(STEAM_PID) / "stat"
        stat.write_text(stat.read_text().replace("(steam)", "(st) eam)"), encoding="utf-8")

        steam = _read(home)

        assert steam is not None
        assert steam.identity.start_time == 1234567


class TestTheDisplayItWasStartedFrom:
    def test_a_host_process_naming_the_container_s_authority_is_walked_past(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        # srt-bwrap is the first host process above Steam, and the file it
        # names exists only inside the container.
        _steam_in_its_container(
            proc_root, home, session_authority=session_authority, container_authority=container_authority
        )

        steam = _read(home)

        assert steam is not None
        assert steam.display == XDisplay(name=":0", authority_path=session_authority)

    def test_no_xauthority_means_the_home_s_file_where_it_exists(self, proc_root: Path, home: Path) -> None:
        (home / ".Xauthority").write_bytes(b"\x01\x00")
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=1,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":0"},
            exe=STEAM_64,
        )
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.display == XDisplay(name=":0", authority_path=str(home / ".Xauthority"))

    def test_no_xauthority_and_no_file_in_the_home_is_no_display(self, proc_root: Path, home: Path) -> None:
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=1,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":0"},
            exe=STEAM_64,
        )
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.display is None

    def test_an_authority_file_this_backend_cannot_read_is_walked_past(
        self, proc_root: Path, home: Path, tmp_path: Path, session_authority: str
    ) -> None:
        if os.geteuid() == 0:
            pytest.skip("root reads a file whatever its mode")
        unreadable = tmp_path / "unreadable-xauth"
        unreadable.write_bytes(b"\x01\x00")
        unreadable.chmod(0)
        _make_process(
            proc_root,
            4000,
            comm="steam.sh",
            parent=1,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":0", "XAUTHORITY": session_authority},
        )
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=4000,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":0", "XAUTHORITY": str(unreadable)},
            exe=STEAM_64,
        )
        _write_pid_file(home, str(STEAM_PID))

        try:
            steam = _read(home)
        finally:
            unreadable.chmod(0o600)

        assert steam is not None
        assert steam.display == XDisplay(name=":0", authority_path=session_authority)

    def test_an_authority_path_that_is_a_directory_is_walked_past(
        self, proc_root: Path, home: Path, tmp_path: Path, session_authority: str
    ) -> None:
        _make_process(
            proc_root,
            4000,
            comm="steam.sh",
            parent=1,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":0", "XAUTHORITY": session_authority},
        )
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=4000,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":0", "XAUTHORITY": str(tmp_path)},
            exe=STEAM_64,
        )
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.display == XDisplay(name=":0", authority_path=session_authority)

    def test_no_display_on_any_host_process_is_no_display(self, proc_root: Path, home: Path) -> None:
        _make_process(proc_root, STEAM_PID, comm="steam", parent=1, namespace=HOST_NAMESPACE, environ={}, exe=STEAM_64)
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.display is None

    def test_no_ancestor_in_this_namespace_is_no_display(self, proc_root: Path, home: Path) -> None:
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=0,
            namespace=CONTAINER_NAMESPACE,
            environ={"DISPLAY": ":0"},
            exe=STEAM_64,
        )
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.display is None

    def test_a_vanished_parent_is_no_display(self, proc_root: Path, home: Path) -> None:
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=4500,
            namespace=CONTAINER_NAMESPACE,
            environ={"DISPLAY": ":0"},
            exe=STEAM_64,
        )
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.display is None


class TestGamingMode:
    def test_gamescope_in_steam_s_own_environment(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        _steam_in_its_container(
            proc_root,
            home,
            session_authority=session_authority,
            container_authority=container_authority,
            steam_environ=GAMESCOPE,
        )

        steam = _read(home)

        assert steam is not None
        assert steam.under_gamescope is True

    def test_gamescope_only_on_a_host_process_above_steam(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        # steam-launcher.service hands its host process the variable; the
        # container may not pass it on.
        _steam_in_its_container(
            proc_root,
            home,
            session_authority=session_authority,
            container_authority=container_authority,
            launcher_environ=GAMESCOPE,
        )

        steam = _read(home)

        assert steam is not None
        assert steam.under_gamescope is True

    def test_gamescope_on_a_container_process_above_steam_does_not_count(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        _steam_in_its_container(
            proc_root, home, session_authority=session_authority, container_authority=container_authority
        )
        environ = proc_root / "4500" / "environ"
        environ.write_bytes(environ.read_bytes() + b"GAMESCOPE_WAYLAND_DISPLAY=gamescope-0\0")

        steam = _read(home)

        assert steam is not None
        assert steam.under_gamescope is False

    def test_no_gamescope_anywhere_is_desktop_mode(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        _steam_in_its_container(
            proc_root, home, session_authority=session_authority, container_authority=container_authority
        )

        steam = _read(home)

        assert steam is not None
        assert steam.under_gamescope is False


class TestNoSteamToRead:
    def test_no_pid_file(self, proc_root: Path, home: Path) -> None:
        assert _read(home) is None

    @pytest.mark.parametrize("text", ["", "abc", "-5", "0", "12 34"])
    def test_a_pid_file_that_names_no_pid(self, proc_root: Path, home: Path, text: str) -> None:
        _write_pid_file(home, text)

        assert _read(home) is None

    def test_a_stale_pid_file_naming_a_gone_process(self, proc_root: Path, home: Path) -> None:
        _write_pid_file(home, str(STEAM_PID))

        assert _read(home) is None

    def test_a_stale_pid_file_naming_another_program(self, proc_root: Path, home: Path) -> None:
        _make_process(
            proc_root, STEAM_PID, comm="bash", parent=1, namespace=HOST_NAMESPACE, environ={}, exe="/bin/bash"
        )
        _write_pid_file(home, str(STEAM_PID))

        assert _read(home) is None

    def test_an_executable_that_cannot_be_read(self, proc_root: Path, home: Path) -> None:
        _make_process(proc_root, STEAM_PID, comm="steam", parent=1, namespace=HOST_NAMESPACE, environ={}, exe=None)
        _write_pid_file(home, str(STEAM_PID))

        assert _read(home) is None

    def test_an_environment_that_cannot_be_read(self, proc_root: Path, home: Path) -> None:
        # Unread, it cannot say whether gamescope started Steam.
        _make_process(
            proc_root, STEAM_PID, comm="steam", parent=1, namespace=HOST_NAMESPACE, environ=None, exe=STEAM_64
        )
        _write_pid_file(home, str(STEAM_PID))

        assert _read(home) is None

    def test_a_stat_line_that_cannot_be_read(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        _steam_in_its_container(
            proc_root, home, session_authority=session_authority, container_authority=container_authority
        )
        (proc_root / str(STEAM_PID) / "stat").write_text(f"{STEAM_PID} (steam) S 1\n", encoding="utf-8")

        assert _read(home) is None

    def test_a_restarted_steam_is_another_identity(
        self, proc_root: Path, home: Path, session_authority: str, container_authority: str
    ) -> None:
        _steam_in_its_container(
            proc_root, home, session_authority=session_authority, container_authority=container_authority
        )
        first = _read(home)
        stat = proc_root / str(STEAM_PID) / "stat"
        stat.write_text(stat.read_text().replace("1234567", "7654321"), encoding="utf-8")

        second = _read(home)

        assert first is not None
        assert second is not None
        assert first.identity != second.identity
