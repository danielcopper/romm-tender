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
from typing import Literal, get_args

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

# The characters the dispatcher lets through in a word for dev_target.sh
# (scripts/dev_dispatch.sh, check_words).
_WORD = re.compile(r"[A-Za-z0-9._,:=+-]+")
_SHIPPED = re.compile(r"^SHIPPED=\(([^)]*)\)", re.MULTILINE)

Task = Literal[
    "dev",
    "dev:bpm",
    "dev:desktop",
    "dev:backend",
    "dev:frontend",
    "dev:bpm-reset",
    "dev:desktop-reset",
    "dev:ui-scale",
    "dev:stop",
    "dev:log",
    "dev:status",
]
TASKS: tuple[Task, ...] = get_args(Task)

Follows = Literal["backend", "ui-scale", "log"]


@dataclass(frozen=True)
class TaskSpec:
    """What a task asks of the target: :data:`DEV_TARGET`'s verb and words, and what it then follows."""

    verb: str
    words: tuple[str, ...] = ()
    takes_argument: bool = False
    builds: bool = False
    follows: Follows | None = None


SPECS: dict[Task, TaskSpec] = {
    "dev": TaskSpec("start", ("restart",), builds=True, follows="backend"),
    "dev:bpm": TaskSpec("start", ("restart", "bpm"), takes_argument=True, builds=True, follows="backend"),
    "dev:desktop": TaskSpec("start", ("restart", "desktop"), takes_argument=True, builds=True, follows="backend"),
    "dev:backend": TaskSpec("start", builds=True, follows="backend"),
    "dev:frontend": TaskSpec("frontend", takes_argument=True, builds=True),
    "dev:bpm-reset": TaskSpec("reset", ("bpm",), takes_argument=True),
    "dev:desktop-reset": TaskSpec("reset", ("desktop",), takes_argument=True),
    "dev:ui-scale": TaskSpec("ui-scale", takes_argument=True, follows="ui-scale"),
    "dev:stop": TaskSpec("stop"),
    "dev:status": TaskSpec("status"),
}
"""Every task but ``dev:log``, which reads through the dispatcher and copies nothing."""

UI_SCALE_STOP = TaskSpec("ui-scale-stop")

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


def find_target(name: str) -> Target:
    """*name*'s entry in the dev loop's SSH config.

    Its :class:`setup.Destination` is the recorded entry read back, not one
    ``ssh -G`` resolved: what the messages here name, and the hop ssh may blame.
    """
    setup.check_name(name)
    config = setup.config_dir() / setup.SSH_CONFIG_NAME
    if not config.exists():
        raise TargetError(
            f"tender-dev: no target named {name} — this machine has set up none ({config} is missing).\n"
            f"  Set it up first: mise run setup:remote-target {name} <destination>"
        )
    entries = setup.read_entries(setup.read_text(config))
    destination = entries.get(name)
    if destination is None:
        known = ", ".join(sorted(entries)) or "none"
        raise TargetError(
            f"tender-dev: no target named {name} in {config} (set up: {known}).\n"
            f"  Set it up first: mise run setup:remote-target {name} <destination>"
        )
    return Target(name, config, destination)


# A target that stops answering — a Deck gone to sleep, a network gone — ends the
# call within a minute rather than leaving it waiting with nothing said. Given
# here rather than in the dev loop's config, so targets set up before them get
# them too.
KEEPALIVE = ("ServerAliveInterval=15", "ServerAliveCountMax=3", "ConnectTimeout=10")


def ssh(target: Target) -> list[str]:
    command = ["ssh", "-F", str(target.config), "-T"]
    for option in KEEPALIVE:
        command += ["-o", option]
    return command


def setup_hint(target: Target) -> str:
    destination = target.destination
    if destination.via or destination.port != "22":
        return f"mise run setup:remote-target {target.name} <destination>"
    return f"mise run setup:remote-target {target.name} {destination.user}@{destination.hostname}"


def connection_failure(stderr: str, target: Target) -> str | None:
    """What to tell the user for an ``ssh`` that ended before the login, or ``None`` for anything else.

    :func:`setup.ssh_failure` reads the failure and its leg; the caller shows
    what ssh said beside the hint, which is a reading of that text and can be
    wrong.
    """
    name = target.name
    destination = target.destination
    known_hosts = setup.config_dir() / setup.KNOWN_HOSTS_NAME
    failure = setup.ssh_failure(stderr, name=name, destination=destination, known_hosts=(known_hosts,))
    if failure is None:
        return None
    nothing_done = f"  Nothing was done on {name}."
    if failure.where == "hop":
        return (
            f"tender-dev: a hop on the way to {name} ({destination.via}) failed.\n"
            f"  Check that hop with your own ssh.\n{nothing_done}"
        )
    if failure.kind == "host key changed":
        return (
            f"tender-dev: the host key at {destination.hostname} is not the one recorded for {name} — another"
            " machine answers at that address. No connection was made.\n"
            f"  If {name} was reinstalled, remove its record and set it up again:\n"
            f"    ssh-keygen -R {setup.host_key_alias(name)} -f {known_hosts}\n"
            f"    {setup_hint(target)}\n{nothing_done}"
        )
    if failure.kind == "host key not accepted":
        return (
            f"tender-dev: {name}'s host key is not recorded on this machine, so no connection was made.\n"
            f"  Set it up again: {setup_hint(target)}\n{nothing_done}"
        )
    if failure.kind == "no login":
        return (
            f"tender-dev: {name} refused this machine's key. It accepts the key from one address only, and this"
            f" machine's may have changed — or {name} no longer holds the key's line.\n"
            f"  Set it up again: {setup_hint(target)}\n{nothing_done}"
        )
    hop = f", or at the hop {destination.via}" if destination.via else ""
    return (
        f"tender-dev: no answer from {name} at {destination.hostname} port {destination.port}{hop}.\n"
        f"  It may have a new address; set it up with that one:\n"
        f"    mise run setup:remote-target {name} <new address>\n{nothing_done}"
    )


def dispatcher_refusal(stderr: str, target: Target) -> str | None:
    """The hint for a request the target's dispatcher refused.

    Most likely its dispatcher is older than this checkout's, but a request
    this machine's rsync spells otherwise is refused too — so the caller shows
    the refusal itself beside the hint.
    """
    if DISPATCHER_REFUSED not in stderr:
        return None
    return (
        f"tender-dev: {target.name}'s dispatcher refused the request; it is most likely older than this"
        " checkout's.\n"
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
    tree = copied_tree()
    missing = [entry for entry in tree if not (REPO / entry).exists()]
    if missing:
        build_first = " — build it first: mise run build" if missing == ["dist"] else ""
        raise TargetError(
            f"tender-dev: {REPO} has no {', '.join(missing)}{build_first}; nothing was copied to {target.name}."
        )
    rules = ["--exclude=__pycache__", *(f"--include=/{entry}" for entry in tree), "--exclude=/*"]
    remote_shell = " ".join(shlex.quote(word) for word in ssh(target))
    command = ["rsync", "-a", "--delete", "--delete-excluded", "-e", remote_shell, *rules]
    print(f"tender-dev: copying to {target.name} …", file=sys.stderr, flush=True)
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
    said = "\n".join(f"    {line}" for line in result.stderr.rstrip().splitlines())
    failure = connection_failure(result.stderr, target) or dispatcher_refusal(result.stderr, target)
    if failure is None:
        failure = f"tender-dev: the copy to {target.name} failed (rsync ended with {result.returncode})."
    raise TargetError(f"{failure}\n  rsync said:\n{said}")


def run_streaming(command: list[str], *, following: bool = False) -> Outcome:
    """*command* run with its output passed on as it comes, and its stderr kept besides.

    *following* says the call follows from its start (``dev:log --follow``);
    otherwise the line :data:`FOLLOWING` says from where, and is taken out of
    stdout. On Ctrl-C the
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
        relay.join(timeout=1)
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


def after_following(follows: Follows, target: Target) -> str:
    """What to say once the following has ended here: what still runs there, as the target answers."""
    name = target.name
    if follows == "log":
        return f"tender-dev: stopped following the log on {name}."
    unknown = f" Whether it still runs could not be asked; `mise run dev:status --target {name}` says what runs there."
    if follows == "ui-scale":
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


def connection_ended(follows: Follows, target: Target) -> str:
    name = target.name
    ended = f"tender-dev: the connection to {name} ended while following"
    status = f"`mise run dev:status --target {name}` says what runs there."
    if follows == "log":
        return f"{ended} the log."
    if follows == "ui-scale":
        return f"{ended} dev:ui-scale, which may still run there; {status}"
    return f"{ended} the log. The backend may still run there; {status}"


def on_target(target: Target, words: list[str], *, follows: Follows | None, from_start: bool = False) -> int:
    """*words* run on the target with their output passed on; *follows* names what a following there is of."""
    outcome = run_streaming([*ssh(target), "--", target.name, *words], following=from_start)
    if outcome.interrupted and not outcome.following:
        # ssh -T gives the far side no terminal, so the Ctrl-C reaches only this side.
        print(
            f"tender-dev: interrupted here. What the task does on {target.name} most likely goes on on {target.name};"
            f" `mise run dev:status --target {target.name}` says what runs there.",
            file=sys.stderr,
        )
        return INTERRUPTED
    if follows is not None and (outcome.interrupted or (outcome.following and outcome.returncode == 0)):
        print(after_following(follows, target), file=sys.stderr)
        return 0
    if follows is not None and outcome.following and outcome.returncode == setup.SSH_FAILED:
        print(connection_ended(follows, target), file=sys.stderr)
        return outcome.returncode
    if outcome.returncode != 0:
        explained = connection_failure(outcome.stderr, target) if outcome.returncode == setup.SSH_FAILED else None
        explained = explained or dispatcher_refusal(outcome.stderr, target)
        if explained is not None:
            print(explained, file=sys.stderr)
    return outcome.returncode


def remote_words(spec: TaskSpec, name: str, argument: str | None) -> list[str]:
    """The words :data:`DEV_TARGET` is sent for a task: its verb, the target's name, its words and argument."""
    extra = [check_word(argument)] if spec.takes_argument and argument else []
    return [DEV_TARGET, spec.verb, name, *spec.words, *extra]


def run(arguments: argparse.Namespace) -> int:
    target = find_target(arguments.target)
    task: Task = arguments.task
    if task == "dev:log":
        follow: bool = arguments.follow
        return on_target(
            target, ["follow" if follow else "read", "log"], follows="log" if follow else None, from_start=follow
        )
    spec = UI_SCALE_STOP if task == "dev:ui-scale" and arguments.stop else SPECS[task]
    words = remote_words(spec, target.name, arguments.argument or None)
    if spec.builds:
        build(target, surface=arguments.surface)
    copy(target)
    return on_target(target, words, follows=spec.follows)


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
