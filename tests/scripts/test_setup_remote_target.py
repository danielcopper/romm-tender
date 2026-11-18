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
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import _remote_target
import pytest
from _remote_target import AGENT, NOTHING, PASSWORD, WORLD_VARIABLE, Target, World, fake_path, log_lines

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
        targets={_ADDRESS: Target(home=str(target_home), client_address=_SEEN_FROM, host_key=_HOST_KEY)},
        log=str(tmp_path / "log"),
    )
    path = fake_path(tools)
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
        ]

    def test_the_line_restricts_the_key_to_the_address_the_target_sees(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """D5: the address ``$SSH_CLIENT`` shows, not the one this machine's route names."""
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
            "ssh-ed25519 AAAAanotherOne other@desktop\n",
            encoding="utf-8",
        )

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 0, err
        lines = remote.key_lines()
        assert lines[:2] == ["ssh-ed25519 AAAAothersKey someone@laptop", "ssh-ed25519 AAAAanotherOne other@desktop"]
        assert lines[3] == _dispatcher_line(remote, _SEEN_FROM)
        assert len(lines) == 4

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
        self, remote: Remote, capsys: pytest.CaptureFixture[str], own_access: str
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
        assert log_lines(remote.world)[logged:] == [f"login {_NEW_ADDRESS} key"]
        assert f"    HostName {_NEW_ADDRESS}\n" in remote.config()
        assert _ADDRESS not in remote.config()
        assert remote.config().count("Host deck\n") == 1
        assert len(remote.key_lines()) == 2

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
        remote.world.targets[_NEW_ADDRESS] = Target(home=str(remote.target_home), client_address=_SEEN_FROM)
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
        assert log_lines(remote.world) == ["host key shown romm-tender-dev.deck", f"login {_ADDRESS} key"]
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
        """D1: the target sees the build host at another address than its route names."""
        remote.target().own_access = NOTHING
        _, script, _ = remote.setup(capsys, "deck", f"deck@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "deck", f"deck@{_ADDRESS}")

        assert code == 1
        assert f"the likely cause is the address it restricts the key to: {remote.world.route_source}" in err

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
        for script in ("setup_remote_target.py", "dev_dispatch.sh"):
            assert '"A remote target"' in (self._REPO / "scripts" / script).read_text(encoding="utf-8")
