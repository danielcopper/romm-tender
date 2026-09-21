"""What the injector asks the MACHINE, and the one thing it puts back.

Contract: the readings taken from this computer rather than from the page — who
else is loading code into Steam, which Steam build is running, and whether Steam
will open its debugger at all. The first two are questions the injector has to
have answered before it evaluates anything, and neither can be put to the page at
the moment it has to be answered; the third is a file under the Steam root, which
is why it is answered here too.

**Why not the window.** Measured on the device: at the earliest moment an
injection is possible, every marker Decky Loader eventually sets — ``DFL``,
``DeckyPluginLoader``, ``DeckyBackend``, ``deckyAuthToken``, ``deckyHasLoaded`` —
is still ``undefined``. A machine with Decky and one without look identical
there, so a probe of the window would answer "no Decky" on both. The injector is
a local process, so it can ask the system instead.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from host.single_instance import someone_listening

if TYPE_CHECKING:
    import logging

# Decky Loader's own HTTP port — the address it serves its interface to Steam
# from. Its systemd unit is ``plugin_loader.service``, running
# ``~/homebrew/services/PluginLoader``; on the reference machine that unit is
# active and something is listening on ``127.0.0.1:1337``.
DECKY_LOADER_PORT = 1337

# Where Steam keeps its installed-package records. Both spellings are tried, in
# this order, the same way ``adapters/steam_config.py`` resolves the user
# directory — on this machine the second is a symlink to the first, and neither
# is guaranteed to be.
_STEAM_ROOTS = ((".local", "share", "Steam"), (".steam", "steam"))

# The file Steam looks for at start-up to decide whether to open its CEF
# debugger. Without it there is no port to attach to and no panel can be loaded.
_DEBUGGER_MARKER = ".cef-enable-remote-debugging"

# Our note that the marker is ours, written beside the log. Its FIRST LINE is
# the absolute path of the marker that was created: the installer's
# ``--uninstall`` removes exactly the path the note names, so the note has to
# name the one file rather than a name to go looking for. ``install.sh`` writes
# the same two-line shape under the same filename — the marker's path, then
# which program wrote it and when — and names this constant;
# ``tests/scripts/test_install_sh.py`` holds the two spellings equal.
DEBUGGER_MARKER_NOTE = "debugger-marker"
_PACKAGE_DIR = "package"
_BRANCH_FILE = "beta"
_MANIFEST_GLOB_PREFIX = "steam_client_"
_MANIFEST_SUFFIX = ".manifest"
_VERSION_FIELD = re.compile(r'"version"\s+"(\d+)"')


async def decky_loader_is_serving(port: int = DECKY_LOADER_PORT, *, connect_timeout: float = 0.5) -> bool:
    """Is Decky Loader's own server answering right now?

    **Three questions look alike here and only one of them is worth asking.**
    "Is Decky installed" is a directory, and a machine that installed it and
    turned it off answers yes. "Is the unit active" is systemd's word for a
    process that may still be starting, or be wedged. What this asks is whether
    the loader's server answers, and that is the closest of the three to the
    question that matters — everything Decky renders into Steam is served from
    this port, so a loader that is rendering has answered here.

    It is still not "Decky is rendering", and the gap is left on the safe side:
    a loader that started five seconds ago has not injected yet, and this
    answers ``True`` for it. Treating that as rendering costs a machine with
    Decky nothing (it gets the bundle that shares Decky's copy of the package,
    which is what it wants either way), where the other direction costs the
    Steam UI. The ordering measurement — Tender loading first is safe — cannot
    be leant on, because a reconnect can put this question at a moment when
    Steam has been up for an hour and Decky has long since rendered.

    **What the safe direction costs, said plainly:** something that is not Decky
    answering on this port is read as Decky. The coexistence panel is then
    chosen, and it waits for a ``DFL`` that will never appear — so the injector
    warns once a readiness window, attaches again, and no panel is ever loaded.
    That is a machine with no panel rather than a machine with no Steam, which
    is the trade this answer makes; the readiness warning names the condition it
    waited for, so the log says ``DFL`` rather than only saying "not ready".

    The connect blocks, so it runs off the event loop — which is also why
    *connect_timeout* bounds the socket rather than this coroutine: a deadline
    on the await would return control while the worker thread was still waiting.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: someone_listening(port, connect_timeout))


def read_steam_build(user_home: str) -> str | None:
    """The build number of the installed Steam client, or ``None`` if unreadable.

    Read from Steam's own record of what it installed:
    ``<steam>/package/steam_client_<branch>_ubuntu12.manifest`` carries a
    ``"version"`` field, and ``<steam>/package/beta`` names the branch that
    manifest belongs to. Where the branch file does not lead to a manifest, a
    single manifest in the directory is taken as the answer and several are
    taken as none — a guess between them would be a build number nobody could
    check.

    **Not the debugger's version string**, which is CEF's (``Chrome/126...``)
    and changes only when Valve moves to a new CEF; Steam's interface is rebuilt
    far more often than that, and this value is read to notice exactly those
    rebuilds.
    """
    package_dir = _find_package_dir(user_home)
    if package_dir is None:
        return None
    manifest = _find_manifest(package_dir)
    if manifest is None:
        return None
    try:
        with open(manifest, encoding="utf-8", errors="replace") as handle:
            found = _VERSION_FIELD.search(handle.read())
    except OSError:
        return None
    return found.group(1) if found else None


def _find_package_dir(user_home: str) -> str | None:
    """The first Steam package directory that exists under *user_home*."""
    for parts in _STEAM_ROOTS:
        candidate = os.path.join(user_home, *parts, _PACKAGE_DIR)
        if os.path.isdir(candidate):
            return candidate
    return None


def _find_manifest(package_dir: str) -> str | None:
    """The client manifest in *package_dir*, chosen by branch where it can be."""
    try:
        names = sorted(
            name
            for name in os.listdir(package_dir)
            if name.startswith(_MANIFEST_GLOB_PREFIX) and name.endswith(_MANIFEST_SUFFIX)
        )
    except OSError:
        return None
    if not names:
        return None

    branch = _read_branch(package_dir)
    if branch:
        for name in names:
            if branch in name:
                return os.path.join(package_dir, name)
    return os.path.join(package_dir, names[0]) if len(names) == 1 else None


def _read_branch(package_dir: str) -> str:
    """The branch Steam records for itself, or the empty string if it records none."""
    try:
        with open(os.path.join(package_dir, _BRANCH_FILE), encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def ensure_debugger_marker(user_home: str, state_dir: str, logger: logging.Logger) -> bool:
    """Make sure Steam's remote-debugging marker exists, and say whether it does.

    Answers ``True`` where the marker is there — already, or because this call
    created it — and ``False`` where it could not be. Never raises.

    It is created rather than only reported, and on every start rather than
    once, because another program on the same machine takes it away — which
    program and why is ``docs/architecture/loading-the-panel.md``. Creating it
    is not enough on its own, since Steam reads it at start-up, so the line this
    writes says a restart is due.

    The note beside it records that the marker is ours, which is the only
    evidence ``install.sh --uninstall`` has for whether it may take the marker
    away again.
    """
    root = _find_steam_root(user_home)
    if root is None:
        logger.warning("inject: no Steam directory found, so Steam's remote-debugging marker cannot be created")
        return False
    marker = os.path.join(root, _DEBUGGER_MARKER)
    if os.path.exists(marker):
        return True
    try:
        # Exclusive: a create that loses the race against the check above has to
        # fail rather than succeed, or the note below would claim a marker some
        # other program had just put there.
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    except OSError as e:
        logger.warning(f"inject: could not create Steam's remote-debugging marker at {marker}: {e}")
        return False
    logger.warning(
        f"inject: created Steam's remote-debugging marker at {marker}; "
        "Steam has to be restarted once before the panel can load"
    )
    # After the answer is settled, and never able to unsettle it: the marker is
    # what Steam reads, and a note that could not be written costs the
    # uninstaller its authority over one file rather than costing this start its
    # debugger. Reporting the created marker as a failure would be a worse
    # answer than the one thing that actually went wrong.
    _write_marker_note(state_dir, marker, logger)
    return True


def _find_steam_root(user_home: str) -> str | None:
    """The first Steam root that exists under *user_home*, in the order tried everywhere here."""
    for parts in _STEAM_ROOTS:
        candidate = os.path.join(user_home, *parts)
        if os.path.isdir(candidate):
            return candidate
    return None


def _write_marker_note(state_dir: str, marker: str, logger: logging.Logger) -> None:
    """Record which marker this program created, for the uninstaller to read.

    The path comes first because that is the line the uninstaller acts on.
    Never raises: a note that could not be written is its own failure and not
    the caller's.
    """
    note = os.path.join(state_dir, DEBUGGER_MARKER_NOTE)
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(note, "w", encoding="utf-8") as handle:
            handle.write(f"{marker}\ncreated by the backend {datetime.now(UTC).date().isoformat()}\n")
    except OSError as e:
        logger.warning(f"inject: created the marker but could not record it at {note}: {e}")
