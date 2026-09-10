"""Where the plugin's user data lives, and which older copy it comes from.

Contract: the pure half of the data-location question — the two roots the
plugin's own data lives under, the launcher's place beneath one of them, and the
ladder that picks which older location a start-up migration copies from. Every
fact the ladder reasons over is handed in; nothing here touches the filesystem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# The directory name both roots carry. Never derived from ``package.json``'s
# ``name``, which happens to spell it identically: read from the manifest, a
# one-line edit there would move every user's library on the next start, with
# nothing failing and nothing said. ``domain/identity.py`` carries the rest of
# that split.
APP_DIR_NAME = "romm-tender"

# The folder names Decky has derived this plugin's data locations from. Decky's
# CLI names a package after the directory it builds from
# (``FilenameSource::Directory``) and Decky derives every per-plugin directory
# from that package name (``decky_loader/plugin/sandboxed_plugin.py``). Built
# from the checkout, that made the delivered folder follow the GitHub
# repository, so the rename at 0.31.0 moved every user's data without anything
# in the plugin asking for it; ``.github/workflows/release.yml`` now builds from
# a copy at a fixed name, so a repository rename cannot move it again. Both
# spellings are searched, and the order is what breaks a tie in the last rung of
# the ladder below.
SOURCE_FOLDER_NAMES = ("decky-romm-sync", "romm-tender")

# The two halves of a location, kept apart because their targets are guarded
# separately: a start can migrate one and leave the other where it is.
SETTINGS_HALF = "settings"
DATA_HALF = "data"

# ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` are deliberately NOT read by the two
# functions below. A Decky plugin's environment descends from a root systemd
# system service — the unit sets only ``UNPRIVILEGED_PATH``, ``PRIVILEGED_PATH``
# and ``LOG_LEVEL``, and ``sandboxed_plugin.py`` adds only ``HOME`` / ``USER`` /
# ``DECKY_*`` on top — so either variable arriving here would describe root's
# directories, not the user's. The home directory is the one thing that is the
# user's by construction, because Decky reads it out of the user's own account.


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
    ``exe`` ENDING in :data:`LAUNCHER_EXE_SUFFIX` — ``src/utils/steamShortcuts.ts``
    and ``services/prune/requests.py`` both match that suffix as their own
    literal — so a launcher kept anywhere but a ``bin`` directory, or under any
    other name, makes every shortcut written before the move stop being
    recognised as ours.
    """
    return os.path.join(root, *_LAUNCHER_COMPONENTS)


@dataclass(frozen=True)
class SourceFacts:
    """One older location, as far as a probe could tell.

    ``name`` is the folder name its two halves sit under. ``present`` means at
    least one of those two directories is actually there — a location is
    named unconditionally, because the folder names are known in advance, so
    this is the only fact that says whether anything stands behind the name.
    ``has_library`` means its database opened and holds at least one ROM — the
    only evidence that a user ever built something there, and deliberately not
    "a database file exists", which is true from the first second of every
    install. Where nothing holds a library, ``settings_mtime`` decides: the
    modification time of the location's settings file, absent when there is none.
    """

    name: str
    present: bool
    has_library: bool
    settings_mtime: float | None


@dataclass(frozen=True)
class MigrationPlan:
    """What one start's ladder decided.

    ``outstanding`` names the halves whose target root is still empty — the only
    halves this start may write. ``source_name`` is the location all of them are
    copied from, absent when there is nothing to copy or nothing may be chosen.
    ``choice_required`` is the one answer the plugin cannot give itself: two
    locations both hold a library, so the user names the winner and the copy
    happens on the next start.
    """

    outstanding: tuple[str, ...]
    source_name: str | None
    choice_required: bool


def plan_migration(
    *,
    settings_target_occupied: bool,
    data_target_occupied: bool,
    recorded_answer: str | None,
    probe_sources: Callable[[], Sequence[SourceFacts]],
) -> MigrationPlan:
    """Decide which older location this start copies from, and for which halves.

    The rungs, in order:

    1. Every target root already holds something — nothing to decide, now or
       ever. Sources are never deleted, so a rung that kept looking at them
       would raise the same question on every start for the rest of the
       install's life.
    2. A previous start recorded the user's answer — it names the source, and no
       question is raised. An answer naming a location that is no longer there
       is ignored rather than obeyed: the folder names are known in advance and
       a location is named whether or not anything stands behind it, so obeying
       one would copy an empty directory into place and settle rung 1 for the
       life of the install, with the surviving library stranded and no question
       ever raised again.
    3. Exactly one location holds a library — it wins, silently.
    4. Two do — nothing is copied and the user is asked.
    5. None does — the one with a settings file wins, so a fresh install that
       was configured but never synced keeps its credentials. Where both have
       one the newer wins; nothing is at stake either way, since both survive
       the copy untouched. A tie goes to the first source probed.
    6. Nothing anywhere has either — there is nothing to migrate.

    Both halves always come from the SAME location: a database paired with the
    other install's settings is a state that never existed.

    ``probe_sources`` is a thunk rather than a sequence, and is called at most
    once — never at all when rung 1 settles it. Probing a location opens its
    database, so a settled install would otherwise open and query both older
    databases on every start for the rest of its life, to answer a question that
    was decided before they were read.
    """
    outstanding = tuple(
        half
        for half, occupied in ((SETTINGS_HALF, settings_target_occupied), (DATA_HALF, data_target_occupied))
        if not occupied
    )
    if not outstanding:
        return MigrationPlan(outstanding=(), source_name=None, choice_required=False)

    sources = probe_sources()

    recorded = next((source for source in sources if source.name == recorded_answer and source.present), None)
    if recorded is not None:
        return MigrationPlan(outstanding=outstanding, source_name=recorded.name, choice_required=False)

    with_library = [source for source in sources if source.has_library]
    if len(with_library) == 1:
        return MigrationPlan(outstanding=outstanding, source_name=with_library[0].name, choice_required=False)
    if len(with_library) > 1:
        return MigrationPlan(outstanding=outstanding, source_name=None, choice_required=True)

    configured = [(source.settings_mtime, source.name) for source in sources if source.settings_mtime is not None]
    if configured:
        # ``max`` returns the FIRST maximal element, so two identical mtimes
        # resolve to the earlier entry of ``sources`` rather than to whichever
        # the comparison happened to visit last.
        newest = max(configured, key=lambda pair: pair[0])[1]
        return MigrationPlan(outstanding=outstanding, source_name=newest, choice_required=False)

    return MigrationPlan(outstanding=outstanding, source_name=None, choice_required=False)
