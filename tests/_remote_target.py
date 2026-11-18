"""Stand-ins for ``ssh``, ``ssh-keygen``, ``ip`` and ``rsync``, and the world they answer from.

No test of the remote dev loop runs one of these programs for real: a real
``ssh`` reads the developer's ``~/.ssh`` and may open a password dialog on their
desktop. :func:`fake_path` builds a ``PATH`` holding only these stand-ins and
the handful of system tools the scripts under test call, so a program missing
here fails as "not found" instead of reaching the real one.

The stand-ins read a :class:`World` from the JSON file ``FAKE_REMOTE_WORLD``
names. The ``ssh`` stand-in plays both ends of a login the way OpenSSH does,
for the parts the setup depends on:

- ``ssh -G`` answers from :attr:`World.configs`, or from ``user@host`` itself;
- a host it has no :class:`Target` for cannot be resolved, and one that is not
  ``serving`` refuses the connection;
- the host key is checked against ``UserKnownHostsFile`` under
  ``HostKeyAlias``: a different recorded key fails as a changed host key, an
  unrecorded one is "shown" (logged) and appended, unless the world declines it;
- the login tries the user's own access first, then each ``IdentityFile``
  against the target's real ``authorized_keys`` — honouring ``from=`` against
  the address the target sees and running ``command=`` with
  ``SSH_ORIGINAL_COMMAND`` — then a password, when the target takes one;
- a login runs its command with ``sh -c`` in the target's home, with
  ``SSH_CLIENT`` set.

Every login, password prompt and host key shown is appended to
:attr:`World.log`, which is how a test tells a run that needed a password from
one that did not.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

WORLD_VARIABLE = "FAKE_REMOTE_WORLD"
RSYNC_RECORD_VARIABLE = "FAKE_RSYNC_RECORD"

# What the scripts under test call besides the stand-ins.
SYSTEM_TOOLS = ("sh", "cat", "mkdir", "chmod", "mv", "rm", "grep", "awk")

# What a real login offers before any IdentityFile: an agent key the target
# accepts (``agent``), a password (``password``), or nothing it accepts.
AGENT = "agent"
PASSWORD = "password"
NOTHING = "nothing"


@dataclass
class Target:
    """One machine the ``ssh`` stand-in can reach."""

    home: str
    client_address: str
    """The address the target sees the build host at — what ``SSH_CLIENT`` shows."""
    own_access: str = PASSWORD
    host_key: str = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyOfTheTarget"
    serving: bool = True


@dataclass
class World:
    targets: dict[str, Target]
    log: str
    configs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    """``ssh -G`` answers by destination, for aliases and jump hosts."""
    accept_host_key: bool = True
    route_source: str = "192.168.1.10"
    """What ``ip route get`` names as this machine's source address."""

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> World:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["targets"] = {host: Target(**target) for host, target in raw["targets"].items()}
        return cls(**raw)


def fake_path(directory: Path) -> str:
    """A ``PATH`` of the stand-ins and :data:`SYSTEM_TOOLS`, built in *directory*."""
    fakes = directory / "fakes"
    system = directory / "system"
    fakes.mkdir()
    system.mkdir()
    for program in ("ssh", "ssh-keygen", "ip", "rsync"):
        wrapper = fakes / program
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{__file__}" {program} "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    for tool in SYSTEM_TOOLS:
        found = shutil.which(tool)
        assert found is not None, f"{tool} is needed to run the scripts under test"
        (system / tool).symlink_to(found)
    return f"{fakes}{os.pathsep}{system}"


def log_lines(world: World) -> list[str]:
    path = Path(world.log)
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _log(world: World, line: str) -> None:
    with open(world.log, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _world() -> World:
    return World.load(Path(os.environ[WORLD_VARIABLE]))


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def _resolve(world: World, destination: str) -> dict[str, list[str]]:
    if destination in world.configs:
        return world.configs[destination]
    user, _, host = destination.rpartition("@")
    return {
        "user": [user or "builder"],
        "hostname": [host],
        "port": ["22"],
        "identityfile": ["~/.ssh/id_rsa", "~/.ssh/id_ed25519"],
    }


def _ssh_g(world: World, destination: str) -> int:
    for key, values in _resolve(world, destination).items():
        for value in values:
            print(f"{key} {value}")
    return 0


def _parse_ssh(argv: list[str]) -> tuple[dict[str, list[str]], str, str]:
    options: dict[str, list[str]] = {}
    index = 0
    while index < len(argv):
        word = argv[index]
        if word == "--":
            index += 1
            break
        if word == "-o":
            key, _, value = argv[index + 1].partition("=")
            options.setdefault(key.lower(), []).append(_unquote(value))
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        break
    return options, argv[index], " ".join(argv[index + 1 :])


def _check_host_key(world: World, target: Target, options: dict[str, list[str]], host: str) -> bool:
    alias = options.get("hostkeyalias", [host])[0]
    known_hosts = Path(options.get("userknownhostsfile", [str(Path.home() / ".ssh" / "known_hosts")])[0])
    lines = known_hosts.read_text(encoding="utf-8").splitlines() if known_hosts.exists() else []
    for line in lines:
        names, _, key = line.partition(" ")
        if alias in names.split(","):
            if key.strip() == target.host_key:
                return True
            print(
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                f"Host key for {alias} has changed and you have requested strict checking.\n"
                "Host key verification failed.",
                file=sys.stderr,
            )
            return False
    _log(world, f"host key shown {alias}")
    if not world.accept_host_key:
        print("Host key verification failed.", file=sys.stderr)
        return False
    with open(known_hosts, "a", encoding="utf-8") as handle:
        handle.write(f"{alias} {target.host_key}\n")
    print(f"Warning: Permanently added '{alias}' (ED25519) to the list of known hosts.", file=sys.stderr)
    return True


def _forced_command(target: Target, identity_files: list[str]) -> str | None:
    """The ``command=`` of the first offered key the target accepts from where it sees us."""
    keys = Path(target.home) / ".ssh" / "authorized_keys"
    if not keys.exists():
        return None
    lines = keys.read_text(encoding="utf-8").splitlines()
    for identity in identity_files:
        public = Path(os.path.expanduser(identity) + ".pub")
        if not public.exists():
            continue
        blob = public.read_text(encoding="utf-8").split()[1]
        for line in lines:
            if line.startswith("#") or f" {blob}" not in line:
                continue
            allowed = re.search(r'from="([^"]*)"', line)
            if allowed is not None and target.client_address not in allowed.group(1).split(","):
                continue
            command = re.search(r'command="([^"]*)"', line)
            return command.group(1) if command is not None else ""
    return None


def _ssh(argv: list[str]) -> int:
    world = _world()
    if argv[:1] == ["-G"]:
        return _ssh_g(world, argv[1])
    options, destination, command = _parse_ssh(argv)
    resolved = _resolve(world, destination)
    host = resolved["hostname"][0]
    user = resolved["user"][0]
    target = world.targets.get(host)
    if target is None:
        print(f"ssh: Could not resolve hostname {host}: Name or service not known", file=sys.stderr)
        return 255
    if not target.serving:
        print(f"ssh: connect to host {host} port 22: Connection refused", file=sys.stderr)
        return 255
    if not _check_host_key(world, target, options, host):
        return 255

    env = {
        "HOME": target.home,
        "PATH": os.environ["PATH"],
        "SSH_CLIENT": f"{target.client_address} 50022 22",
    }
    if "TENDER_PYTHON" in os.environ:
        env["TENDER_PYTHON"] = os.environ["TENDER_PYTHON"]
    if target.own_access == AGENT:
        _log(world, f"login {host} own")
    else:
        forced = _forced_command(target, options.get("identityfile", []))
        if forced is not None:
            _log(world, f"login {host} key")
            env["SSH_ORIGINAL_COMMAND"] = command
            command = forced
        elif target.own_access == PASSWORD:
            _log(world, f"password asked {host}")
            _log(world, f"login {host} own")
        else:
            print(f"{user}@{host}: Permission denied (publickey).", file=sys.stderr)
            return 255
    return subprocess.run(["sh", "-c", command], cwd=target.home, env=env, check=False).returncode


def _ssh_keygen(argv: list[str]) -> int:
    if argv[:1] == ["-lf"]:
        content = Path(argv[1]).read_text(encoding="utf-8").split()
        print(f"256 SHA256:{hashlib.sha256(content[1].encode()).hexdigest()[:43]} {content[-1]} (ED25519)")
        return 0
    path = Path(argv[argv.index("-f") + 1])
    comment = argv[argv.index("-C") + 1]
    blob = "AAAAC3NzaC1lZDI1NTE5" + hashlib.sha256(str(path).encode() + os.urandom(8)).hexdigest()
    path.write_text("fake private key\n", encoding="utf-8")
    path.chmod(0o600)
    path.with_name(path.name + ".pub").write_text(f"ssh-ed25519 {blob} {comment}\n", encoding="utf-8")
    return 0


def _ip(argv: list[str]) -> int:
    world = _world()
    address = argv[2]
    print(f"{address} dev wlan0 src {world.route_source} uid 1000 \n    cache ")
    return 0


def _rsync(argv: list[str]) -> int:
    with open(os.environ[RSYNC_RECORD_VARIABLE], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\n")
    return 0


_PROGRAMS = {"ssh": _ssh, "ssh-keygen": _ssh_keygen, "ip": _ip, "rsync": _rsync}

if __name__ == "__main__":
    sys.exit(_PROGRAMS[sys.argv[1]](sys.argv[2:]))
