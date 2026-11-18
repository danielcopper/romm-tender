#!/usr/bin/env python3
"""Set a machine up as a dev target, or repair it: ``mise run setup:remote-target <name> <destination> [--print]``.

Contract: run on the build host. *destination* is anything the user's own
``ssh`` reaches — ``user@address``, or an alias from the user's SSH config,
which is read with ``ssh -G`` and never written. What the setup leaves, on both
machines, and how the dev loop uses it: docs/contributing/frontend-dev-loop.md,
"A remote target".

Without ``--print`` it logs in once through the user's own ``ssh``, offering the
dev loop's key last, after the user's own identities and before a password:

- a login of the user's own runs the install script, which installs the
  dispatcher and the one ``authorized_keys`` line restricting the key to the
  address ``$SSH_CLIENT`` shows;
- a login with the dev loop's key reaches the dispatcher, whose refusal of the
  install says the key works from here — the target is left as it is, and only
  this machine's side is brought up to date. That is what lets a target with no
  password login finish after ``--print``, and a target whose address changed
  be set up again without one.

Nothing is written on either machine before the login holds: a key this run
creates waits in a temporary directory, and the host key ``ssh`` records at
first contact goes to a temporary copy of the dev loop's known-hosts file. Each
way ``ssh`` can fail before the login is told apart by what ``ssh`` writes on
stderr, and ends the run with what to do next.

With ``--print`` it contacts nothing: it prints the same install script for the
target, restricted to the address this machine sends from towards it
(``ip route get``), and the run without ``--print`` finishes it.
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

CONFIG_DIR_NAME = "romm-tender-dev"
KEY_NAME = "id_ed25519"
SSH_CONFIG_NAME = "ssh_config"
KNOWN_HOSTS_NAME = "known_hosts"

# Where the printed script finds the host keys whose fingerprints it shows:
# OpenSSH's default HostKey paths.
HOST_KEY_DIR = "/etc/ssh"

# The dispatcher's answer to anything it does not let through
# (scripts/dev_dispatch.sh, REFUSED).
DISPATCHER_REFUSED = 3
DISPATCHER_REFUSAL = "tender-dev: refused:"

# The install script's last line on stdout. Searched for rather than expected
# alone: a login shell may print its own lines first.
INSTALLED = "tender-dev-setup: installed from="

# What ssh says on stderr for each failure that ends a login before it starts,
# in the order they are tested: a host key mismatch also prints "Host key
# verification failed", and a refused login can end in "Connection closed".
_HOST_KEY_CHANGED = re.compile(r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key for \S+ has changed")
_HOST_KEY_NOT_ACCEPTED = re.compile(r"Host key verification failed")
_NO_LOGIN = re.compile(r"Permission denied \(|Too many authentication failures")
_UNRESOLVED = re.compile(r"Could not resolve hostname")
_NO_SERVER = re.compile(
    r"Connection refused|Connection timed out|Operation timed out|No route to host|Network is unreachable"
    r"|kex_exchange_identification|Connection closed by|Connection reset"
)

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

_CONFIG_HEADER = (
    "# The dev loop's own SSH config: every --target call runs ssh with this file alone.\n"
    "# setup:remote-target writes one entry per target (docs/contributing/frontend-dev-loop.md).\n"
)

# The ssh -G keys a target entry is built from.
_USER = "user"
_HOSTNAME = "hostname"
_PORT = "port"
_PROXY_JUMP = "proxyjump"
_PROXY_COMMAND = "proxycommand"
_IDENTITY_FILE = "identityfile"


class SetupError(Exception):
    """A refusal: the message is what the user reads, and the run ends with it."""


@dataclass(frozen=True)
class Destination:
    """What ``ssh -G`` resolved a destination to."""

    user: str
    hostname: str
    port: str
    proxy_jump: str | None
    proxy_command: str | None
    identity_files: tuple[str, ...]


def config_dir() -> pathlib.Path:
    """The dev loop's directory on the build host: its key, SSH config and known-hosts file."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / CONFIG_DIR_NAME


def host_key_alias(name: str) -> str:
    """The name a target's host key is recorded under, in place of its address.

    An address can change and the machine behind it stays the same, so the
    record follows the target's name: a target at a new address keeps its host
    key, and another machine at a known address is still caught.
    """
    return f"romm-tender-dev.{name}"


def build_host_name() -> str:
    """This machine's name, as the comments on both machines give it."""
    return re.sub(r"[^A-Za-z0-9.-]", "-", socket.gethostname()) or "build-host"


def stamp() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")


def ssh_resolve(destination: str) -> dict[str, list[str]]:
    """``ssh -G``'s answer for *destination*: each key with every value it printed."""
    result = subprocess.run(["ssh", "-G", destination], capture_output=True, text=True, check=False)
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


def resolve(destination: str) -> Destination:
    answer = ssh_resolve(destination)
    hostname = _single(answer, _HOSTNAME)
    user = _single(answer, _USER)
    if hostname is None or user is None:
        raise SetupError(f"tender-dev: ssh resolved {destination} to no host or no user.")
    return Destination(
        user=user,
        hostname=hostname,
        port=_single(answer, _PORT) or "22",
        proxy_jump=_single(answer, _PROXY_JUMP),
        proxy_command=_single(answer, _PROXY_COMMAND),
        identity_files=tuple(answer.get(_IDENTITY_FILE, [])),
    )


def resolve_jump(proxy_jump: str) -> str:
    """*proxy_jump* with every hop spelled out as ``user@host:port``.

    The dev loop's config is read alone, so an alias from the user's config in
    a hop would name nothing there.
    """
    hops = []
    for hop in proxy_jump.split(","):
        resolved = resolve(hop)
        host = f"[{resolved.hostname}]" if ":" in resolved.hostname else resolved.hostname
        hops.append(f"{resolved.user}@{host}:{resolved.port}")
    return ",".join(hops)


def generate_key(directory: pathlib.Path, comment: str) -> pathlib.Path:
    path = directory / KEY_NAME
    result = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(f"tender-dev: ssh-keygen could not create a key:\n{result.stderr.rstrip()}")
    return path


def install_script(*, public_key: str, build_host: str, written: str, default_from: str, show_host_keys: bool) -> str:
    """The script that installs the dispatcher and the key's line on the target.

    *default_from* empty takes the address from ``$SSH_CLIENT``, which is the
    setup login's; ``--print`` writes the route's address in instead. A first
    argument overrides either.
    """
    dispatcher = DISPATCHER.read_text(encoding="utf-8")
    values = "\n".join(
        f"{name}={shlex.quote(value)}"
        for name, value in (
            ("public_key", public_key),
            ("build_host", build_host),
            ("written", written),
            ("default_from", default_from),
            ("show_host_keys", "yes" if show_host_keys else "no"),
            ("host_key_dir", HOST_KEY_DIR),
        )
    )
    return _INSTALL_TEMPLATE.replace("@VALUES@", values).replace("@DISPATCHER@", dispatcher.rstrip("\n"))


_INSTALL_TEMPLATE = """\
#!/bin/sh
# Sets this machine up as a Tender dev target: written by setup:remote-target
# (docs/contributing/frontend-dev-loop.md, "A remote target").
#
#   sh <this file> [address]
#
# Restricts the build host's key to [address] — the address this machine sees
# the build host at — or, without one, to the address shown before anything is
# written. Running it again changes what changed.
set -eu
umask 077

@VALUES@

from_address="${1:-$default_from}"
if [ -z "$from_address" ]; then
    from_address="${SSH_CLIENT%% *}"
fi
case "$from_address" in
    '' | *[!0-9A-Fa-f.:]*)
        printf 'tender-dev-setup: no address to restrict the key to (got "%s").\\n' "$from_address" >&2
        exit 1
        ;;
esac
printf 'tender-dev-setup: the key from %s will be accepted from %s only.\\n' "$build_host" "$from_address" >&2

root="$HOME/.local/lib/romm-tender-dev"
mkdir -p "$root/current" "$root/incoming"
cat > "$root/.dispatch.sh.new" <<'TENDER_DEV_DISPATCHER'
@DISPATCHER@
TENDER_DEV_DISPATCHER
chmod 755 "$root/.dispatch.sh.new"
mv -f "$root/.dispatch.sh.new" "$root/dispatch.sh"

key_comment="${public_key##* }"
key_blob="${public_key#* }"
key_blob="${key_blob%% *}"
line="from=\\"$from_address\\",command=\\"$root/dispatch.sh\\",restrict $public_key"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
keys="$HOME/.ssh/authorized_keys"
[ -f "$keys" ] || : > "$keys"
if grep -qxF -- "$line" "$keys"; then
    printf 'tender-dev-setup: %s already holds this line; left as it was.\\n' "$keys" >&2
else
    # Every line of this build host's key goes — by its key, by its comment, and
    # the comment line written above it — and every other line stays.
    awk -v blob="$key_blob" -v comment="$key_comment" '
        index($0, blob) { next }
        $NF == comment { next }
        $1 == "#" && $2 == comment ":" { next }
        { print }
    ' "$keys" > "$keys.tender-dev.new"
    printf '# %s: written by setup:remote-target from %s, %s. Running it again changes it.\\n%s\\n' \\
        "$key_comment" "$build_host" "$written" "$line" >> "$keys.tender-dev.new"
    chmod 600 "$keys.tender-dev.new"
    mv -f "$keys.tender-dev.new" "$keys"
fi

if [ "$show_host_keys" = yes ]; then
    printf '\\nThis machine'"'"'s host key fingerprints. The setup run again on %s shows one of them\\n' "$build_host"
    printf 'before it connects for the first time; go on only if it is one of these:\\n'
    shown=no
    for host_key in "$host_key_dir"/ssh_host_*_key.pub; do
        [ -f "$host_key" ] || continue
        ssh-keygen -lf "$host_key"
        shown=yes
    done
    [ "$shown" = yes ] || printf '  none found in %s — is an SSH server set up here?\\n' "$host_key_dir"
    printf '\\n'
fi
printf 'tender-dev-setup: installed from=%s\\n' "$from_address"
"""


def classify(stderr: str, *, name: str, destination: Destination, known_hosts: pathlib.Path) -> str | None:
    """What to tell the user for an ``ssh`` that ended before the login.

    ``None`` for a refused login, which ``no_login_message`` words because it
    depends on what was offered, and for anything that is no failure of ssh's.
    """
    host = destination.hostname
    if _HOST_KEY_CHANGED.search(stderr):
        return (
            f"tender-dev: the host key at {host} is not the one recorded for {name} — another machine may be"
            " answering at that address. No connection was made.\n"
            "  If the target was reinstalled, remove its record and run the setup again:\n"
            f"    ssh-keygen -R {host_key_alias(name)} -f {known_hosts}"
        )
    if _HOST_KEY_NOT_ACCEPTED.search(stderr):
        return f"tender-dev: the host key at {host} was not accepted, so no connection was made."
    if _NO_LOGIN.search(stderr):
        return None
    if _UNRESOLVED.search(stderr):
        return (
            f"tender-dev: this machine cannot resolve {host}.\n"
            "  Give the target's address instead, or a name the network resolves."
        )
    if _NO_SERVER.search(stderr):
        return (
            f"tender-dev: no SSH server answers at {host} port {destination.port}.\n"
            "  Check the address, and that the target runs an SSH server."
        )
    return None


def is_no_login(stderr: str) -> bool:
    return bool(_NO_LOGIN.search(stderr))


def route_source(hostname: str, port: str) -> str:
    """The address this machine sends from towards *hostname* (``ip route get``)."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise SetupError(
            f"tender-dev: this machine cannot resolve {hostname}.\n"
            "  Give the target's address instead, or a name the network resolves."
        ) from exc
    address = str(infos[0][4][0])
    result = subprocess.run(["ip", "route", "get", address], capture_output=True, text=True, check=False)
    words = result.stdout.split()
    if result.returncode != 0 or "src" not in words or words.index("src") + 1 >= len(words):
        raise SetupError(
            f"tender-dev: `ip route get {address}` named no address this machine sends from towards the target:\n"
            f"{(result.stderr or result.stdout).rstrip()}"
        )
    return words[words.index("src") + 1]


def no_login_message(destination_text: str, destination: Destination, key: pathlib.Path) -> str:
    message = (
        f"tender-dev: {destination_text} accepted no login from this machine.\n"
        "  Run the setup with --print, run the script it prints on the target, then run the setup here again."
    )
    if not key.exists() or destination.proxy_jump or destination.proxy_command:
        return message
    try:
        address = route_source(destination.hostname, destination.port)
    except SetupError:
        return message
    return (
        message + "\n  The dev loop's key was refused too. If the target was set up with the script --print printed,"
        f" the likely cause is the address it restricts the key to: {address}, the one this machine sends from"
        " towards the target. If the target sees this machine at another, run that script there again with that"
        " address as its argument."
    )


def write_atomically(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.new")
    with open(partial, "w", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        handle.write(text)
    os.replace(partial, path)


def config_entry(name: str, destination: Destination, jump: str | None, directory: pathlib.Path) -> list[str]:
    """The lines of a target's entry in the dev loop's SSH config, without its comment."""
    lines = [
        f"Host {name}",
        f"    HostName {destination.hostname}",
        f"    Port {destination.port}",
        f"    User {destination.user}",
    ]
    if jump:
        lines.append(f"    ProxyJump {jump}")
    lines += [
        f"    HostKeyAlias {host_key_alias(name)}",
        f'    IdentityFile "{directory / KEY_NAME}"',
        "    IdentitiesOnly yes",
        f'    UserKnownHostsFile "{directory / KNOWN_HOSTS_NAME}"',
        "    GlobalKnownHostsFile /dev/null",
        "    StrictHostKeyChecking yes",
        "    BatchMode yes",
    ]
    return lines


def updated_config(current: str, name: str, entry: list[str], comment: str) -> str:
    """*current* with *name*'s entry set to *entry*; an entry that says the same keeps its comment."""
    blocks = [block for block in current.strip("\n").split("\n\n") if block.strip()]
    kept: list[str] = []
    replaced = False
    for block in blocks:
        lines = block.split("\n")
        body = [line for line in lines if not line.startswith("#")]
        if body and body[0] == f"Host {name}":
            replaced = True
            kept.append(block if body == entry else "\n".join([comment, *entry]))
        elif block.startswith(_CONFIG_HEADER.split("\n", 1)[0]):
            continue
        else:
            kept.append(block)
    if not replaced:
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
    if destination.proxy_jump or destination.proxy_command:
        via = destination.proxy_jump or destination.proxy_command
        raise SetupError(
            f"tender-dev: --print cannot set up a target reached through {via}: the target sees that hop's address,"
            " which this machine cannot know.\n"
            "  Run the setup without --print, through a login the target accepts."
        )
    address = route_source(destination.hostname, destination.port)
    directory = config_dir()
    build_host = build_host_name()
    key = directory / KEY_NAME
    if not key.exists():
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        generate_key(directory, f"romm-tender-dev@{build_host}")
    public_key = key.with_name(KEY_NAME + ".pub").read_text(encoding="utf-8").strip()
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
    destination_text: str,
    destination: Destination,
    *,
    name: str,
    script: str,
    known_hosts: pathlib.Path,
    key: pathlib.Path | None,
) -> subprocess.CompletedProcess[str]:
    """The one login through the user's own ssh, with *script* on its stdin."""
    options = [
        f'UserKnownHostsFile="{known_hosts}"',
        "GlobalKnownHostsFile=/dev/null",
        "HashKnownHosts=no",
        f"HostKeyAlias={host_key_alias(name)}",
    ]
    if key is not None:
        # An IdentityFile given here replaces ssh's default identities rather
        # than joining them (ssh_config(5), IdentityFile), so the ones ssh -G
        # resolved are named first and the dev loop's key after them.
        options += [f'IdentityFile="{path}"' for path in destination.identity_files]
        options.append(f'IdentityFile="{key}"')
    command = ["ssh", "-T"]
    for option in options:
        command += ["-o", option]
    command += ["--", destination_text, "sh", "-s"]
    return subprocess.run(command, input=script, capture_output=True, text=True, check=False)


def run_setup(name: str, destination_text: str) -> int:
    destination = resolve(destination_text)
    jump = resolve_jump(destination.proxy_jump) if destination.proxy_jump else None
    directory = config_dir()
    key = directory / KEY_NAME
    known_hosts = directory / KNOWN_HOSTS_NAME
    build_host = build_host_name()
    written = stamp()

    with tempfile.TemporaryDirectory(prefix="romm-tender-dev-setup-") as scratch_name:
        scratch = pathlib.Path(scratch_name)
        new_key = None if key.exists() else generate_key(scratch, f"romm-tender-dev@{build_host}")
        public_key = (new_key or key).with_name(KEY_NAME + ".pub").read_text(encoding="utf-8").strip()
        trial_known_hosts = scratch / KNOWN_HOSTS_NAME
        recorded = known_hosts.read_text(encoding="utf-8") if known_hosts.exists() else ""
        trial_known_hosts.write_text(recorded, encoding="utf-8")

        if not known_hosts.exists() or host_key_alias(name) not in recorded:
            print(
                f"tender-dev: first contact with {name}: ssh shows its host key fingerprint. If you ran the script"
                " --print printed, go on only if it is one that script showed.",
                file=sys.stderr,
            )
        script = install_script(
            public_key=public_key, build_host=build_host, written=written, default_from="", show_host_keys=False
        )
        result = setup_login(
            destination_text,
            destination,
            name=name,
            script=script,
            known_hosts=trial_known_hosts,
            key=None if new_key else key,
        )

        installed = next(
            (line[len(INSTALLED) :] for line in result.stdout.splitlines() if line.startswith(INSTALLED)), None
        )
        reached_dispatcher = result.returncode == DISPATCHER_REFUSED and DISPATCHER_REFUSAL in result.stderr
        if installed is None and not reached_dispatcher:
            failure = classify(result.stderr, name=name, destination=destination, known_hosts=known_hosts)
            if failure is None and is_no_login(result.stderr):
                failure = no_login_message(destination_text, destination, key)
            if failure is not None:
                raise SetupError(failure + "\n  Nothing was changed, here or on the target.")
            raise SetupError(
                f"tender-dev: the setup on {name} failed (ssh ended with {result.returncode}):\n"
                f"{result.stderr.rstrip()}\n"
                "  Nothing was changed on this machine."
            )

        if new_key is not None:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            for source in (new_key, new_key.with_name(KEY_NAME + ".pub")):
                shutil.move(source, directory / source.name)
        trial = trial_known_hosts.read_text(encoding="utf-8")
        if trial != recorded:
            write_atomically(known_hosts, trial)

    config_path = directory / SSH_CONFIG_NAME
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config = updated_config(
        current, name, config_entry(name, destination, jump, directory), entry_comment(name, build_host, written)
    )
    if config != current:
        write_atomically(config_path, config)

    if installed is not None:
        print(
            f"tender-dev: {name} is set up. It accepts this machine's key from {installed} only — the address it"
            " sees this machine at; if that changes, run the setup again.",
            file=sys.stderr,
        )
    else:
        print(
            f"tender-dev: {name} accepts this machine's key, and its install there was left as it was; this"
            f" machine's entry for it is up to date. To install it there afresh, run the setup with --print.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="the target's name, as --target takes it")
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
