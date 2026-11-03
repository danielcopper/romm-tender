"""The caveat codes of the opens reading, and the closed vocabulary of a failure's ``effect``.

They live apart from :mod:`atlas.disc_opens`, which raises them, because
:data:`atlas.placement.ENUMERATED_DATA` registers the ``effect`` vocabulary
under each failure code, and :mod:`atlas.placement` cannot import the module
that imports its :class:`~atlas.placement.Caveat`.
"""

from __future__ import annotations

from typing import Final, Literal

# The reasons, one code each. A refusal is the emulator's own stop; the same
# code on a launchable answer is a file the emulator goes on without. A
# refusal can carry go-on caveats of these codes as well, said before the
# stop, so each caveat of the six failure codes states which it is in its
# data key ``effect`` — or that what the emulator does there is not
# established.
CAVEAT_OPENS_FILE_MISSING: Final = "opens-file-missing"
CAVEAT_OPENS_TRACK_REFUSED: Final = "opens-track-refused"
CAVEAT_OPENS_SHEET_REFUSED: Final = "opens-sheet-refused"
CAVEAT_OPENS_FORMAT_REFUSED: Final = "opens-format-refused"
CAVEAT_OPENS_PLAYLIST_REFUSED: Final = "opens-playlist-refused"
CAVEAT_OPENS_FORMAT_UNREAD: Final = "opens-format-unread"
CAVEAT_OPENS_CONTENT_UNREAD: Final = "opens-content-unread"
CAVEAT_OPENS_FILE_UNREAD: Final = "opens-file-unread"
CAVEAT_OPENS_SETTING_UNREAD: Final = "opens-setting-unread"
CAVEAT_OPENS_BOUND_EXCEEDED: Final = "opens-bound-exceeded"
CAVEAT_OPENS_PATH_TOO_LONG: Final = "opens-path-too-long"
CAVEAT_OPENS_ENTRY_IGNORED: Final = "opens-entry-ignored"

# The codes a failure of the open is stated under, each carrying ``effect``.
OPENS_FAILURE_CODES = (
    CAVEAT_OPENS_FILE_MISSING,
    CAVEAT_OPENS_TRACK_REFUSED,
    CAVEAT_OPENS_SHEET_REFUSED,
    CAVEAT_OPENS_FORMAT_REFUSED,
    CAVEAT_OPENS_PATH_TOO_LONG,
    CAVEAT_OPENS_PLAYLIST_REFUSED,
)

OpensEffect = Literal["stops", "goes-on", "unestablished"]
EFFECT_STOPS: OpensEffect = "stops"
"""The emulator stops its open at this failure: it is the refusal behind ``entry-not-accepted``."""
EFFECT_GOES_ON: OpensEffect = "goes-on"
"""The emulator goes on without this file, beside a launchable answer or beside another file's refusal."""
EFFECT_UNESTABLISHED: OpensEffect = "unestablished"
"""The emulator's course at this file is not established: the take is unestablished, not refused."""
# What follows a failure of the open, the vocabulary of ``effect``.
OPENS_EFFECTS = (EFFECT_STOPS, EFFECT_GOES_ON, EFFECT_UNESTABLISHED)
