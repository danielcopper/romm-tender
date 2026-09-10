"""Where one start ended up reading and writing the user's data.

Produced by the start-up migration, which is the only thing that can answer it:
the roots are the plugin's own only once the data is actually there, and until
then the Decky-assigned directories are what the plugin runs from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class SourceDescription(TypedDict):
    """One older location as the choice modal shows it.

    Every location the plugin knows is described, so ``present`` carries whether
    this one is on disk at all — a list quietly reduced to one entry would read
    as a choice with a single answer.

    ``size_bytes`` and ``changed_at`` describe the data half alone — the library
    the reader is choosing between — and both are ``None`` together where the
    reading could not be completed, because a size that is merely close is worse
    than none when it is what the reader chooses on. ``changed_at`` is an
    ISO-8601 instant.
    """

    source: str
    path: str
    present: bool
    size_bytes: int | None
    changed_at: str | None


@dataclass(frozen=True)
class UserDataLocations:
    """The two directories this run uses, and what the user still has to answer.

    ``settings_dir`` and ``data_dir`` are each either the plugin's own root or —
    where that half could not be filled — the Decky-assigned directory the data
    is still in. The two are decided separately, so a half that migrated stays
    migrated while the other one retries on the next start.

    ``choice_required`` means two older locations both hold a library and the
    plugin declined to pick. ``failure`` carries what went wrong where a copy was
    attempted and did not finish; both are conditions the panel raises, and
    neither stops the plugin, which keeps running from the directories above.
    """

    settings_dir: str
    data_dir: str
    choice_required: bool
    failure: str | None
