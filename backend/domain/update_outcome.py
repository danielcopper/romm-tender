"""What became of the last update, as a start of this program finds it.

Contract: everything pure about an update's outcome — the record the installer
leaves when it rolled an update back or its pre-install check refused the new
version (its filename, how it is read, and whether it still stands), and which
announcement, if any, a start owes the user: the version running now and which
way it moved. Reading the record stays in the adapter; the version a start
remembers stays in the service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeGuard

from domain.version import is_newer_version

# Which way the version moved since the previous start: to a later release is
# ``updated``, to an earlier one ``back``.
UpdateDirection = Literal["updated", "back"]

# The installer writes it into its state directory (``UPDATE_FAILURE`` in
# ``install.sh``, which ``tests/scripts/test_install_sh.py`` holds equal to this
# spelling) and removes it once a later update's new version answers.
UPDATE_FAILURE_FILENAME = "update-failure.json"


class UpdateFailureKind(StrEnum):
    """What the installer's record says became of the update, as its ``kind`` key spells it.

    A record without the key is a rollback: that is the only record an installer
    wrote before its pre-install check existed, and an older backend reading a
    ``check`` record ignores the key and tells it as one too.
    """

    ROLLBACK = "rollback"
    # The pre-install check refused the new version before anything was stopped
    # or replaced (``refuse_the_new_version`` in ``install.sh`` writes it).
    CHECK = "check"
    # A kind this reader does not know — a later installer's. No installer
    # writes this spelling; the record still says the update did not go
    # through, and names no cause this reader could word.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UpdateFailure:
    """An update that did not go through, as the installer's record states it.

    ``restored_version`` is the version the user is still on: the one a
    rollback put back, or the one a refused pre-install check never replaced.
    ``rolled_back_at`` is the record's own ISO-8601 UTC text — when the
    rollback or the refusal happened — kept as written: it is shown and
    compared, never computed with, and it is what tells one record from the
    next. The key keeps its name for both kinds so that an older backend still
    reads a ``check`` record.
    """

    attempted_version: str
    restored_version: str
    rolled_back_at: str
    kind: UpdateFailureKind = UpdateFailureKind.ROLLBACK

    def to_wire(self) -> dict[str, str]:
        """The JSON shape the outcome's answer and the refusal's push both carry."""
        return {
            "attempted_version": self.attempted_version,
            "restored_version": self.restored_version,
            "rolled_back_at": self.rolled_back_at,
            "kind": self.kind.value,
        }


def decode_update_failure(raw: str) -> UpdateFailure | None:
    """Read the installer's record, or ``None`` where it says nothing usable.

    Every one of its three version and time keys has to be a non-empty string.
    A record short of that is treated as no record at all rather than shown in
    part, because a card naming half an update would state something the
    installer did not. ``kind`` is optional and read as a rollback where it is
    absent. A ``kind`` this reader does not know is still a record — the
    update did not go through, whatever the cause — and reads as
    :attr:`UpdateFailureKind.UNKNOWN`.
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
    kind = decoded.get("kind", UpdateFailureKind.ROLLBACK.value)
    return UpdateFailure(
        attempted_version=attempted,
        restored_version=restored,
        rolled_back_at=rolled_back_at,
        kind=UpdateFailureKind(kind) if _is_known_kind(kind) else UpdateFailureKind.UNKNOWN,
    )


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


@dataclass(frozen=True)
class UpdateAnnouncement:
    """The one announcement a start owes: the version running now, and which way it moved to get there."""

    version: str
    direction: UpdateDirection


def announced_update(last_run: str | None, running: str, failure: UpdateFailure | None) -> UpdateAnnouncement | None:
    """The announcement a start owes the user, or ``None``.

    *running* is announced when *last_run* — the version the previous start
    recorded — exists and differs from it: as ``updated`` where *running* is the
    later release, as ``back`` where it is the earlier one. With no *last_run*
    this is the first start that records one, and there is nothing to compare it
    with. Two versions that differ while neither is the later — one of them
    unreadable, or two pre-releases of one release, which
    :func:`domain.version.is_newer_version` does not rank — announce nothing
    rather than a direction nothing established.

    A start the installer's record calls a rollback — back on *running* after
    trying *last_run* — announces nothing. The installer restores the database
    the recorded version lives in, so *last_run* is normally the restored version
    already; the record is the second witness, and a rollback is never
    announced, not even as ``back``: the rolled-back notice is what tells it.
    """
    if last_run is None or last_run == running:
        return None
    if failure is not None and failure.restored_version == running and failure.attempted_version == last_run:
        return None
    if is_newer_version(running, last_run):
        return UpdateAnnouncement(version=running, direction="updated")
    if is_newer_version(last_run, running):
        return UpdateAnnouncement(version=running, direction="back")
    return None


def _is_text(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _is_known_kind(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value in UpdateFailureKind
