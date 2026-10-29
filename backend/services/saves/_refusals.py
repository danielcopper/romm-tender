"""Refusals that more than one module of the saves package answers with.

A benign skip is a refusal (``domain.save_answer.BENIGN_SYNC_SKIP_REASONS``
names the reasons): it subclasses ``lib.errors.Refused`` rather than
``domain.refusal.DomainRefused``, because the session lifecycle reads a sync's
refusals as ``Refused`` and answers a benign one with no toast.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.save_answer import save_finding_details, save_shape_message
from lib.errors import NamedRefused

if TYPE_CHECKING:
    from domain.save_answer import SaveAnswer


class SaveShapeUnsupported(NamedRefused):
    """The emulator's save is not a per-game file set this program can carry.

    Every save write outside the sync (slot switch, rollback) raises it for a
    missing saves root too, with that answer's own message; a sync, and the
    slot choice that would set one up, refuse that one with
    ``saves_root_missing`` instead.
    """

    reason = "save_shape_unsupported"


def shape_refusal(answer: SaveAnswer, **details: Any) -> SaveShapeUnsupported:
    """The refusal of *answer*, worded for it and carrying what the panel words it from."""
    return SaveShapeUnsupported(save_shape_message(answer), **save_finding_details(answer), **details)


class SavefilesInContentDir(NamedRefused):
    """The emulator writes this game's save beside the game's content, where the sync does not reach."""

    reason = "savefiles_in_content_dir"


class GameRunning(NamedRefused):
    """The game is running, so nothing may change its saves until it is quit."""

    reason = "game_running"


def game_running(name: str, **details: Any) -> GameRunning:
    """The refusal of a press that would change the saves of *name*, which is running."""
    return GameRunning(f"{name} is running. Quit it first.", **details)
