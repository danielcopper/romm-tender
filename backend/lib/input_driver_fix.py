"""What an attempt to repair RetroArch's ``input_driver`` came to.

Cross-cutting enum shared by the adapter that rewrites ``retroarch.cfg``
(``adapters/steam_config.py``) and the Protocol services depend on
(``services/protocols/transport.py``). It lives in ``lib/`` because both
layers import it and ``import-linter`` forbids the adapter→service and
service→adapter directions. What the user is told about each outcome is the
service's decision, not the adapter's.
"""

from __future__ import annotations

from enum import StrEnum


class InputDriverFix(StrEnum):
    """The outcome of one repair attempt."""

    FIXED = "fixed"
    """``input_driver`` was ``x`` and now reads ``sdl2``."""

    NOTHING_TO_FIX = "nothing_to_fix"
    """No RetroArch config sets ``input_driver``, or the first one that does is not ``x``. Nothing was written."""

    WRITE_FAILED = "write_failed"
    """The value needed changing and the repair failed, reading the config or writing its replacement.

    The config is left as it was.
    """
