"""ActiveCoreResolver — the single read-path core-resolution seam per ROM.

The one place that answers "which RetroArch core will this ROM actually launch
with?", combining the per-game ``emulator_override`` and per-platform core
selection (the two deviations Tender owns) with the system-layer
ES-DE/RetroDECK resolution. Every per-game core read consumer and every
launch-bake site draws from this seam so the read-path core never diverges from
the launched core.

Precedence (three layers, then the plain launch): DB ``emulator_override`` (top)
→ ``settings.json`` per-platform core → the default, the entry the source ships
first → ``None`` (:func:`domain.emulator_choice.choose_emulator`). An ES-DE
``<alternativeEmulator>`` or ``<altemulator>`` never moves that default
(ADR-0012). A stored label stands while it names an entry, whatever that
entry's state: one Tender cannot bake starts through the plain launch, and one
that cannot start is refused at Play. Only a label that no longer exists gives
way to the next layer, with a warning. The per-game/per-platform label may name
a **standalone** emulator (not just a libretro core); it resolves the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.emulator_choice import DEFAULT, ResolvedEmulator, choose_emulator
from domain.emulator_commands import option_for_label, option_to_invocation
from domain.emulator_pick import StoredPick
from domain.rom_files import folder_boot_root
from domain.shortcut_data import DIRECT, RUN_GAME, EmulatorInvocation

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
    classified emulator options + the system-layer default), the per-platform
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
    """Resolve the active RetroArch core for one ROM by ``rom_id``."""

    def __init__(self, *, config: ActiveCoreResolverConfig) -> None:
        self._uow_factory = config.uow_factory
        self._core_info = config.core_info
        self._platform_core_reader = config.platform_core_reader
        self._platform_systems = config.platform_systems
        self._logger = config.logger

    def active_emulator_for_rom(
        self, rom_id: int, *, reading: SourcesReading | None = None
    ) -> EmulatorInvocation | None:
        """Return the :class:`EmulatorInvocation` the ROM ``rom_id`` will launch with.

        The launch-bake seam, the invocation half of :meth:`resolve_rom`.
        ``None`` is the plain launch: where the ROM's entry cannot be baked,
        where it has no entry, including when ``es_systems.xml`` cannot be read
        and when the platform has no switched-on system in the answering source.
        """
        resolved = self.resolve_rom(rom_id, reading=reading)
        return resolved.invocation if resolved is not None else None

    def resolve_rom(self, rom_id: int, *, reading: SourcesReading | None = None) -> ResolvedEmulator | None:
        """Return the ROM ``rom_id``'s emulator choice and what it bakes to, or ``None`` with no system to ask.

        Reads the ROM's ``platform_slug`` + ``emulator_override`` once, then
        applies the three-layer precedence
        (:func:`domain.emulator_choice.choose_emulator`). The default layer's
        invocation is the catalogue's own (``get_default_emulator``); a stored
        pick renders its entry. A pick whose label no longer exists is passed
        over with a WARNING.

        Final step: a resolved emulator program (no RetroArch core) whose
        install is a folder-boot layout (PS3 — ``…/PS3_GAME/USRDIR/EBOOT.BIN``) is rewritten
        to the ``direct`` sandbox form (ADR-0019). RetroDECK's ``run_game.sh``
        reinterprets a directory ``%ROM%`` as an ES-DE "directory as a file" and
        can never launch a bare game folder, so a folder-boot game must run its
        emulator launcher directly inside the sandbox instead. The rewrite keys
        off :func:`folder_boot_root` — the same fact the disc/bake-path seam uses
        to fold the target to the game folder — so the invocation form and the
        baked path are always decided from one layout fact. A RetroArch core,
        a non-folder install, or a program that carries no launcher (which
        launchers the reading hands on is
        ``adapters/atlas_catalogue.py::_sandbox_launcher``'s rule) all leave the
        invocation unchanged, and a folder-boot install left without the direct
        form is ``folder_unstartable``.

        *reading* is a run's one reading of the emulator sources, handed down so
        a run resolving many ROMs asks each catalogue question once; a call
        without one asks fresh.
        """
        rom, install = self._read_rom_and_install(rom_id)
        if rom is None:
            self._logger.warning("active_core_resolver: no ROM for rom_id=%s; resolving to plain launch", rom_id)
            return None

        platform = self._platform_systems.rom_system(rom.platform_slug, install, reading=reading)
        system = platform.taken
        if system is None:
            return None
        options = self._core_info.get_emulator_options(system, reading=reading)["options"]
        game = (
            StoredPick(label=rom.emulator_override, source=rom.emulator_override_source)
            if rom.emulator_override is not None
            else None
        )
        platform_label = (
            None
            if game is not None and option_for_label(options, game.label) is not None
            else self._platform_core_reader.get_platform_core(rom.platform_slug)
        )
        choice = choose_emulator(
            options,
            game=game,
            platform=StoredPick(label=platform_label, source=None) if platform_label is not None else None,
        )
        self._warn_vanished(game, platform_label, options, rom, system)
        if choice.layer in (DEFAULT, None):
            emulator = self._core_info.get_default_emulator(system, reading=reading)
        else:
            emulator = option_to_invocation(choice.option)
        invocation = self._maybe_folder_boot_direct(emulator, install, rom_id)
        folder = install is not None and folder_boot_root(install.file_path, install.rom_dir) is not None
        return ResolvedEmulator(
            choice=choice,
            options=options,
            invocation=invocation,
            folder_unstartable=folder and (invocation is None or invocation.form != DIRECT),
        )

    def _warn_vanished(
        self, game: StoredPick | None, platform_label: str | None, options: list[EmulatorOption], rom: Rom, system: str
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
        if platform_label is not None and option_for_label(options, platform_label) is None:
            self._logger.warning(
                "active_core_resolver: per-platform core '%s' for %s (rom_id=%s) no longer resolves; "
                "degrading to the system default",
                platform_label,
                rom.platform_slug,
                rom.rom_id,
            )

    def _maybe_folder_boot_direct(
        self, emulator: EmulatorInvocation | None, install: RomInstall | None, rom_id: int
    ) -> EmulatorInvocation | None:
        """Rewrite an emulator program to the folder-boot ``direct`` form when warranted.

        Fires only for a ``run_game`` invocation that loads no RetroArch core and
        whose *install* is a folder-boot layout (:func:`folder_boot_root` returns
        a game root), and returns a ``direct`` invocation through the launcher it
        carries. The fold execs an emulator program, never RetroArch with a core,
        so a core invocation is left unchanged whatever it carries. So is one on
        a non-folder install or a missing install, and one with no launcher; the
        last is logged, because the baked ``run_game`` form will fail to launch
        a folder until a later re-bake heals it.
        """
        if emulator is None or emulator.form != RUN_GAME or emulator.core_so is not None or emulator.command is None:
            return emulator
        if install is None or folder_boot_root(install.file_path, install.rom_dir) is None:
            return emulator
        launcher = emulator.launcher
        if launcher is None:
            self._logger.warning(
                "active_core_resolver: folder-boot rom_id=%s resolves to '%s' but it carries no sandbox "
                "launcher to exec; keeping the run_game form (launch will fail until healed)",
                rom_id,
                emulator.label,
            )
            return emulator
        return EmulatorInvocation.direct(emulator.command, launcher, emulator.label, emulator.emulator)

    def active_core_for_rom(self, rom_id: int) -> tuple[str | None, str | None]:
        """Return the ``(core_so, label)`` the ROM ``rom_id`` will launch with.

        The read-path projection of :meth:`active_emulator_for_rom`, kept for the
        ``.so``-space consumers (BIOS status, per-core save dir, save-emulator
        tag, core-change detection, the cores menu's active marker). An emulator
        that loads a RetroArch core yields its ``(core_so, label)``; any other
        yields ``(None, label)`` — those consumers already degrade on a ``None``
        core exactly as they did for the old ``(None, None)`` resolution, so the
        read-path core never disagrees with the launch.
        """
        emulator = self.active_emulator_for_rom(rom_id)
        if emulator is None:
            return (None, None)
        return (emulator.core_so, emulator.label)

    def _read_rom_and_install(self, rom_id: int) -> tuple[Rom | None, RomInstall | None]:
        with self._uow_factory() as uow:
            return (uow.roms.get(rom_id), uow.rom_installs.get(rom_id))
