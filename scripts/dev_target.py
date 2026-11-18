#!/usr/bin/env python3
"""Run a dev task on a target: build here, copy the tree there, and do the rest there.

    python scripts/dev_target.py --target <name> [--surface] [--follow] [--stop] -- <task> [argument]

The dev tasks call this when given ``--target``; <task> is the task's name
(``dev``, ``dev:bpm``, ... ``dev:status``) and [argument] its display or mode.
The target is one ``mise run setup:remote-target`` prepared, and every ``ssh``
and ``rsync`` here reads the dev loop's own SSH config and nothing else.

What each task does on the target, and what each refusal means:
docs/contributing/frontend-dev-loop.md, "The dev tasks on a target".
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import setup_remote_target as setup

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGE_SCRIPT = REPO / "scripts" / "package.sh"
INCOMING = f"{setup.TARGET_ROOT}/incoming/"
DEV_TARGET = "scripts/dev_target.sh"

# What scripts/dev_target.sh prints alone on stdout once the rest is output being
# followed: Ctrl-C from then on ends only the following.
FOLLOWING = "tender-dev-target: following"

# How the dispatcher opens a refusal (scripts/dev_dispatch.sh, refuse).
DISPATCHER_REFUSED = "tender-dev: refused: "

# The characters the dispatcher lets through in a word for dev_target.sh.
_WORD = re.compile(r"[A-Za-z0-9._,:=+-]+")
_SHIPPED = re.compile(r"^SHIPPED=\(([^)]*)\)", re.MULTILINE)

STARTS_BACKEND = ("dev", "dev:bpm", "dev:desktop", "dev:backend")
BUILDS = (*STARTS_BACKEND, "dev:frontend")
TASKS = (*BUILDS, "dev:bpm-reset", "dev:desktop-reset", "dev:ui-scale", "dev:stop", "dev:log", "dev:status")

INTERRUPTED = 130


class TargetError(Exception):
    """A refusal on this machine: the message is what the user reads, and the task ends with it."""


@dataclass(frozen=True)
class Target:
    name: str
    config: pathlib.Path
    destination: setup.Destination


@dataclass(frozen=True)
class Outcome:
    returncode: int
    stderr: str
    following: bool
    interrupted: bool


def shipped() -> list[str]:
    """``SHIPPED`` in ``scripts/package.sh``: the paths a release carries."""
    match = _SHIPPED.search(setup.read_text(PACKAGE_SCRIPT))
    if match is None:
        raise TargetError(f"tender-dev: {PACKAGE_SCRIPT} names no SHIPPED list to copy.")
    return match.group(1).split()


def copied_tree() -> list[str]:
    """The top-level paths copied to a target: what a release ships, and the scripts that run there."""
    return [*shipped(), "scripts"]


def config_entries(text: str) -> dict[str, dict[str, str]]:
    """Each ``Host`` block of a dev loop SSH config: its keys, lowercased, with their first value."""
    entries: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        words = line.split(maxsplit=1)
        if not words or words[0].startswith("#"):
            continue
        key = words[0].lower()
        value = words[1].strip() if len(words) > 1 else ""
        if key == "host":
            current = {}
            for host in value.split():
                entries.setdefault(host, current)
        elif current is not None:
            current.setdefault(key, value.strip('"'))
    return entries


def find_target(name: str) -> Target:
    config = setup.config_dir() / setup.SSH_CONFIG_NAME
    if not config.exists():
        raise TargetError(
            f"tender-dev: no target named {name} — this machine has set up none ({config} is missing).\n"
            f"  Set it up first: mise run setup:remote-target {name} <destination>"
        )
    entries = config_entries(setup.read_text(config))
    entry = entries.get(name)
    if entry is None or "hostname" not in entry:
        known = ", ".join(sorted(entries)) or "none"
        raise TargetError(
            f"tender-dev: no target named {name} in {config} (set up: {known}).\n"
            f"  Set it up first: mise run setup:remote-target {name} <destination>"
        )
    destination = setup.Destination(
        text=name,
        user=entry.get("user", ""),
        hostname=entry["hostname"],
        port=entry.get("port", "22"),
        proxy_jump=entry.get("proxyjump"),
        proxy_command=entry.get("proxycommand"),
    )
    return Target(name, config, destination)


def ssh(target: Target) -> list[str]:
    return ["ssh", "-F", str(target.config), "-T"]


def setup_hint(target: Target) -> str:
    destination = target.destination
    if destination.via or destination.port != "22":
        return f"mise run setup:remote-target {target.name} <destination>"
    return f"mise run setup:remote-target {target.name} {destination.user}@{destination.hostname}"


def connection_failure(stderr: str, target: Target) -> str | None:
    """What to tell the user for an ``ssh`` that ended before the login, or ``None`` for anything else.

    A failure is the target's unless ssh names a hop as the one that failed.
    """
    name = target.name
    destination = target.destination
    nothing_done = f"  Nothing was done on {name}."
    if setup.failed_where(stderr, name=name, destination=destination) == "hop" and (
        setup.HOST_KEY_CHANGED.search(stderr)
        or setup.HOST_KEY_NOT_ACCEPTED.search(stderr)
        or setup.NO_LOGIN.search(stderr)
        or setup.UNRESOLVED.search(stderr)
        or setup.NO_SERVER.search(stderr)
    ):
        return (
            f"tender-dev: a hop on the way to {name} ({destination.via}) failed — ssh said:\n{stderr.rstrip()}\n"
            f"  Check that hop with your own ssh.\n{nothing_done}"
        )
    if setup.HOST_KEY_CHANGED.search(stderr):
        known_hosts = setup.config_dir() / setup.KNOWN_HOSTS_NAME
        return (
            f"tender-dev: the host key at {destination.hostname} is not the one recorded for {name} — another"
            " machine answers at that address. No connection was made.\n"
            f"  If {name} was reinstalled, remove its record and set it up again:\n"
            f"    ssh-keygen -R {setup.host_key_alias(name)} -f {known_hosts}\n"
            f"    {setup_hint(target)}\n{nothing_done}"
        )
    if setup.HOST_KEY_NOT_ACCEPTED.search(stderr):
        return (
            f"tender-dev: {name}'s host key is not recorded on this machine, so no connection was made.\n"
            f"  Set it up again: {setup_hint(target)}\n{nothing_done}"
        )
    if setup.NO_LOGIN.search(stderr):
        return (
            f"tender-dev: {name} refused this machine's key. It accepts the key from one address only, and this"
            f" machine's may have changed — or {name} no longer holds the key's line.\n"
            f"  Set it up again: {setup_hint(target)}\n{nothing_done}"
        )
    if setup.UNRESOLVED.search(stderr) or setup.NO_SERVER.search(stderr):
        hop = f", or at the hop {destination.via}" if destination.via else ""
        return (
            f"tender-dev: no answer from {name} at {destination.hostname} port {destination.port}{hop}.\n"
            f"  It may have a new address; set it up with that one:\n"
            f"    mise run setup:remote-target {name} <new address>\n{nothing_done}"
        )
    return None


def dispatcher_refusal(stderr: str, target: Target) -> str | None:
    """The hint for a request the target's dispatcher refused: one older than this checkout's."""
    if DISPATCHER_REFUSED not in stderr:
        return None
    return (
        f"tender-dev: {target.name}'s dispatcher refused the request, so it is older than this checkout's.\n"
        f"  Set it up again, which puts the current one in place: {setup_hint(target)}"
    )


def check_word(word: str) -> str:
    if not _WORD.fullmatch(word):
        raise TargetError(f"tender-dev: {word!r} cannot be sent to a target: letters, digits and . _ , : = + - only.")
    return word


def build(target: Target, *, surface: bool) -> None:
    result = subprocess.run(["mise", "run", "build", *(["--surface"] if surface else [])], cwd=REPO, check=False)
    if result.returncode != 0:
        raise TargetError(f"tender-dev: the build failed; nothing was copied to {target.name}.")


def copy(target: Target) -> None:
    """Mirror :func:`copied_tree` into the target's incoming copy, beside the running tree.

    The filter names the tree from that one list, and ``--delete-excluded``
    drops from the copy whatever the list no longer names. Compiled modules
    stay here: the target's Python is not this machine's.
    """
    rules = ["--exclude=__pycache__", *(f"--include=/{entry}" for entry in copied_tree()), "--exclude=/*"]
    remote_shell = " ".join(shlex.quote(word) for word in ssh(target))
    command = ["rsync", "-a", "--delete", "--delete-excluded", "-e", remote_shell, *rules]
    try:
        result = subprocess.run(
            [*command, f"{REPO}/", f"{target.name}:{INCOMING}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise TargetError("tender-dev: a --target task needs rsync, and this machine has none on its PATH.") from error
    if result.returncode == 0:
        return
    failure = connection_failure(result.stderr, target) or dispatcher_refusal(result.stderr, target)
    raise TargetError(
        failure
        or f"tender-dev: the copy to {target.name} failed (rsync ended with {result.returncode}):\n"
        f"{result.stderr.rstrip()}"
    )


def run_streaming(command: list[str]) -> Outcome:
    """*command* run with its output passed on as it comes, and its stderr kept besides.

    The line :data:`FOLLOWING` is taken out of stdout and noted. On Ctrl-C the
    terminal has sent ``ssh`` the same signal; letting go of its output ends
    whatever on the far side still writes to it.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    errors: list[str] = []
    stderr = process.stderr

    def relay_errors() -> None:
        for line in stderr:
            errors.append(line)
            sys.stderr.write(line)
            sys.stderr.flush()

    relay = threading.Thread(target=relay_errors, daemon=True)
    relay.start()
    following = False
    try:
        for line in process.stdout:
            if line.rstrip("\n") == FOLLOWING:
                following = True
                continue
            sys.stdout.write(line)
            sys.stdout.flush()
        process.wait()
    except KeyboardInterrupt:
        process.stdout.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return Outcome(process.returncode, "".join(errors), following, interrupted=True)
    relay.join()
    return Outcome(process.returncode, "".join(errors), following, interrupted=False)


def still_running(target: Target, unit: str) -> bool | None:
    """Whether *unit* (``backend`` or ``ui-scale``) runs on the target, or ``None`` where that could not be asked."""
    result = subprocess.run(
        [*ssh(target), "--", target.name, DEV_TARGET, "running", target.name, unit],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return {0: True, 1: False}.get(result.returncode)


def after_following(task: str, target: Target, *, follow: bool = False) -> str:
    """What to say once the following has ended here: what still runs there, as the target answers."""
    name = target.name
    unknown = f" Whether it still runs could not be asked; `mise run dev:status --target {name}` says what runs there."
    if task == "dev:log" and follow:
        return f"tender-dev: stopped following the log on {name}."
    if task == "dev:ui-scale":
        stopped = "tender-dev: stopped following dev:ui-scale."
        running = still_running(target, "ui-scale")
        if running is None:
            return stopped + unknown
        if not running:
            return f"{stopped} dev:ui-scale no longer runs on {name}."
        return (
            f"{stopped} It keeps running on {name}, holding the scale;"
            f" `mise run dev:ui-scale --stop --target {name}` restores the scale and the window."
        )
    stopped = "tender-dev: stopped following the log."
    running = still_running(target, "backend")
    if running is None:
        return stopped + unknown
    if not running:
        return (
            f"{stopped} The backend no longer runs on {name}; `mise run dev:log --target {name}` shows what it logged."
        )
    return (
        f"{stopped} The backend keeps running on {name};"
        f" `mise run dev:stop --target {name}` stops it, and `mise run dev:log --follow --target {name}`"
        " follows its log again."
    )


def on_target(target: Target, task: str, words: list[str], *, follow: bool = False) -> int:
    outcome = run_streaming([*ssh(target), "--", target.name, *words])
    if outcome.interrupted and not outcome.following and not (task == "dev:log" and follow):
        print(
            f"tender-dev: interrupted. What the task started on {target.name} may have stopped part-way;"
            f" `mise run dev:status --target {target.name}` says what runs there.",
            file=sys.stderr,
        )
        return INTERRUPTED
    if outcome.interrupted or (outcome.following and outcome.returncode == 0):
        print(after_following(task, target, follow=follow), file=sys.stderr)
        return 0
    if outcome.returncode != 0 and not outcome.following:
        explained = connection_failure(outcome.stderr, target) if outcome.returncode == setup.SSH_FAILED else None
        explained = explained or dispatcher_refusal(outcome.stderr, target)
        if explained is not None:
            print(explained, file=sys.stderr)
    return outcome.returncode


def remote_words(task: str, name: str, argument: str | None, *, stop: bool) -> list[str]:
    """The words :data:`DEV_TARGET` is sent for *task*."""
    extra = [check_word(argument)] if argument else []
    words = {
        "dev": ["start", name, "restart"],
        "dev:bpm": ["start", name, "restart", "bpm", *extra],
        "dev:desktop": ["start", name, "restart", "desktop", *extra],
        "dev:backend": ["start", name],
        "dev:frontend": ["frontend", name, *extra],
        "dev:bpm-reset": ["reset", name, "bpm", *extra],
        "dev:desktop-reset": ["reset", name, "desktop", *extra],
        "dev:ui-scale": ["ui-scale-stop", name] if stop else ["ui-scale", name, *(extra or ["deck"])],
        "dev:stop": ["stop", name],
        "dev:status": ["status", name],
    }[task]
    return [DEV_TARGET, *words]


def run(arguments: argparse.Namespace) -> int:
    target = find_target(check_word(arguments.target))
    task: str = arguments.task
    argument = arguments.argument or None
    if task == "dev:log":
        return on_target(target, task, ["follow" if arguments.follow else "read", "log"], follow=arguments.follow)
    words = remote_words(task, target.name, argument, stop=arguments.stop)
    if task in BUILDS:
        build(target, surface=arguments.surface)
    copy(target)
    return on_target(target, task, words)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, help="the target's name, as setup:remote-target recorded it")
    parser.add_argument("--surface", action="store_true", help="build the desktop surface into the panel")
    parser.add_argument("--follow", action="store_true", help="dev:log: follow the log rather than show it once")
    parser.add_argument("--stop", action="store_true", help="dev:ui-scale: stop the one running on the target")
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("argument", nargs="?", help="the task's display or mode")
    try:
        return run(parser.parse_args(argv))
    except TargetError as refusal:
        print(refusal, file=sys.stderr)
        return 1
    except setup.SetupError as refusal:
        print(refusal, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("tender-dev: interrupted.", file=sys.stderr)
        return INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
