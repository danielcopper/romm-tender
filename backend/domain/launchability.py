"""Whether one installed game's file starts with the entry Tender starts it through, in Tender's words.

The verdict is the resolver's, read in :mod:`adapters.atlas_catalogue`; this is
the value it is read into. Each of the five states has its own sentence, worded
by the panel (#2195 D2), which also decides which of them refuse a start
(``frontend/src/utils/gameLaunch.ts``).

Pure compute — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The file starts with the entry it was asked about.
STARTS = "starts"
# The format is real content for the system, and has to be installed in an
# emulator before anything starts it.
NEEDS_INSTALLING = "needs_installing"
# The system does not start this file; it starts from other extensions.
NOT_A_LAUNCH_FILE = "not_a_launch_file"
# The system starts this file, but the entry asked about does not.
NOT_THIS_EMULATOR = "not_this_emulator"
# Whether the file starts could not be established.
NOT_ESTABLISHED = "not_established"

# Why a verdict could not be established, in Tender's words.
SYSTEM_UNKNOWN = "system_unknown"
ENTRY_UNKNOWN = "entry_unknown"
CATALOGUE_UNREAD = "catalogue_unread"
NO_SOURCE = "no_source"

# Why the entry does not start the file, where the resolver states a reason of
# its own, in Tender's words (#2195 D40): it takes no folder, it finds nothing
# to start in the folder, the file is neither a disc image nor a game program,
# the game's disc cannot be mounted, or an encrypted image has no key file.
TAKES_NO_FOLDER = "takes_no_folder"
NOTHING_TO_START = "nothing_to_start"
NOT_A_GAME_FILE = "not_a_game_file"
DISC_NOT_FOUND = "disc_not_found"
DISC_KEY_MISSING = "disc_key_missing"

# What could not be established about a file the entry starts, in Tender's
# words (#2195 D40). Play stays on, with a note naming it.
CONTENT_MISSING = "content_missing"
CONTENT_UNREADABLE = "content_unreadable"
GAME_LIST_UNREAD = "game_list_unread"
GAME_KIND_UNFOLLOWED = "game_kind_unfollowed"
INSIDE_EMULATOR_DRIVE = "inside_emulator_drive"
EMULATOR_DRIVE_UNKNOWN = "emulator_drive_unknown"
SAVESTATE_UNREAD = "savestate_unread"
IMAGE_LAYOUT_UNREAD = "image_layout_unread"
ENCRYPTION_UNREAD = "encryption_unread"
KEY_UNTESTED = "key_untested"
KEY_FOLDER_UNKNOWN = "key_folder_unknown"
DISC_FOLDER_SET = "disc_folder_set"
DISC_FOLDER_UNKNOWN = "disc_folder_unknown"
CONTENT_UNCHECKED = "content_unchecked"
VERSION_DEPENDENT = "version_dependent"


@dataclass(frozen=True, slots=True)
class Launchability:
    """One file's verdict for one entry, with what each state's sentence names.

    ``system`` is the system it was asked as, ``file`` the file's name and
    ``extension`` the token compared, as the resolver states it.
    ``starts_from`` is the system's accept-list, read for
    :data:`NOT_A_LAUNCH_FILE`; ``other_entries`` the labels of the system's
    entries established to start the file, read for :data:`NOT_THIS_EMULATOR`;
    ``install_in`` the labels of the system's entries that install it, ``None``
    on every state but :data:`NEEDS_INSTALLING`, where an empty tuple says the
    system has an entry for none of the emulators known to (#2195 D35).
    ``reason`` is the resolver's reason in Tender's words, ``None`` where it
    named none Tender words: why a verdict was :data:`NOT_ESTABLISHED`
    (:data:`SYSTEM_UNKNOWN` …), why the entry does not start the file on
    :data:`NOT_THIS_EMULATOR` (:data:`TAKES_NO_FOLDER` …), and what could not be
    established about a file that :data:`STARTS` (:data:`CONTENT_MISSING` …).
    ``contents_unread`` says the file is an archive whose contents were not
    read; ``format_unestablished`` that whether the entry reads this format was
    not established.
    """

    state: str
    entry: str
    system: str
    file: str
    extension: str
    starts_from: tuple[str, ...] = ()
    other_entries: tuple[str, ...] = ()
    install_in: tuple[str, ...] | None = None
    reason: str | None = None
    contents_unread: bool = False
    format_unestablished: bool = False

    def payload(self, *, platform_name: str) -> dict[str, Any]:
        """The verdict as the panel reads it, with the name the game page shows the game's platform under.

        The sentences name the platform by that name rather than by the system
        it was asked as (#2195 D36); the resolver knows only the system.
        """
        return {
            "state": self.state,
            "entry": self.entry,
            "system": self.system,
            "platform_name": platform_name,
            "file": self.file,
            "extension": self.extension,
            "starts_from": list(self.starts_from),
            "other_entries": list(self.other_entries),
            "install_in": None if self.install_in is None else list(self.install_in),
            "reason": self.reason,
            "contents_unread": self.contents_unread,
            "format_unestablished": self.format_unestablished,
        }
