"""Where the plugin's user data lives.

Contract: the pure half of the data-location question — the directory name both
roots carry, the two roots themselves, and the launcher's place beneath one of
them. Nothing here touches the filesystem.

The roots are named after :data:`APP_DIR_NAME` and nothing else. Which older
directory a user's data might once have come from is not a question this program
asks any more: the backend is told where its directories are
(``domain/app_directories.py``) rather than deriving them from a folder a plugin
loader named.
"""

from __future__ import annotations

import os

# The directory name both roots carry. Never derived from ``package.json``'s
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
    """The root holding the database, covers, artwork and the legacy state file."""
    return os.path.join(user_home, ".local", "share", APP_DIR_NAME)


# The two path components the launcher sits under, and the suffix a shortcut is
# recognised by. Derived from one tuple rather than written twice: the suffix IS
# the components, and a second spelling of them is exactly how ownership
# detection would drift away from where the file is put.
_LAUNCHER_COMPONENTS = ("bin", "rom-launcher")
LAUNCHER_EXE_SUFFIX = "/" + "/".join(_LAUNCHER_COMPONENTS)


def launcher_path(root: str) -> str:
    """The launcher's place beneath *root* — the data root, or the plugin folder it ships in.

    Its home is under the data root, outside the plugin folder, because Decky
    deletes that folder whole before it unpacks an update: a shortcut's ``exe``
    is the one thing about it this plugin cannot repair from inside, so an
    update that failed to unpack would leave every game pointing at a file
    nothing is going to put back. The release's own copy is still shipped, at
    the same two components below the plugin folder, which is why one function
    answers for both.

    Those two components are not free. A shortcut is recognised as ours by its
    ``exe`` ENDING in :data:`LAUNCHER_EXE_SUFFIX` —
    ``frontend/src/utils/steamShortcuts.ts`` and ``services/prune/requests.py``
    both match that suffix as their own literal — so a launcher kept anywhere
    but a ``bin`` directory, or under any other name, makes every shortcut
    written before the move stop being recognised as ours.
    """
    return os.path.join(root, *_LAUNCHER_COMPONENTS)
