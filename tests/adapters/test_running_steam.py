"""Unit tests for ``RunningSteamAdapter`` — which Steam runs, and how, read from its pid file and ``/proc``.

Drives a fake ``/proc`` under ``tmp_path`` by pointing the adapter's ``_PROC``
module constant at it, and the test's own home holding ``.steam/steam.pid``.
"""

from __future__ import annotations

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
HOST_AUTHORITY = "/run/user/1000/xauth_QkUzSn"


@pytest.fixture
def proc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proc"
    (root / "self" / "ns").mkdir(parents=True)
    (root / "self" / "ns" / "user").symlink_to(HOST_NAMESPACE)
    monkeypatch.setattr(running_steam, "_PROC", str(root))
    return root


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
    proc_root: Path, home: Path, *, exe: str = STEAM_64, environ: dict[str, str] | None = None
) -> None:
    """Steam inside its container, under a launch script on the host that names the session's display."""
    _make_process(
        proc_root,
        4000,
        comm="steam.sh",
        parent=1,
        namespace=HOST_NAMESPACE,
        environ={"DISPLAY": ":0", "XAUTHORITY": HOST_AUTHORITY},
    )
    _make_process(
        proc_root,
        4500,
        comm="pv-adverb",
        parent=4000,
        namespace=CONTAINER_NAMESPACE,
        environ={"DISPLAY": ":0", "XAUTHORITY": "/run/pressure-vessel/Xauthority"},
    )
    _make_process(
        proc_root,
        STEAM_PID,
        comm="steam",
        parent=4500,
        namespace=CONTAINER_NAMESPACE,
        environ=environ if environ is not None else {"DISPLAY": ":0", "XAUTHORITY": "/run/pressure-vessel/Xauthority"},
        exe=exe,
    )
    _write_pid_file(home, f"{STEAM_PID}\n")


def _read(home: Path) -> RunningSteam | None:
    return RunningSteamAdapter(user_home=str(home)).read()


class TestTheSteamThatRuns:
    def test_the_64_bit_client_in_its_container(self, proc_root: Path, home: Path) -> None:
        _steam_in_its_container(proc_root, home)

        assert _read(home) == RunningSteam(
            identity=SteamIdentity(pid=STEAM_PID, start_time=1234567),
            exe=STEAM_64,
            under_gamescope=False,
            display=XDisplay(name=":0", authority_path=HOST_AUTHORITY),
        )

    def test_the_32_bit_client_on_the_host_names_its_own_display(self, proc_root: Path, home: Path) -> None:
        _make_process(
            proc_root,
            STEAM_PID,
            comm="steam",
            parent=1,
            namespace=HOST_NAMESPACE,
            environ={"DISPLAY": ":1", "XAUTHORITY": HOST_AUTHORITY},
            exe=STEAM_32,
        )
        _write_pid_file(home, str(STEAM_PID))

        steam = _read(home)

        assert steam is not None
        assert steam.exe == STEAM_32
        assert steam.display == XDisplay(name=":1", authority_path=HOST_AUTHORITY)

    def test_gamescope_in_steam_s_environment_is_game_mode(self, proc_root: Path, home: Path) -> None:
        _steam_in_its_container(proc_root, home, environ={"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"})

        steam = _read(home)

        assert steam is not None
        assert steam.under_gamescope is True

    def test_the_authority_comes_from_the_host_process_not_the_container(self, proc_root: Path, home: Path) -> None:
        _steam_in_its_container(proc_root, home)

        steam = _read(home)

        assert steam is not None
        assert steam.display is not None
        assert steam.display.authority_path == HOST_AUTHORITY

    def test_no_xauthority_means_the_home_s_file(self, proc_root: Path, home: Path) -> None:
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

    def test_no_display_on_the_host_process_is_no_display(self, proc_root: Path, home: Path) -> None:
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

    def test_a_command_with_a_parenthesis_still_yields_its_start_time(self, proc_root: Path, home: Path) -> None:
        _steam_in_its_container(proc_root, home)
        stat = proc_root / str(STEAM_PID) / "stat"
        stat.write_text(stat.read_text().replace("(steam)", "(st) eam)"), encoding="utf-8")

        steam = _read(home)

        assert steam is not None
        assert steam.identity.start_time == 1234567


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

    def test_a_stat_line_that_cannot_be_read(self, proc_root: Path, home: Path) -> None:
        _steam_in_its_container(proc_root, home)
        (proc_root / str(STEAM_PID) / "stat").write_text(f"{STEAM_PID} (steam) S 1\n", encoding="utf-8")

        assert _read(home) is None

    def test_a_restarted_steam_is_another_identity(self, proc_root: Path, home: Path) -> None:
        _steam_in_its_container(proc_root, home)
        first = _read(home)
        stat = proc_root / str(STEAM_PID) / "stat"
        stat.write_text(stat.read_text().replace("1234567", "7654321"), encoding="utf-8")

        second = _read(home)

        assert first is not None
        assert second is not None
        assert first.identity != second.identity
