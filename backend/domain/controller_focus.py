"""Which controller layout Steam is told to load for the focused window.

Contract: the pure rules of the focus watcher — when it acts at all, which input
id a focused window gets, and the one URL that carries that id to Steam. Nothing
here reads a process, a window or the environment; the readings arrive as
values, which is what makes every rule checkable against a table.

What the ids do inside Steam, and why the watcher exists, is
docs/architecture/controller-focus.md.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# The switch, read once at start. Any other value, an empty one included,
# leaves the watcher on: refusing to start the backend over a typo in a unit
# file would be the worse failure.
FOCUS_WATCHER_ENV = "TENDER_FOCUS_WATCHER"
FOCUS_WATCHER_OFF = "off"

# Steam's own ids for the two layouts that are not a game's: Big Picture's
# (``basicui_neptune.vdf`` loads for it) and the desktop's.
BIG_PICTURE_INPUT_ID = 769
DESKTOP_INPUT_ID = 413080

# Hands the choice back to Steam, which takes effect only at the next focus
# change; sent once, at stop.
RELEASED_INPUT_ID = 0

# The directory Steam's 64-bit client runs from (``<Steam>/steamrt64/steam``).
STEAMRT64_DIR = "steamrt64"

# SteamOS's gamescope session writes this into the environment Steam is started
# with (``gamescope-session`` -> ``gamescope-environment`` ->
# ``steam-launcher.service``). The sockets and the environment file it names
# outlive Game Mode, so their presence on disk says nothing.
GAMESCOPE_DISPLAY_VAR = "GAMESCOPE_WAYLAND_DISPLAY"

_FORCE_INPUT_URL = "steam://forceinputappid/{}"


@dataclass(frozen=True)
class XDisplay:
    """The X display Steam's desktop session runs on, and the authority file that admits a client to it."""

    name: str
    authority_path: str


@dataclass(frozen=True)
class SteamIdentity:
    """One run of Steam: its pid and the kernel's start time for it, so a reused pid is a different run."""

    pid: int
    start_time: int


@dataclass(frozen=True)
class RunningSteam:
    """What the watcher reads off the running Steam process.

    *exe* is where the process's executable resolves to; *under_gamescope* is
    whether its environment carries :data:`GAMESCOPE_DISPLAY_VAR`; *display* is
    the X display of the desktop session it was started from, ``None`` where
    none could be read.
    """

    identity: SteamIdentity
    exe: str
    under_gamescope: bool
    display: XDisplay | None


@dataclass(frozen=True)
class FocusedWindow:
    """What the X server says about the focused window.

    *host_pid* is the pid of the client that created it, as the host's process
    table numbers it, ``None`` where the server could not give one.
    *steam_game* is the window's ``STEAM_GAME`` property, ``None`` where it has
    none; *steam_input_focus* is whether it carries ``STEAM_INPUT_FOCUS`` at all.
    """

    host_pid: int | None
    steam_game: int | None
    steam_input_focus: bool


def focus_watcher_on(environ: Mapping[str, str]) -> bool:
    """Whether the switch leaves the watcher on; only ``off`` turns it off, in any case and padding."""
    return environ.get(FOCUS_WATCHER_ENV, "").strip().lower() != FOCUS_WATCHER_OFF


def watcher_applies(steam: RunningSteam | None) -> bool:
    """Whether the watcher acts for this Steam run: the 64-bit client, outside gamescope.

    ``None`` — no Steam, or one whose process could not be read — never
    applies. Under the 32-bit client and in Game Mode Steam attributes a
    game's window itself, and a pin would override that.
    """
    if steam is None or steam.under_gamescope:
        return False
    return posixpath.basename(posixpath.dirname(steam.exe)) == STEAMRT64_DIR


def input_id_for(window: FocusedWindow | None, launched_app_id: int | None) -> int:
    """The input id for the focused window, in the order the rules are tried.

    *launched_app_id* is the app id of the Steam launch the window's process
    descends from, ``None`` where it descends from none. That wins; then a
    window carrying ``STEAM_GAME`` = 769 and ``STEAM_INPUT_FOCUS`` is Big
    Picture — the desktop client's windows carry ``STEAM_GAME`` = 769 without
    ``STEAM_INPUT_FOCUS``; everything else, no focused window included, gets
    the desktop's id.
    """
    if launched_app_id is not None and launched_app_id > 0:
        return launched_app_id
    if window is not None and window.steam_game == BIG_PICTURE_INPUT_ID and window.steam_input_focus:
        return BIG_PICTURE_INPUT_ID
    return DESKTOP_INPUT_ID


def force_input_url(input_id: int) -> str:
    """The ``steam://`` URL that makes Steam load the layout of *input_id*.

    Raises ``ValueError`` for anything but a non-negative int, so nothing but a
    number ever reaches the URL.
    """
    if type(input_id) is not int or input_id < 0:
        raise ValueError(f"an input id is a non-negative int, not {input_id!r}")
    return _FORCE_INPUT_URL.format(input_id)
