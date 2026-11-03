"""Which controller layout Steam is told to load for the focused window.

Contract: the pure rules of the focus watcher — when it acts at all, which
window it pins and what it sends when focus leaves a pin, and the one URL that
carries an id to Steam. Nothing
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
# change; the id sent just before it decides the window focused now.
RELEASED_INPUT_ID = 0

# The directory Steam's 64-bit client runs from (``<Steam>/steamrt64/steam``).
STEAMRT64_DIR = "steamrt64"

# SteamOS's gamescope session writes this into the environment Steam is started
# with (``gamescope-session`` -> ``gamescope-environment`` ->
# ``steam-launcher.service``). The sockets and the environment file it names
# outlive Gaming Mode, so their presence on disk says nothing.
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
    applies. Under the 32-bit client and in Gaming Mode Steam attributes a
    game's window itself, and a pin would override that.
    """
    if steam is None or steam.under_gamescope:
        return False
    return posixpath.basename(posixpath.dirname(steam.exe)) == STEAMRT64_DIR


def pin_for(window: FocusedWindow | None, launched_app_id: int | None) -> int | None:
    """The id to pin on the focused window, or ``None`` where Steam attributes it itself.

    Only a window Steam cannot attribute is pinned: its process descends from
    a Steam launch (*launched_app_id*, ``None`` where it descends from none)
    and it carries no ``STEAM_GAME``. A native game's window carries its own
    app id there, and a pin on it would keep the game's in-game overlay, drawn
    inside the same window, from taking the controller.
    """
    if window is None or window.steam_game is not None:
        return None
    if launched_app_id is None or launched_app_id <= 0:
        return None
    return launched_app_id


def steam_choice_for(window: FocusedWindow | None) -> int:
    """The id Steam's own choice would be for the focused window.

    ``STEAM_GAME`` = 769 with ``STEAM_INPUT_FOCUS`` is Big Picture; the desktop
    client's windows carry ``STEAM_GAME`` = 769 without ``STEAM_INPUT_FOCUS``
    and get the desktop's id. Any other ``STEAM_GAME`` is the game Steam
    attributes the window to. Everything else, no focused window and a
    ``STEAM_GAME`` of 0 included, gets the desktop's id.
    """
    if window is None or window.steam_game is None or window.steam_game <= 0:
        return DESKTOP_INPUT_ID
    if window.steam_game == BIG_PICTURE_INPUT_ID:
        return BIG_PICTURE_INPUT_ID if window.steam_input_focus else DESKTOP_INPUT_ID
    return window.steam_game


@dataclass(frozen=True)
class FocusAnswer:
    """What to send for one focus change, in order, and the pin standing once all of it went out."""

    sends: tuple[int, ...]
    pin: int | None


def answer_focus(standing: int | None, window: FocusedWindow | None, launched_app_id: int | None) -> FocusAnswer:
    """What the watcher sends for the focused window, given the pin *standing* before it (``None`` for none).

    A window to pin gets its id, an unchanged one included, and the pin
    stands. Focus leaving a standing pin gets Steam's own choice for the new
    window and then :data:`RELEASED_INPUT_ID`, so Steam decides every later
    change itself. With no pin standing, nothing is sent.
    """
    pin = pin_for(window, launched_app_id)
    if pin is not None:
        return FocusAnswer(sends=(pin,), pin=pin)
    if standing is None:
        return FocusAnswer(sends=(), pin=None)
    return FocusAnswer(sends=(steam_choice_for(window), RELEASED_INPUT_ID), pin=None)


def force_input_url(input_id: int) -> str:
    """The ``steam://`` URL that makes Steam load the layout of *input_id*.

    Raises ``ValueError`` for anything but a non-negative int, so nothing but a
    number ever reaches the URL.
    """
    if type(input_id) is not int or input_id < 0:
        raise ValueError(f"an input id is a non-negative int, not {input_id!r}")
    return _FORCE_INPUT_URL.format(input_id)
