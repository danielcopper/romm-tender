"""EmulatorSourcesService — the emulator sources as the settings list shows them, and the user's say over them.

Owns the three use cases of Settings → Emulator sources: list every detected
source with its health, its switch and its place in the order; switch one on
or off; move one up or down. The order and the switches are stored by kind in
``settings.json`` through its one writer; which source a game's questions go to
follows from them (:mod:`domain.emulator_sources`), and the next reading of the
sources takes a change into account. Refusals are raised, never returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_sources import (
    ORDER_SETTING,
    SWITCHED_OFF_SETTING,
    ArrangedSource,
    answering_source,
    move_source,
    stored_kinds,
    switch_source,
)
from lib.errors import Refused

if TYPE_CHECKING:
    import asyncio

    from domain.emulator_sources import SourceReport, SourcesReading
    from services.protocols import DebugLogger, EmulatorSourcesReader, SettingsPersister

_OFFSETS = {"up": -1, "down": 1}


@dataclass(frozen=True)
class EmulatorSourcesServiceConfig:
    """Frozen wiring bundle handed to ``EmulatorSourcesService.__init__``.

    Carries the sources seam the listing and the detected kinds are read
    through, the live settings dict the order and the switches live in, the
    persister that writes it, the event loop the I/O is run off, and the debug
    logger.
    """

    sources: EmulatorSourcesReader
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    loop: asyncio.AbstractEventLoop
    log_debug: DebugLogger


class EmulatorSourcesService:
    """Lists the emulator sources and stores the user's order and switches for them."""

    def __init__(self, *, config: EmulatorSourcesServiceConfig) -> None:
        self._sources = config.sources
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._loop = config.loop
        self._log_debug = config.log_debug

    async def get_emulator_sources(self) -> dict[str, Any]:
        """Every detected source, in the user's order, as the settings list shows it.

        ``{"sources": [...], "answering"}``, each source ``{"kind", "enabled",
        "starts_games", "root", "findings", "catalogue"}``: ``root`` is the
        folder shown under the name, ``None`` where it is a default; ``findings``
        the health findings as ``{"code", "data"}``; ``catalogue`` ``read``,
        ``sealed`` or ``unavailable``. ``answering`` is the kind of the source a
        game's questions go to, ``None`` where none answers. An empty list where
        nothing is detected.
        """
        return await self._loop.run_in_executor(None, self._listing_io)

    async def set_emulator_source_enabled(self, kind: str, enabled: object) -> dict[str, Any]:
        """Switch the detected source of *kind* on or off, and answer the listing as it now stands.

        Refuses ``invalid_value`` for an *enabled* that is not a boolean (it
        arrives off the untrusted frontend wire), and ``unknown_source`` for a
        kind that is not detected: only a listed source has a switch. One
        detection serves both the check and the listing the switch answers with.
        """
        if not isinstance(enabled, bool):
            raise Refused("invalid_value", f"A source is switched on or off, not {enabled!r}.")
        return await self._loop.run_in_executor(None, self._switch_io, kind, enabled)

    async def move_emulator_source(self, kind: str, direction: str) -> dict[str, Any]:
        """Move the detected source of *kind* one place ``up`` or ``down``, and answer the listing as it now stands.

        Refuses ``invalid_direction`` for anything but those two words,
        ``unknown_source`` for a kind that is not detected, and ``cannot_move``
        for a move past either end of the list. One detection serves both the
        check and the listing the move answers with.
        """
        offset = _OFFSETS.get(direction)
        if offset is None:
            raise Refused("invalid_direction", f"A source moves up or down, not {direction!r}.")
        return await self._loop.run_in_executor(None, self._move_io, kind, offset)

    def _listing_io(self, reading: SourcesReading | None = None) -> dict[str, Any]:
        reports = self._sources.describe(reading)
        answering = answering_source(
            tuple(ArrangedSource(kind=r.kind, enabled=r.enabled, starts_games=r.starts_games) for r in reports)
        )
        return {
            "sources": [_report_payload(report) for report in reports],
            "answering": answering.kind if answering is not None else None,
        }

    def _switch_io(self, kind: str, enabled: bool) -> dict[str, Any]:
        reading = self._sources.read()
        switched_off = switch_source(
            kind=kind,
            enabled=enabled,
            detected=_detected_kinds(reading),
            switched_off=stored_kinds(self._settings.get(SWITCHED_OFF_SETTING)),
        )
        self._settings[SWITCHED_OFF_SETTING] = list(switched_off)
        self._settings_persister.save_settings()
        self._log_debug(f"[sources] {kind} switched {'on' if enabled else 'off'}; off={list(switched_off)}")
        return self._listing_io(reading)

    def _move_io(self, kind: str, offset: int) -> dict[str, Any]:
        reading = self._sources.read()
        order = move_source(
            kind=kind,
            offset=offset,
            detected=_detected_kinds(reading),
            stored_order=stored_kinds(self._settings.get(ORDER_SETTING)),
        )
        self._settings[ORDER_SETTING] = list(order)
        self._settings_persister.save_settings()
        self._log_debug(f"[sources] {kind} moved {offset:+d}; order={list(order)}")
        return self._listing_io(reading)


def _detected_kinds(reading: SourcesReading) -> tuple[str, ...]:
    """The kinds *reading* detected, in the resolver's probe order."""
    return tuple(installation.kind for installation in reading.installations)


def _report_payload(report: SourceReport) -> dict[str, Any]:
    return {
        "kind": report.kind,
        "enabled": report.enabled,
        "starts_games": report.starts_games,
        "root": report.root,
        "findings": [{"code": finding.code, "data": dict(finding.data)} for finding in report.findings],
        "catalogue": report.catalogue,
    }
