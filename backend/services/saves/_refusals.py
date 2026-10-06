"""Refusals that more than one module of the saves package answers with.

A benign skip is a refusal (``domain.save_answer.BENIGN_SYNC_SKIP_REASONS``
names the reasons): it subclasses ``lib.errors.Refused`` rather than
``domain.refusal.DomainRefused``, because the session lifecycle reads a sync's
refusals as ``Refused`` and answers a benign one with no toast.
"""

from lib.errors import NamedRefused


class SaveShapeUnsupported(NamedRefused):
    """The emulator's save is not a per-game file set this program can carry."""

    reason = "save_shape_unsupported"


class SavefilesInContentDir(NamedRefused):
    """The emulator writes this game's save beside the game's content, where the sync does not reach."""

    reason = "savefiles_in_content_dir"
