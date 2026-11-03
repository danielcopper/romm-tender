"""Unit tests for ``SteamProcessTreeAdapter`` — Steam's ``reaper`` trees read from ``/proc``.

Drives a fake ``/proc`` under ``tmp_path`` by pointing the adapter's ``_PROC``
module constant at it, so finding the ``reaper`` Steam started a shortcut
under, the deepest-first descent below it and every fail-soft path are
exercised without a live game. The signal methods are driven through a stubbed
``os.kill`` — nothing here ever signals a real process.
"""

from __future__ import annotations

import ast
import signal
from pathlib import Path

import pytest

from adapters import steam_process_tree
from adapters.steam_process_tree import SteamProcessTreeAdapter

APP_ID = 3467250184
OTHER_APP_ID = 2157604804
LAUNCHER = "/home/deck/.local/bin/tender-rom-launcher"


@pytest.fixture
def proc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``/proc`` the adapter reads process identity + children from."""
    root = tmp_path / "proc"
    root.mkdir()
    monkeypatch.setattr(steam_process_tree, "_PROC", str(root))
    return root


def _make_process(
    proc_root: Path,
    pid: int,
    comm: str,
    children: list[int] | None = None,
    argv: list[str] | None = None,
) -> None:
    """Write a fake ``/proc/<pid>`` entry: ``comm``, ``children``, and ``cmdline``.

    *argv* ``None`` writes just the *comm* as the command line, NUL-terminated
    as the kernel writes it.
    """
    entry = proc_root / str(pid)
    task = entry / "task" / str(pid)
    task.mkdir(parents=True)
    (entry / "comm").write_text(f"{comm}\n", encoding="utf-8")
    task.joinpath("children").write_text(" ".join(str(c) for c in (children or [])) + " \n", encoding="utf-8")
    tokens = argv if argv is not None else [comm]
    (entry / "cmdline").write_text("".join(f"{token}\0" for token in tokens), encoding="utf-8")


def _reaper_argv(app_id: int, *command: str) -> list[str]:
    """The command line Steam starts a shortcut's ``reaper`` with."""
    return [
        "/home/deck/.local/share/Steam/steamrt64/reaper",
        "SteamLaunch",
        f"AppId={app_id}",
        "--",
        LAUNCHER,
        *command,
    ]


def _make_reaper(proc_root: Path, pid: int, app_id: int, children: list[int]) -> None:
    """Write the ``reaper`` Steam starts shortcut *app_id* under, with *children* below it."""
    _make_process(proc_root, pid, "reaper", children, _reaper_argv(app_id))


def _find_pids(app_id: int = APP_ID) -> list[int]:
    return SteamProcessTreeAdapter().find_game_processes(app_id)


def _make_status(proc_root: Path, pid: int, state: str) -> None:
    """Write a fake ``/proc/<pid>/status`` whose ``State:`` line carries *state*."""
    entry = proc_root / str(pid)
    entry.mkdir(exist_ok=True)
    (entry / "status").write_text(
        f"Name:\tretroarch\nUmask:\t0022\nState:\t{state} (whatever)\nTgid:\t{pid}\n",
        encoding="utf-8",
    )


# ── find_game_processes ──────────────────────────────────────────────────────


class TestTheShortcutsTree:
    def test_returns_the_reapers_subtree_deepest_first_without_the_reaper_or_bwrap(self, proc_root: Path) -> None:
        # reaper(1000) → bwrap(1001) → bwrap(1002) → launcher(1003) → retroarch(1004).
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "bwrap", [1002])
        _make_process(proc_root, 1002, "bwrap", [1003])
        _make_process(proc_root, 1003, "component_launc", [1004])
        _make_process(proc_root, 1004, "retroarch", [])

        assert _find_pids() == [1004, 1003]

    def test_sibling_children_share_a_level_and_precede_their_parent(self, proc_root: Path) -> None:
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "launcher", [1002, 1003])
        _make_process(proc_root, 1002, "duckstation", [])
        _make_process(proc_root, 1003, "helper", [])

        pids = _find_pids()
        assert pids[-1] == 1001
        assert set(pids[:2]) == {1002, 1003}

    def test_another_games_tree_in_the_same_emulator_is_never_returned(self, proc_root: Path) -> None:
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "retroarch", [])
        _make_reaper(proc_root, 2000, OTHER_APP_ID, [2001])
        _make_process(proc_root, 2001, "retroarch", [])

        assert _find_pids(APP_ID) == [1001]
        assert _find_pids(OTHER_APP_ID) == [2001]

    def test_a_flatpak_other_than_retrodeck_is_found(self, proc_root: Path) -> None:
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "bwrap", [1002])
        _make_process(proc_root, 1002, "bwrap", [1003])
        _make_process(proc_root, 1003, "PPSSPPSDL", [], ["/app/bin/PPSSPPSDL", "/roms/psp/game.iso"])

        assert _find_pids() == [1003]

    def test_an_appimage_is_found(self, proc_root: Path) -> None:
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(
            proc_root, 1001, "Cemu", [], ["/home/deck/Applications/Cemu.AppImage", "-g", "/roms/wiiu/game.wua"]
        )

        assert _find_pids() == [1001]

    def test_an_app_id_that_is_only_a_prefix_of_ours_does_not_match(self, proc_root: Path) -> None:
        _make_reaper(proc_root, 1000, int(str(APP_ID) + "7"), [1001])
        _make_process(proc_root, 1001, "retroarch", [])

        assert _find_pids() == []

    def test_an_app_id_after_the_end_of_the_reapers_options_does_not_match(self, proc_root: Path) -> None:
        # The command the shortcut runs may carry anything; only the reaper's own options name the shortcut.
        argv = [*_reaper_argv(OTHER_APP_ID), f"AppId={APP_ID}"]
        _make_process(proc_root, 1000, "reaper", [1001], argv)
        _make_process(proc_root, 1001, "retroarch", [])

        assert _find_pids() == []

    def test_a_reaper_not_started_by_steam_launch_does_not_match(self, proc_root: Path) -> None:
        argv = ["/usr/bin/reaper", "Other", f"AppId={APP_ID}", "--", LAUNCHER]
        _make_process(proc_root, 1000, "reaper", [1001], argv)
        _make_process(proc_root, 1001, "retroarch", [])

        assert _find_pids() == []

    def test_a_process_that_is_not_the_reaper_never_roots_the_tree(self, proc_root: Path) -> None:
        # The command line alone names no reaper: another program can be started with the same one.
        _make_process(proc_root, 1000, "bash", [1001], _reaper_argv(APP_ID))
        _make_process(proc_root, 1001, "retroarch", [])

        assert _find_pids() == []

    def test_steam_launch_wrapper_above_the_reaper_never_roots_the_tree(self, proc_root: Path) -> None:
        # steam-launch-wrapper's command line carries the reaper's own as its tail.
        argv = ["steam-launch-wrapper", "--", *_reaper_argv(APP_ID)]
        _make_process(proc_root, 1000, "steam-launch-wr", [1001], argv)
        _make_process(proc_root, 1001, "retroarch", [])

        assert _find_pids() == []

    def test_a_reaper_of_another_user_is_never_ours(self, proc_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "retroarch", [])
        foreign_uid = (proc_root / "1000").stat().st_uid + 1
        monkeypatch.setattr(steam_process_tree.os, "getuid", lambda: foreign_uid)

        assert _find_pids() == []

    def test_no_reaper_for_the_shortcut_yields_no_pids(self, proc_root: Path) -> None:
        _make_process(proc_root, 1000, "systemd", [1001])
        _make_process(proc_root, 1001, "retroarch", [])

        assert _find_pids() == []

    def test_an_unreadable_proc_yields_no_pids(self, proc_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "retroarch", [])

        def _boom(_path: str) -> list[str]:
            raise PermissionError("nope")

        monkeypatch.setattr(steam_process_tree.os, "listdir", _boom)

        assert _find_pids() == []

    def test_a_pid_that_vanished_mid_scan_is_skipped_not_fatal(self, proc_root: Path) -> None:
        # 1002 is listed as a child but its /proc entry is already gone.
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "retroarch", [1002])

        assert _find_pids() == [1001]

    def test_a_self_referencing_children_list_terminates(self, proc_root: Path) -> None:
        # /proc can't really produce a cycle, but the visited set must make one
        # harmless rather than hanging the backend.
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        _make_process(proc_root, 1001, "retroarch", [1000, 1001])

        assert _find_pids() == [1001]

    def test_a_children_file_with_junk_tokens_keeps_only_the_pids(self, proc_root: Path) -> None:
        _make_reaper(proc_root, 1000, APP_ID, [])
        (proc_root / "1000" / "task" / "1000" / "children").write_text("1001 -3 abc \n", encoding="utf-8")
        _make_process(proc_root, 1001, "retroarch", [])

        assert _find_pids() == [1001]

    def test_a_tree_deeper_than_the_depth_cap_stops_at_the_cap(self, proc_root: Path) -> None:
        # A chain twice as deep as the cap: the walk is bounded, and what it did
        # reach is still ordered deepest-first.
        depth = steam_process_tree._MAX_DEPTH * 2
        _make_reaper(proc_root, 1000, APP_ID, [1001])
        for offset in range(1, depth):
            _make_process(proc_root, 1000 + offset, f"level{offset}", [1000 + offset + 1])
        _make_process(proc_root, 1000 + depth, "deepest", [])

        pids = _find_pids()
        assert len(pids) == steam_process_tree._MAX_DEPTH
        assert pids == sorted(pids, reverse=True)


# ── request_stop / force_kill ────────────────────────────────────────────────


class TestSignalDelivery:
    @staticmethod
    def _record_kills(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(steam_process_tree.os, "kill", lambda pid, sig: sent.append((pid, sig)))
        return sent

    def test_request_stop_sends_exactly_one_sigterm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = self._record_kills(monkeypatch)

        assert SteamProcessTreeAdapter().request_stop(4321) is True
        assert sent == [(4321, signal.SIGTERM)]

    def test_force_kill_sends_sigkill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = self._record_kills(monkeypatch)

        assert SteamProcessTreeAdapter().force_kill(4321) is True
        assert sent == [(4321, signal.SIGKILL)]

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(ProcessLookupError(), id="already-exited"),
            pytest.param(PermissionError(), id="not-ours-to-signal"),
            pytest.param(OSError("weird"), id="other-oserror"),
        ],
    )
    def test_an_undeliverable_signal_reports_false_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch, error: OSError
    ) -> None:
        def _boom(_pid: int, _sig: int) -> None:
            raise error

        monkeypatch.setattr(steam_process_tree.os, "kill", _boom)
        adapter = SteamProcessTreeAdapter()

        assert adapter.request_stop(4321) is False
        assert adapter.force_kill(4321) is False


# ── is_alive ─────────────────────────────────────────────────────────────────


class TestIsAlive:
    @pytest.mark.parametrize("state", ["R", "S", "D", "T", "I"])
    def test_a_running_state_reads_as_alive(self, proc_root: Path, state: str) -> None:
        _make_status(proc_root, 900, state)

        assert SteamProcessTreeAdapter().is_alive(900) is True

    @pytest.mark.parametrize("state", ["Z", "X", "x"])
    def test_an_exited_state_reads_as_not_alive(self, proc_root: Path, state: str) -> None:
        # A zombie has already run its exit path; calling it alive would burn the
        # caller's whole grace window waiting for something that never changes.
        _make_status(proc_root, 900, state)

        assert SteamProcessTreeAdapter().is_alive(900) is False

    def test_a_missing_status_reads_as_not_alive(self, proc_root: Path) -> None:
        assert SteamProcessTreeAdapter().is_alive(900) is False

    def test_a_status_without_a_state_line_reads_as_not_alive(self, proc_root: Path) -> None:
        entry = proc_root / "900"
        entry.mkdir()
        (entry / "status").write_text("Name:\tretroarch\nTgid:\t900\n", encoding="utf-8")

        assert SteamProcessTreeAdapter().is_alive(900) is False

    def test_a_truncated_state_line_reads_as_not_alive(self, proc_root: Path) -> None:
        # A mid-write read can leave "State:" with no value — the IndexError guard.
        entry = proc_root / "900"
        entry.mkdir()
        (entry / "status").write_text("Name:\tretroarch\nState:\n", encoding="utf-8")

        assert SteamProcessTreeAdapter().is_alive(900) is False


# ── launched_app_id ──────────────────────────────────────────────────────────


def _set_parent(proc_root: Path, pid: int, parent: int) -> None:
    """Write a fake ``/proc/<pid>/status`` naming *parent* on its ``PPid:`` line."""
    (proc_root / str(pid) / "status").write_text(
        f"Name:\tx\nState:\tS (sleeping)\nTgid:\t{pid}\nPid:\t{pid}\nPPid:\t{parent}\n", encoding="utf-8"
    )


def _chain(proc_root: Path, *processes: tuple[int, str, list[str] | None]) -> None:
    """Write *processes* as a parent chain, the first the topmost, each child of the one before it."""
    parent = 1
    for pid, comm, argv in processes:
        _make_process(proc_root, pid, comm, [], argv)
        _set_parent(proc_root, pid, parent)
        parent = pid


def _launched(pid: int) -> int | None:
    return SteamProcessTreeAdapter().launched_app_id(pid)


class TestTheLaunchAProcessRunsUnder:
    def test_the_walk_up_through_the_sandbox_finds_the_reaper_s_app_id(self, proc_root: Path) -> None:
        # reaper(1000) → bwrap(1001) → bwrap(1002) → launcher(1003) → retroarch(1004).
        _chain(
            proc_root,
            (1000, "reaper", _reaper_argv(APP_ID)),
            (1001, "bwrap", None),
            (1002, "bwrap", None),
            (1003, "component_launc", None),
            (1004, "retroarch", None),
        )

        assert _launched(1004) == APP_ID

    def test_any_game_s_launch_counts_not_only_a_shortcut_s(self, proc_root: Path) -> None:
        steam_game = 1663850
        _chain(proc_root, (1000, "reaper", _reaper_argv(steam_game)), (1001, "game", None))

        assert _launched(1001) == steam_game

    def test_a_process_under_no_reaper_runs_under_no_launch(self, proc_root: Path) -> None:
        _chain(proc_root, (900, "plasmashell", None), (901, "konsole", None))

        assert _launched(901) is None

    def test_a_reaper_of_another_user_is_not_this_user_s_launch(
        self, proc_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _chain(proc_root, (1000, "reaper", _reaper_argv(APP_ID)), (1001, "retroarch", None))
        foreign_uid = (proc_root / "1000").stat().st_uid + 1
        monkeypatch.setattr(steam_process_tree.os, "getuid", lambda: foreign_uid)

        assert _launched(1001) is None

    def test_a_reaper_without_steam_launch_is_no_launch(self, proc_root: Path) -> None:
        _chain(
            proc_root,
            (1000, "reaper", ["/usr/bin/reaper", "Other", f"AppId={APP_ID}", "--", LAUNCHER]),
            (1001, "retroarch", None),
        )

        assert _launched(1001) is None

    def test_an_app_id_after_the_end_of_the_reaper_s_options_is_ignored(self, proc_root: Path) -> None:
        argv = ["/home/deck/.local/share/Steam/steamrt64/reaper", "SteamLaunch", "--", LAUNCHER, f"AppId={APP_ID}"]
        _chain(proc_root, (1000, "reaper", argv), (1001, "retroarch", None))

        assert _launched(1001) is None

    def test_a_process_that_only_carries_the_reaper_s_command_line_is_not_one(self, proc_root: Path) -> None:
        _chain(
            proc_root,
            (1000, "steam-launch-wr", ["steam-launch-wrapper", "--", *_reaper_argv(APP_ID)]),
            (1001, "retroarch", None),
        )

        assert _launched(1001) is None

    def test_the_nearest_reaper_answers(self, proc_root: Path) -> None:
        _chain(
            proc_root,
            (1000, "reaper", _reaper_argv(OTHER_APP_ID)),
            (1001, "reaper", _reaper_argv(APP_ID)),
            (1002, "retroarch", None),
        )

        assert _launched(1002) == APP_ID

    def test_a_parent_that_vanished_ends_the_walk(self, proc_root: Path) -> None:
        _make_process(proc_root, 1001, "retroarch", [])
        _set_parent(proc_root, 1001, 1000)

        assert _launched(1001) is None

    def test_a_status_without_a_ppid_line_ends_the_walk(self, proc_root: Path) -> None:
        _make_process(proc_root, 1001, "retroarch", [])
        (proc_root / "1001" / "status").write_text("Name:\tretroarch\nState:\tS\n", encoding="utf-8")

        assert _launched(1001) is None

    def test_a_pid_with_no_proc_entry_runs_under_no_launch(self, proc_root: Path) -> None:
        assert _launched(4321) is None

    def test_the_walk_stops_at_the_depth_cap(self, proc_root: Path) -> None:
        depth = steam_process_tree._MAX_DEPTH
        _chain(
            proc_root,
            (1000, "reaper", _reaper_argv(APP_ID)),
            *[(1000 + offset, f"level{offset}", None) for offset in range(1, depth + 1)],
        )

        assert _launched(1000 + depth - 1) == APP_ID
        assert _launched(1000 + depth) is None

    def test_a_parent_loop_terminates(self, proc_root: Path) -> None:
        _make_process(proc_root, 1000, "a", [])
        _make_process(proc_root, 1001, "b", [])
        _set_parent(proc_root, 1000, 1001)
        _set_parent(proc_root, 1001, 1000)

        assert _launched(1001) is None

    @pytest.mark.parametrize("app_id", ["0", "", "12ab", "-5"])
    def test_an_app_id_that_is_no_positive_number_names_no_launch(self, proc_root: Path, app_id: str) -> None:
        argv = ["/home/deck/.local/share/Steam/steamrt64/reaper", "SteamLaunch", f"AppId={app_id}", "--", LAUNCHER]
        _chain(proc_root, (1000, "reaper", argv), (1001, "retroarch", None))

        assert _launched(1001) is None


# ── Flatpak's instance registry ──────────────────────────────────────────────

_BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _modules_naming_the_instance_registry(root: Path) -> list[str]:
    """Every backend module, the vendored trees aside, whose code spells Flatpak's per-user instance registry.

    The registry is ``$XDG_RUNTIME_DIR/.flatpak/<instance>/``, each instance
    with its ``bwrapinfo.json``.
    """
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts[0] in {"_vendor", "native"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lines = sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (node.value == ".flatpak" or "/.flatpak" in node.value or "bwrapinfo" in node.value)
        )
        hits.extend(f"{relative}:{line}" for line in lines)
    return hits


class TestNoModuleReadsFlatpaksInstanceRegistry:
    """A running game is found through Steam's tree, never through Flatpak's own record of what it runs."""

    def test_no_backend_module_names_it(self) -> None:
        assert _modules_naming_the_instance_registry(_BACKEND) == []

    def test_the_scan_sees_a_module_that_does(self, tmp_path: Path) -> None:
        # The control: a scan that never matched would pass the test above too.
        (tmp_path / "adapters").mkdir()
        (tmp_path / "adapters" / "instances.py").write_text(
            'import os\nBASE = os.path.join("/run/user/1000", ".flatpak")\nINFO = "bwrapinfo.json"\n',
            encoding="utf-8",
        )

        assert _modules_naming_the_instance_registry(tmp_path) == ["adapters/instances.py:2", "adapters/instances.py:3"]
