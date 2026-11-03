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

# Why the entry does not start a disc file, where the resolver read the files
# its emulator opens through it and states where the open stops (#2698 D5): a
# file it opens is missing, it refuses a track's type or a cue sheet or
# playlist, a file is not a disc image it opens, a path is too long for it to
# open, or it starts no game from a playlist.
OPENED_FILE_MISSING = "opened_file_missing"
TRACK_REFUSED = "track_refused"
SHEET_REFUSED = "sheet_refused"
FORMAT_REFUSED = "format_refused"
PLAYLIST_REFUSED = "playlist_refused"
PATH_TOO_LONG = "path_too_long"

# What the emulator is established to do short of the whole start, where the
# file starts (#2698 D7): it goes on without a file it opens, for each failure
# above but the playlist's, or it does not read every disc a playlist lists.
GOES_ON_FILE_MISSING = "goes_on_file_missing"
GOES_ON_TRACK_REFUSED = "goes_on_track_refused"
GOES_ON_SHEET_REFUSED = "goes_on_sheet_refused"
GOES_ON_FORMAT_REFUSED = "goes_on_format_refused"
GOES_ON_PATH_TOO_LONG = "goes_on_path_too_long"
PLAYLIST_DISCS_PAST_LIMIT = "playlist_discs_past_limit"

# What could not be established about a file the entry starts, in Tender's
# words (#2195 D40).
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
# The same, for the files a disc file opens (#2698 D7): a format the reading
# does not follow, content it does not read, a file it could not check, the
# setting that loads a patch, a limit of the reading reached, or a path too long
# for what the emulator does with it to be established.
OPENED_FORMAT_UNREAD = "opened_format_unread"
OPENED_CONTENT_UNREAD = "opened_content_unread"
OPENED_FILE_UNREAD = "opened_file_unread"
PATCH_SETTING_UNREAD = "patch_setting_unread"
OPENS_PAST_BOUND = "opens_past_bound"
PATH_TOO_LONG_UNCHECKED = "path_too_long_unchecked"

# Said beside a start as statements of their own (#2698 D8): whether the entry
# reads the format was not established (#2195 D41), and an archive's contents
# were not read.
FORMAT_UNESTABLISHED = "format_unestablished"
CONTENTS_UNREAD = "contents_unread"


@dataclass(frozen=True, slots=True)
class LaunchStatement:
    """One statement said beside a verdict: its word, and the name of the file it is about where it names one."""

    reason: str
    file: str | None = None

    def payload(self) -> dict[str, Any]:
        """The statement as the panel reads it."""
        return {"reason": self.reason, "file": self.file}


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
    established about a file that :data:`STARTS` (:data:`CONTENT_MISSING` …)
    or what is said beside its start (:data:`GOES_ON_FILE_MISSING` …); on a
    start, the first such statement, the format and archive notes left out —
    what a start says is ``statements``. ``reason_file`` is the name of the file
    the reason is about, where the resolver names one, and ``None`` otherwise.
    ``statements`` is everything said beside the verdict, in the resolver's
    order (#2698 D8): each statement beside a start, and beside a refusal each
    file the emulator would have gone on without, what else it is established
    to do, and each thing the resolver could not establish.
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
    reason_file: str | None = None
    statements: tuple[LaunchStatement, ...] = ()
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
            "reason_file": self.reason_file,
            "statements": [statement.payload() for statement in self.statements],
            "contents_unread": self.contents_unread,
            "format_unestablished": self.format_unestablished,
        }
