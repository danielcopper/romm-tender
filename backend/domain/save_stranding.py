"""Whether a ROM's local saves would be left behind by a change of its shortcut.

The stranding reading of the local-save check. Which callers read it, which
read the check's ``drifted`` instead, and why "nothing recorded" means the
opposite to the two, is ``docs/architecture/save-file-sync-architecture.md``,
"The Local-Save Check".
"""

from __future__ import annotations

from typing import Literal

STRANDING_NONE = "none"
STRANDING_UNSYNCED = "unsynced"
STRANDING_UNCHECKED = "unchecked"

Stranding = Literal["none", "unsynced", "unchecked"]


def stranding_verdict(*, save_sync_on: bool, checked: bool, drifted: bool, only_here: bool) -> Stranding:
    """The stranding callers' one answer about a ROM's local saves.

    ``none`` while save sync is off, whatever the reading found: nothing is
    synced, so nothing can be stranded from a sync. Otherwise ``unchecked`` when
    the reading failed, so the callers warn without claiming the save was never
    uploaded; ``unsynced`` when a save changed since its last recorded sync
    (*drifted*) or has no recorded sync at all (*only_here*); ``none`` when
    neither holds.
    """
    if not save_sync_on:
        return STRANDING_NONE
    if not checked:
        return STRANDING_UNCHECKED
    if drifted or only_here:
        return STRANDING_UNSYNCED
    return STRANDING_NONE
