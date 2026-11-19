"""Tests for ``scripts/setup_remote_target.py`` — ``mise run setup:remote-target``.

The script is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``, and run through its ``main``. Every program it starts is a
stand-in from ``tests/_remote_target.py`` on a ``PATH`` that holds nothing
else of the kind, so no case runs a real ``ssh``, ``ssh-keygen`` or ``ip``, or
contacts anything. The build host is the test's ``home``; the target is a
directory of its own, which the ``ssh`` stand-in logs into and whose real
``authorized_keys`` it honours — so a second run with the dev loop's key really
goes through the ``from=`` and the dispatcher the first run installed.

"Both machines as they were" is checked as every file and directory under both
homes, with its bytes, before and after.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import _remote_target
import pytest
from _remote_target import (
    AGENT,
    NOTHING,
    PASSWORD,
    WORLD_VARIABLE,
    OwnAccess,
    Target,
    World,
    fake_path,
    log_lines,
    ssh_calls,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_remote_target.py"
_spec = importlib.util.spec_from_file_location("setup_remote_target", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
setup_remote_target = importlib.util.module_from_spec(_spec)
sys.modules["setup_remote_target"] = setup_remote_target
_spec.loader.exec_module(setup_remote_target)

_DISPATCHER = Path(__file__).resolve().parents[2] / "scripts" / "dev_dispatch.sh"

_ADDRESS = "192.168.1.20"
_NEW_ADDRESS = "192.168.1.30"
# Where the target sees the build host — deliberately not the address the build
# host's route names (World.route_source), so a test can tell which one a line got.
_SEEN_FROM = "10.8.0.6"
_HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyOfTheTarget"


@dataclass
class Remote:
    world: World
    world_file: Path
    tools_path: str
    target_home: Path
    build_home: Path
    host_keys: Path

    def save(self) -> None:
        self.world.save(self.world_file)

    def target(self, address: str = _ADDRESS) -> Target:
        return self.world.targets[address]

    def move_target(self, address: str) -> None:
        self.world.targets = {address: next(iter(self.world.targets.values()))}
        self.save()

    def setup(self, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
        self.save()
        code = setup_remote_target.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def run_on_target(self, script: str, *argv: str) -> subprocess.CompletedProcess[str]:
        """Run a printed script on the target, as its user would in a terminal there."""
        path = self.target_home / "setup.sh"
        path.write_text(script, encoding="utf-8")
        env = {"HOME": str(self.target_home), "PATH": self.tools_path}
        result = subprocess.run(
            ["sh", str(path), *argv], cwd=self.target_home, capture_output=True, text=True, check=False, env=env
        )
        path.unlink()
        return result

    @property
    def dev_dir(self) -> Path:
        return self.build_home / ".config" / "romm-tender-dev"

    @property
    def authorized_keys(self) -> Path:
        return self.target_home / ".ssh" / "authorized_keys"

    def key_lines(self) -> list[str]:
        return [line for line in self.authorized_keys.read_text(encoding="utf-8").splitlines() if line]

    def public_key(self) -> str:
        return (self.dev_dir / "id_ed25519.pub").read_text(encoding="utf-8").strip()

    def config(self) -> str:
        return (self.dev_dir / "ssh_config").read_text(encoding="utf-8")


@pytest.fixture
def remote(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Remote:
    tools = tmp_path / "tools"
    tools.mkdir()
    target_home = tmp_path / "target"
    target_home.mkdir()
    host_keys = tmp_path / "etc-ssh"
    host_keys.mkdir()
    (host_keys / "ssh_host_ed25519_key.pub").write_text(f"{_HOST_KEY} root@deck\n", encoding="utf-8")
    world = World(
        targets={},
        log=str(tmp_path / "log"),
    )
    path = fake_path(tools)
    world.targets[_ADDRESS] = Target(home=str(target_home), client_address=_SEEN_FROM, host_key=_HOST_KEY, path=path)
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv(WORLD_VARIABLE, str(tmp_path / "world.json"))
    monkeypatch.setattr(setup_remote_target, "HOST_KEY_DIR", str(host_keys))
    return Remote(world, tmp_path / "world.json", path, target_home, home, host_keys)


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every file and directory under *root*: a file's bytes, ``None`` for a directory."""
    tree: dict[str, bytes | None] = {}
    for directory, folders, files in os.walk(root):
        for folder in folders:
            tree[os.path.relpath(os.path.join(directory, folder), root)] = None
        for name in files:
            path = os.path.join(directory, name)
            tree[os.path.relpath(path, root)] = Path(path).read_bytes()
    return tree


def _dispatcher_line(remote: Remote, from_address: str) -> str:
    dispatcher = remote.target_home / ".local/lib/romm-tender-dev/dispatch.sh"
    return f'from="{from_address}",command="{dispatcher}",restrict {remote.public_key()}'


class TestTheSetupLogin:
    def test_a_first_run_installs_the_dispatcher_and_the_one_restricted_line(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        root = remote.target_home / ".local/lib/romm-tender-dev"
        assert (root / "dispatch.sh").read_text(encoding="utf-8") == _DISPATCHER.read_text(encoding="utf-8")
        assert os.access(root / "dispatch.sh", os.X_OK)
        assert (root / "current").is_dir()
        assert (root / "incoming").is_dir()
        comment, line = remote.key_lines()
        assert comment.startswith(f"# romm-tender-dev@{setup_remote_target.build_host_name()}: written by")
        assert "Running it again changes it." in comment
        assert line == _dispatcher_line(remote, _SEEN_FROM)
        assert log_lines(remote.world) == [
            "host key shown romm-tender-dev.deck",
            f"password asked {_ADDRESS}",
            f"login {_ADDRESS} own",
            f"login {_ADDRESS} key",
        ]

    def test_the_line_restricts_the_key_to_the_address_the_target_sees(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#2844 D5: the address ``$SSH_CLIENT`` shows, not the one this machine's route names."""
        remote.world.route_source = "192.168.1.10"

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert remote.key_lines()[1].startswith(f'from="{_SEEN_FROM}",')
        assert f"from {_SEEN_FROM} only" in err

    def test_it_writes_the_target_into_the_dev_loops_own_config_and_known_hosts(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        directory = remote.dev_dir
        lines = remote.config().splitlines()
        entry = lines[lines.index("Host deck") :]
        assert lines[lines.index("Host deck") - 1].startswith("# deck: written by setup:remote-target on ")
        assert entry == [
            "Host deck",
            f"    HostName {_ADDRESS}",
            "    Port 22",
            "    User deck",
            "    HostKeyAlias romm-tender-dev.deck",
            f'    IdentityFile "{directory / "id_ed25519"}"',
            "    IdentitiesOnly yes",
            f'    UserKnownHostsFile "{directory / "known_hosts"}"',
            "    GlobalKnownHostsFile /dev/null",
            "    StrictHostKeyChecking yes",
            "    BatchMode yes",
        ]
        assert (directory / "known_hosts").read_text(encoding="utf-8") == f"romm-tender-dev.deck {_HOST_KEY}\n"
        assert (directory / "id_ed25519").stat().st_mode & 0o077 == 0
        assert (directory / "ssh_config").stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o077 == 0
        assert not (remote.build_home / ".ssh").exists()

    def test_an_alias_from_the_users_config_is_written_resolved_with_its_jump_host(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.configs = {
            "deck-alias": {"user": ["deck"], "hostname": [_ADDRESS], "port": ["2222"], "proxyjump": ["bastion"]},
            "bastion": {"user": ["jumper"], "hostname": ["203.0.113.5"], "port": ["22022"]},
        }

        code, _, err = remote.setup(capsys, "deck", "deck-alias")

        assert code == 0, err
        assert "    HostName 192.168.1.20\n    Port 2222\n    User deck\n" in remote.config()
        assert "    ProxyJump jumper@203.0.113.5:22022\n" in remote.config()

    @pytest.mark.parametrize(
        ("hop", "written"),
        [
            ("bastion:2222", "jumper@203.0.113.5:2222"),
            ("ops@bastion:2222", "ops@203.0.113.5:2222"),
            ("ssh://bastion:2222", "jumper@203.0.113.5:2222"),
            ("[fd00::5]:2200", "builder@[fd00::5]:2200"),
            ("ops@[fd00::5]", "ops@[fd00::5]:22"),
            ("bastion,ops@[fd00::5]:2200", "jumper@203.0.113.5:22022,ops@[fd00::5]:2200"),
        ],
    )
    def test_each_hop_is_resolved_with_its_own_user_and_port(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], hop: str, written: str
    ) -> None:
        remote.world.configs = {
            "deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxyjump": [hop]},
            "bastion": {"user": ["jumper"], "hostname": ["203.0.113.5"], "port": ["22022"]},
        }

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 0, err
        assert f"    ProxyJump {written}\n" in remote.config()

    def test_a_proxy_command_from_the_users_config_is_written_into_the_entry(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        proxy = "nc -X connect -x proxy.example:3128 %h %p"
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxycommand": [proxy]}}

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 0, err
        assert f"    User deck\n    ProxyCommand {proxy}\n    HostKeyAlias" in remote.config()

    def test_print_refuses_a_proxy_command(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxycommand": ["nc %h %p"]}}

        code, script, err = remote.setup(capsys, "deck", "deck", "--print")

        assert code == 1
        assert script == ""
        assert "--print cannot set up a target reached through nc %h %p" in err

    def test_first_contact_asks_even_where_the_users_config_checks_strictly(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "stricthostkeychecking": ["yes"]}}

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 0, err
        assert log_lines(remote.world)[0] == "host key shown romm-tender-dev.deck"

    def test_other_lines_of_authorized_keys_stay_and_an_old_line_of_this_build_host_goes(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        comment = f"romm-tender-dev@{setup_remote_target.build_host_name()}"
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.write_text(
            "ssh-ed25519 AAAAothersKey someone@laptop\n"
            f"# {comment}: written by setup:remote-target from somewhere, long ago. Running it again changes it.\n"
            f'from="10.0.0.1",command="/x",restrict ssh-ed25519 AAAAanOldKeyOfThisHost {comment}\n'
            "ssh-ed25519 AAAAanotherOne other@desktop\n"
            f"# {comment}x: written by setup:remote-target from elsewhere. Running it again changes it.\n"
            f'from="10.0.0.2",command="/y",restrict ssh-ed25519 AAAAanotherBuildHost {comment}x\n',
            encoding="utf-8",
        )

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        lines = remote.key_lines()
        assert lines[:4] == [
            "ssh-ed25519 AAAAothersKey someone@laptop",
            "ssh-ed25519 AAAAanotherOne other@desktop",
            f"# {comment}x: written by setup:remote-target from elsewhere. Running it again changes it.",
            f'from="10.0.0.2",command="/y",restrict ssh-ed25519 AAAAanotherBuildHost {comment}x',
        ]
        assert lines[5] == _dispatcher_line(remote, _SEEN_FROM)
        assert len(lines) == 6

    def test_an_unrestricted_line_of_the_same_key_goes(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A line that would let the key past the dispatcher, however it got there."""
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        blob = remote.public_key().split()[1]
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.write_text(f"ssh-ed25519 {blob} copied-by-hand\n", encoding="utf-8")
        remote.target().own_access = AGENT

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert [line for line in remote.key_lines() if blob in line] == [_dispatcher_line(remote, _SEEN_FROM)]


class TestRunAgain:
    @pytest.mark.parametrize("own_access", [AGENT, PASSWORD])
    def test_run_twice_it_leaves_one_key_one_line_and_one_entry(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], own_access: OwnAccess
    ) -> None:
        remote.target().own_access = own_access
        first, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        assert first == 0, err
        key = (remote.dev_dir / "id_ed25519").read_bytes()
        target_after_first = _snapshot(remote.target_home)

        second, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert second == 0, err
        assert (remote.dev_dir / "id_ed25519").read_bytes() == key
        assert sorted(p.name for p in remote.dev_dir.iterdir()) == [
            "id_ed25519",
            "id_ed25519.pub",
            "known_hosts",
            "ssh_config",
        ]
        assert len(remote.key_lines()) == 2
        assert _snapshot(remote.target_home) == target_after_first
        assert remote.config().count("Host deck\n") == 1
        assert len((remote.dev_dir / "known_hosts").read_text(encoding="utf-8").splitlines()) == 1

    def test_a_second_run_after_the_targets_address_changed_needs_no_password(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        first, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        assert first == 0, err
        remote.move_target(_NEW_ADDRESS)
        logged = len(log_lines(remote.world))

        second, _, err = remote.setup(capsys, "deck", f"deck@{_NEW_ADDRESS}")

        assert second == 0, err
        assert log_lines(remote.world)[logged:] == [f"login {_NEW_ADDRESS} key"] * 2
        assert f"    HostName {_NEW_ADDRESS}\n" in remote.config()
        assert _ADDRESS not in remote.config()
        assert remote.config().count("Host deck\n") == 1
        assert len(remote.key_lines()) == 2

    def test_a_rerun_repairs_the_target_through_the_key_without_a_password(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#2844 D11: a user whose own login is a password repairs the target with the dev key."""
        first, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        assert first == 0, err
        root = remote.target_home / ".local/lib/romm-tender-dev"
        with open(root / "dispatch.sh", "a", encoding="utf-8") as dispatcher:
            dispatcher.write("# edited on the target\n")
        (root / "incoming").rmdir()
        remote.target().client_address = "10.8.0.7"
        remote.authorized_keys.write_text(
            remote.authorized_keys.read_text(encoding="utf-8").replace(_SEEN_FROM, "10.8.0.7"), encoding="utf-8"
        )
        logged = len(log_lines(remote.world))

        second, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert second == 0, err
        assert log_lines(remote.world)[logged:] == [f"login {_ADDRESS} key"] * 2
        assert (root / "dispatch.sh").read_text(encoding="utf-8") == _DISPATCHER.read_text(encoding="utf-8")
        assert (root / "incoming").is_dir()
        assert remote.key_lines()[1] == _dispatcher_line(remote, "10.8.0.7")
        assert "is set up" in err

    def test_an_unchanged_entry_keeps_its_comment(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        config = remote.config().replace("written by setup:remote-target on", "written by hand on")
        (remote.dev_dir / "ssh_config").write_text(config, encoding="utf-8")

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert remote.config() == config

    def test_a_second_target_gets_an_entry_beside_the_first(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.targets[_NEW_ADDRESS] = Target(
            home=str(remote.target_home), client_address=_SEEN_FROM, path=remote.tools_path
        )
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        code, _, err = remote.setup(capsys, "other", f"deck@{_NEW_ADDRESS}")

        assert code == 0, err
        assert remote.config().count("Host deck\n") == 1
        assert remote.config().count("Host other\n") == 1


class TestNoPasswordLogin:
    def test_it_is_set_up_through_an_existing_key_of_the_users(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = AGENT

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert remote.key_lines()[1] == _dispatcher_line(remote, _SEEN_FROM)
        assert not any(line.startswith("password asked") for line in log_lines(remote.world))

    def test_it_is_set_up_through_print_and_the_second_run(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.target().client_address = remote.world.route_source

        code, script, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        assert code == 0, err
        assert log_lines(remote.world) == []
        assert sorted(p.name for p in remote.dev_dir.iterdir()) == ["id_ed25519", "id_ed25519.pub"]
        on_target = remote.run_on_target(script)
        assert on_target.returncode == 0, on_target.stderr
        assert f"accepted from {remote.world.route_source} only" in on_target.stderr
        host_key = remote.host_keys / "ssh_host_ed25519_key.pub"
        fingerprint = subprocess.run(
            [sys.executable, _remote_target.__file__, "ssh-keygen", "-lf", str(host_key)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert fingerprint in on_target.stdout
        assert remote.key_lines()[1] == _dispatcher_line(remote, remote.world.route_source)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert log_lines(remote.world) == ["host key shown romm-tender-dev.deck", *[f"login {_ADDRESS} key"] * 2]
        assert "Host deck\n" in remote.config()
        assert (remote.dev_dir / "known_hosts").read_text(encoding="utf-8") == f"romm-tender-dev.deck {_HOST_KEY}\n"

    def test_the_printed_script_takes_another_address_as_its_argument(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script, "10.0.0.99")

        assert on_target.returncode == 0, on_target.stderr
        assert "accepted from 10.0.0.99 only" in on_target.stderr
        assert remote.key_lines()[1] == _dispatcher_line(remote, "10.0.0.99")

    def test_the_printed_script_run_twice_leaves_one_line(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        remote.run_on_target(script)
        on_target = remote.run_on_target(script, "10.0.0.99")

        assert on_target.returncode == 0, on_target.stderr
        assert remote.key_lines()[1:] == [_dispatcher_line(remote, "10.0.0.99")]
        assert len(remote.key_lines()) == 2

    def test_a_refused_key_after_print_names_the_printed_address_as_the_likely_cause(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#2845 D1: the target sees the build host at another address than its route names."""
        remote.target().own_access = NOTHING
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "The target refused the dev loop's key as well." in err
        assert (
            f"the likely cause is the address that script restricts the key to — by default {remote.world.route_source}"
            in err
        )

    def test_print_refuses_a_jump_host_and_writes_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxyjump": ["bastion"]}}

        code, script, err = remote.setup(capsys, "deck", "deck", "--print")

        assert code == 1
        assert script == ""
        assert "--print cannot set up a target reached through bastion" in err
        assert _snapshot(remote.build_home) == {}

    def test_print_keeps_an_existing_key(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        public_key = remote.public_key()

        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        assert remote.public_key() == public_key
        assert public_key in script


class TestTheFourFailures:
    """Each ends with its message, and leaves both machines as they were."""

    def _fails(self, remote: Remote, capsys: pytest.CaptureFixture[str], destination: str) -> str:
        build_before = _snapshot(remote.build_home)
        target_before = _snapshot(remote.target_home)

        code, out, err = remote.setup(capsys, "deck", destination)

        assert code == 1
        assert out == ""
        assert "Nothing was changed, here or on the target." in err
        assert _snapshot(remote.build_home) == build_before
        assert _snapshot(remote.target_home) == target_before
        return err

    def test_a_name_this_machine_cannot_resolve(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        err = self._fails(remote, capsys, "deck@steamdeck.invalid")

        assert "this machine cannot resolve steamdeck.invalid" in err
        assert "Give the target's address instead, or a name the network resolves." in err

    def test_no_ssh_server_answering(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.target().serving = False

        err = self._fails(remote, capsys, f"deck@{_ADDRESS}")

        assert f"no SSH server answers at {_ADDRESS} port 22" in err
        assert "that the target runs an SSH server" in err

    def test_no_login_accepted(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.target().own_access = NOTHING

        err = self._fails(remote, capsys, f"deck@{_ADDRESS}")

        assert f"deck@{_ADDRESS} accepted no login from this machine" in err
        assert "Run the setup with --print" in err
        assert "likely cause" not in err

    @pytest.mark.parametrize(
        "hop", [{"proxyjump": ["jumper@203.0.113.5:22"]}, {"proxycommand": ["nc %h %p"]}], ids=["jump", "proxy"]
    )
    def test_no_login_behind_a_hop_names_a_step_that_works_there(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], hop: dict[str, list[str]]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], **hop}}

        err = self._fails(remote, capsys, "deck")

        assert "deck accepted no login from this machine" in err
        assert "Run the setup with --print" not in err
        assert "a key of yours in its ~/.ssh/authorized_keys, or a password" in err

    def test_a_host_key_that_is_not_the_recorded_one(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        first, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        assert first == 0, err
        remote.target().host_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAnotherMachine"

        err = self._fails(remote, capsys, f"deck@{_ADDRESS}")

        assert f"the host key at {_ADDRESS} is not the one recorded for deck" in err
        assert "No connection was made." in err

    def test_a_failure_after_an_earlier_setup_leaves_its_files_as_they_were(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        remote.target().serving = False

        err = self._fails(remote, capsys, f"deck@{_ADDRESS}")

        assert "no SSH server answers" in err

    def test_a_host_key_declined_at_first_contact(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.accept_host_key = False

        err = self._fails(remote, capsys, f"deck@{_ADDRESS}")

        assert f"the host key at {_ADDRESS} was not accepted" in err


class TestAFailureOnTheTarget:
    """The login held and the install started: no pre-login message, and this machine unchanged."""

    def test_a_connection_dropped_after_the_install_started_says_it_stopped_part_way(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.drop_after_first_line = True
        build_before = _snapshot(remote.build_home)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "stopped part-way on deck" in err
        assert "Connection reset by peer" in err
        assert "This machine is unchanged; run the setup again to finish it." in err
        assert "no SSH server answers" not in err
        assert "Nothing was changed, here or on the target" not in err
        assert _snapshot(remote.build_home) == build_before

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode")
    def test_a_step_failing_after_the_dispatcher_is_built_moves_nothing_into_place(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        remote.authorized_keys.chmod(0o000)
        build_before = _snapshot(remote.build_home)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        remote.authorized_keys.chmod(0o600)
        assert code == 1
        assert "stopped part-way on deck" in err
        assert remote.authorized_keys.read_text(encoding="utf-8") == "ssh-ed25519 AAAAothersKey someone@laptop\n"
        root = remote.target_home / ".local/lib/romm-tender-dev"
        assert sorted(path.name for path in root.iterdir()) == []
        assert sorted(path.name for path in remote.authorized_keys.parent.iterdir()) == ["authorized_keys"]
        assert _snapshot(remote.build_home) == build_before


class TestTheAddressCheck:
    """The printed script's address is the one guard against key options smuggled into ``from="…"``."""

    @pytest.mark.parametrize(
        "address", ['1.2.3.4",no-restrict', "1.2.3.4 5.6.7.8", "1.2.3.4/24", "*", "deck.local", "1.2.3.4,5.6.7.8"]
    )
    def test_an_address_that_is_not_one_is_refused_and_writes_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], address: str
    ) -> None:
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script, address)

        assert on_target.returncode == 1
        assert "no address to restrict the key to" in on_target.stderr
        assert _snapshot(remote.target_home) == {}

    def test_a_login_without_ssh_client_is_refused_and_writes_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        script = script.replace("default_from=192.168.1.10", "default_from=''")

        on_target = remote.run_on_target(script)

        assert on_target.returncode == 1
        assert 'no address to restrict the key to (got "")' in on_target.stderr
        assert _snapshot(remote.target_home) == {}

    def test_an_ipv6_address_is_taken(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script, "fd00::6")

        assert on_target.returncode == 0, on_target.stderr
        assert remote.key_lines()[1] == _dispatcher_line(remote, "fd00::6")


class TestTheKeyFirst:
    """#2845 D2: with the dev loop's key here, the setup logs in with that key alone first."""

    def test_a_rerun_logs_in_with_the_key_alone_and_asks_for_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        calls = len(ssh_calls(remote.world))

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        login, _ = ssh_calls(remote.world)[calls:]
        assert login["_offered"] == [str(remote.dev_dir / "id_ed25519")]
        assert login["identitiesonly"] == ["yes"]
        assert login["preferredauthentications"] == ["publickey"]
        assert login["batchmode"] == ["yes"]

    def test_a_target_whose_max_auth_tries_ends_a_login_of_the_users_own_keys_is_set_up_with_the_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.target().refused_agent_keys = 6
        remote.target().client_address = remote.world.route_source
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert log_lines(remote.world)[-1] == f"login {_ADDRESS} key"
        assert "Host deck\n" in remote.config()

    def test_the_first_key_login_after_print_may_ask_about_the_host_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.target().client_address = remote.world.route_source
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        login, _ = ssh_calls(remote.world)
        assert login["batchmode"] == ["no"]
        assert login["identitiesonly"] == ["yes"]
        assert login["preferredauthentications"] == ["publickey"]

    def test_a_key_refused_after_print_asks_for_no_password_before_the_users_own_login(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        key_login, own_login, _ = ssh_calls(remote.world)
        assert key_login["preferredauthentications"] == ["publickey"]
        assert "preferredauthentications" not in own_login
        assert log_lines(remote.world) == [
            "host key shown romm-tender-dev.deck",
            f"password asked {_ADDRESS}",
            f"login {_ADDRESS} own",
            f"login {_ADDRESS} key",
        ]

    def test_a_refused_key_hands_over_to_the_users_own_login_which_repairs_the_target(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        remote.target().client_address = "10.8.0.7"
        calls = len(ssh_calls(remote.world))

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        key_login, own_login, _ = ssh_calls(remote.world)[calls:]
        assert key_login["identitiesonly"] == ["yes"]
        assert "identityfile" not in own_login
        assert own_login["batchmode"] == ["no"]
        assert remote.key_lines()[1] == _dispatcher_line(remote, "10.8.0.7")

    def test_too_many_authentication_failures_without_the_key_blames_no_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().refused_agent_keys = 6

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert f"deck@{_ADDRESS} accepted no login from this machine" in err
        assert "dev loop's key" not in err

    def test_every_login_opens_a_connection_of_its_own(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        remote.target().client_address = "10.8.0.7"
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        calls = ssh_calls(remote.world)

        assert len(calls) == 5
        for login in calls:
            assert login["controlmaster"] == ["no"]
            assert login["controlpath"] == ["none"]
        installs = [calls[0], calls[2], calls[3]]
        for login in installs:
            assert login["stricthostkeychecking"] == ["ask"]

    def test_a_key_without_its_public_half_is_refused_before_any_login(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        (remote.dev_dir / "id_ed25519.pub").unlink()

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "has no id_ed25519.pub beside it" in err
        assert ssh_calls(remote.world) == []


class TestTheAuthorizedKeysRebuild:
    def test_an_unrestricted_line_beside_the_exact_restricted_one_goes(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        blob = remote.public_key().split()[1]
        with open(remote.authorized_keys, "a", encoding="utf-8") as keys:
            keys.write(f"ssh-ed25519 {blob} added-by-hand\n")

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert not any("added-by-hand" in line for line in remote.key_lines())
        assert len(remote.key_lines()) == 2

    def test_a_new_file_that_did_not_come_out_whole_leaves_authorized_keys_as_it_was(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        remote.authorized_keys.parent.mkdir()
        before = "ssh-ed25519 AAAAothersKey someone@laptop\nssh-ed25519 AAAAanotherOne other@desktop\n"
        remote.authorized_keys.write_text(before, encoding="utf-8")
        _short_awk(tmp_path / "tools" / "fakes" / "awk")

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "the setup stopped on deck: the new" in err
        assert "did not come out whole, so it was left as it was" in err
        assert "run the setup again" not in err
        assert remote.authorized_keys.read_text(encoding="utf-8") == before
        assert sorted(path.name for path in remote.authorized_keys.parent.iterdir()) == ["authorized_keys"]
        assert not (remote.target_home / ".local/lib/romm-tender-dev/dispatch.sh").exists()


def _short_awk(path: Path) -> None:
    """An ``awk`` whose output loses its last line whenever it has more than one — a short write."""
    real = shutil.which("awk")
    assert real is not None
    path.write_text(
        f'#!/bin/sh\nout=$("{real}" "$@"; echo .)\nout="${{out%.}}"\n'
        'if [ "$(printf %s "$out" | "' + real + "\" 'END {{ print NR }}')\" -gt 1 ]; then\n"
        '    printf %s "$out" | "' + real + "\" 'NR > 1 {{ print previous }} {{ previous = $0 }}'\n"
        "else\n"
        '    printf %s "$out"\n'
        "fi\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


class TestHops:
    @pytest.mark.parametrize("hop", ["bastion", "jumper@bastion:2222"])
    def test_a_hop_this_machine_cannot_resolve_is_named_as_the_hop(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], hop: str
    ) -> None:
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxyjump": [hop]}}
        remote.world.hop_failure = "unresolved"

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 1
        assert f"this machine cannot resolve bastion, a hop on the way to deck ({hop})" in err
        assert f"cannot resolve {_ADDRESS}" not in err

    def test_a_hop_whose_host_key_changed_is_named_as_the_hop_and_its_record_left_alone(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxyjump": ["bastion"]}}
        remote.world.hop_failure = "host-key"

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 1
        assert "a hop on the way to deck (bastion) has a host key that is not the one" in err
        assert str(remote.build_home / ".ssh" / "known_hosts") in err
        assert "ssh-keygen -R" not in err


class TestMissingTools:
    @pytest.mark.parametrize(("tool", "argv"), [("ssh", ()), ("ssh-keygen", ()), ("ip", ("--print",))])
    def test_a_tool_this_machine_lacks_is_named(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tmp_path: Path, tool: str, argv: tuple[str, ...]
    ) -> None:
        (tmp_path / "tools" / "fakes" / tool).unlink()

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", *argv)

        assert code == 1
        assert f"the setup needs {tool}, and this machine has none on its PATH" in err
        assert not remote.dev_dir.exists()


class TestTheBuildHostsSide:
    def test_a_write_here_that_fails_after_the_target_is_set_up_says_so(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (remote.dev_dir / "ssh_config").mkdir(parents=True)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "deck is set up, but this machine's side could not be written" in err
        assert "Run the setup again to finish it." in err
        assert "Traceback" not in err

    def test_a_target_whose_name_another_target_starts_with_still_gets_the_first_contact_notice(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.targets[_NEW_ADDRESS] = Target(
            home=str(remote.target_home), client_address=_SEEN_FROM, path=remote.tools_path
        )
        remote.setup(capsys, "deck2", f"deck@{_NEW_ADDRESS}")

        _, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert "first contact with deck:" in err

    def test_a_login_that_runs_no_install_leaves_this_machine_as_it_was(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        dispatcher = remote.target_home / ".local/lib/romm-tender-dev/dispatch.sh"
        dispatcher.write_text(
            "#!/bin/sh\necho 'tender-dev: refused: an older dispatcher' >&2\nexit 3\n", encoding="utf-8"
        )
        remote.target().own_access = NOTHING
        build_before = _snapshot(remote.build_home)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert f"deck@{_ADDRESS} accepted no login from this machine" in err
        assert "deck accepted the dev loop's key, but its forced command failed (ssh ended with 3)" in err
        assert "an older dispatcher" in err
        assert _snapshot(remote.build_home) == build_before


class TestPrintWritesNothingWhenItCannotPrint:
    def test_a_name_this_machine_cannot_resolve(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unresolvable(*_: object, **__: object) -> list[object]:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", unresolvable)

        code, script, err = remote.setup(capsys, "deck", "deck@steamdeck.lan", "--print")

        assert (code, script) == (1, "")
        assert "this machine cannot resolve steamdeck.lan" in err
        assert _snapshot(remote.build_home) == {}

    def test_a_route_that_names_no_source_address(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.route_source = ""

        code, script, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        assert (code, script) == (1, "")
        assert f"`ip route get {_ADDRESS}` named no address" in err
        assert _snapshot(remote.build_home) == {}

    @pytest.mark.parametrize("argv", [("--print",), ()], ids=["print", "login"])
    def test_a_key_ssh_keygen_could_not_make(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
    ) -> None:
        remote.world.keygen_fails = True

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", *argv)

        assert code == 1
        assert "ssh-keygen could not create a key" in err
        assert _snapshot(remote.build_home) == {}


# What OpenSSH writes on stderr, verbatim, for each failure before a login.
_STDERR = {
    "timed out": "ssh: connect to host 192.168.1.20 port 22: Connection timed out\r\n",
    "no route": "ssh: connect to host 192.168.1.20 port 22: No route to host\r\n",
    "network unreachable": "ssh: connect to host 192.168.1.20 port 22: Network is unreachable\r\n",
    "refused": "ssh: connect to host 192.168.1.20 port 22: Connection refused\r\n",
    "kex reset": "kex_exchange_identification: read: Connection reset by peer\r\n"
    "Connection reset by 192.168.1.20 port 22\r\n",
    "unresolved": "ssh: Could not resolve hostname steamdeck.lan: Name or service not known\r\n",
    "too many": "Received disconnect from 192.168.1.20 port 22:2: Too many authentication failures\r\n"
    "Disconnected from 192.168.1.20 port 22\r\n",
    "denied": "deck@192.168.1.20: Permission denied (publickey,password).\r\n",
    "denied then closed": "deck@192.168.1.20: Permission denied (publickey).\r\n"
    "Connection closed by 192.168.1.20 port 22\r\n",
}


class TestReadingWhatSshSaid:
    _DESTINATION = setup_remote_target.Destination(
        text="deck@192.168.1.20", user="deck", hostname="192.168.1.20", port="22", proxy_jump=None, proxy_command=None
    )

    def _classify(self, stderr: str) -> str | None:
        return setup_remote_target.classify(
            stderr,
            name="deck",
            destination=self._DESTINATION,
            known_hosts=Path("/build/known_hosts"),
            trial_known_hosts=Path("/tmp/trial/known_hosts"),
        )

    def _kind(self, stderr: str) -> str | None:
        failure = setup_remote_target.ssh_failure(stderr, name="deck", destination=self._DESTINATION)
        return None if failure is None else failure.kind

    @pytest.mark.parametrize("case", ["timed out", "no route", "network unreachable", "refused", "kex reset"])
    def test_no_server_answering(self, case: str) -> None:
        assert (self._classify(_STDERR[case]) or "").startswith("tender-dev: no SSH server answers at 192.168.1.20")
        assert self._kind(_STDERR[case]) == "no server"

    _BEHIND_A_HOP = setup_remote_target.Destination(
        text="deck", user="deck", hostname="192.168.1.20", port="22", proxy_jump="bastion", proxy_command=None
    )

    def test_the_targets_refusal_behind_a_hop_it_got_past_is_the_targets(self) -> None:
        refused = subprocess.CompletedProcess(["ssh"], 255, "", _STDERR["denied"])

        assert (
            setup_remote_target.classify(
                _STDERR["denied"],
                name="deck",
                destination=self._BEHIND_A_HOP,
                known_hosts=Path("/build/known_hosts"),
                trial_known_hosts=Path("/tmp/trial/known_hosts"),
            )
            is None
        )
        assert setup_remote_target.hand_over(refused, name="deck", destination=self._BEHIND_A_HOP) == "refused"

    def test_no_server_named_on_no_host_behind_a_hop_is_the_targets_or_the_hops(self) -> None:
        message = setup_remote_target.classify(
            _STDERR["kex reset"],
            name="deck",
            destination=self._BEHIND_A_HOP,
            known_hosts=Path("/build/known_hosts"),
            trial_known_hosts=Path("/tmp/trial/known_hosts"),
        )

        assert (message or "").startswith(
            "tender-dev: no SSH server answers at 192.168.1.20 port 22, or at the hop bastion"
        )

    def test_a_name_that_does_not_resolve(self) -> None:
        assert (self._classify(_STDERR["unresolved"]) or "").startswith(
            "tender-dev: this machine cannot resolve 192.168.1.20"
        )

    def test_a_changed_host_key_with_no_hop_on_the_way_is_the_targets_whatever_file_ssh_names(self) -> None:
        stderr = (
            "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
            "Offending ED25519 key in /home/u/.ssh/known_hosts:3\n"
            "Host key for 192.168.1.20 has changed and you have requested strict checking.\n"
            "Host key verification failed."
        )

        failure = setup_remote_target.ssh_failure(stderr, name="deck", destination=self._DESTINATION)

        assert failure is not None
        assert (failure.kind, failure.where) == ("host key changed", "target")

    @pytest.mark.parametrize(
        "stderr",
        [
            "client_loop: send disconnect: Connection reset by peer",
            "Connection to 192.168.1.20 closed by remote host.",
            "Timeout, server 192.168.1.20 not responding.",
            "packet_write_wait: Connection to 192.168.1.20 port 22: Broken pipe",
            "Read from remote host 192.168.1.20: Connection reset by peer",
        ],
        ids=["reset", "closed", "timeout", "broken pipe", "read from remote host"],
    )
    def test_a_connection_lost_after_the_login_is_no_failure_before_it(self, stderr: str) -> None:
        assert setup_remote_target.ssh_failure(stderr, name="deck", destination=self._DESTINATION) is None

    def test_too_many_authentication_failures_hands_nothing_over(self) -> None:
        """The key was not refused: the login ended before it was tried, and the user's own login is not tried."""
        result = subprocess.CompletedProcess(
            ["ssh"],
            255,
            "",
            "Received disconnect from 192.168.1.20 port 22:2: Too many authentication failures\n"
            "Disconnected from 192.168.1.20 port 22",
        )

        assert setup_remote_target.hand_over(result, name="deck", destination=self._DESTINATION) is None

    @pytest.mark.parametrize(
        ("kind", "field"),
        [("no server", {"key_refused": True}), ("no login", {"offending": "/x"}), ("no server", {"unresolved": "x"})],
    )
    def test_a_failure_carries_only_its_own_kinds_fields(self, kind: Any, field: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="carries"):
            setup_remote_target.SshFailure(kind, "target", **field)

    @pytest.mark.parametrize("case", ["too many", "denied", "denied then closed"])
    def test_no_login_accepted(self, case: str) -> None:
        assert self._classify(_STDERR[case]) is None
        assert self._kind(_STDERR[case]) == "no login"


_DECK_ENTRY = ["Host deck", "    HostName 192.168.1.20"]


class TestTheConfigUpdate:
    def test_duplicate_entries_of_one_name_become_one(self) -> None:
        current = "Host deck\n    HostName old\n\nHost other\n    HostName x\n\nHost deck\n    HostName older\n"

        updated = setup_remote_target.updated_config(current, "deck", _DECK_ENTRY, "# deck: new")

        assert updated.count("Host deck\n") == 1
        assert "Host other\n    HostName x" in updated
        assert "old" not in updated

    def test_only_the_headers_own_lines_are_dropped(self) -> None:
        header_first = setup_remote_target._CONFIG_HEADER.splitlines()[0]
        current = f"{header_first}\n# a note of the user's\nHost other\n    HostName x\n"

        updated = setup_remote_target.updated_config(current, "deck", _DECK_ENTRY, "# deck: new")

        assert "# a note of the user's\nHost other\n    HostName x" in updated
        assert updated.count(header_first) == 1


class TestAKeyWhoseForcedCommandFails:
    """The target takes the key, but what the key's line runs does not run the install."""

    @pytest.mark.parametrize("dispatcher", [None, "#!/bin/sh\necho 'tender-dev: refused: sh -s' >&2\nexit 3\n"])
    def test_the_users_own_login_repairs_it(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], dispatcher: str | None
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        installed = remote.target_home / ".local/lib/romm-tender-dev/dispatch.sh"
        if dispatcher is None:
            installed.unlink()
        else:
            installed.write_text(dispatcher, encoding="utf-8")
        logged = len(log_lines(remote.world))

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert log_lines(remote.world)[logged:] == [
            f"login {_ADDRESS} key",
            f"password asked {_ADDRESS}",
            f"login {_ADDRESS} own",
            f"login {_ADDRESS} key",
        ]
        assert installed.read_text(encoding="utf-8") == _DISPATCHER.read_text(encoding="utf-8")

    def test_without_a_login_of_the_users_own_the_message_names_the_forced_command(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        (remote.target_home / ".local/lib/romm-tender-dev/dispatch.sh").unlink()
        remote.target().own_access = NOTHING

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "deck accepted the dev loop's key, but its forced command failed (ssh ended with 127)" in err
        assert "dispatch.sh: not found" in err
        assert "refused the dev loop's key" not in err
        forced = err.index("its forced command failed")
        assert forced < err.index(f"deck@{_ADDRESS} accepted no login") < err.index("Nothing was changed")


class TestAForcedCommandFailureBeforeAnOwnLoginThatStopped:
    """The forced command's failure is told first, whatever the user's own login then did."""

    def test_an_own_login_whose_install_refused_to_go_on(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        with open(remote.authorized_keys, "a", encoding="utf-8") as keys:
            keys.write("ssh-ed25519 AAAAothersKey someone@laptop\nssh-ed25519 AAAAanotherOne other@desktop\n")
        (remote.target_home / ".local/lib/romm-tender-dev/dispatch.sh").unlink()
        _short_awk(tmp_path / "tools" / "fakes" / "awk")

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        forced = err.index("deck accepted the dev loop's key, but its forced command failed (ssh ended with 127)")
        assert forced < err.index("the setup stopped on deck: the new") < err.index("This machine is unchanged.")
        assert "Nothing was changed" not in err

    def test_an_own_login_that_stopped_part_way(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        (remote.target_home / ".local/lib/romm-tender-dev/dispatch.sh").unlink()
        remote.world.drop_after_first_line = True
        remote.world.drop_only_without_config = True

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        forced = err.index("deck accepted the dev loop's key, but its forced command failed (ssh ended with 127)")
        assert forced < err.index("the setup stopped part-way on deck") < err.index("run the setup again to finish it")
        assert "Nothing was changed" not in err


class TestTheKeysLoginReadsNoConfigOfTheUsers:
    def test_it_offers_the_dev_loops_key_and_no_identity_from_the_users_config(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        own_key = remote.build_home / ".ssh" / "id_user"
        own_key.parent.mkdir()
        own_key.write_text("private\n", encoding="utf-8")
        own_key.with_name("id_user.pub").write_text("ssh-ed25519 AAAAusersOwnKey me@laptop\n", encoding="utf-8")
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "identityfile": ["~/.ssh/id_user"]}}
        remote.setup(capsys, "deck", "deck")

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 0, err
        first, _, second, _ = ssh_calls(remote.world)
        assert first["_offered"] == ["~/.ssh/id_user"]
        assert "_config" not in first
        assert second["_offered"] == [str(remote.dev_dir / "id_ed25519")]
        assert "_config" in second


class TestTheTargetStops:
    """A refusal of the install script's own: no use running the setup again as it stands."""

    def test_an_address_it_cannot_restrict_the_key_to(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.target().client_address = "fe80::6%wlan0"

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "the setup stopped on deck: no address to restrict the key to" in err
        assert "run the setup again" not in err
        assert "This machine is unchanged." in err


class TestASymlinkedAuthorizedKeys:
    def test_it_is_written_through_and_stays_a_symlink(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        managed = remote.target_home / "dotfiles" / "authorized_keys"
        managed.parent.mkdir()
        managed.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.symlink_to(managed)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert remote.authorized_keys.is_symlink()
        lines = managed.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "ssh-ed25519 AAAAothersKey someone@laptop"
        assert lines[2] == _dispatcher_line(remote, _SEEN_FROM)
        assert sorted(path.name for path in managed.parent.iterdir()) == ["authorized_keys"]

    def test_a_relative_link_is_written_through_too(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        managed = remote.target_home / "dotfiles" / "authorized_keys"
        managed.parent.mkdir()
        managed.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.symlink_to(Path("..") / "dotfiles" / "authorized_keys")

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        assert remote.authorized_keys.is_symlink()
        assert managed.read_text(encoding="utf-8").splitlines()[2] == _dispatcher_line(remote, _SEEN_FROM)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a directory whatever its mode")
    def test_a_link_into_a_place_that_cannot_be_written_stops_with_what_to_do(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        managed = remote.target_home / "store" / "authorized_keys"
        managed.parent.mkdir()
        managed.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        managed.parent.chmod(0o555)
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.symlink_to(managed)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        managed.parent.chmod(0o755)
        assert code == 1
        assert f"the setup stopped on deck: {managed.parent} cannot be written" in err
        assert "add this line to it through whatever manages it" in err
        assert "run the setup again" not in err
        assert managed.read_text(encoding="utf-8") == "ssh-ed25519 AAAAothersKey someone@laptop\n"

    def test_a_link_that_cannot_be_resolved_stops_naming_the_link(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.symlink_to(remote.target_home / "gone" / "dotfiles" / "authorized_keys")

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert f"the setup stopped on deck: {remote.authorized_keys} is a link that cannot be resolved" in err
        assert "add this line through whatever manages it" in err
        assert remote.authorized_keys.is_symlink()


class TestTheCheckLogin:
    """#2845 D3: a setup that installed ends with a login through the dev loop's own config."""

    def test_a_setup_ends_with_the_check_through_the_dev_loops_own_config(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        install, check = ssh_calls(remote.world)
        assert "_config" not in install
        assert "_config" in check
        assert check["batchmode"] == ["yes"]
        assert check["_offered"] == [check["_offered"][0]]
        assert check["_offered"][0].endswith("/id_ed25519")
        assert log_lines(remote.world)[-1] == f"login {_ADDRESS} key"
        assert "deck is set up." in err

    def _behind_a_hop_only_the_users_config_passes(self, remote: Remote) -> None:
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxyjump": ["bastion"]}}
        remote.world.hop_failure = "denied"
        remote.world.hop_fails_only_with_config = True

    def _says_the_dev_loop_cannot_reach_it(self, code: int, err: str) -> None:
        assert code == 1
        assert "deck is set up, but the dev loop cannot reach it with its own config" in err
        assert "jumper@bastion: Permission denied" in err
        assert "carries none of your SSH config" in err
        assert "deck is set up. It accepts" not in err

    def test_a_first_setup_behind_a_hop_only_the_users_config_passes(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._behind_a_hop_only_the_users_config_passes(remote)

        code, _, err = remote.setup(capsys, "deck", "deck")

        self._says_the_dev_loop_cannot_reach_it(code, err)
        assert "Host deck\n" in remote.config()
        assert (remote.dev_dir / "id_ed25519").exists()

    def test_a_rerun_handed_over_at_that_hop_says_the_same(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._behind_a_hop_only_the_users_config_passes(remote)
        remote.setup(capsys, "deck", "deck")
        calls = len(ssh_calls(remote.world))

        code, _, err = remote.setup(capsys, "deck", "deck")

        self._says_the_dev_loop_cannot_reach_it(code, err)
        key_login, own_login, check = ssh_calls(remote.world)[calls:]
        assert "_config" in key_login
        assert "_config" not in own_login
        assert "_config" in check
        assert "refused the dev loop's key" not in err

    def test_a_hop_failure_other_than_a_refusal_in_the_keys_login_stands(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#2845 D2: only a hop's refusal hands over to the user's own login."""
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxyjump": ["bastion"]}}
        remote.setup(capsys, "deck", "deck")
        remote.world.hop_failure = "refused"
        remote.world.hop_fails_only_with_config = True
        calls = len(ssh_calls(remote.world))

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 1
        assert "no SSH server answers at the hop bastion" in err
        assert len(ssh_calls(remote.world)) == calls + 1

    def test_a_key_login_that_started_and_stopped_hands_nothing_over(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        remote.setup(capsys, "deck", f"deck@{_ADDRESS}")
        with open(remote.authorized_keys, "a", encoding="utf-8") as keys:
            keys.write("ssh-ed25519 AAAAothersKey someone@laptop\nssh-ed25519 AAAAanotherOne other@desktop\n")
        _short_awk(tmp_path / "tools" / "fakes" / "awk")
        calls = len(ssh_calls(remote.world))
        logged = len(log_lines(remote.world))

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "the setup stopped on deck: the new" in err
        assert "forced command failed" not in err
        assert len(ssh_calls(remote.world)) == calls + 1
        assert log_lines(remote.world)[logged:] == [f"login {_ADDRESS} key"]


class TestMoreHops:
    def _behind_bastion(self, remote: Remote) -> None:
        remote.world.configs = {"deck": {"user": ["deck"], "hostname": [_ADDRESS], "proxyjump": ["bastion"]}}

    def test_a_hop_that_refuses_the_login_is_named_and_blames_no_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._behind_bastion(remote)
        remote.setup(capsys, "deck", "deck")
        remote.world.hop_failure = "denied"

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 1
        assert "the hop bastion accepted no login from this machine" in err
        assert "dev loop's key" not in err
        assert "deck accepted no login" not in err

    def test_a_hop_with_no_server_answering_is_named(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        self._behind_bastion(remote)
        remote.world.hop_failure = "refused"

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 1
        assert "no SSH server answers at the hop bastion" in err
        assert f"answers at {_ADDRESS}" not in err

    def test_an_unaccepted_host_key_ssh_does_not_place_names_the_target_or_the_hop(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._behind_bastion(remote)
        remote.world.hop_failure = "host-key-unknown"

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 1
        assert f"the host key of {_ADDRESS} or of the hop bastion was not accepted" in err


class TestOtherFailures:
    def test_a_failure_no_pattern_knows_quotes_ssh(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.fail_with = "ssh: a failure nobody has seen before"

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "the setup did not start on deck (ssh ended with 255):\nssh: a failure nobody has seen before" in err

    def test_a_refused_key_whose_route_cannot_be_read_gets_the_shorter_message(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        remote.run_on_target(script)
        remote.world.route_source = ""

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert "The target refused the dev loop's key as well.\n  Nothing was changed" in err
        assert "likely cause" not in err
        assert "Traceback" not in err

    def test_an_ssh_that_cannot_read_its_config(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.ssh_g_fails = True

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert f"ssh could not read its configuration for deck@{_ADDRESS}:\n/home/u/.ssh/config line 3" in err
        assert _snapshot(remote.build_home) == {}

    def test_a_destination_that_resolves_to_no_host(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.configs = {"deck": {"user": ["deck"]}}

        code, _, err = remote.setup(capsys, "deck", "deck")

        assert code == 1
        assert "ssh resolved deck to no host or no user" in err

    def test_print_on_a_target_with_no_host_keys_says_so(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (remote.host_keys / "ssh_host_ed25519_key.pub").unlink()
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script)

        assert on_target.returncode == 0, on_target.stderr
        assert f"none found in {remote.host_keys} — is an SSH server set up here?" in on_target.stdout

    def test_the_printed_script_run_again_later_leaves_an_unchanged_line_as_it_was(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, first, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        remote.run_on_target(first)
        before = remote.authorized_keys.read_bytes()
        monkeypatch.setattr(setup_remote_target, "stamp", lambda: "2099-01-01 00:00 UTC")
        _, later, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(later)

        assert on_target.returncode == 0, on_target.stderr
        assert "already holds this line; left as it was" in on_target.stderr
        assert remote.authorized_keys.read_bytes() == before


class TestReadingAnEntryBack:
    """read_entries gives the dev tasks back what config_entry wrote, the one format both sides use."""

    @pytest.mark.parametrize(
        ("jump", "command"),
        [(None, None), ("jumper@203.0.113.5:22022", None), (None, "nc -X connect -x proxy.example:3128 %h %p")],
        ids=["direct", "jump", "proxy-command"],
    )
    def test_what_config_entry_writes_reads_back_as_the_destination(
        self, tmp_path: Path, jump: str | None, command: str | None
    ) -> None:
        destination = setup_remote_target.Destination(
            text="deck",
            user="deck",
            hostname=_ADDRESS,
            port="2222",
            proxy_jump=jump,
            proxy_command=command,
            jump=jump,
        )
        entry = setup_remote_target.config_entry(
            "deck", destination, key=tmp_path / "id_ed25519", known_hosts=tmp_path / "known_hosts"
        )
        text = setup_remote_target.updated_config("", "deck", entry, "# deck: a comment")

        assert setup_remote_target.read_entries(text).get("deck") == destination
        assert "tv" not in setup_remote_target.read_entries(text)


class TestArguments:
    @pytest.mark.parametrize("name", ["", "-deck", "my deck", "deck/1", "deck;x"])
    def test_a_name_ssh_could_not_carry_is_refused(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], name: str
    ) -> None:
        code, _, err = remote.setup(capsys, "--", name, f"deck@{_ADDRESS}")

        assert code == 1
        assert "a target's name is letters" in err
        assert log_lines(remote.world) == []

    def test_a_destination_that_reads_as_an_option_is_refused(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = remote.setup(capsys, "--", "deck", "-oProxyCommand=touch /tmp/x")

        assert code == 1
        assert "no destination ssh could reach" in err
        assert log_lines(remote.world) == []


class TestTheTaskAndTheGuide:
    _REPO = Path(__file__).resolve().parents[2]

    def test_the_task_hands_its_name_destination_and_print_flag_to_the_script(self) -> None:
        task = tomllib.loads((self._REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"]["setup:remote-target"]

        assert task["raw"] is True
        assert 'arg "<name>"' in task["usage"]
        assert 'arg "<destination>"' in task["usage"]
        assert 'flag "--print"' in task["usage"]
        assert (
            'python scripts/setup_remote_target.py ${usage_print:+--print} -- "$usage_name" "$usage_destination"'
            in task["run"]
        )

    def test_the_guide_covers_the_setup_where_the_scripts_point(self) -> None:
        guide = (self._REPO / "docs/contributing/frontend-dev-loop.md").read_text(encoding="utf-8")

        assert "\n## A remote target\n" in guide
        section = guide.split("\n## A remote target\n", 1)[1].split("\n## ", 1)[0]
        assert "mise run setup:remote-target <name> <destination>  " in section
        assert "mise run setup:remote-target <name> <destination> --print" in section
        for failure in ("cannot resolve", "no SSH server answering", "no login accepted", "not the recorded one"):
            assert failure in section
        headings = {line.lstrip("#").strip() for line in guide.splitlines() if line.startswith("#")}
        for script in ("setup_remote_target.py", "dev_dispatch.sh", "dev_target_install.sh"):
            text = " ".join((self._REPO / "scripts" / script).read_text(encoding="utf-8").replace("#", " ").split())
            pointers = re.findall(r'docs/contributing/frontend-dev-loop\.md, "([^"]+)"', text)
            assert pointers, script
            assert set(pointers) <= headings, script
