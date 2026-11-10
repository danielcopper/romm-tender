"""ActiveCoreResolver — the single read-path emulator-resolution seam per ROM.

The one place that answers "which emulator entry does this ROM start with?",
combining the per-game ``emulator_override`` and per-platform core selection
(the two deviations Tender owns) with the system-layer default. Every per-game
read consumer and the start check (``services/game_start.py``) draw from this
seam, so what a page says and what a start asks with never diverge.

Precedence (three layers): DB ``emulator_override`` (top) → ``settings.json``
per-platform core → the default, the entry the source ships first
(:func:`domain.emulator_choice.choose_emulator`). An ES-DE
``<alternativeEmulator>`` or ``<altemulator>`` never moves that default
(ADR-0012). A stored pick applies only while its source is the one games start
through; otherwise the default applies and the pick is kept (#2222 D5). A
stored label stands while it names an entry, whatever that entry's state: one
that cannot start is refused at Play (#2195 D7, D23). Only a label that no
longer exists gives way to the next layer, with a warning. The per-game or
per-platform label may name a **standalone** emulator (not just a libretro
core); it resolves the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.emulator_choice import ResolvedEmulator, choose_emulator
from domain.emulator_commands import option_for_label
from domain.emulator_pick import StoredPick, pick_for, source_kind

if TYPE_CHECKING:
    import logging

    from domain.emulator_commands import EmulatorOption
    from domain.emulator_sources import SourcesReading
    from domain.rom import Rom
    from domain.rom_install import RomInstall
    from services.protocols import (
        CoreInfoProvider,
        PlatformCoreReader,
        PlatformSystems,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class ActiveCoreResolverConfig:
    """Frozen wiring bundle handed to ``ActiveCoreResolver.__init__``.

    Carries the SQLite Unit-of-Work factory (to read the ROM's
    ``platform_slug`` + ``emulator_override``), the core-info read seam (the
    system's emulator options), the per-platform
    core reader (the ``settings.json`` ``platform_cores`` map), the platform's
    system — the install record's for an installed ROM — and the logger used to
    warn on a stale label.
    """

    uow_factory: UnitOfWorkFactory
    core_info: CoreInfoProvider
    platform_core_reader: PlatformCoreReader
    platform_systems: PlatformSystems
    logger: logging.Logger


class ActiveCoreResolver:
    """Resolve the emulator entry one ROM starts with, by ``rom_id``."""

    def __init__(self, *, config: ActiveCoreResolverConfig) -> None:
        self._uow_factory = config.uow_factory
        self._core_info = config.core_info
        self._platform_core_reader = config.platform_core_reader
        self._platform_systems = config.platform_systems
        self._logger = config.logger

    def active_emulator_for_rom(self, rom_id: int, *, reading: SourcesReading | None = None) -> EmulatorOption | None:
        """Return the entry the ROM ``rom_id`` starts with, ``None`` where none is in effect.

        The entry half of :meth:`resolve_rom`. ``None`` where no entry is in
        effect, where the system lists none, where no system is found in the
        answering source, or where the catalogue cannot be read.
        """
        resolved = self.resolve_rom(rom_id, reading=reading)
        return resolved.choice.option if resolved is not None else None

    def resolve_rom(self, rom_id: int, *, reading: SourcesReading | None = None) -> ResolvedEmulator | None:
        """Return the ROM ``rom_id``'s emulator choice and its options, or ``None`` with no system to ask.

        Reads the ROM's ``platform_slug`` + ``emulator_override`` once, then
        applies the three-layer precedence
        (:func:`domain.emulator_choice.choose_emulator`). A pick whose label no
        longer exists is passed over with a WARNING. What the game starts with
        that entry is the resolver's launch command, asked at the start
        (#2221 D7); whether the entry takes the game's file at all is the
        launch verdict's, asked on the game's page (#2195 D21, D26).

        *reading* is a run's one reading of the emulator sources, handed down so
        a run resolving many ROMs asks each catalogue question once; a call
        without one asks fresh.
        """
        rom, install = self._read_rom_and_install(rom_id)
        if rom is None:
            self._logger.warning("active_core_resolver: no ROM for rom_id=%s; no emulator to resolve", rom_id)
            return None

        platform = self._platform_systems.rom_system(rom.platform_slug, install, reading=reading)
        system = platform.taken
        if system is None:
            return None
        answer = self._core_info.get_emulator_options(system, reading=reading)
        options = answer["options"]
        starting = source_kind(answer)
        game = pick_for(
            StoredPick(label=rom.emulator_override, source=rom.emulator_override_source)
            if rom.emulator_override is not None and rom.emulator_override_source is not None
            else None,
            starting,
        )
        platform = (
            None
            if game is not None and option_for_label(options, game.label) is not None
            else pick_for(self._platform_core_reader.get_platform_core(rom.platform_fs_slug), starting)
        )
        choice = choose_emulator(options, game=game, platform=platform)
        self._warn_vanished(game, platform, options, rom, system)
        return ResolvedEmulator(choice=choice, options=options, source=answer["source"])

    def _warn_vanished(
        self,
        game: StoredPick | None,
        platform: StoredPick | None,
        options: list[EmulatorOption],
        rom: Rom,
        system: str,
    ) -> None:
        """Warn for each stored label the precedence passed over because no entry carries it any more."""
        if game is not None and option_for_label(options, game.label) is None:
            self._logger.warning(
                "active_core_resolver: per-game override '%s' for rom_id=%s no longer resolves on %s; "
                "degrading to the per-platform/system default",
                game.label,
                rom.rom_id,
                system,
            )
        if platform is not None and option_for_label(options, platform.label) is None:
            self._logger.warning(
                "active_core_resolver: per-platform core '%s' for %s (rom_id=%s) no longer resolves; "
                "degrading to the system default",
                platform.label,
                rom.platform_slug,
                rom.rom_id,
            )

    def active_core_for_rom(self, rom_id: int) -> tuple[str | None, str | None]:
        """Return the ``(core_so, label)`` of the entry the ROM ``rom_id`` starts with.

        The ``.so``-space projection of :meth:`active_emulator_for_rom`, kept for
        the save-sync reads keyed off the libretro core (core-change detection,
        the upload's emulator tag). An entry that loads a RetroArch core yields
        its ``(core_so, label)``; any other yields ``(None, label)``, and no
        entry ``(None, None)``.
        """
        emulator = self.active_emulator_for_rom(rom_id)
        if emulator is None:
            return (None, None)
        return (emulator.core_so, emulator.label)

    def _read_rom_and_install(self, rom_id: int) -> tuple[Rom | None, RomInstall | None]:
        with self._uow_factory() as uow:
            return (uow.roms.get(rom_id), uow.rom_installs.get(rom_id))
