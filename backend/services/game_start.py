"""GameStartService — prepares one start of a game: the resolver's command, handed to the launcher.

Every start goes through here, Tender's Play and Steam's own Play alike (#2221
D7): the start check asks for the command the resolver gives for the game's
entry in effect, with the game's own path, and hands it to the shortcut
launcher for that one start (D15). Nothing of the command is stored; a start
whose hand-over does not come about starts nothing (D11, D19).

The shortcut's launch options are Tender's: the answer names them, the
shortcut's appId, and the panel sets them back before the start (D16).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_choice import NO_ENTRY, STARTS, start_payload
from domain.firmware_store import STORE_FOLDER
from domain.rom_files import asked_path
from domain.shortcut_data import shortcut_launch_options
from domain.start_command import StartRefused, source_refusal
from lib.errors import Refused

if TYPE_CHECKING:
    import asyncio

    from domain.rom import Rom
    from domain.rom_install import RomInstall
    from services.protocols import (
        ActiveCoreReader,
        ConflictRules,
        DebugLogger,
        DiscResolver,
        StartCommandReader,
        StartHandover,
        TenderFolderPlace,
        UnitOfWorkFactory,
    )


# The folder in Tender's data folder a command that names none starts in (#2221 D6).
START_FOLDER = "start-folder"


@dataclass(frozen=True)
class GameStartServiceConfig:
    """Frozen wiring bundle handed to ``GameStartService.__init__``.

    Carries the Unit-of-Work factory (the game's record, its install and its
    shortcut), the entry the game starts with (``active_core``) and the path it
    starts with (``disc_resolver``), the resolver's command
    (``start_commands``), the hand-over to the launcher — ``None`` where this
    session has no runtime directory to hand a start over in (#2708 D4) — and
    Tender's folder, whose firmware store a start may read. ``start_folder`` is
    the folder a command that names none starts in (#2221 D6). ``loop`` runs the
    preparation off the loop thread, ``conflict_rules`` are what it checks and
    leases through.
    """

    uow_factory: UnitOfWorkFactory
    active_core: ActiveCoreReader
    disc_resolver: DiscResolver
    start_commands: StartCommandReader
    handover: StartHandover | None
    tender_folder: TenderFolderPlace
    start_folder: str
    loop: asyncio.AbstractEventLoop
    conflict_rules: ConflictRules
    log_debug: DebugLogger


class GameStartService:
    """Prepares the next start of a game's shortcut."""

    def __init__(self, *, config: GameStartServiceConfig) -> None:
        self._uow_factory = config.uow_factory
        self._active_core = config.active_core
        self._disc_resolver = config.disc_resolver
        self._start_commands = config.start_commands
        self._handover = config.handover
        self._tender_folder = config.tender_folder
        self._start_folder = config.start_folder
        self._loop = config.loop
        self._rules = config.conflict_rules
        self._log_debug = config.log_debug

    async def prepare_game_start(self, rom_id: int | str) -> dict[str, Any]:
        """Hand the next start of ``rom_id``'s shortcut its command, and answer the launch options it is started with.

        Answers ``{success, app_id, launch_options, prune_lease_token}``:
        ``launch_options`` is the shortcut's appId, which the panel sets on the
        shortcut before it starts it (#2221 D16), under a ``launch_reconfirm``
        lease. Refuses ``not_found`` for a game Tender has no shortcut for,
        ``not_installed`` for one that is not downloaded, ``cannot_start`` —
        with the ``start`` it comes to — where its entry cannot start it or the
        resolver gives no complete command (D2), and ``start_unavailable``
        where the command cannot be handed to the launcher.
        """
        async with self._rules.hold("prepare_game_start", prune=True):
            app_id = await self._loop.run_in_executor(None, self._prepare_io, int(rom_id))
            return {
                "success": True,
                "app_id": app_id,
                "launch_options": shortcut_launch_options(app_id),
                "prune_lease_token": await self._rules.acquire_lease("launch_reconfirm"),
            }

    def _prepare_io(self, rom_id: int) -> int:
        rom, install = self._read(rom_id)
        app_id = rom.shortcut_app_id
        if app_id is None:
            raise Refused("not_found", f"ROM {rom_id} has no shortcut")
        if install is None:
            raise Refused("not_installed", f"ROM {rom_id} is not downloaded")
        resolved = self._active_core.resolve_rom(rom_id)
        start = (
            start_payload(resolved.choice, resolved.options, source_refusal(resolved.source))
            if resolved is not None
            else _NO_EMULATOR
        )
        if start["state"] != STARTS or resolved is None or resolved.choice.option is None:
            raise Refused("cannot_start", f"ROM {rom_id} cannot start: {start}", start=start)
        label = resolved.choice.option.label
        path = asked_path(
            install.file_path, install.rom_dir, self._disc_resolver.resolve_for_install(install, rom.selected_disc)
        )
        command = self._start_commands.start_command(
            install.system,
            path,
            label=label,
            writable=(install.rom_dir or os.path.dirname(path),),
            read_only=(os.path.join(self._tender_folder.place().folder, STORE_FOLDER),),
        )
        if isinstance(command, StartRefused):
            start = start_payload(resolved.choice, resolved.options, command)
            raise Refused("cannot_start", f"ROM {rom_id} cannot start with {label}: {command.reason}", start=start)
        folder = command.folder if command.folder is not None else self._start_folder
        self._hand_over(app_id, folder, command.argv, create_folder=command.folder is None or command.create_folder)
        self._log_debug(f"[start] handed rom_id={rom_id} app_id={app_id} over: {label}")
        return app_id

    def _hand_over(self, app_id: int, folder: str, argv: tuple[str, ...], *, create_folder: bool) -> None:
        if self._handover is None:
            raise Refused("start_unavailable", "Tender has no runtime folder to hand the start to the launcher in")
        try:
            self._handover.hand_over(app_id, folder, argv, create_folder=create_folder)
        except (OSError, ValueError) as exc:
            raise Refused("start_unavailable", f"Could not hand the start to the launcher: {exc}") from exc

    def _read(self, rom_id: int) -> tuple[Rom, RomInstall | None]:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None:
                raise Refused("not_found", f"ROM {rom_id} is not tracked")
            return rom, uow.rom_installs.get(rom_id)


# A game with no system, no catalogue or no entry to ask (#2221 D20).
_NO_EMULATOR: dict[str, Any] = {"state": NO_ENTRY, "reason": None, "entry": None}
