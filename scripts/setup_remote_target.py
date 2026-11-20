#!/usr/bin/env python3
"""Set another machine up as a dev target, or repair it.

    mise run setup:remote-target <name> <destination> [--print]

<destination> is anything your own ssh reaches: user@address, or an alias from
your SSH config, which is read with `ssh -G` and never written. With --print
nothing is contacted: a script to run on the target is printed instead.

What the setup does on both machines, and what each refusal means:
docs/contributing/frontend-dev-loop.md, "A remote target".
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from typing import Literal, assert_never

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPATCHER = REPO / "scripts" / "dev_dispatch.sh"
INSTALL_SCRIPT = REPO / "scripts" / "dev_target_install.sh"

CONFIG_DIR_NAME = "romm-tender-dev"
KEY_NAME = "id_ed25519"
PUBLIC_KEY_NAME = f"{KEY_NAME}.pub"
SSH_CONFIG_NAME = "ssh_config"
KNOWN_HOSTS_NAME = "known_hosts"

# Where the install script puts the dispatcher, the running tree and the
# incoming copy on the target, relative to the target user's home.
TARGET_ROOT = ".local/lib/romm-tender-dev"

# Where the printed script finds the host keys whose fingerprints it shows:
# OpenSSH's default HostKey paths.
HOST_KEY_DIR = "/etc/ssh"

# The install script's first and last lines on stdout, searched for rather than
# expected alone: the user's shell may print lines of its own first. Without
# STARTED the script never ran, and only then can the setup say that a failure
# changed nothing on the target. STOPPED opens the line on stderr with which the
# script refuses to go on, which running the setup again would not change.
STARTED = "tender-dev-setup: started"
INSTALLED = "tender-dev-setup: installed from="
STOPPED = "tender-dev-setup: stopped: "

# What ssh exits with when it fails itself, rather than handing on the remote
# command's status (ssh(1), EXIT STATUS).
SSH_FAILED = 255

# What ssh says on stderr for each failure that ends a login before it starts,
# in the order they are tested: a host key mismatch also prints "Host key
# verification failed", and a refused login can end in "Connection closed".
_HOST_KEY_CHANGED_FOR = re.compile(r"Host key for (\S+) has changed")
HOST_KEY_CHANGED = re.compile(r"REMOTE HOST IDENTIFICATION HAS CHANGED|" + _HOST_KEY_CHANGED_FOR.pattern)
_OFFENDING_FILE = re.compile(r"Offending \S+ key in (\S+):\d+")
HOST_KEY_NOT_ACCEPTED = re.compile(r"Host key verification failed")
KEY_REFUSED = re.compile(r"Permission denied \(")
NO_LOGIN = re.compile(KEY_REFUSED.pattern + r"|Too many authentication failures")
UNRESOLVED = re.compile(r"Could not resolve hostname ([^:\s]+)")
# What ssh says when a connection it had logged in on is lost — the far side
# gone to sleep, a network gone (OpenSSH's "Read from remote host %s: %s" among
# them). "Connection reset" in NO_SERVER matches some of these too, so a line
# of these is read as no failure before the login.
LOST_AFTER_LOGIN = re.compile(
    r"client_loop: |packet_write_wait: |Read from remote host |Timeout, server \S+ not responding"
    r"|Connection to \S+ closed by remote host"
)
NO_SERVER = re.compile(
    r"Connection refused|Connection timed out|Operation timed out|No route to host|Network is unreachable"
    r"|kex_exchange_identification|Connection closed by|Connection reset"
)

# Where ssh names the host a failure is about: a refused login as user@host, a
# connection as "connect to host <host> port", a name and a changed key by it.
_NAMED_HOSTS = (
    re.compile(r"\S+@(\S+): " + KEY_REFUSED.pattern),
    re.compile(r"connect to host (\S+) port"),
    UNRESOLVED,
    _HOST_KEY_CHANGED_FOR,
)

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

_CONFIG_HEADER = (
    "# The dev loop's own SSH config, which setup:remote-target prepares for the dev tasks' --target:\n"
    "# one entry per target, to be read with this file alone (docs/contributing/frontend-dev-loop.md).\n"
)

_USER = "user"
_HOSTNAME = "hostname"
_PORT = "port"
_PROXY_JUMP = "proxyjump"
_PROXY_COMMAND = "proxycommand"


class SetupError(Exception):
    """A refusal: the message is what the user reads, and the run ends with it."""


@dataclass(frozen=True)
class Destination:
    """Where a target is: what ``ssh -G`` resolved :attr:`text` to, or an entry :func:`read_entries` read back."""

    text: str
    user: str
    hostname: str
    port: str
    proxy_jump: str | None
    proxy_command: str | None
    jump: str | None = None
    """:attr:`proxy_jump` with every hop spelled out (:func:`resolve_jump`), once :func:`with_jump` has run."""
    key_alias: str | None = None
    """A HostKeyAlias the user's own SSH config gives the destination."""

    @property
    def key_name(self) -> str:
        """The name the user's own ``ssh`` files this host's key under: OpenSSH's ``host``, or ``[host]:port``."""
        if self.key_alias:
            return self.key_alias
        return self.hostname if self.port == "22" else f"[{self.hostname}]:{self.port}"

    @property
    def via(self) -> str | None:
        """The hop the target is reached through, if any: the target then sees that hop's address."""
        return self.proxy_jump or self.proxy_command


def run(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """*command* run to its end, its output captured; a program that is not there is a refusal."""
    try:
        return subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise SetupError(f"tender-dev: the setup needs {command[0]}, and this machine has none on its PATH.") from error


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SetupError(f"tender-dev: could not read {path}: {reason(error)}.") from error


def reason(error: OSError | UnicodeError) -> str:
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error)


def config_dir() -> pathlib.Path:
    """The dev loop's directory on the build host: its key, SSH config and known-hosts file."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / CONFIG_DIR_NAME


def host_key_alias(name: str) -> str:
    """The name the target *name*'s host key is recorded under, whatever its address."""
    return f"romm-tender-dev.{name}"


def _renamed(line: str, names: str) -> str:
    fields = line.split(maxsplit=1)
    return f"{names} {fields[1]}" if len(fields) == 2 else line


def _names(line: str) -> list[str]:
    fields = line.split()
    return fields[0].split(",") if fields and not line.startswith("#") else []


def with_key_name(known_hosts: str, alias: str, key_name: str) -> str:
    """*known_hosts* with each key recorded under *alias* recorded under *key_name* as well.

    The user's own login looks the target's key up under *key_name*, so a key
    already recorded is not asked about again, and one that changed fails.
    """
    lines = known_hosts.splitlines()
    copies = [_renamed(line, key_name) for line in lines if alias in _names(line)]
    return "".join(f"{line}\n" for line in dict.fromkeys([*lines, *copies]))


def under_alias(known_hosts: str, alias: str, key_name: str) -> str:
    """*known_hosts* with every key filed under *key_name* filed under *alias* instead, each line once."""
    lines = [_renamed(line, alias) if key_name in _names(line) else line for line in known_hosts.splitlines()]
    return "".join(f"{line}\n" for line in dict.fromkeys(lines))


def is_recorded(known_hosts: str, alias: str) -> bool:
    """Whether a line of *known_hosts* names *alias* among its host names, its first field."""
    for line in known_hosts.splitlines():
        fields = line.split()
        if fields and alias in fields[0].split(","):
            return True
    return False


def build_host_name() -> str:
    """This machine's name, as the comments on both machines give it."""
    return re.sub(r"[^A-Za-z0-9.-]", "-", socket.gethostname()) or "build-host"


def stamp() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")


def ssh_resolve(destination: str, port: str | None = None) -> dict[str, list[str]]:
    """``ssh -G``'s answer for *destination*: each key with every value it printed."""
    result = run(["ssh", "-G", *(["-p", port] if port else []), destination])
    if result.returncode != 0:
        raise SetupError(
            f"tender-dev: ssh could not read its configuration for {destination}:\n{result.stderr.rstrip()}"
        )
    answer: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        answer.setdefault(key.lower(), []).append(value.strip())
    return answer


def _single(answer: dict[str, list[str]], key: str) -> str | None:
    values = answer.get(key)
    if not values or values[0] in ("", "none"):
        return None
    return values[0]


def resolve(text: str, port: str | None = None) -> Destination:
    answer = ssh_resolve(text, port)
    hostname = _single(answer, _HOSTNAME)
    user = _single(answer, _USER)
    if hostname is None or user is None:
        raise SetupError(f"tender-dev: ssh resolved {text} to no host or no user.")
    return Destination(
        text=text,
        user=user,
        hostname=hostname,
        port=_single(answer, _PORT) or "22",
        proxy_jump=_single(answer, _PROXY_JUMP),
        proxy_command=_single(answer, _PROXY_COMMAND),
        key_alias=_single(answer, "hostkeyalias"),
    )


def resolve_jump(proxy_jump: str) -> str:
    """*proxy_jump* with every hop spelled out as ``user@host:port``, through ``ssh -G``."""
    hops = []
    for hop in proxy_jump.split(","):
        user, host, port = split_hop(hop)
        resolved = resolve(f"{user}@{host}" if user else host, port=port)
        address = f"[{resolved.hostname}]" if ":" in resolved.hostname else resolved.hostname
        hops.append(f"{resolved.user}@{address}:{resolved.port}")
    return ",".join(hops)


def with_jump(destination: Destination) -> Destination:
    """*destination* with its ProxyJump resolved, for :func:`config_entry` to write."""
    if destination.proxy_jump is None:
        return destination
    return replace(destination, jump=resolve_jump(destination.proxy_jump))


def split_hop(hop: str) -> tuple[str | None, str, str | None]:
    """One ProxyJump hop, ``[ssh://][user@]host[:port]``, as its user, host and port.

    Split here rather than handed to ``ssh -G`` whole: ``ssh -G`` takes a
    ``host:port`` for a host name, and an IPv6 address in brackets for one too.
    """
    rest = hop.removeprefix("ssh://")
    user, _, rest = rest.rpartition("@")
    if rest.startswith("["):
        host, _, after = rest[1:].partition("]")
        port = after.removeprefix(":") or None
    elif rest.count(":") == 1:
        host, _, port_text = rest.partition(":")
        port = port_text or None
    else:
        host, port = rest, None
    return user or None, host, port


def existing_key(directory: pathlib.Path) -> pathlib.Path | None:
    """The dev loop's key, if this machine holds one, checked for its public half."""
    key = directory / KEY_NAME
    if not key.exists():
        return None
    if not (directory / PUBLIC_KEY_NAME).exists():
        raise SetupError(
            f"tender-dev: {key} has no {PUBLIC_KEY_NAME} beside it.\n"
            f"  Put it back, or remove {key} so that the setup makes a new key."
        )
    return key


def generate_key(directory: pathlib.Path, comment: str) -> pathlib.Path:
    path = directory / KEY_NAME
    result = run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)])
    if result.returncode != 0:
        raise SetupError(f"tender-dev: ssh-keygen could not create a key:\n{result.stderr.rstrip()}")
    return path


def install_key(new_key: pathlib.Path, directory: pathlib.Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in (KEY_NAME, PUBLIC_KEY_NAME):
        shutil.move(new_key.with_name(name), directory / name)


def install_script(*, public_key: str, build_host: str, written: str, default_from: str, show_host_keys: bool) -> str:
    """``scripts/dev_target_install.sh`` with its values and the dispatcher in place.

    *default_from* empty has the script take the address from ``$SSH_CLIENT``,
    the setup login's.
    """
    values = "\n".join(
        f"{name}={shlex.quote(value)}"
        for name, value in (
            ("started_marker", STARTED),
            ("installed_marker", INSTALLED),
            ("stopped_marker", STOPPED),
            ("public_key", public_key),
            ("build_host", build_host),
            ("written", written),
            ("target_root", TARGET_ROOT),
            ("default_from", default_from),
            ("show_host_keys", "yes" if show_host_keys else "no"),
            ("host_key_dir", HOST_KEY_DIR),
        )
    )
    template = read_text(INSTALL_SCRIPT)
    return template.replace("# @VALUES@", values, 1).replace("@DISPATCHER@", read_text(DISPATCHER).rstrip("\n"), 1)


Where = Literal["target", "hop", "either"]
HandOver = Literal["refused", "forced command", "hop"]


def failed_where(stderr: str, *, name: str, destination: Destination) -> Where:
    """Whose failure ssh reported: ``target``, ``hop``, or ``either`` where it names no host."""
    if not destination.via:
        return "target"
    named = {match.group(1) for pattern in _NAMED_HOSTS for match in pattern.finditer(stderr)}
    if named & {destination.hostname, host_key_alias(name), name}:
        return "target"
    return "hop" if named else "either"


FailureKind = Literal["host key changed", "host key not accepted", "no login", "unresolved", "no server"]


@dataclass(frozen=True)
class SshFailure:
    """What ended an ``ssh`` before the login, as its stderr says, and on which leg."""

    kind: FailureKind
    where: Where
    unresolved: str | None = None
    """The name ssh could not resolve, for ``unresolved``."""
    offending: str | None = None
    """The known-hosts file that records another key, for ``host key changed`` on a hop."""
    key_refused: bool = False
    """For ``no login``: a key was refused ("Permission denied"), not the login ended for too many tries."""

    def __post_init__(self) -> None:
        own = {
            "unresolved": self.kind == "unresolved",
            "offending": self.kind == "host key changed",
            "key_refused": self.kind == "no login",
        }
        for field_name, allowed in own.items():
            if getattr(self, field_name) not in (None, False) and not allowed:
                raise ValueError(f"a {self.kind!r} failure carries no {field_name}")


def ssh_failure(
    stderr: str, *, name: str, destination: Destination, known_hosts: tuple[pathlib.Path, ...] = ()
) -> SshFailure | None:
    """The failure *stderr* reports before the login, or ``None`` for one no pattern here knows.

    A connection lost after the login (:data:`LOST_AFTER_LOGIN`) is none.
    Tested in this order: a changed host key also prints "Host key verification
    failed", and a refused login can end in "Connection closed". A changed host
    key is the target's when there is no hop on the way, or ssh names no file
    or one of *known_hosts*, and a hop's otherwise; every other failure is
    placed by :func:`failed_where`. Each tool words the message itself.
    """
    if LOST_AFTER_LOGIN.search(stderr):
        return None
    where = failed_where(stderr, name=name, destination=destination)
    if HOST_KEY_CHANGED.search(stderr):
        offending = _OFFENDING_FILE.search(stderr)
        ours = {str(path) for path in known_hosts}
        if offending is None or offending.group(1) in ours or not destination.via:
            return SshFailure("host key changed", "target")
        return SshFailure("host key changed", "hop", offending=offending.group(1))
    if HOST_KEY_NOT_ACCEPTED.search(stderr):
        return SshFailure("host key not accepted", where)
    if NO_LOGIN.search(stderr):
        return SshFailure("no login", where, key_refused=bool(KEY_REFUSED.search(stderr)))
    unresolved = UNRESOLVED.search(stderr)
    if unresolved is not None:
        return SshFailure("unresolved", where, unresolved=unresolved.group(1))
    if NO_SERVER.search(stderr):
        return SshFailure("no server", where)
    return None


def classify(
    stderr: str, *, name: str, destination: Destination, known_hosts: pathlib.Path, trial_known_hosts: pathlib.Path
) -> str | None:
    """What to tell the user for an ``ssh`` that ended before the login.

    ``None`` for the target's refusal of the login, which ``no_login_message``
    words, and for anything :func:`ssh_failure` does not know — a failure that
    is not ssh's, or one of ssh's worded otherwise. A failure ssh names on a hop
    is worded as the hop's; one it names on no host, as the target's or the hop's.
    """
    failure = ssh_failure(stderr, name=name, destination=destination, known_hosts=(known_hosts, trial_known_hosts))
    return (
        None if failure is None else word_failure(failure, name=name, destination=destination, known_hosts=known_hosts)
    )


def word_failure(failure: SshFailure, *, name: str, destination: Destination, known_hosts: pathlib.Path) -> str | None:
    """The setup's message for *failure*, as :func:`classify` gives it."""
    host = destination.hostname
    hop = f"the hop {destination.via}"
    match failure.kind:
        case "host key changed" if failure.where == "target":
            return (
                f"tender-dev: the host key at {host} is not the one recorded for {name} — another machine may be"
                " answering at that address. No connection was made.\n"
                "  If the target was reinstalled, remove its record and run the setup again:\n"
                f"    ssh-keygen -R {host_key_alias(name)} -f {known_hosts}"
            )
        case "host key changed":
            return (
                f"tender-dev: a hop on the way to {name} ({destination.via}) has a host key that is not the one"
                f" {failure.offending} records — another machine may be answering there. No connection was made.\n"
                "  Check that hop with your own ssh."
            )
        case "host key not accepted":
            whose = {"target": f"at {host}", "hop": f"of {hop}", "either": f"of {host} or of {hop}"}[failure.where]
            return (
                f"tender-dev: the host key {whose} was not accepted, or ssh could not ask about it — it asks on a"
                " terminal. No connection was made."
            )
        case "no login" if failure.where == "hop":
            return f"tender-dev: {hop} accepted no login from this machine.\n  Check that hop with your own ssh."
        case "no login":
            return None
        case "unresolved" if failure.where == "hop":
            return (
                f"tender-dev: this machine cannot resolve {failure.unresolved}, a hop on the way to {name}"
                f" ({destination.via}).\n"
                "  Check that hop in your SSH config, or give its address."
            )
        case "unresolved":
            return (
                f"tender-dev: this machine cannot resolve {host}.\n"
                "  Give the target's address instead, or a name the network resolves."
            )
        case "no server" if failure.where == "hop":
            return f"tender-dev: no SSH server answers at {hop}.\n  Check that hop with your own ssh."
        case "no server" if failure.where == "either":
            return (
                f"tender-dev: no SSH server answers at {host} port {destination.port}, or at {hop}.\n"
                "  Check the address, that the target runs an SSH server, and that hop."
            )
        case "no server":
            return (
                f"tender-dev: no SSH server answers at {host} port {destination.port}.\n"
                "  Check the address, and that the target runs an SSH server."
            )
        case unknown:
            assert_never(unknown)


def route_source(destination: Destination) -> str:
    """The address this machine sends from towards the target (``ip route get``)."""
    try:
        infos = socket.getaddrinfo(destination.hostname, destination.port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as error:
        raise SetupError(
            f"tender-dev: this machine cannot resolve {destination.hostname}.\n"
            "  Give the target's address instead, or a name the network resolves."
        ) from error
    address = str(infos[0][4][0])
    result = run(["ip", "route", "get", address])
    words = result.stdout.split()
    if result.returncode != 0 or "src" not in words or words.index("src") + 1 >= len(words):
        raise SetupError(
            f"tender-dev: `ip route get {address}` named no address this machine sends from towards the target:\n"
            f"{(result.stderr or result.stdout).rstrip()}"
        )
    return words[words.index("src") + 1]


def after_marker(text: str, marker: str) -> str | None:
    """What follows *marker* on the first line of *text* that starts with it."""
    return next((line[len(marker) :] for line in text.splitlines() if line.startswith(marker)), None)


def failure_message(
    result: subprocess.CompletedProcess[str],
    *,
    name: str,
    destination: Destination,
    handed_over: HandOver | None,
    key_result: subprocess.CompletedProcess[str] | None,
    known_hosts: pathlib.Path,
    trial_known_hosts: pathlib.Path,
) -> str:
    """What to tell the user for a setup that did not end with the install done.

    *result* is the last login's. *key_result* is the key's login whenever one
    ran; it is told only when that login handed over for ``forced command``,
    and then first, before whatever the user's own login did — refused, stopped
    or stopped part-way — and that login's closing line last.
    """
    ssh_said = result.stderr.rstrip()
    stopped = after_marker(result.stderr, STOPPED)
    if stopped is not None:
        failure = f"tender-dev: the setup stopped on {name}: {stopped}"
        close = "This machine is unchanged."
    elif STARTED in result.stdout.splitlines():
        failure = f"tender-dev: the setup stopped part-way on {name} (ssh ended with {result.returncode}):\n{ssh_said}"
        close = "This machine is unchanged; run the setup again to finish it."
    else:
        failure = None
        if result.returncode == SSH_FAILED:
            read = ssh_failure(
                result.stderr, name=name, destination=destination, known_hosts=(known_hosts, trial_known_hosts)
            )
            if read is not None:
                failure = word_failure(read, name=name, destination=destination, known_hosts=known_hosts)
            if failure is None and read is not None and read.kind == "no login":
                failure = no_login_message(destination, key_refused=handed_over == "refused")
        if failure is None:
            failure = f"tender-dev: the setup did not start on {name} (ssh ended with {result.returncode}):\n{ssh_said}"
        close = "Nothing was changed, here or on the target."
    if handed_over == "forced command" and key_result is not None:
        failure = (
            f"tender-dev: {name} accepted the dev loop's key, but its forced command failed"
            f" (ssh ended with {key_result.returncode}):\n{key_result.stderr.rstrip()}\n"
            "  Your own login, tried next, did not install either:\n" + failure
        )
    return f"{failure}\n  {close}"


def no_login_message(destination: Destination, *, key_refused: bool) -> str:
    refused = f"tender-dev: {destination.text} accepted no login from this machine.\n"
    if destination.via:
        return (
            refused + f"  --print cannot set up a target reached through {destination.via}. Give the target a login"
            " of yours first — a key of yours in its ~/.ssh/authorized_keys, or a password — then run the setup"
            " here again."
        )
    message = (
        refused + "  Run the setup with --print, run the script it prints on the target, then run the setup here again."
    )
    if not key_refused:
        return message
    try:
        address = route_source(destination)
    except SetupError:
        return message + "\n  The target refused the dev loop's key as well."
    return (
        message + "\n  The target refused the dev loop's key as well. If it was set up with the script --print"
        " printed, the likely cause is the address that script restricts the key to — by default"
        f" {address}, the one this machine sends from towards the target. If the target sees this machine at"
        " another, run that script there again with that address as its argument."
    )


def write_atomically(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.new")
    with open(partial, "w", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        handle.write(text)
    os.replace(partial, path)


def config_entry(name: str, destination: Destination, *, key: pathlib.Path, known_hosts: pathlib.Path) -> list[str]:
    """The lines of a target's entry in a dev loop SSH config, without its comment.

    A ProxyJump goes in as :func:`with_jump` resolved it; a ProxyCommand as
    ``ssh -G`` printed it, a ``%h`` or ``%p`` left in it expanding against this
    entry's HostName and Port when it runs.
    """
    if destination.proxy_jump and not destination.jump:
        raise ValueError("config_entry needs the jump resolved: with_jump(destination)")
    lines = [
        f"Host {name}",
        f"    HostName {destination.hostname}",
        f"    Port {destination.port}",
        f"    User {destination.user}",
    ]
    if destination.jump:
        lines.append(f"    ProxyJump {destination.jump}")
    elif destination.proxy_command:
        lines.append(f"    ProxyCommand {destination.proxy_command}")
    lines += [
        f"    HostKeyAlias {host_key_alias(name)}",
        f'    IdentityFile "{key}"',
        "    IdentitiesOnly yes",
        f'    UserKnownHostsFile "{known_hosts}"',
        "    GlobalKnownHostsFile /dev/null",
        "    StrictHostKeyChecking yes",
        "    BatchMode yes",
    ]
    return lines


def read_entries(text: str) -> dict[str, Destination]:
    """Each target's entry in *text*, a dev loop SSH config as :func:`config_entry` writes it.

    Read back with the keys ``ssh -G`` prints, lowercased, and the first value
    of each. Not a reader of SSH configs in general: one ``Host`` name a block,
    one value a key. A recorded ProxyJump is already resolved, so it stands as
    :attr:`Destination.jump` as well; an entry with no HostName or User is not
    one the setup wrote, and is left out.
    """
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        words = line.split(maxsplit=1)
        if not words or words[0].startswith("#"):
            continue
        key, value = words[0].lower(), (words[1].strip() if len(words) > 1 else "")
        if key == "host":
            current = blocks.setdefault(value, {})
        elif current is not None:
            current.setdefault(key, value.strip('"'))
    entries: dict[str, Destination] = {}
    for name, block in blocks.items():
        if _HOSTNAME not in block or _USER not in block:
            continue
        jump = block.get(_PROXY_JUMP)
        entries[name] = Destination(
            text=name,
            user=block[_USER],
            hostname=block[_HOSTNAME],
            port=block.get(_PORT, "22"),
            proxy_jump=jump,
            proxy_command=block.get(_PROXY_COMMAND),
            jump=jump,
        )
    return entries


def updated_config(current: str, name: str, entry: list[str], comment: str) -> str:
    """*current* with *name*'s entry set to *entry*, and only one entry of that name left.

    An entry that says the same keeps its comment; every other block stays as
    it was, and only the header's own lines are dropped, to be written afresh.
    """
    header = set(_CONFIG_HEADER.splitlines())
    kept: list[str] = []
    seen = False
    for block in current.strip("\n").split("\n\n"):
        lines = [line for line in block.split("\n") if line not in header]
        if not any(line.strip() for line in lines):
            continue
        body = [line for line in lines if not line.startswith("#")]
        if body[:1] != [f"Host {name}"]:
            kept.append("\n".join(lines))
        elif not seen:
            seen = True
            kept.append("\n".join(lines) if body == entry else "\n".join([comment, *entry]))
    if not seen:
        kept.append("\n".join([comment, *entry]))
    return _CONFIG_HEADER + "\n" + "\n\n".join(kept) + "\n"


def entry_comment(name: str, build_host: str, written: str) -> str:
    return f"# {name}: written by setup:remote-target on {build_host}, {written}. Running it again changes it."


def check_name(name: str) -> None:
    """Refuse a target's name that ssh could not carry as a host, or rsync as one before its ``:``."""
    if not _NAME.fullmatch(name):
        raise SetupError(f"tender-dev: a target's name is letters, digits, '.', '_' and '-' — not {name!r}.")


def check_arguments(name: str, destination: str) -> None:
    check_name(name)
    if not destination or destination.startswith("-"):
        raise SetupError(f"tender-dev: {destination!r} is no destination ssh could reach.")


def run_print(name: str, destination_text: str) -> int:
    destination = resolve(destination_text)
    if destination.via:
        raise SetupError(
            f"tender-dev: --print cannot set up a target reached through {destination.via}: the target sees that"
            " hop's address, which this machine cannot know.\n"
            "  Run the setup without --print, through a login the target accepts."
        )
    address = route_source(destination)
    directory = config_dir()
    build_host = build_host_name()
    if existing_key(directory) is None:
        with tempfile.TemporaryDirectory(prefix="romm-tender-dev-setup-") as scratch:
            new_key = generate_key(pathlib.Path(scratch), f"romm-tender-dev@{build_host}")
            try:
                install_key(new_key, directory)
            except OSError as error:
                raise SetupError(f"tender-dev: could not put the new key in {directory}: {reason(error)}.") from error
    public_key = read_text(directory / PUBLIC_KEY_NAME).strip()
    sys.stdout.write(
        install_script(
            public_key=public_key, build_host=build_host, written=stamp(), default_from=address, show_host_keys=True
        )
    )
    print(
        f"tender-dev: the script above sets {name} up; it restricts this machine's key to {address}.\n"
        "  Save it, copy it to the target and run it there with `sh <file>` — or `sh <file> <address>` if the\n"
        "  target sees this machine at another address. Then run here again, without --print:\n"
        f"    mise run setup:remote-target {name} {shlex.quote(destination_text)}",
        file=sys.stderr,
    )
    return 0


def setup_login(
    destination: Destination,
    *,
    name: str,
    script: str,
    known_hosts: pathlib.Path,
    key_config: pathlib.Path | None,
    host_key_recorded: bool,
) -> subprocess.CompletedProcess[str]:
    """One login with *script* on its stdin: with the dev loop's key alone, or with the user's own access.

    The key's login reads *key_config* alone, an entry :func:`config_entry`
    built for the key and *known_hosts*, so nothing of the user's SSH config —
    another IdentityFile among it — takes part. The user's own login reads the
    user's config, *known_hosts* given beside it, and no HostKeyAlias of ours:
    OpenSSH names that alias in its password prompt (``userauth_passwd`` in
    sshconnect2.c), where the user has to see the host. Its key is looked up
    under :attr:`Destination.key_name`, and :func:`under_alias` files it under
    the alias afterwards.

    Every login asks about a host key it has not recorded, whatever a config
    says, and opens a connection of its own: one shared through a ControlMaster
    would skip the host key check that fills *known_hosts*. The key's login
    asks for nothing but that host key — no password, no passphrase — and once
    the host key is recorded runs in batch mode, so it asks for nothing at all.
    Before that it cannot: the first login after ``--print`` has to show the
    fingerprint for the user to compare.
    """
    options = ["StrictHostKeyChecking=ask", "HashKnownHosts=no", "ControlMaster=no", "ControlPath=none"]
    if key_config is None:
        command = ["ssh", "-T"]
        options += [f'UserKnownHostsFile="{known_hosts}"', "GlobalKnownHostsFile=/dev/null", "BatchMode=no"]
        target = destination.text
    else:
        command = ["ssh", "-F", str(key_config), "-T"]
        options += [
            "IdentitiesOnly=yes",
            "PreferredAuthentications=publickey",
            f"BatchMode={'yes' if host_key_recorded else 'no'}",
        ]
        target = name
    for option in options:
        command += ["-o", option]
    command += ["--", target, "sh", "-s"]
    return run(command, stdin=script)


def installed_from(result: subprocess.CompletedProcess[str]) -> str | None:
    """The address the install restricted the key to, once it ran to its end."""
    return after_marker(result.stdout, INSTALLED)


def hand_over(result: subprocess.CompletedProcess[str], *, name: str, destination: Destination) -> HandOver | None:
    """Why the key's login gives way to the user's own, or ``None`` when its result stands.

    ``refused`` for the target's refusal of the key; ``forced command`` for a
    key the target took whose forced command ran no install (a dispatcher gone,
    or one that refuses the setup request); ``hop`` for a hop's refusal of the
    login, which the user's own config may pass (#2845 D2). An install that
    started stands, and so does every other failure: the user's own login is not
    tried then. Where the user's own login did the install, :func:`check_login`
    finds what only the user's config gets past (#2845 D3).
    """
    if installed_from(result) is not None or STARTED in result.stdout.splitlines():
        return None
    if result.returncode != SSH_FAILED:
        return "forced command"
    failure = ssh_failure(result.stderr, name=name, destination=destination)
    if failure is None:
        return None
    if failure.where == "target" and failure.key_refused:
        return "refused"
    if failure.where == "hop" and failure.kind == "no login":
        return "hop"
    return None


def check_login(name: str, config: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """A login to *name* through the dev loop's own *config* alone, sending an empty setup request.

    The target's leg runs in batch mode and asks for nothing: its host key is
    recorded by then. A hop's leg reads *config* too, but no entry the setup
    writes names the hop, and ``-o`` options apply to the destination only
    (ssh(1), ``-J``), so it may still ask, about its host key among others. It
    gets in exactly where the dev loop will.
    """
    command = ["ssh", "-F", str(config), "-T"]
    for option in ("BatchMode=yes", "ControlMaster=no", "ControlPath=none"):
        command += ["-o", option]
    return run([*command, "--", name, "sh", "-s"], stdin="")


def run_setup(name: str, destination_text: str) -> int:
    destination = with_jump(resolve(destination_text))
    directory = config_dir()
    key = existing_key(directory)
    known_hosts = directory / KNOWN_HOSTS_NAME
    config_path = directory / SSH_CONFIG_NAME
    build_host = build_host_name()
    written = stamp()
    final_entry = config_entry(name, destination, key=directory / KEY_NAME, known_hosts=known_hosts)

    with tempfile.TemporaryDirectory(prefix="romm-tender-dev-setup-") as scratch_name:
        scratch = pathlib.Path(scratch_name)
        new_key = None if key is not None else generate_key(scratch, f"romm-tender-dev@{build_host}")
        public_key = read_text((new_key or directory / KEY_NAME).with_name(PUBLIC_KEY_NAME)).strip()
        recorded = read_text(known_hosts) if known_hosts.exists() else ""
        trial_known_hosts = scratch / KNOWN_HOSTS_NAME
        trial_known_hosts.write_text(recorded, encoding="utf-8")
        host_key_recorded = is_recorded(recorded, host_key_alias(name))

        if not host_key_recorded:
            print(
                f"tender-dev: first contact with {name}: ssh shows its host key fingerprint. If you ran the script"
                " --print printed, go on only if it is one that script showed.",
                file=sys.stderr,
            )
        script = install_script(
            public_key=public_key, build_host=build_host, written=written, default_from="", show_host_keys=False
        )

        def log_in(key_config: pathlib.Path | None) -> subprocess.CompletedProcess[str]:
            return setup_login(
                destination,
                name=name,
                script=script,
                known_hosts=trial_known_hosts,
                key_config=key_config,
                host_key_recorded=host_key_recorded,
            )

        handed_over: HandOver | None = None
        key_result = None
        if key is not None:
            key_config = scratch / SSH_CONFIG_NAME
            entry = config_entry(name, destination, key=key, known_hosts=trial_known_hosts)
            key_config.write_text("\n".join(entry) + "\n", encoding="utf-8")
            key_result = log_in(key_config)
            handed_over = hand_over(key_result, name=name, destination=destination)
        if key_result is None or handed_over is not None:
            alias = host_key_alias(name)
            trial_known_hosts.write_text(
                with_key_name(trial_known_hosts.read_text(encoding="utf-8"), alias, destination.key_name),
                encoding="utf-8",
            )
            result = log_in(None)
        else:
            result = key_result

        installed = installed_from(result)
        if installed is None:
            raise SetupError(
                failure_message(
                    result,
                    name=name,
                    destination=destination,
                    handed_over=handed_over,
                    key_result=key_result,
                    known_hosts=known_hosts,
                    trial_known_hosts=trial_known_hosts,
                )
            )

        try:
            if new_key is not None:
                install_key(new_key, directory)
            trial = under_alias(
                trial_known_hosts.read_text(encoding="utf-8"), host_key_alias(name), destination.key_name
            )
            if trial != recorded:
                write_atomically(known_hosts, trial)
            current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
            config = updated_config(current, name, final_entry, entry_comment(name, build_host, written))
            if config != current:
                write_atomically(config_path, config)
        except (OSError, UnicodeError) as error:
            where = getattr(error, "filename", None) or directory
            raise SetupError(
                f"tender-dev: {name} is set up, but this machine's side could not be written — {where}:"
                f" {reason(error)}.\n"
                "  Run the setup again to finish it."
            ) from error

    check = check_login(name, config_path)
    if check.returncode != 0:
        raise SetupError(
            f"tender-dev: {name} is set up, but the dev loop cannot reach it with its own config"
            f" (ssh ended with {check.returncode}):\n{check.stderr.rstrip()}\n"
            f"  {config_path} carries none of your SSH config, a hop's settings among it. Its entry for {name}"
            " is written all the same."
        )
    print(
        f"tender-dev: {name} is set up. It accepts this machine's key from {installed} only — the address it"
        " sees this machine at; if that changes, run the setup again.",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="the name to give the target: letters, digits, '.', '_' and '-'")
    parser.add_argument("destination", help="user@address, or an alias from your SSH config")
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_script",
        help="print a script to run on the target instead of logging in",
    )
    arguments = parser.parse_args(argv)
    try:
        check_arguments(arguments.name, arguments.destination)
        if arguments.print_script:
            return run_print(arguments.name, arguments.destination)
        return run_setup(arguments.name, arguments.destination)
    except SetupError as refusal:
        print(refusal, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
