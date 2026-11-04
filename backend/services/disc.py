"""DiscService — the multi-disc picker's read + write use cases.

Owns the two use cases behind the disc picker: ``get_disc_selection``
reports whether a ROM is multi-disc and, if so, the launchable discs plus the
current and default targets; ``select_disc`` pins a disc (or clears the pin back
to the default), which the next start is asked with.

The disc pick lands on the ``Rom`` aggregate via the Unit-of-Work (the pin-only
``set_selected_disc`` write path, never the sync UPSERT), mirroring how the
per-game emulator override is written in :class:`CoreService`. Enumeration and
launch-path resolution both go through the shared :class:`DiscLaunchResolver`
seam so the list the picker shows is the list the start resolves over, and the
disc a start opens never diverges from the picker's selection. The picker only
ever applies to a folder-backed (multi-file) install — a single-file ROM owns no
disc folder and reports ``multi_disc: False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.disc_selection import default_descriptor
from lib.errors import NotInstalled, Refused

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols import (
        ConflictRules,
        DiscResolver,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class DiscServiceConfig:
    """Frozen wiring bundle handed to ``DiscService.__init__``.

    Carries the runtime infrastructure (event loop, logger), the SQLite
    Unit-of-Work factory (to read the ROM + its install and write the disc pin),
    the shared per-ROM disc resolver (enumeration + launch-path resolution), and
    the ``ConflictRules`` ``select_disc`` checks at its entry.
    """

    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    uow_factory: UnitOfWorkFactory
    disc_resolver: DiscResolver
    conflict_rules: ConflictRules


class DiscService:
    """Disc-picker reads (``get_disc_selection``) and writes (``select_disc``).

    The write checks its endpoint's conflict rules at its entry, under that
    endpoint's name, and raises the rule's refusal when one holds
    (GLOSSARY.md → Conflict rules).
    """

    def __init__(self, *, config: DiscServiceConfig) -> None:
        self._loop = config.loop
        self._logger = config.logger
        self._uow_factory = config.uow_factory
        self._disc_resolver = config.disc_resolver
        self._rules = config.conflict_rules

    async def get_disc_selection(self, rom_id: int) -> dict[str, Any]:
        """Report the disc picker's state for ``rom_id``.

        Returns ``{"multi_disc": False}`` when the ROM is unknown, not installed,
        owns no disc folder (single-file install), or enumerates fewer than two
        discs — the frontend renders no picker in any of those cases. For a
        multi-disc ROM returns ``{"multi_disc": True, "discs": [{"filename",
        "label", "index"}, ...], "selected": <roms.selected_disc | None>,
        "default": {"kind", "label", "filename"}}``. ``selected`` is
        down-validated: a stale pin whose file is no longer enumerated reports as
        ``None`` so the badge matches what a start opens (the start degrades the
        same stale pin to the default). Read-only over the local filesystem;
        the not-multi-disc answers are the normal "no picker" response, not
        failures.
        """
        return await self._loop.run_in_executor(None, self._get_disc_selection_io, rom_id)

    def _get_disc_selection_io(self, rom_id: int) -> dict[str, Any]:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            install = uow.rom_installs.get(rom_id)
            if rom is None or install is None or install.rom_dir is None:
                return {"multi_disc": False}
            selected = rom.selected_disc
            file_path = install.file_path
        # Enumeration lists the install directory, so it runs on the snapshot
        # after the read UoW closes: a UoW takes SQLite's BEGIN IMMEDIATE write
        # lock, and holding one across file I/O stalls every other writer.
        discs = self._disc_resolver.enumerate_discs(install)
        if len(discs) < 2:
            return {"multi_disc": False}
        # Down-validate the pin: a stale pin (the file is no longer enumerated)
        # surfaces as ``None`` so the UI badge matches what a start actually opens
        # (the start degrades the same stale pin to the default). A live pin is
        # returned verbatim.
        if selected is not None and selected not in {disc.filename for disc in discs}:
            selected = None
        return {
            "multi_disc": True,
            "discs": [{"filename": d.filename, "label": d.label, "index": d.index} for d in discs],
            "selected": selected,
            "default": default_descriptor(file_path, discs),
        }

    async def select_disc(self, rom_id: int, filename: str | None) -> dict[str, Any]:
        """Pin (or clear with ``None``) the disc selection for ``rom_id``.

        ``filename is None`` clears the pin so the ROM follows the default (the
        ``.m3u`` when ``file_path`` is one, else disc 1). A non-``None``
        *filename* must name one of the enumerated discs — an unknown filename is
        refused with ``not_found`` and **nothing is written**. The ROM must be a
        multi-disc install: an unknown/uninstalled ROM or a single-file install
        is refused with ``not_installed``, a folder with fewer than two discs
        with ``unsupported``, and nothing is written. On success the
        pick is persisted via the pin-only ``set_selected_disc`` write path and
        the response carries the now-effective ``selected`` value; the next start
        opens that disc.
        """
        async with self._rules.hold("select_disc", update=True, migration=True, prune=True):
            return await self._loop.run_in_executor(None, self._select_disc_io, rom_id, filename)

    def _select_disc_io(self, rom_id: int, filename: str | None) -> dict[str, Any]:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            install = uow.rom_installs.get(rom_id)
            if rom is None or install is None or install.rom_dir is None:
                raise NotInstalled(f"ROM {rom_id} is not installed as a multi-disc ROM")
        # Enumerate and validate between the two transactions: enumeration lists
        # the install directory, and a UoW holds SQLite's BEGIN IMMEDIATE write
        # lock, so file I/O inside one stalls every other writer in the backend.
        discs = self._disc_resolver.enumerate_discs(install)
        if len(discs) < 2:
            raise Refused("unsupported", f"ROM {rom_id} is not a multi-disc ROM")
        if filename is not None and filename not in {disc.filename for disc in discs}:
            # Refuse BEFORE any write — never pin a disc no enumeration
            # can resolve to a launchable path.
            raise Refused("not_found", f"'{filename}' is not a disc of ROM {rom_id}")
        with self._uow_factory() as uow:
            # The row is re-read because the pick is now decided against a
            # snapshot: a background sync, a finishing download or the
            # removed-game cleanup can retire the ROM between the two
            # transactions, each on its own connection. The install is re-read
            # for the same admission the first transaction applied — it exists
            # and is folder-backed.
            rom = uow.roms.get(rom_id)
            current_install = uow.rom_installs.get(rom_id)
            if rom is None or current_install is None or current_install.rom_dir is None:
                raise NotInstalled(f"ROM {rom_id} is not installed as a multi-disc ROM")
            if filename is None:
                rom.clear_selected_disc()
            else:
                rom.pin_selected_disc(filename)
            uow.roms.set_selected_disc(rom_id, rom.selected_disc)
            selected = rom.selected_disc
        return {"success": True, "selected": selected}
