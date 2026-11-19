"""Stand-ins for the programs the remote dev loop starts on both machines, and the world they answer from.

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
  ``SSH_CLIENT`` set and the target's own ``PATH`` (:attr:`Target.path`) —
  nothing of the caller's environment reaches it, as nothing does through
  OpenSSH;
- a destination behind a ProxyJump fails on its hop when the world says so,
  with what OpenSSH prints for that failure;
- a login also sets ``XDG_RUNTIME_DIR`` as ``pam_systemd`` does, and — the one
  departure from OpenSSH — hands on every ``FAKE_*`` variable, so the
  stand-ins on the far side find the world too.

``rsync`` as the client asks its ``-e`` remote shell for the server command
rsync 3.2.7 sends (``server_options`` in its ``options.c``) for the options
the dev tasks use, and refuses any other option; the ``rsync --server`` that
command reaches records what it was started with and makes the copy, so a
copy lands only where the dispatcher let the request through.

On the target, ``systemd-run --unit`` starts a stand-in process as the unit's
main process — for ``backend/main.py`` one that takes the backend's lock and
writes a line to its log — and ``systemctl`` answers from the units it
started or :func:`set_unit` declared; ``stop`` ends the unit's process.
``tail -F`` and ``journalctl -f`` follow until the process ``--pid`` names
ends or nothing reads their output any more, or, with
:attr:`World.follow_holds` false, print once and end.

Every login, password prompt and host key shown is appended to
:attr:`World.log`, which is how a test tells a run that needed a password from
one that did not; every login ``ssh`` is asked for leaves its options and the
identity files it offered in :func:`ssh_calls`.
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final, Literal, TypedDict, get_args

WORLD_VARIABLE = "FAKE_REMOTE_WORLD"
RSYNC_RECORD_VARIABLE = "FAKE_RSYNC_RECORD"

# What the scripts under test call besides the stand-ins.
SYSTEM_TOOLS = (
    "sh", "bash", "cat", "mkdir", "chmod", "mv", "rm", "cp", "grep", "awk", "readlink", "wc", "date", "id",
    "dirname", "seq", "tr", "sleep", "python3",
)  # fmt: skip
STAND_INS = (
    "ssh", "ssh-keygen", "ip", "rsync", "mise", "systemctl", "systemd-run", "pgrep", "journalctl", "tail",
    "kscreen-doctor",
)  # fmt: skip

# What rsync 3.2.7's client sends its server for ``-a`` (``server_options``).
RSYNC_ARCHIVE = "-logDtpre.iLsfxCIvu"
# How long a stand-in that follows or stands in for a process lasts at most,
# so that a test that fails part-way leaves nothing running for long.
LIFETIME = 30.0
# The environment variables each recorded command keeps.
RECORDED_ENVIRONMENT = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY", "PATH")

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
    path: str = ""
    """The ``PATH`` a login on this target runs with, as sshd sets it there; a test names it."""


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
    """Drop the connection once the login's command has written its first line to stdout, or ended writing none;
    what it wrote on stderr is handed on first."""
    drop_only_for: str | None = None
    """Drop only a login whose request holds this — the task's call, not the copy before it."""
    drop_says: str = "client_loop: send disconnect: Connection reset by peer"
    """What ssh writes on stderr when it drops the connection."""
    drop_after_line: str | None = None
    """Drop the connection once a login's command has written a line starting with this to stdout; a login that
    writes no such line runs as it would."""
    processes_exit: bool = False
    """A unit's stand-in process ends at once, as a unit that fails at its start."""
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
    session_environment: list[str] = field(
        default_factory=lambda: [
            "DISPLAY=:0",
            "WAYLAND_DISPLAY=wayland-0",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
            "PATH=/from/the/user/manager",
            "LANG=$'odd\\nvalue'",
        ]
    )
    """What ``systemctl --user show-environment`` prints on the target."""
    steam_running: bool = False
    build_fails: bool = False
    backend_exits: bool = False
    """The stand-in backend exits right after its first log line, as one that fails to start."""
    follow_holds: bool = True
    """Whether ``tail -F`` without ``--pid`` and ``journalctl -f`` keep following, or print once and end."""
    journal: list[str] = field(default_factory=list)
    """What ``journalctl`` prints."""
    outputs: list[str] = field(default_factory=list)
    """The connected, enabled outputs ``kscreen-doctor --json`` lists on the target."""
    manager_unreachable: bool = False
    """``systemctl --user`` cannot reach the user manager: every call fails."""
    main_pid_fails: bool = False
    """Only ``systemctl --user show -p MainPID`` fails."""
    main_pid_fails_while_running: bool = False
    """``systemctl --user show -p MainPID`` fails for a unit only once it runs — the user manager lost after a start."""
    unit_states: dict[str, str] = field(default_factory=dict)
    """An ``ActiveState`` a unit reports whatever its process does — ``activating``, ``deactivating``."""
    stop_fails: bool = False
    """``systemctl --user stop`` fails and stops nothing."""
    journal_late: bool = False
    """``journalctl`` without ``-f`` hands on a restore's last line ("Restore ...") only from its second call."""
    service_python: str | None = None
    """The interpreter the installed service's ``ExecStart`` runs, or ``None`` for no service installed."""

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
    for program in STAND_INS:
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


class Command(TypedDict):
    program: str
    argv: list[str]
    env: dict[str, str]
    """The variables of :data:`RECORDED_ENVIRONMENT` it was started with."""
    pid: int


def commands(world: World, program: str | None = None) -> list[Command]:
    """What each stand-in on the target, and ``mise`` here, was started with, in order."""
    path = Path(world.log + ".commands")
    if not path.exists():
        return []
    every = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [call for call in every if program is None or call["program"] == program]


def units(world: World) -> dict[str, dict[str, int]]:
    """Each unit a stand-in started or :func:`set_unit` declared, with its main process as ``pid``."""
    path = Path(world.log + ".units")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def set_unit(world: World, name: str, pid: int) -> None:
    """Declare *name* a unit whose main process is *pid*, as if the user manager ran it."""
    every = units(world)
    every[name] = {"pid": pid}
    Path(world.log + ".units").write_text(json.dumps(every), encoding="utf-8")


def end_processes(world: World) -> None:
    """End every process a stand-in started, and every login's session on a target, for a test's teardown."""
    for unit in units(world).values():
        pid = unit["pid"]
        if pid > 0 and _alive(pid):
            os.kill(pid, signal.SIGKILL)
    sessions = Path(world.log + ".sessions")
    for line in sessions.read_text(encoding="utf-8").splitlines() if sessions.exists() else []:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(int(line), signal.SIGKILL)


def _start_session(
    world: World, command: str, *, home: str, env: dict[str, str], capture: bool = False
) -> subprocess.Popen[str]:
    """*command* run on the target as sshd runs it: in a session of its own, which a Ctrl-C here does not reach."""
    output = subprocess.PIPE if capture else None
    started = subprocess.Popen(
        ["sh", "-c", command], cwd=home, env=env, stdout=output, stderr=output, start_new_session=True, text=True
    )
    with open(world.log + ".sessions", "a", encoding="utf-8") as handle:
        handle.write(f"{started.pid}\n")
    return started


def _alive(pid: int) -> bool:
    """Whether *pid* runs: a zombie, ended and not yet reaped, does not."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _record(world: World, program: str, argv: list[str]) -> None:
    command = Command(
        program=program,
        pid=os.getpid(),
        argv=argv,
        env={name: os.environ[name] for name in RECORDED_ENVIRONMENT if name in os.environ},
    )
    with open(world.log + ".commands", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(command) + "\n")


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

    assert target.path, "a Target names the PATH a login on it runs with"
    env = {
        "HOME": target.home,
        "PATH": target.path,
        "SSH_CLIENT": f"{target.client_address} 50022 22",
        "XDG_RUNTIME_DIR": os.path.join(target.home, ".run"),
        **{name: value for name, value in os.environ.items() if name.startswith("FAKE_")},
    }
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
    requested = command
    if login != "own":
        env["SSH_ORIGINAL_COMMAND"] = command
        command = login
    drops = world.drop_after_first_line or world.drop_after_line is not None
    if world.drop_only_for is not None and world.drop_only_for not in requested:
        drops = False
    if not drops or (world.drop_only_without_config and "_config" in options):
        return _start_session(world, command, home=target.home, env=env).wait()
    session = _start_session(world, command, home=target.home, env=env, capture=True)
    out, err = session.communicate()
    lines = out.splitlines()
    marker = world.drop_after_line
    matching = [index for index, line in enumerate(lines) if marker is not None and line.startswith(marker)]
    if marker is not None and not matching:
        sys.stdout.write(out)
        sys.stderr.write(err)
        return session.returncode
    end = matching[0] + 1 if marker is not None else 1
    if lines[:end]:
        print("\n".join(lines[:end]))
    sys.stderr.write(err)
    print(world.drop_says, file=sys.stderr)
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


TRANSFER_VARIABLE = "FAKE_RSYNC_TRANSFER"


def _rsync(argv: list[str]) -> int:
    if argv[:1] != ["--server"]:
        return _rsync_client(argv)
    if RSYNC_RECORD_VARIABLE in os.environ:
        with open(os.environ[RSYNC_RECORD_VARIABLE], "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\n")
    if TRANSFER_VARIABLE in os.environ:
        transfer = json.loads(os.environ[TRANSFER_VARIABLE])
        _mirror(Path(transfer["source"]), Path(argv[-1]), transfer["rules"], delete_excluded=transfer["excluded"])
    return 0


def _rsync_client(argv: list[str]) -> int:
    """``rsync [options] <source>/ <host>:<path>``: the server command through ``-e``, the copy by the server."""
    archive = delete = delete_excluded = False
    rules: list[list[str]] = []
    remote_shell: str | None = None
    paths: list[str] = []
    words = iter(argv)
    for word in words:
        if word == "-a":
            archive = True
        elif word == "--delete":
            delete = True
        elif word == "--delete-excluded":
            delete_excluded = True
        elif word == "-e":
            remote_shell = next(words)
        elif word.startswith(("--include=", "--exclude=")):
            kind, _, pattern = word[2:].partition("=")
            rules.append([kind, pattern])
        elif word.startswith("-"):
            print(f"the rsync stand-in knows no {word}", file=sys.stderr)
            return 1
        else:
            paths.append(word)
    if not archive or remote_shell is None or len(paths) != 2 or not paths[0].endswith("/"):
        print(f"the rsync stand-in cannot run: {argv}", file=sys.stderr)
        return 1
    host, _, path = paths[1].partition(":")
    server = ["rsync", "--server", RSYNC_ARCHIVE]
    if delete_excluded:
        server.append("--delete-excluded")
    elif delete:
        server.append("--delete")
    transfer = {"source": paths[0], "rules": rules, "excluded": delete_excluded}
    env = {**os.environ, TRANSFER_VARIABLE: json.dumps(transfer)}
    ended = subprocess.run([*shlex.split(remote_shell), host, *server, ".", path], env=env, check=False).returncode
    if ended != 0:
        print(
            "rsync: connection unexpectedly closed (0 bytes received so far) [sender]\n"
            f"rsync error: unexplained error (code {ended}) at io.c(231) [sender=3.2.7]",
            file=sys.stderr,
        )
    return ended


def _included(relative: str, is_dir: bool, rules: list[list[str]]) -> bool:
    """rsync's first matching rule for *relative*: ``/`` anchors at the root, ``*`` stays within a name."""
    parts = relative.split("/")
    for kind, raw in rules:
        if raw.endswith("/") and not is_dir:
            continue
        pattern = raw.rstrip("/")
        if pattern.startswith("/"):
            wanted = pattern[1:].split("/")
            hit = len(wanted) == len(parts) and all(map(fnmatch.fnmatchcase, parts, wanted))
        else:
            hit = fnmatch.fnmatchcase(parts[-1], pattern)
        if hit:
            return kind == "include"
    return True


def _mirror(source: Path, destination: Path, rules: list[list[str]], *, delete_excluded: bool) -> None:
    sent: set[str] = set()
    destination.mkdir(parents=True, exist_ok=True)
    for directory, folders, files in os.walk(source):
        base = Path(directory).relative_to(source)
        for folder in list(folders):
            relative = (base / folder).as_posix()
            linked = (Path(directory) / folder).is_symlink()
            if not _included(relative, not linked, rules):
                folders.remove(folder)
                continue
            sent.add(relative)
            target = destination / relative
            if linked:
                folders.remove(folder)
                _replace(target)
                target.symlink_to(os.readlink(Path(directory) / folder))
            elif not target.is_dir() or target.is_symlink():
                _replace(target)
                target.mkdir()
        for name in files:
            relative = (base / name).as_posix()
            if not _included(relative, False, rules):
                continue
            sent.add(relative)
            if _unchanged(Path(directory) / name, destination / relative):
                continue
            _replace(destination / relative)
            shutil.copy2(Path(directory) / name, destination / relative, follow_symlinks=False)
    for directory, folders, files in os.walk(destination, topdown=False):
        base = Path(directory).relative_to(destination)
        for name in [*folders, *files]:
            relative = (base / name).as_posix()
            path = destination / relative
            protected = not delete_excluded and not _included(relative, path.is_dir(), rules)
            if relative not in sent and not protected:
                _replace(path)


def _unchanged(source: Path, copy: Path) -> bool:
    """rsync's quick check: a file of the same size and modification time is not sent again."""
    if source.is_symlink() or copy.is_symlink() or not copy.is_file():
        return False
    mine, theirs = source.stat(), copy.stat()
    return mine.st_size == theirs.st_size and int(mine.st_mtime) == int(theirs.st_mtime)


def _replace(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _mise(argv: list[str]) -> int:
    world = _world()
    _record(world, "mise", argv)
    if world.build_fails:
        print("vite: build failed", file=sys.stderr)
        return 1
    built = Path.cwd() / "dist" / "index.js"
    built.parent.mkdir(exist_ok=True)
    built.write_text("build:desktop\n" if "--surface" in argv else "build:dev\n", encoding="utf-8")
    return 0


def _unit_pid(world: World, name: str) -> int:
    unit = units(world).get(name)
    pid = 0 if unit is None else unit["pid"]
    return pid if pid > 0 and _alive(pid) else 0


def _systemctl(argv: list[str]) -> int:
    world = _world()
    _record(world, "systemctl", argv)
    words = [word for word in argv if word not in ("--user", "--quiet")]
    lost = world.main_pid_fails_while_running and "MainPID" in words and _unit_pid(world, words[-1]) > 0
    if world.manager_unreachable or (world.main_pid_fails and "MainPID" in words) or lost:
        print("Failed to connect to bus: No medium found", file=sys.stderr)
        return 1
    if words == ["show-environment"]:
        print("\n".join(world.session_environment))
        return 0
    if words[:1] == ["is-active"]:
        # systemd 261: 3 for a loaded unit that is not active, 4 for one not loaded at all.
        active = _unit_pid(world, words[1]) > 0
        if "--quiet" not in argv:
            print("active" if active else "inactive")
        return 0 if active else 3 if words[1] in units(world) else 4
    if words[:1] == ["show"] and "ActiveState" in words:
        state = world.unit_states.get(words[-1])
        print(state or ("active" if _unit_pid(world, words[-1]) > 0 else "inactive"))
        return 0
    if words[:1] == ["show"] and "MainPID" in words:
        print(_unit_pid(world, words[-1]))
        return 0
    if words[:1] == ["show"] and "ExecStart" in words:
        # systemd 257's spelling; a unit that is not installed has the property empty.
        python = world.service_python if words[-1] == "romm-tender" else None
        if python is not None:
            print(
                f"{{ path={python} ; argv[]={python} /home/deck/.local/lib/romm-tender/backend/main.py ;"
                " ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
            )
        else:
            print()
        return 0
    if words[:1] == ["stop"] and world.stop_fails:
        print(f"Job for {words[1]}.service failed.", file=sys.stderr)
        return 1
    if words[:1] == ["stop"]:
        for name in words[1:]:
            pid = _unit_pid(world, name)
            if pid:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while _alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
        return 0
    return 0


def _systemd_run(argv: list[str]) -> int:
    """Records the call; with ``--unit``, starts its stand-in as the unit's main process."""
    world = _world()
    _record(world, "systemd-run", argv)
    options = argv[: argv.index("--")]
    command = argv[argv.index("--") + 1 :]
    unit = next((word.split("=", 1)[1] for word in options if word.startswith("--unit=")), None)
    if unit is None:
        return 0
    role = "backend" if command[-1].endswith("backend/main.py") else "process"
    started = subprocess.Popen(
        [sys.executable, __file__, role],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    set_unit(world, unit, started.pid)
    if role == "backend":
        log = _backend_log()
        deadline = time.monotonic() + 5
        while not (log.exists() and log.read_text(encoding="utf-8").endswith("backend started\n")):
            if time.monotonic() > deadline:
                raise AssertionError("the stand-in backend never wrote its log")
            time.sleep(0.02)
        if world.backend_exits:
            started.wait()
    return 0


def _backend_log() -> Path:
    return Path(os.environ["HOME"]) / ".local/state/romm-tender/backend.log"


def _backend(_argv: list[str]) -> int:
    """A backend: it takes the single-instance lock where the backend does, and writes one log line."""
    world = _world()
    lock = Path(os.environ["HOME"]) / ".local/share/romm-tender/backend.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock, "a")  # noqa: SIM115 — held for the life of the process, as the backend's is
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    log = _backend_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as written:
        written.write("backend started\n")
    if world.backend_exits:
        return 1
    return _process([])


def _process(_argv: list[str]) -> int:
    if _world().processes_exit:
        return 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    time.sleep(LIFETIME)
    return 0


def _kscreen_doctor(argv: list[str]) -> int:
    world = _world()
    _record(world, "kscreen-doctor", argv)
    print(json.dumps({"outputs": [{"name": name, "connected": True, "enabled": True} for name in world.outputs]}))
    return 0


def _pgrep(argv: list[str]) -> int:
    world = _world()
    _record(world, "pgrep", argv)
    return 0 if world.steam_running else 1


def _reader_gone() -> bool:
    poller = select.poll()
    poller.register(sys.stdout.fileno(), select.POLLERR)
    return any(events & (select.POLLERR | select.POLLHUP) for _, events in poller.poll(0))


def _follow(path: Path | None, start: int | None, last_lines: int, pid: int | None, holds: bool) -> int:
    """Write *path* from *start* (a byte), or its last lines, then what is added, until told to end."""
    deadline = time.monotonic() + LIFETIME
    position = start
    while True:
        alive = pid is None or _alive(pid)
        if path is not None and path.exists():
            data = path.read_bytes()
            if position is None:
                lines = data.splitlines(keepends=True)
                position = len(data) - sum(len(line) for line in lines[-last_lines:]) if last_lines else len(data)
            try:
                sys.stdout.buffer.write(data[position:])
                sys.stdout.flush()
            except BrokenPipeError:
                return 0
            position = len(data)
        if not alive or (pid is None and not holds) or _reader_gone() or time.monotonic() > deadline:
            return 0
        time.sleep(0.02)


def _tail(argv: list[str]) -> int:
    if "-F" not in argv:
        real = shutil.which("tail", path="/usr/bin:/bin")
        assert real is not None
        os.execv(real, ["tail", *argv])
    world = _world()
    _record(world, "tail", argv)
    start = None
    last_lines = 10
    pid = None
    words = iter(argv)
    for word in words:
        if word == "-c":
            start = int(next(words).lstrip("+")) - 1
        elif word == "-n":
            last_lines = int(next(words))
        elif word.startswith("--pid="):
            pid = int(word.split("=", 1)[1])
    return _follow(Path(argv[-1]), start, last_lines, pid, world.follow_holds)


def _journalctl(argv: list[str]) -> int:
    world = _world()
    _record(world, "journalctl", argv)
    reads = [call for call in commands(world, "journalctl") if "-f" not in call["argv"]]
    late = world.journal_late and "-f" not in argv and len(reads) < 2
    for line in world.journal:
        if not (late and line.startswith("Restore ")):
            print(line, flush=True)
    if "-f" in argv:
        return _follow(None, None, 0, None, world.follow_holds)
    return 0


_PROGRAMS = {
    "ssh": _ssh,
    "ssh-keygen": _ssh_keygen,
    "ip": _ip,
    "rsync": _rsync,
    "mise": _mise,
    "systemctl": _systemctl,
    "systemd-run": _systemd_run,
    "pgrep": _pgrep,
    "kscreen-doctor": _kscreen_doctor,
    "journalctl": _journalctl,
    "tail": _tail,
    "backend": _backend,
    "process": _process,
}

if __name__ == "__main__":
    # Ctrl-C ends a stand-in as it ends the program it stands in for: at once, and with no traceback.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(_PROGRAMS[sys.argv[1]](sys.argv[2:]))
