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
  a login reads the same, or with ``-F <file>`` that file alone, and an ``-o``
  option wins over either;
- a host it has no :class:`Target` for cannot be resolved, and one that is not
  ``serving`` refuses the connection;
- the host key is checked against ``UserKnownHostsFile`` under
  ``HostKeyAlias``: a different recorded key fails as a changed host key, an
  unrecorded one is "shown" (logged) and appended — unless the world declines
  it, or ``StrictHostKeyChecking`` is ``yes``, or ``BatchMode`` is ``yes``, which
  leaves ssh no way to ask;
- authentication runs in OpenSSH's order: the user's agent keys (none with
  ``IdentitiesOnly=yes``), then each ``IdentityFile`` against the target's real
  ``authorized_keys`` — honouring ``from=`` against the address the target sees
  and running ``command=`` with ``SSH_ORIGINAL_COMMAND`` — then a password,
  unless ``BatchMode=yes`` or ``PreferredAuthentications`` leaves it out; every
  refused key counts towards the target's ``MaxAuthTries``;
- a login runs its command with ``sh -c`` in the target's home, with
  ``SSH_CLIENT`` set;
- a destination behind a ProxyJump fails on its hop when the world says so,
  with what OpenSSH prints for that failure.

Every login, password prompt and host key shown is appended to
:attr:`World.log`, which is how a test tells a run that needed a password from
one that did not; every login ``ssh`` is asked for leaves its options and the
identity files it offered in :func:`ssh_calls`.
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
from typing import Final, Literal, get_args

WORLD_VARIABLE = "FAKE_REMOTE_WORLD"
RSYNC_RECORD_VARIABLE = "FAKE_RSYNC_RECORD"

# What the scripts under test call besides the stand-ins.
SYSTEM_TOOLS = ("sh", "cat", "mkdir", "chmod", "mv", "rm", "grep", "awk", "tail", "readlink")

# What the user's own access to a target is: an agent key it accepts, a
# password, or nothing it accepts.
OwnAccess = Literal["agent", "password", "nothing"]
AGENT: Final = "agent"
PASSWORD: Final = "password"
NOTHING: Final = "nothing"

# How a ProxyJump hop fails: a name this machine cannot resolve, a changed host
# key, an unrecorded one ssh could not ask about, a refused login, or no server.
HopFailure = Literal["unresolved", "host-key", "host-key-unknown", "denied", "refused"]


@dataclass
class Target:
    """One machine the ``ssh`` stand-in can reach."""

    home: str
    client_address: str
    """The address the target sees the build host at — what ``SSH_CLIENT`` shows."""
    own_access: OwnAccess = PASSWORD
    host_key: str = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyOfTheTarget"
    serving: bool = True
    refused_agent_keys: int = 0
    """Keys in the user's agent this target does not accept, offered before anything else."""
    max_auth_tries: int = 6
    """sshd's MaxAuthTries: the refused keys after which it ends the login."""


@dataclass
class World:
    targets: dict[str, Target]
    log: str
    configs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    """``ssh -G`` answers by destination, for aliases and jump hosts."""
    accept_host_key: bool = True
    route_source: str = "192.168.1.10"
    """What ``ip route get`` names as this machine's source address."""
    drop_after_first_line: bool = False
    """Drop the connection once the login's command has written its first line to stdout."""
    drop_only_without_config: bool = False
    """Drop it only for a login not given ``-F`` — the user's own."""
    hop_failure: HopFailure | None = None
    """How a ProxyJump hop fails, if it does."""
    hop_fails_only_with_config: bool = False
    """The hop fails only for a login given ``-F`` — one the user's own config, not read then, would let past."""
    keygen_fails: bool = False
    ssh_g_fails: bool = False
    fail_with: str | None = None
    """What ssh writes on stderr before it exits 255 at every login, for a failure no pattern knows."""

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> World:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["targets"] = {host: Target(**target) for host, target in raw["targets"].items()}
        world = cls(**raw)
        for target in world.targets.values():
            if target.own_access not in get_args(OwnAccess):
                raise AssertionError(f"no such own access: {target.own_access!r}")
        if world.hop_failure is not None and world.hop_failure not in get_args(HopFailure):
            raise AssertionError(f"no such hop failure: {world.hop_failure!r}")
        return world


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


def ssh_calls(world: World) -> list[dict[str, list[str]]]:
    """Every login ``ssh`` was asked for, in order: its ``-o`` options, ``-F`` as ``_config``
    and the identity files it offered as ``_offered``."""
    path = Path(world.log + ".calls")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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


def _ssh_g(world: World, argv: list[str]) -> int:
    """``ssh -G [-p port] destination``: a port given here wins over the config's, as in ssh."""
    if world.ssh_g_fails:
        print("/home/u/.ssh/config line 3: Bad configuration option: hostnme", file=sys.stderr)
        return 255
    port = argv[argv.index("-p") + 1] if "-p" in argv else None
    user, _, host = argv[-1].rpartition("@")
    answer = dict(_resolve(world, host))
    if user:
        answer["user"] = [user]
    if port is not None:
        answer["port"] = [port]
    for key, values in answer.items():
        for value in values:
            print(f"{key} {value}")
    return 0


def _parse_ssh(argv: list[str]) -> tuple[dict[str, list[str]], str, str]:
    """The ``-o`` options (and ``-F`` as ``_config``), the destination, and the command."""
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
        if word == "-F":
            options["_config"] = [argv[index + 1]]
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        break
    return options, argv[index], " ".join(argv[index + 1 :])


def _config_file(path: str, destination: str) -> dict[str, list[str]]:
    """The settings ``-F <path>`` gives *destination*: its ``Host`` block, keys lowercased."""
    settings: dict[str, list[str]] = {}
    matching = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        words = line.split(maxsplit=1)
        if not words or words[0].startswith("#"):
            continue
        key = words[0].lower()
        if key == "host":
            matching = destination in words[1].split()
        elif matching:
            settings.setdefault(key, []).append(_unquote(words[1].strip()))
    return settings


def _setting(options: dict[str, list[str]], config: dict[str, list[str]], name: str, default: str) -> str:
    """An ``-o`` option, else the config's value, else ssh's default: the first value obtained wins."""
    for source in (options, config):
        if source.get(name):
            return source[name][0]
    return default


def _check_host_key(
    world: World, target: Target, options: dict[str, list[str]], resolved: dict[str, list[str]], host: str
) -> bool:
    alias = _setting(options, resolved, "hostkeyalias", host)
    strict = _setting(options, resolved, "stricthostkeychecking", "ask")
    known_hosts = Path(os.path.expanduser(_setting(options, resolved, "userknownhostsfile", "~/.ssh/known_hosts")))
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
                f"Offending ED25519 key in {known_hosts}:{lines.index(line) + 1}\n"
                f"Host key for {alias} has changed and you have requested strict checking.\n"
                "Host key verification failed.",
                file=sys.stderr,
            )
            return False
    if strict == "yes" or _setting(options, resolved, "batchmode", "no") == "yes":
        print(
            f"No ED25519 host key is known for {alias} and you have requested strict checking.\n"
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
    """The ``command=`` of the first offered key the target accepts from where it sees us.

    ``own`` for a key whose line forces no command: that login runs what it asked for.
    """
    keys = Path(target.home) / ".ssh" / "authorized_keys"
    try:
        lines = keys.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
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
            return command.group(1) if command is not None else "own"
    return None


def _identity_files(options: dict[str, list[str]], config: dict[str, list[str]]) -> list[str]:
    """The identity files a login offers: ``-o`` ones and the config's, those that exist."""
    files = [*options.get("identityfile", []), *config.get("identityfile", [])]
    present = [path for path in files if Path(os.path.expanduser(path) + ".pub").exists()]
    return list(dict.fromkeys(present))


def _authenticate(
    world: World, target: Target, options: dict[str, list[str]], config: dict[str, list[str]], host: str
) -> str | None:
    """``own`` for a login of the user's own, the forced command for one with a key, ``None`` or ``too many``."""
    refused = 0
    if _setting(options, config, "identitiesonly", "no") != "yes":
        refused += target.refused_agent_keys
        if refused >= target.max_auth_tries:
            return "too many"
        if target.own_access == AGENT:
            _log(world, f"login {host} own")
            return "own"
    for identity in _identity_files(options, config):
        forced = _forced_command(target, [identity])
        if forced is not None:
            _log(world, f"login {host} key")
            return forced
        refused += 1
        if refused >= target.max_auth_tries:
            return "too many"
    preferred = _setting(options, config, "preferredauthentications", "publickey,password")
    password_allowed = _setting(options, config, "batchmode", "no") != "yes" and "password" in preferred
    if target.own_access == PASSWORD and password_allowed:
        _log(world, f"password asked {host}")
        _log(world, f"login {host} own")
        return "own"
    return None


def _hop_host(proxy_jump: str) -> str:
    """The first hop's host, out of ``[user@]host[:port]``."""
    first = proxy_jump.split(",")[0].rpartition("@")[2]
    return first.rpartition(":")[0] if first.count(":") == 1 else first


def _hop_fails(failure: HopFailure, hop: str) -> int:
    closed = "kex_exchange_identification: Connection closed by remote host\nConnection closed by UNKNOWN port 65535"
    if failure == "unresolved":
        print(f"ssh: Could not resolve hostname {hop}: Name or service not known\n{closed}", file=sys.stderr)
        return 255
    if failure == "host-key-unknown":
        print(f"Host key verification failed.\n{closed}", file=sys.stderr)
        return 255
    if failure == "denied":
        print(f"jumper@{hop}: Permission denied (publickey).\n{closed}", file=sys.stderr)
        return 255
    if failure == "refused":
        print(f"ssh: connect to host {hop} port 22: Connection refused\n{closed}", file=sys.stderr)
        return 255
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    print(
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
        "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
        f"Offending ED25519 key in {known_hosts}:3\n"
        f"Host key for {hop} has changed and you have requested strict checking.\n"
        "Host key verification failed.\n"
        "kex_exchange_identification: Connection closed by remote host",
        file=sys.stderr,
    )
    return 255


def _ssh(argv: list[str]) -> int:
    world = _world()
    if argv[:1] == ["-G"]:
        return _ssh_g(world, argv[1:])
    options, destination, command = _parse_ssh(argv)
    if "_config" in options:
        resolved = _config_file(options["_config"][0], destination)
    else:
        resolved = _resolve(world, destination)
    with open(world.log + ".calls", "a", encoding="utf-8") as calls:
        calls.write(json.dumps({**options, "_offered": _identity_files(options, resolved)}) + "\n")
    hop_fails = world.hop_failure is not None and (not world.hop_fails_only_with_config or "_config" in options)
    if "proxyjump" in resolved and world.hop_failure is not None and hop_fails:
        return _hop_fails(world.hop_failure, _hop_host(resolved["proxyjump"][0]))
    if world.fail_with is not None:
        print(world.fail_with, file=sys.stderr)
        return 255
    host = resolved["hostname"][0]
    user = _setting(options, resolved, "user", "builder")
    target = world.targets.get(host)
    if target is None:
        print(f"ssh: Could not resolve hostname {host}: Name or service not known", file=sys.stderr)
        return 255
    if not target.serving:
        print(f"ssh: connect to host {host} port 22: Connection refused", file=sys.stderr)
        return 255
    if not _check_host_key(world, target, options, resolved, host):
        return 255

    env = {
        "HOME": target.home,
        "PATH": os.environ["PATH"],
        "SSH_CLIENT": f"{target.client_address} 50022 22",
    }
    if "TENDER_PYTHON" in os.environ:
        env["TENDER_PYTHON"] = os.environ["TENDER_PYTHON"]
    login = _authenticate(world, target, options, resolved, host)
    if login is None:
        print(f"{user}@{host}: Permission denied (publickey,password).", file=sys.stderr)
        return 255
    if login == "too many":
        print(
            f"Received disconnect from {host} port 22:2: Too many authentication failures\n"
            f"Disconnected from {host} port 22",
            file=sys.stderr,
        )
        return 255
    if login != "own":
        env["SSH_ORIGINAL_COMMAND"] = command
        command = login
    if not world.drop_after_first_line or (world.drop_only_without_config and "_config" in options):
        return subprocess.run(["sh", "-c", command], cwd=target.home, env=env, check=False).returncode
    ran = subprocess.run(["sh", "-c", command], cwd=target.home, env=env, check=False, capture_output=True, text=True)
    print(ran.stdout.splitlines()[0] if ran.stdout else "")
    print("client_loop: send disconnect: Connection reset by peer", file=sys.stderr)
    return 255


def _ssh_keygen(argv: list[str]) -> int:
    if argv[:1] != ["-lf"] and _world().keygen_fails:
        print("Saving key failed: No space left on device", file=sys.stderr)
        return 1
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
    source = f" src {world.route_source}" if world.route_source else ""
    print(f"{address} dev wlan0{source} uid 1000 \n    cache ")
    return 0


def _rsync(argv: list[str]) -> int:
    with open(os.environ[RSYNC_RECORD_VARIABLE], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\n")
    return 0


_PROGRAMS = {"ssh": _ssh, "ssh-keygen": _ssh_keygen, "ip": _ip, "rsync": _rsync}

if __name__ == "__main__":
    sys.exit(_PROGRAMS[sys.argv[1]](sys.argv[2:]))
