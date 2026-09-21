"""Where this program's directories are, computed from an environment.

Contract: the pure mapping from an environment and a home directory to the
seven places this backend reads and writes. Nothing here touches the filesystem or
reads ``os.environ`` itself — the environment arrives as an argument, which is
what makes the whole ladder checkable against a table.

**The ladder has three rungs, and only the first one is meant for a service.**
An installer resolves these once and writes the answers into the unit as
``TENDER_*`` variables, because the installer and the service do not share an
environment: the installer runs in a login shell, the service in the user
manager's, and an ``XDG_DATA_HOME`` set in a shell profile never reaches the
latter. The XDG variables and the built-in defaults below are the second and
third rungs, and they exist for a start by hand.

The split between the roots is not filing tidiness. What lies under the cache
root is re-derivable — covers and artwork are fetched again if they are gone —
and what lies under the data root is not: the database is the only copy of what
the user has installed, synced and chosen. A system that clears caches must be
able to clear one and not the other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.user_data_location import APP_DIR_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping

# The installer's answers. First rung of the ladder, and the only one a service
# is expected to run on.
ENV_CONFIG_DIR = "TENDER_CONFIG_DIR"
ENV_DATA_DIR = "TENDER_DATA_DIR"
ENV_CACHE_DIR = "TENDER_CACHE_DIR"
ENV_STATE_DIR = "TENDER_STATE_DIR"
ENV_CODE_DIR = "TENDER_CODE_DIR"
ENV_BIN_DIR = "TENDER_BIN_DIR"

# Second rung. ``XDG_RUNTIME_DIR`` is the only one of these that is reliably set
# on the reference machine; the other four were measured unset in both the login
# shell and the user manager's environment, which is precisely why the first
# rung exists.
XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
XDG_DATA_HOME = "XDG_DATA_HOME"
XDG_CACHE_HOME = "XDG_CACHE_HOME"
XDG_STATE_HOME = "XDG_STATE_HOME"
XDG_RUNTIME_DIR = "XDG_RUNTIME_DIR"

# Third rung, and the one directory more than one default sits under: the XDG
# basedir spec puts two of its four built-in defaults here (`share` and
# `state`, while config and cache are their own dotted names), and `~/.local/bin`
# — which has no variable at all — is the third use of it.
_LOCAL = ".local"


@dataclass(frozen=True)
class AppDirectories:
    """The seven places this backend uses, five of them named after the program."""

    config_dir: str
    """User intent — ``settings.json`` and nothing else."""

    data_dir: str
    """What cannot be fetched again. CONTEXT.md names what is in it."""

    cache_dir: str
    """Covers and artwork: everything re-derivable from the server."""

    state_dir: str
    """The log file."""

    runtime_dir: str
    """The port file, in a directory the session clears when the user logs out."""

    code_dir: str
    """The program itself — the launcher it ships is copied out of here."""

    bin_dir: str
    """Where a user's own executables go — the launcher is installed here.

    One of the TWO roots not named after this program — the other is
    :attr:`code_dir`, which is wherever the program was installed. This one is a
    directory shared with every other program the user installed for themselves,
    so a name of ours in it would be wrong. XDG names no variable for it either
    — the basedir spec names the path itself — so the ladder here is
    :data:`ENV_BIN_DIR` and then the built-in default, with no XDG rung between
    them.
    """


def resolve_directories(environ: Mapping[str, str], user_home: str, code_fallback: str) -> AppDirectories:
    """Answer where this backend's directories are, for *environ* and *user_home*.

    *code_fallback* is where the running program actually sits, used only when
    nothing in the environment says — a start by hand from a checkout. The
    installed case always says, because the installer wrote it into the unit.

    XDG names no default for the runtime directory, so a start that has none
    falls back to the state directory. The port file then survives a logout
    instead of being cleared with the session, which is the lesser of the two
    problems: it is a hint and connecting is the proof, so a stale one misleads
    nobody who checks.
    """
    config_home = _first(environ, XDG_CONFIG_HOME, os.path.join(user_home, ".config"))
    data_home = _first(environ, XDG_DATA_HOME, os.path.join(user_home, _LOCAL, "share"))
    cache_home = _first(environ, XDG_CACHE_HOME, os.path.join(user_home, ".cache"))
    state_home = _first(environ, XDG_STATE_HOME, os.path.join(user_home, _LOCAL, "state"))

    state_dir = _first(environ, ENV_STATE_DIR, os.path.join(state_home, APP_DIR_NAME))
    runtime_home = environ.get(XDG_RUNTIME_DIR, "").strip()

    return AppDirectories(
        config_dir=_first(environ, ENV_CONFIG_DIR, os.path.join(config_home, APP_DIR_NAME)),
        data_dir=_first(environ, ENV_DATA_DIR, os.path.join(data_home, APP_DIR_NAME)),
        cache_dir=_first(environ, ENV_CACHE_DIR, os.path.join(cache_home, APP_DIR_NAME)),
        state_dir=state_dir,
        runtime_dir=os.path.join(runtime_home, APP_DIR_NAME) if runtime_home else state_dir,
        code_dir=_first(environ, ENV_CODE_DIR, code_fallback),
        bin_dir=_first(environ, ENV_BIN_DIR, os.path.join(user_home, _LOCAL, "bin")),
    )


def _first(environ: Mapping[str, str], name: str, fallback: str) -> str:
    """The value of *name* if it says something, else *fallback*.

    A variable set to the empty string counts as unset. An empty path would
    otherwise resolve to the working directory, which is wherever the process
    happened to be started.
    """
    value = environ.get(name, "").strip()
    return value or fallback
