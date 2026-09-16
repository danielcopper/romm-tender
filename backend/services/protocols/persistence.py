"""On-disk persistence Protocols for the program's settings.

Services delegate disk round-trips for settings to these Protocols so
atomic writes, locking, and corrupt-file recovery stay in adapters. Each
Protocol carries a domain-specific method name (e.g. ``save_settings``)
rather than a generic ``__call__`` so the type checker rejects mis-wires
between the plugin-level persisters.
"""

from __future__ import annotations

from typing import Protocol


class SettingsPersister(Protocol):
    """Persist the live settings dict (``settings.json``)."""

    def save_settings(self) -> None: ...
