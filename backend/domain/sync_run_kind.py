"""SyncRunKind enum — what a library sync run in flight is DOING.

A preview run and an apply run narrate the same work queue through
frames of identical shape: both emit ``DISCOVERING`` and then a
``FETCHING`` frame per unit, carrying the same step counter and the same
unit count. Nothing in that stream distinguishes them, and no sequence of
frames can be read as the answer either — the stage alternates
fetch/apply within a single apply run, and a plan's ABSENCE conflates
"this is a preview" with "nobody has established the kind yet". So the
kind is stated on the wire instead: it is claimed with the run slot
(``LibrarySyncStateBox.try_begin_run``) and every frame the run emits
carries it, the terminal frames and ``get_sync_status``'s snapshot
included.

Each member's value is the exact string the frontend's
``SyncProgress.runKind`` discriminant expects, so ``str``-based members
serialize straight onto the wire without a lookup table.
"""

from enum import StrEnum


class SyncRunKind(StrEnum):
    PREVIEW = "preview"
    APPLY = "apply"
