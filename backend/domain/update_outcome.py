"""What became of the last update, as a start of this program finds it.

Contract: everything pure about an update's outcome — the record the installer
leaves when it rolled an update back (its filename, how it is read, and whether
it still stands), and which version, if any, a start owes the user an
announcement of. Reading the
record stays in the adapter; the version a start remembers stays in the service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeGuard

# The installer writes it into its state directory (``UPDATE_FAILURE`` in
# ``install.sh``, which ``tests/scripts/test_install_sh.py`` holds equal to this
# spelling) and removes it once a later update's new version answers.
UPDATE_FAILURE_FILENAME = "update-failure.json"


@dataclass(frozen=True)
class UpdateFailure:
    """An update the installer rolled back, as its record states it.

    ``rolled_back_at`` is the record's own ISO-8601 UTC text, kept as written: it
    is shown and compared, never computed with, and it is what tells one record
    from the next.
    """

    attempted_version: str
    restored_version: str
    rolled_back_at: str


def decode_update_failure(raw: str) -> UpdateFailure | None:
    """Read the installer's record, or ``None`` where it says nothing usable.

    Every one of its three keys has to be a non-empty string. A record short of
    that is treated as no record at all rather than shown in part, because a
    card naming half an update would state something the installer did not.
    """
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    attempted = decoded.get("attempted_version")
    restored = decoded.get("restored_version")
    rolled_back_at = decoded.get("rolled_back_at")
    if not (_is_text(attempted) and _is_text(restored) and _is_text(rolled_back_at)):
        return None
    return UpdateFailure(attempted_version=attempted, restored_version=restored, rolled_back_at=rolled_back_at)


def standing_update_failure(failure: UpdateFailure | None, running: str) -> UpdateFailure | None:
    """The record, where it still describes this start, or ``None``.

    A record stands only while *running* is the version it restored. The
    installer removes it once a later update's new version answered, but a
    record that outlived that — the removal failed, or the version moved some
    other way — describes a start that is over, and is a leftover rather than a
    rollback to tell the user about.
    """
    if failure is None or failure.restored_version != running:
        return None
    return failure


def announced_update(last_run: str | None, running: str, failure: UpdateFailure | None) -> str | None:
    """The version a start owes the user an announcement of, or ``None``.

    *running* is announced when *last_run* — the version the previous start
    recorded — exists and differs from it. With no *last_run* this is the first
    start that records one, and there is nothing to compare it with.

    A start the installer's record calls a rollback — back on *running* after
    trying *last_run* — announces nothing. The installer restores the database
    the recorded version lives in, so *last_run* is normally the restored version
    already; the record is the second witness, and a rollback is never
    announced as an update.
    """
    if last_run is None or last_run == running:
        return None
    if failure is not None and failure.restored_version == running and failure.attempted_version == last_run:
        return None
    return running


def _is_text(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())
