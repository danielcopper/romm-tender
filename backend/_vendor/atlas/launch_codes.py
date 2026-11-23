"""The caveat codes the launchability question adds in issue #776, and the closed vocabularies of their data.

They live apart from :mod:`atlas.installations`, which raises them, because
:data:`atlas.placement.ENUMERATED_DATA` registers their ``reason`` and ``kind``
vocabularies, and :mod:`atlas.placement` cannot import the module that imports
its :class:`~atlas.placement.Caveat`.
"""

from __future__ import annotations

from typing import Final, Literal

# Where ES-DE's scan would not list the content path under its own name: the
# verdict is the emulator's (#776 D1), and how the frontend expects the game
# named rides beside it as data (D2, D4) — never where the path lies (D6).
CAVEAT_FRONTEND_NAME_UNSCANNED: Final = "frontend-name-unscanned"

# A directory handed to an entry whose route atlas does not follow to the end:
# a core that opens the path through its own loader, a core whose per-extension
# content overrides could apply, or a core whose reading says neither.
CAVEAT_ENTRY_DIRECTORY_UNREAD: Final = "entry-directory-unread"

# The files in a directory handed to an entry that the entry would take as
# files of their own (#776 D5): where the directory does not start, the file
# is the remedy; where it does, it is what the start rests on.
CAVEAT_ENTRY_DIRECTORY_HOLDS: Final = "entry-directory-holds"

ScannedKind = Literal["file", "directory"]
SCANNED_KIND_FILE: ScannedKind = "file"
"""The content path is a file: ES-DE lists it where its extension is a token of the system's accept-list."""
SCANNED_KIND_DIRECTORY: ScannedKind = "directory"
"""The content path is a directory: ES-DE lists it as one game where its name ends in a token of the accept-list
other than ``.``, and otherwise walks into it as a folder."""
# What kind of path ES-DE's scan meets, the vocabulary of the naming caveat's ``kind``.
SCANNED_KINDS = (SCANNED_KIND_FILE, SCANNED_KIND_DIRECTORY)

UnscannedReason = Literal["token-not-listed", "directory-without-token"]
UNSCANNED_TOKEN_NOT_LISTED: UnscannedReason = "token-not-listed"
"""The path's extension, from its last dot with case preserved, is no token of the system's accept-list."""
UNSCANNED_DIRECTORY_WITHOUT_TOKEN: UnscannedReason = "directory-without-token"
"""The path is a directory whose name yields no token — it has no dot, or ends in one — so ES-DE takes it as a folder
of games, never as a game."""
# Why ES-DE's scan would not list the path under its name, the vocabulary of the naming caveat's ``reason``.
UNSCANNED_REASONS = (UNSCANNED_TOKEN_NOT_LISTED, UNSCANNED_DIRECTORY_WITHOUT_TOKEN)

DirectoryUnreadReason = Literal["own-loader", "content-override-unread", "core-unread"]
DIRECTORY_UNREAD_OWN_LOADER: DirectoryUnreadReason = "own-loader"
"""The core takes its content by path (``need_fullpath``), so RetroArch hands it the directory and the core's own
loader decides, which atlas has not read."""
DIRECTORY_UNREAD_CONTENT_OVERRIDE: DirectoryUnreadReason = "content-override-unread"
"""The core takes its content as bytes, but the directory's name carries an extension, and a content override the
core registers for that extension may hand it the path instead; atlas does not read the overrides."""
DIRECTORY_UNREAD_CORE: DirectoryUnreadReason = "core-unread"
"""The installed core could not be read, or its reading does not say how it takes its content."""
# Why a directory's route is not followed to the end, the vocabulary of ``entry-directory-unread``'s ``reason``.
DIRECTORY_UNREAD_REASONS = (
    DIRECTORY_UNREAD_OWN_LOADER,
    DIRECTORY_UNREAD_CONTENT_OVERRIDE,
    DIRECTORY_UNREAD_CORE,
)
