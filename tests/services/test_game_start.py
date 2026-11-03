"""Tests for GameStartService — one start of a game: the resolver's command, handed to the launcher (#2221 D7, D15).

The command reader and the hand-over are stand-ins that record what they are
asked; the entry in effect and the path come through the same fake seams the
other launch tests use, and the conflict rules are the real ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from _factories import (
    _make_conflict_rules,
    _make_prune_conflicts,
    _record_operations_at_lease,
    _refused_by_conflict_rule,
)
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_core_info_provider import core_invocation
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop

from domain.disc_selection import Disc
from domain.emulator_choice import EmulatorChoice, ResolvedEmulator
from domain.emulator_commands import NOT_INSTALLED, PROGRAM, EmulatorOption
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.start_command import ENVIRONMENT_UNREAD, StartCommand, StartRefused
from domain.tender_folder import TenderPlace
from lib.errors import Refused
from services.game_start import START_FOLDER, GameStartService, GameStartServiceConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain.start_command import StartAnswer
    from domain.tender_folder import TenderPlace as Place
    from lib.prune_conflicts import PruneConflicts

_APP_ID = 2981234567
_ROMS = "/run/media/deck/Emulation/retrodeck/roms"
_GAME = f"{_ROMS}/n64/Game.z64"
_COMMAND = ("flatpak", "run", "--nosocket=wayland", "--command=/app/ra.sh", "net.retrodeck.retrodeck", _GAME)


class _Commands:
    """The resolver's command for every question, recorded."""

    def __init__(self, answer: StartAnswer | None = None) -> None:
        self.answer = answer if answer is not None else StartCommand(argv=_COMMAND, folder=None)
        self.asked: list[dict[str, Any]] = []

    def start_command(self, system: str, content_path: str, **asked: Any) -> StartAnswer:
        self.asked.append({"system": system, "content_path": content_path, **asked})
        return self.answer


class _Handover:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.handed: list[tuple[int, str, tuple[str, ...], bool]] = []

    def hand_over(self, app_id: int, folder: str, argv: Sequence[str], *, create_folder: bool) -> None:
        if self.error is not None:
            raise self.error
        self.handed.append((app_id, folder, tuple(argv), create_folder))


class _TenderFolder:
    def place(self) -> Place:
        return TenderPlace(place="/home/deck", mount_point=None, default=True)


def _seed(
    uow: FakeUnitOfWork,
    *,
    app_id: int | None = _APP_ID,
    installed: bool = True,
    file_path: str = _GAME,
    rom_dir: str | None = None,
    selected_disc: str | None = None,
) -> None:
    with uow:
        uow.roms.save(
            Rom(
                rom_id=1,
                platform_slug="n64",
                name="Game",
                fs_name="Game.z64",
                shortcut_app_id=app_id,
                last_synced_at="2025-01-01T00:00:00",
            )
        )
        if installed:
            uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=1,
                    file_path=file_path,
                    rom_dir=rom_dir,
                    platform_slug="n64",
                    system="n64",
                    installed_at="2025-01-01T00:00:00",
                )
            )
        if selected_disc is not None:
            uow.roms.set_selected_disc(1, selected_disc)


def _service(
    uow: FakeUnitOfWork,
    *,
    commands: _Commands | None = None,
    handover: _Handover | None = None,
    no_handover: bool = False,
    active_core: FakeActiveCoreResolver | None = None,
    disc_resolver: FakeDiscResolver | None = None,
    prune_conflicts: PruneConflicts | None = None,
) -> GameStartService:
    return GameStartService(
        config=GameStartServiceConfig(
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            active_core=active_core
            if active_core is not None
            else FakeActiveCoreResolver(
                default_emulator=core_invocation("mupen64plus_next_libretro", "Mupen64Plus-Next")
            ),
            disc_resolver=disc_resolver if disc_resolver is not None else FakeDiscResolver(),
            start_commands=commands if commands is not None else _Commands(),
            handover=None if no_handover else (handover if handover is not None else _Handover()),
            tender_folder=_TenderFolder(),
            start_folder=f"/data/{START_FOLDER}",
            loop=running_loop(),
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
            log_debug=lambda msg: None,
        )
    )


async def _refusal(service: GameStartService) -> Refused:
    with pytest.raises(Refused) as refused:
        await service.prepare_game_start(1)
    return refused.value


class TestAStartThatComesAbout:
    async def test_the_resolvers_command_is_handed_over_and_the_launch_options_are_the_app_id(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        handover = _Handover()

        answer = await _service(uow, handover=handover).prepare_game_start(1)

        assert answer["success"] is True
        assert (answer["app_id"], answer["launch_options"]) == (_APP_ID, str(_APP_ID))
        assert answer["prune_lease_token"]
        assert [(app_id, argv) for app_id, _folder, argv, _create in handover.handed] == [(_APP_ID, _COMMAND)]

    async def test_it_is_asked_with_the_entry_in_effect_the_installs_system_and_the_games_own_path(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        commands = _Commands()

        await _service(uow, commands=commands).prepare_game_start(1)

        assert [(a["system"], a["content_path"], a["label"]) for a in commands.asked] == [
            ("n64", _GAME, "Mupen64Plus-Next")
        ]

    async def test_a_picked_disc_is_the_path_it_is_asked_with(self):
        uow = FakeUnitOfWork()
        folder = f"{_ROMS}/psx/Game"
        _seed(uow, file_path=f"{folder}/Game.m3u", rom_dir=folder, selected_disc="Game (Disc 2).chd")
        discs = FakeDiscResolver()
        discs.set_discs(
            folder,
            [
                Disc(filename="Game (Disc 1).chd", path=f"{folder}/Game (Disc 1).chd", label="Disc 1", index=1),
                Disc(filename="Game (Disc 2).chd", path=f"{folder}/Game (Disc 2).chd", label="Disc 2", index=2),
            ],
        )
        commands = _Commands()

        await _service(uow, commands=commands, disc_resolver=discs).prepare_game_start(1)

        assert commands.asked[0]["content_path"] == f"{folder}/Game (Disc 2).chd"

    async def test_a_game_kept_as_a_folder_is_asked_about_as_its_folder(self):
        uow = FakeUnitOfWork()
        folder = f"{_ROMS}/ps3/Game.ps3"
        _seed(uow, file_path=f"{folder}/PS3_GAME/USRDIR/EBOOT.BIN", rom_dir=folder)
        commands = _Commands()

        await _service(uow, commands=commands).prepare_game_start(1)

        assert commands.asked[0]["content_path"] == folder

    async def test_the_games_folder_is_granted_writable_and_tenders_firmware_store_read_only(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        commands = _Commands()

        await _service(uow, commands=commands).prepare_game_start(1)

        assert commands.asked[0]["writable"] == (f"{_ROMS}/n64",)
        assert commands.asked[0]["read_only"] == ("/home/deck/Tender/bios",)

    async def test_a_game_with_a_folder_of_its_own_has_that_folder_granted(self):
        uow = FakeUnitOfWork()
        folder = f"{_ROMS}/psx/Game"
        _seed(uow, file_path=f"{folder}/Game (USA)/Game.cue", rom_dir=folder)
        commands = _Commands()

        await _service(uow, commands=commands).prepare_game_start(1)

        assert commands.asked[0]["writable"] == (folder,)

    async def test_the_folder_the_command_names_is_where_it_starts(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        handover = _Handover()
        commands = _Commands(StartCommand(argv=_COMMAND, folder="/home/deck/emu", create_folder=False))

        await _service(uow, commands=commands, handover=handover).prepare_game_start(1)

        assert [(folder, create) for _app, folder, _argv, create in handover.handed] == [("/home/deck/emu", False)]

    async def test_a_folder_the_command_names_and_the_frontend_creates_is_created(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        handover = _Handover()
        commands = _Commands(StartCommand(argv=_COMMAND, folder="/home/deck/emu", create_folder=True))

        await _service(uow, commands=commands, handover=handover).prepare_game_start(1)

        assert [(folder, create) for _app, folder, _argv, create in handover.handed] == [("/home/deck/emu", True)]

    async def test_a_command_that_names_no_folder_starts_in_tenders_own_which_is_created(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        handover = _Handover()

        await _service(uow, handover=handover).prepare_game_start(1)

        assert [(folder, create) for _app, folder, _argv, create in handover.handed] == [
            (f"/data/{START_FOLDER}", True)
        ]

    async def test_the_lease_is_taken_under_the_endpoints_operation(self, monkeypatch):
        uow = FakeUnitOfWork()
        _seed(uow)
        prune_conflicts = _make_prune_conflicts()
        seen = _record_operations_at_lease(prune_conflicts, monkeypatch)

        await _service(uow, prune_conflicts=prune_conflicts).prepare_game_start(1)

        assert seen == [["prepare_game_start"]]


class TestAStartThatDoesNotComeAbout:
    async def test_a_game_tender_does_not_track_is_not_found(self):
        refused = await _refusal(_service(FakeUnitOfWork()))

        assert refused.reason == "not_found"

    async def test_a_game_with_no_shortcut_is_not_found(self):
        uow = FakeUnitOfWork()
        _seed(uow, app_id=None)

        assert (await _refusal(_service(uow))).reason == "not_found"

    async def test_a_game_that_is_not_downloaded_is_refused_and_nothing_is_asked(self):
        uow = FakeUnitOfWork()
        _seed(uow, installed=False)
        commands = _Commands()

        refused = await _refusal(_service(uow, commands=commands))

        assert refused.reason == "not_installed"
        assert commands.asked == []

    async def test_a_game_no_emulator_starts_is_refused_with_its_start(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        handover = _Handover()

        refused = await _refusal(_service(uow, handover=handover, active_core=FakeActiveCoreResolver()))

        assert refused.reason == "cannot_start"
        assert refused.details == {"start": {"state": "no_entry", "reason": None, "entry": None}}
        assert handover.handed == []

    async def test_a_game_with_no_system_to_ask_is_refused_as_one_no_emulator_starts(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        active_core = FakeActiveCoreResolver()
        active_core.resolved[1] = None

        refused = await _refusal(_service(uow, active_core=active_core))

        assert refused.reason == "cannot_start"
        assert refused.details == {"start": {"state": "no_entry", "reason": None, "entry": None}}

    async def test_an_entry_in_effect_that_is_not_installed_is_refused_with_its_start(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        option = EmulatorOption(
            label="PCSX2",
            kind=PROGRAM,
            core_so=None,
            command="%EMULATOR_PCSX2% %ROM%",
            status="needs_setup",
            reason=NOT_INSTALLED,
            emulator="PCSX2",
        )
        active_core = FakeActiveCoreResolver()
        active_core.resolved[1] = ResolvedEmulator(
            choice=EmulatorChoice(option=option, layer="game", vanished=None), options=[option], invocation=None
        )
        commands = _Commands()

        refused = await _refusal(_service(uow, commands=commands, active_core=active_core))

        assert refused.reason == "cannot_start"
        assert refused.details == {"start": {"state": "cannot_start", "reason": NOT_INSTALLED, "entry": "PCSX2"}}
        assert commands.asked == []

    async def test_no_complete_command_is_refused_with_the_resolvers_reason_and_nothing_is_handed_over(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        handover = _Handover()

        refused = await _refusal(_service(uow, commands=_Commands(StartRefused(ENVIRONMENT_UNREAD)), handover=handover))

        assert refused.reason == "cannot_start"
        assert refused.details == {
            "start": {"state": "cannot_start", "reason": ENVIRONMENT_UNREAD, "entry": "Mupen64Plus-Next"}
        }
        assert handover.handed == []

    async def test_without_a_runtime_directory_nothing_is_handed_over(self):
        uow = FakeUnitOfWork()
        _seed(uow)

        refused = await _refusal(_service(uow, no_handover=True))

        assert refused.reason == "start_unavailable"

    @pytest.mark.parametrize("error", [OSError("read-only file system"), ValueError("NUL in an argument")])
    async def test_a_hand_over_that_fails_is_refused_with_its_error(self, error):
        uow = FakeUnitOfWork()
        _seed(uow)

        refused = await _refusal(_service(uow, handover=_Handover(error)))

        assert refused.reason == "start_unavailable"
        assert str(error) in refused.message

    async def test_a_refused_start_takes_no_lease(self, monkeypatch):
        uow = FakeUnitOfWork()
        _seed(uow, installed=False)
        prune_conflicts = _make_prune_conflicts()
        seen = _record_operations_at_lease(prune_conflicts, monkeypatch)

        await _refusal(_service(uow, prune_conflicts=prune_conflicts))

        assert seen == []

    async def test_a_running_cleanup_refuses_the_start_and_nothing_is_handed_over(self):
        uow = FakeUnitOfWork()
        _seed(uow)
        handover = _Handover()
        prune_conflicts = _make_prune_conflicts()
        prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await _service(uow, handover=handover, prune_conflicts=prune_conflicts).prepare_game_start(1)

        assert handover.handed == []
        assert prune_conflicts.conflicting_operations == 0
