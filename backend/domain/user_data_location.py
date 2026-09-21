"""Where the plugin's user data lives.

Contract: the name every directory this program derives for itself carries, two
roots composed out of a home directory, and the two ways the launcher's path is
composed. Nothing here touches the filesystem.

``domain/app_directories.py`` is what actually answers where the directories
are, and it names five of its seven after the constant below. The two functions
here compose the same two names from a home directory alone; they have no
production caller today.

The roots are named after :data:`APP_DIR_NAME` and nothing else. Which older
directory a user's data might once have come from is not a question this program
asks any more: the backend is told where its directories are
(``domain/app_directories.py``) rather than deriving them from a folder a plugin
loader named.
"""

from __future__ import annotations

import os

# The name every directory this program derives for itself carries — the two
# below, and five of the seven in ``domain/app_directories.py``. Never derived
# from ``domain/identity.py``'s ``PACKAGE_NAME``, which happens to spell it
# identically: that one names the package a server is told about and is free to
# be renamed with it, and a one-line edit there would then move every user's
# library on the next start, with nothing failing and nothing said.
# ``domain/identity.py`` carries the rest of that split.
APP_DIR_NAME = "romm-tender"

# The two functions below compose a root out of a home directory and nothing
# else. The environment is read in ``domain/app_directories.py``, which is the
# module that answers where this program's directories are; these two answer
# what they are CALLED, and are what a caller uses when it already has a home.


def config_root(user_home: str) -> str:
    """The root holding user-intent configuration for the user at *user_home*."""
    return os.path.join(user_home, ".config", APP_DIR_NAME)


def data_root(user_home: str) -> str:
    """The root holding what cannot be fetched again — CONTEXT.md names what that is.

    Two things are NOT here, and both were once. Covers and artwork are
    re-derivable from the server and live under the cache root
    (``domain/app_directories.py``); the launcher is an executable and lives in
    the directory a user's own executables go in, which is not named after this
    program at all.
    """
    return os.path.join(user_home, ".local", "share", APP_DIR_NAME)


# The launcher's own file name, the directory name it sits in where a root is
# what a caller has, and the suffix a shortcut is recognised by. All three are
# derived from one tuple rather than written out again: the suffix IS the
# components, and a second spelling of them is exactly how ownership detection
# would drift away from where the file is put.
_LAUNCHER_COMPONENTS = ("bin", "tender-rom-launcher")
LAUNCHER_EXE_SUFFIX = "/" + "/".join(_LAUNCHER_COMPONENTS)


def launcher_path(root: str) -> str:
    """The launcher's place beneath *root*, a directory holding a ``bin`` of its own.

    That is the copy the release ships — ``launcher_path(code_dir)`` — and the
    only caller left. The INSTALLED launcher is not beneath a root of this
    program's at all: it goes straight into the shared directory a user's own
    executables live in, which is :func:`launcher_in_bin_dir`.

    The two components are not free. A shortcut is recognised as ours by its
    ``exe`` ENDING in :data:`LAUNCHER_EXE_SUFFIX` —
    ``frontend/src/utils/steamShortcuts.ts`` and ``services/prune/requests.py``
    both match that suffix as their own literal — so a launcher kept anywhere
    but a ``bin`` directory, or under any other name, makes every shortcut
    written before that change stop being recognised as ours.
    """
    return os.path.join(root, *_LAUNCHER_COMPONENTS)


def launcher_in_bin_dir(bin_dir: str) -> str:
    """The launcher's place inside *bin_dir*, the directory it is installed into.

    *bin_dir* is ``AppDirectories.bin_dir`` — a shared directory, already named
    ``bin`` by whoever owns it, so the file goes directly in rather than under a
    ``bin`` of its own. The file name is the same one :func:`launcher_path`'s
    last component spells.

    A ``bin_dir`` whose own last component is NOT ``bin`` therefore yields a
    path that does not end in :data:`LAUNCHER_EXE_SUFFIX`, and every shortcut
    built against it stops being recognised as ours. Nothing here can enforce
    that — the value arrives from the environment — so the default and the
    installer both name a directory called ``bin``.
    """
    return os.path.join(bin_dir, _LAUNCHER_COMPONENTS[-1])
