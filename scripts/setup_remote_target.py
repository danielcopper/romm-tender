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
from dataclasses import dataclass

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
_HOST_KEY_CHANGED = re.compile(r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key for \S+ has changed")
_OFFENDING_FILE = re.compile(r"Offending \S+ key in (\S+):\d+")
_HOST_KEY_NOT_ACCEPTED = re.compile(r"Host key verification failed")
_KEY_REFUSED = re.compile(r"Permission denied \(")
_NO_LOGIN = re.compile(_KEY_REFUSED.pattern + r"|Too many authentication failures")
_UNRESOLVED = re.compile(r"Could not resolve hostname ([^:\s]+)")
_NO_SERVER = re.compile(
    r"Connection refused|Connection timed out|Operation timed out|No route to host|Network is unreachable"
    r"|kex_exchange_identification|Connection closed by|Connection reset"
)

# Where ssh names the host a failure is about: a refused login as user@host, a
# connection as "connect to host <host> port", a name and a changed key by it.
_NAMED_HOSTS = (
    re.compile(r"\S+@(\S+): Permission denied \("),
    re.compile(r"connect to host (\S+) port"),
    _UNRESOLVED,
    re.compile(r"Host key for (\S+) has changed"),
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
    """What ``ssh -G`` resolved :attr:`text` to."""

    text: str
    user: str
    hostname: str
    port: str
    proxy_jump: str | None
    proxy_command: str | None

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


def failed_where(stderr: str, *, name: str, destination: Destination) -> str:
    """Whose failure ssh reported: ``target``, ``hop``, or ``either`` where it names no host."""
    if not destination.via:
        return "target"
    named = {match.group(1) for pattern in _NAMED_HOSTS for match in pattern.finditer(stderr)}
    if named & {destination.hostname, host_key_alias(name), name}:
        return "target"
    return "hop" if named else "either"


def classify(
    stderr: str, *, name: str, destination: Destination, known_hosts: pathlib.Path, trial_known_hosts: pathlib.Path
) -> str | None:
    """What to tell the user for an ``ssh`` that ended before the login.

    ``None`` for the target's refusal of the login, which ``no_login_message``
    words, and for anything that is no failure of ssh's. A failure ssh names on
    a hop is worded as the hop's; one it names on no host, as the target's or
    the hop's.
    """
    host = destination.hostname
    where = failed_where(stderr, name=name, destination=destination)
    hop = f"the hop {destination.via}"
    if _HOST_KEY_CHANGED.search(stderr):
        offending = _OFFENDING_FILE.search(stderr)
        ours = (str(known_hosts), str(trial_known_hosts))
        if offending is None or offending.group(1) in ours:
            return (
                f"tender-dev: the host key at {host} is not the one recorded for {name} — another machine may be"
                " answering at that address. No connection was made.\n"
                "  If the target was reinstalled, remove its record and run the setup again:\n"
                f"    ssh-keygen -R {host_key_alias(name)} -f {known_hosts}"
            )
        return (
            f"tender-dev: a hop on the way to {name} ({destination.via}) has a host key that is not the one"
            f" {offending.group(1)} records — another machine may be answering there. No connection was made.\n"
            "  Check that hop with your own ssh."
        )
    if _HOST_KEY_NOT_ACCEPTED.search(stderr):
        whose = {"target": f"at {host}", "hop": f"of {hop}", "either": f"of {host} or of {hop}"}[where]
        return (
            f"tender-dev: the host key {whose} was not accepted, or ssh could not ask about it — it asks on a"
            " terminal. No connection was made."
        )
    if _NO_LOGIN.search(stderr):
        if where == "hop":
            return f"tender-dev: {hop} accepted no login from this machine.\n  Check that hop with your own ssh."
        return None
    unresolved = _UNRESOLVED.search(stderr)
    if unresolved is not None and destination.via and unresolved.group(1) != host:
        return (
            f"tender-dev: this machine cannot resolve {unresolved.group(1)}, a hop on the way to {name}"
            f" ({destination.via}).\n"
            "  Check that hop in your SSH config, or give its address."
        )
    if unresolved is not None:
        return (
            f"tender-dev: this machine cannot resolve {host}.\n"
            "  Give the target's address instead, or a name the network resolves."
        )
    if _NO_SERVER.search(stderr) and where == "hop":
        return f"tender-dev: no SSH server answers at {hop}.\n  Check that hop with your own ssh."
    if _NO_SERVER.search(stderr) and where == "either":
        return (
            f"tender-dev: no SSH server answers at {host} port {destination.port}, or at {hop}.\n"
            "  Check the address, that the target runs an SSH server, and that hop."
        )
    if _NO_SERVER.search(stderr):
        return (
            f"tender-dev: no SSH server answers at {host} port {destination.port}.\n"
            "  Check the address, and that the target runs an SSH server."
        )
    return None


def is_no_login(stderr: str) -> bool:
    return bool(_NO_LOGIN.search(stderr))


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


def failure_message(
    result: subprocess.CompletedProcess[str],
    *,
    name: str,
    destination: Destination,
    key_refused: bool,
    known_hosts: pathlib.Path,
    trial_known_hosts: pathlib.Path,
) -> str:
    """What to tell the user for a setup login that did not end with the install done."""
    ssh_said = result.stderr.rstrip()
    stopped = next((line[len(STOPPED) :] for line in result.stderr.splitlines() if line.startswith(STOPPED)), None)
    if stopped is not None:
        return f"tender-dev: the setup stopped on {name}: {stopped}\n  This machine is unchanged."
    if STARTED in result.stdout.splitlines():
        return (
            f"tender-dev: the setup stopped part-way on {name} (ssh ended with {result.returncode}):\n{ssh_said}\n"
            "  This machine is unchanged; run the setup again to finish it."
        )
    if result.returncode == SSH_FAILED:
        failure = classify(
            result.stderr,
            name=name,
            destination=destination,
            known_hosts=known_hosts,
            trial_known_hosts=trial_known_hosts,
        )
        if failure is None and is_no_login(result.stderr):
            failure = no_login_message(destination, key_refused=key_refused)
        if failure is not None:
            return failure + "\n  Nothing was changed, here or on the target."
    return (
        f"tender-dev: the setup did not start on {name} (ssh ended with {result.returncode}):\n{ssh_said}\n"
        "  Nothing was changed, here or on the target."
    )


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

    A ProxyJump goes in resolved (:func:`resolve_jump`); a ProxyCommand as
    ``ssh -G`` printed it, a ``%h`` or ``%p`` left in it expanding against this
    entry's HostName and Port when it runs.
    """
    lines = [
        f"Host {name}",
        f"    HostName {destination.hostname}",
        f"    Port {destination.port}",
        f"    User {destination.user}",
    ]
    if destination.proxy_jump:
        lines.append(f"    ProxyJump {resolve_jump(destination.proxy_jump)}")
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


def check_arguments(name: str, destination: str) -> None:
    if not _NAME.fullmatch(name):
        raise SetupError(f"tender-dev: a target's name is letters, digits, '.', '_' and '-' — not {name!r}.")
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
    user's config, *known_hosts* given beside it.

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
        options += [
            f'UserKnownHostsFile="{known_hosts}"',
            "GlobalKnownHostsFile=/dev/null",
            f"HostKeyAlias={host_key_alias(name)}",
            "BatchMode=no",
        ]
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
    return next((line[len(INSTALLED) :] for line in result.stdout.splitlines() if line.startswith(INSTALLED)), None)


def hand_over(result: subprocess.CompletedProcess[str], *, name: str, destination: Destination) -> str | None:
    """Why the key's login gives way to the user's own, or ``None`` when its result stands.

    ``refused`` for the target's refusal of the key; ``forced command`` for a
    key the target took whose forced command ran no install (a dispatcher gone,
    or one that refuses the setup request); ``hop`` for a failure on a hop, which
    the user's own config may pass. Any other failure, and an install that
    started, stand: the user's own login would meet them too.
    """
    if installed_from(result) is not None or STARTED in result.stdout.splitlines():
        return None
    if result.returncode != SSH_FAILED:
        return "forced command"
    where = failed_where(result.stderr, name=name, destination=destination)
    if where == "target" and _KEY_REFUSED.search(result.stderr):
        return "refused"
    if where == "hop":
        return "hop"
    return None


def run_setup(name: str, destination_text: str) -> int:
    destination = resolve(destination_text)
    directory = config_dir()
    key = existing_key(directory)
    known_hosts = directory / KNOWN_HOSTS_NAME
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

        handed_over = None
        key_result = None
        if key is not None:
            key_config = scratch / SSH_CONFIG_NAME
            entry = config_entry(name, destination, key=key, known_hosts=trial_known_hosts)
            key_config.write_text("\n".join(entry) + "\n", encoding="utf-8")
            key_result = log_in(key_config)
            handed_over = hand_over(key_result, name=name, destination=destination)
        result = log_in(None) if key_result is None or handed_over is not None else key_result

        installed = installed_from(result)
        if installed is None:
            message = failure_message(
                result,
                name=name,
                destination=destination,
                key_refused=handed_over == "refused",
                known_hosts=known_hosts,
                trial_known_hosts=trial_known_hosts,
            )
            if handed_over == "forced command" and key_result is not None:
                message += (
                    f"\n  {name} accepted the dev loop's key, but its forced command failed"
                    f" (ssh ended with {key_result.returncode}):\n{key_result.stderr.rstrip()}"
                )
            raise SetupError(message)

        try:
            if new_key is not None:
                install_key(new_key, directory)
            trial = trial_known_hosts.read_text(encoding="utf-8")
            if trial != recorded:
                write_atomically(known_hosts, trial)
            config_path = directory / SSH_CONFIG_NAME
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
