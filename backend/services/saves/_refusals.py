"""Refusals the saves package raises from more than one of its modules.

A benign skip is a refusal (``domain.save_answer.BENIGN_SYNC_SKIP_REASONS``
names the reasons): it subclasses ``lib.errors.Refused`` rather than
``domain.refusal.DomainRefused``, because the session lifecycle reads a sync's
refusals as ``Refused`` and answers a benign one with no toast.
"""

from lib.errors import NamedRefused


class SaveShapeUnsupported(NamedRefused):
    """The emulator's save is not a per-game file set this program can carry."""

    reason = "save_shape_unsupported"
