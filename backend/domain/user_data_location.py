"""Where the plugin's user data lives.

Contract: the name every directory this program derives for itself carries, two
roots composed out of a home directory, and the launcher's place beneath one of
them. Nothing here touches the filesystem.

``domain/app_directories.py`` is what actually answers where the directories
are, and it names five of its six after the constant below. The two functions
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
# below, and five of the six in ``domain/app_directories.py``. Never derived from ``package.json``'s
# ``name``, which happens to spell it identically: read from the manifest, a
# one-line edit there would move every user's library on the next start, with
# nothing failing and nothing said. ``domain/identity.py`` carries the rest of
# that split.
APP_DIR_NAME = "romm-tender"

# The two functions below compose a root out of a home directory and nothing
# else. The environment is read in ``domain/app_directories.py``, which is the
# module that answers where this program's directories are; these two answer
# what they are CALLED, and are what a caller uses when it already has a home.


def config_root(user_home: str) -> str:
    """The root holding user-intent configuration for the user at *user_home*."""
    return os.path.join(user_home, ".config", APP_DIR_NAME)


def data_root(user_home: str) -> str:
    """The root holding the database, the launcher and the legacy state file.

    Covers and artwork are NOT here — they are re-derivable from the server and
    live under the cache root (``domain/app_directories.py``).
    """
    return os.path.join(user_home, ".local", "share", APP_DIR_NAME)


# The two path components the launcher sits under, and the suffix a shortcut is
# recognised by. Derived from one tuple rather than written twice: the suffix IS
# the components, and a second spelling of them is exactly how ownership
# detection would drift away from where the file is put.
_LAUNCHER_COMPONENTS = ("bin", "rom-launcher")
LAUNCHER_EXE_SUFFIX = "/" + "/".join(_LAUNCHER_COMPONENTS)


def launcher_path(root: str) -> str:
    """The launcher's place beneath *root* — the data root, or the directory the program ships in.

    Its home is under the data root, outside the directory the program itself
    occupies, because a shortcut's ``exe`` is the one thing about it this
    program cannot repair from inside: an update that replaced its own install
    directory would leave every game pointing at a file nothing is going to put
    back. That was literal under the plugin loader, which deleted a plugin's
    folder whole before unpacking an update; it stays true of any install step
    that replaces the program in place. The release's own copy still ships at
    the same two components below the install directory, which is why one
    function answers for both.

    Those two components are not free. A shortcut is recognised as ours by its
    ``exe`` ENDING in :data:`LAUNCHER_EXE_SUFFIX` —
    ``frontend/src/utils/steamShortcuts.ts`` and ``services/prune/requests.py``
    both match that suffix as their own literal — so a launcher kept anywhere
    but a ``bin`` directory, or under any other name, makes every shortcut
    written before that change stop being recognised as ours.
    """
    return os.path.join(root, *_LAUNCHER_COMPONENTS)
