"""Whether one installed game's file starts with the entry Tender starts it through, in Tender's words.

The verdict is the resolver's, read in :mod:`adapters.atlas_catalogue`; this is
the value it is read into. Each of the five states has its own sentence, worded
by the panel (#2195 D2). Three of them refuse a start — Play is off, Steam's own
Play is refused — and two let it go ahead with a sentence on the game page.

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


@dataclass(frozen=True, slots=True)
class Launchability:
    """One file's verdict for one entry, with what each state's sentence names.

    ``system`` is the system it was asked as, ``file`` the file's name and
    ``extension`` the token compared, as the resolver states it. ``starts_from`` is the system's accept-list, read for
    :data:`NOT_A_LAUNCH_FILE`; ``other_entries`` the labels of the system's
    entries established to start the file, read for :data:`NOT_THIS_EMULATOR`;
    ``install_in`` the emulators that install it, ``None`` on every state but
    :data:`NEEDS_INSTALLING`, where an empty tuple says none is known.
    ``reason`` says why a verdict was :data:`NOT_ESTABLISHED`, ``None`` where
    the resolver named no reason Tender words. ``contents_unread`` says the file
    is an archive whose contents were not read; ``format_unestablished`` that
    whether the entry reads this format was not established.
    """

    state: str
    entry: str | None
    system: str
    file: str
    extension: str
    starts_from: tuple[str, ...] = ()
    other_entries: tuple[str, ...] = ()
    install_in: tuple[str, ...] | None = None
    reason: str | None = None
    contents_unread: bool = False
    format_unestablished: bool = False

    def payload(self) -> dict[str, Any]:
        """The verdict as the panel reads it."""
        return {
            "state": self.state,
            "entry": self.entry,
            "system": self.system,
            "file": self.file,
            "extension": self.extension,
            "starts_from": list(self.starts_from),
            "other_entries": list(self.other_entries),
            "install_in": None if self.install_in is None else list(self.install_in),
            "reason": self.reason,
            "contents_unread": self.contents_unread,
            "format_unestablished": self.format_unestablished,
        }
