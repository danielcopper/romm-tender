"""Tests for the atlas launch adapter — the resolver's launch command read into Tender's view.

Answers are built from the resolver's own value objects, so a field renamed
upstream fails here rather than passing against a stand-in.
``tests/adapters/test_launch_command_vectors.py`` runs every recorded launch
machine through the same reading.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from _vendor.atlas import (
    AVAILABILITY_NOT_INSTALLED,
    AVAILABILITY_STARTABLE,
    AVAILABILITY_UNESTABLISHED,
    CAVEAT_EMULATOR_RULES_MISSING,
    CAVEAT_FRONTEND_BUILD_SEALED,
    CAVEAT_FRONTEND_BUILD_UNPINNED,
    CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN,
    CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS,
    CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE,
    CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID,
    CAVEAT_LAUNCH_COMMAND_ENV_OPTION,
    CAVEAT_LAUNCH_COMMAND_INJECT_LOOP,
    CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE,
    CAVEAT_LAUNCH_COMMAND_LABEL_UNKNOWN,
    CAVEAT_LAUNCH_COMMAND_NO_PROGRAM,
    CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN,
    CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED,
    CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE,
    CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX,
    CAVEAT_LAUNCH_ENVIRONMENT_UNPINNED,
    CAVEAT_LAUNCH_ENVIRONMENT_UNREAD,
    CAVEAT_LAUNCHER_SCRIPT_UNPINNED,
    KIND_STANDALONE,
    VARIANT_APPIMAGE,
    VARIANT_FLATPAK,
    VARIANT_NATIVE,
    DistributionVariable,
    EnvironmentVariable,
    LaunchCommand,
    LaunchProgram,
    WorkingFolder,
)
from _vendor.atlas.esde import EmulatorSpec
from _vendor.atlas.installations import EmulatorEntry, LaunchCommandAnswer
from _vendor.atlas.launch import Launcher, LaunchResolution
from _vendor.atlas.placement import Caveat

from adapters.atlas_launch import AtlasLaunchAdapter, read_start
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.emulator_commands import NOT_ESTABLISHED_NO_FIND_RULE, NOT_INSTALLED
from domain.start_command import (
    COMMAND_TOO_LONG,
    COMMAND_UNREAD,
    DESKTOP_FILE,
    ENTRY_UNKNOWN,
    ENVIRONMENT_UNKNOWN,
    ENVIRONMENT_UNREAD,
    FOLDER_UNPLACED,
    FRONTEND_BUILD_UNREAD,
    INJECTED_FILE_UNREAD,
    NO_PROGRAM,
    PLACEHOLDER_UNFILLED,
    ROM_ROOT_UNKNOWN,
    StartCommand,
    StartNote,
    StartRefused,
    start_verdict,
)

_APP = "net.retrodeck.retrodeck"
_DOLPHIN = "/app/retrodeck/components/dolphin/component_launcher.sh"
_GAME = "/run/media/deck/Emulation/retrodeck/roms/gc/Game.rvz"


def _flatpak(command: str = _DOLPHIN) -> LaunchProgram:
    return LaunchProgram(variant=VARIANT_FLATPAK, app_id=_APP, command=command)


def _variable(name: str, value: str | None, *, provided: bool = True) -> DistributionVariable:
    return DistributionVariable(name=name, value=value, set_by="/app/libexec/all_vars.sh:49", provided=provided)


def _command(
    *,
    program: LaunchProgram | None = None,
    arguments: tuple[str, ...] = ("-b", "-e", _GAME),
    environment: tuple[EnvironmentVariable, ...] = (),
    folder: WorkingFolder | None = None,
    distribution: tuple[DistributionVariable, ...] | None = (),
) -> LaunchCommand:
    return LaunchCommand(
        program=program or _flatpak(),
        arguments=arguments,
        environment=environment,
        working_folder=folder,
        injections=(),
        distribution_environment=distribution,
    )


def _caveat(code: str, data: dict[str, Any] | None = None) -> Caveat:
    return Caveat(code=code, message="stated by the resolver", data=data or {})


def _entry(availability: str = AVAILABILITY_STARTABLE, caveats: tuple[Caveat, ...] = ()) -> EmulatorEntry:
    launcher = (
        Launcher(path=_DOLPHIN, replacement_command=None, app_id=_APP, rule="staticpath", entry=_DOLPHIN)
        if availability == AVAILABILITY_STARTABLE
        else None
    )
    return EmulatorEntry(
        cast("Any", None),
        EmulatorSpec(
            system="gc",
            label="Dolphin (Standalone)",
            kind=KIND_STANDALONE,
            core_so=None,
            command="%EMULATOR_DOLPHIN% -b -e %ROM%",
            provenance="bundled es_systems.xml",
            declared_index=0,
        ),
        caveats=caveats,
        launch=LaunchResolution(availability=cast("Any", availability), launcher=launcher),
    )


def _answer(command: LaunchCommand | None, *caveats: Caveat, entry: EmulatorEntry | None = None) -> LaunchCommandAnswer:
    return LaunchCommandAnswer(entry=entry or _entry(), command=command, sources=(), caveats=caveats)


class TestACompleteFlatpakCommand:
    def test_it_runs_the_resolvers_program_in_its_sandbox_with_its_arguments(self):
        result = read_start(_answer(_command()))

        assert isinstance(result, StartCommand)
        assert result.argv == (
            "flatpak",
            "run",
            "--nosocket=wayland",
            f"--command={_DOLPHIN}",
            _APP,
            "-b",
            "-e",
            _GAME,
        )

    def test_an_environment_prefix_is_applied_and_never_passed_as_an_argument(self):
        command = _command(environment=(EnvironmentVariable("QT_QPA_PLATFORM", "xcb"),))

        result = read_start(_answer(command))

        assert isinstance(result, StartCommand)
        assert "--env=QT_QPA_PLATFORM=xcb" in result.argv
        assert result.argv.index("--env=QT_QPA_PLATFORM=xcb") < result.argv.index(f"--command={_DOLPHIN}")
        assert result.argv[-3:] == ("-b", "-e", _GAME)
        assert "env" not in result.argv
        assert "QT_QPA_PLATFORM=xcb" not in result.argv

    def test_the_distribution_environment_is_set_before_the_commands_own(self):
        command = _command(
            environment=(EnvironmentVariable("SHARED", "from-the-command"),),
            distribution=(
                _variable("rd_shared_libs", "/app/retrodeck/components/shared-libs"),
                _variable("SHARED", "x"),
            ),
        )

        result = read_start(_answer(command))

        assert isinstance(result, StartCommand)
        envs = [word for word in result.argv if word.startswith("--env=")]
        assert envs == [
            "--env=rd_shared_libs=/app/retrodeck/components/shared-libs",
            "--env=SHARED=x",
            "--env=SHARED=from-the-command",
        ]

    def test_a_variable_set_and_empty_is_passed_as_set_and_empty(self):
        result = read_start(_answer(_command(distribution=(_variable("DEFAULT_LD_LIBRARY_PATH", ""),))))

        assert isinstance(result, StartCommand)
        assert "--env=DEFAULT_LD_LIBRARY_PATH=" in result.argv

    def test_a_variable_the_installation_does_not_provide_is_passed_on_as_stated(self):
        result = read_start(_answer(_command(distribution=(_variable("rd_shared_libs", "/x", provided=False),))))

        assert isinstance(result, StartCommand)
        assert "--env=rd_shared_libs=/x" in result.argv

    def test_the_game_folder_is_granted_writable_and_the_firmware_store_read_only(self):
        result = read_start(
            _answer(_command()),
            writable=("/run/media/deck/Emulation/retrodeck/roms/gc",),
            read_only=("/home/deck/Tender/bios",),
        )

        assert isinstance(result, StartCommand)
        assert result.argv[:5] == (
            "flatpak",
            "run",
            "--nosocket=wayland",
            "--filesystem=/run/media/deck/Emulation/retrodeck/roms/gc",
            "--filesystem=/home/deck/Tender/bios:ro",
        )

    def test_a_colon_and_a_backslash_in_a_granted_folder_are_escaped_as_flatpak_reads_them(self):
        result = read_start(_answer(_command()), writable=("/games/a:b\\c",))

        assert isinstance(result, StartCommand)
        assert "--filesystem=/games/a\\:b\\\\c" in result.argv

    def test_the_folder_it_names_is_where_it_starts(self):
        folder = WorkingFolder(frontend_path="~/emu", host_path="/home/deck/emu", created_if_missing=True)

        result = read_start(_answer(_command(folder=folder)))

        assert isinstance(result, StartCommand)
        assert (result.folder, result.create_folder) == ("/home/deck/emu", True)

    def test_without_a_folder_it_names_none(self):
        result = read_start(_answer(_command()))

        assert isinstance(result, StartCommand)
        assert (result.folder, result.create_folder) == (None, False)


class TestAHostProgram:
    @pytest.mark.parametrize("variant", [VARIANT_APPIMAGE, VARIANT_NATIVE])
    def test_it_runs_the_file_with_its_arguments_and_nothing_of_flatpaks(self, variant):
        program = LaunchProgram(variant=variant, path="/home/deck/Applications/emu.AppImage")

        result = read_start(_answer(_command(program=program, arguments=("--fullscreen", _GAME))), writable=("/g",))

        assert isinstance(result, StartCommand)
        assert result.argv == ("/home/deck/Applications/emu.AppImage", "--fullscreen", _GAME)

    def test_its_environment_is_set_through_env(self):
        program = LaunchProgram(variant=VARIANT_NATIVE, path="/usr/bin/emu")
        command = _command(
            program=program, arguments=(_GAME,), environment=(EnvironmentVariable("QT_QPA_PLATFORM", "xcb"),)
        )

        result = read_start(_answer(command))

        assert isinstance(result, StartCommand)
        assert result.argv == ("env", "QT_QPA_PLATFORM=xcb", "/usr/bin/emu", _GAME)


class TestACommandThatIsNotComplete:
    def test_a_distribution_environment_not_stated_is_not_known_yet(self):
        answer = _answer(_command(distribution=None), _caveat(CAVEAT_LAUNCH_ENVIRONMENT_UNPINNED))

        assert read_start(answer) == StartRefused(ENVIRONMENT_UNKNOWN)

    def test_a_variable_stated_without_a_value_could_not_be_read(self):
        command = _command(distribution=(_variable("rd_shared_libs", "/x"), _variable("DEFAULT_LD_LIBRARY_PATH", None)))
        answer = _answer(command, _caveat(CAVEAT_LAUNCH_ENVIRONMENT_UNREAD, {"variable": "DEFAULT_LD_LIBRARY_PATH"}))

        assert read_start(answer) == StartRefused(ENVIRONMENT_UNREAD)

    def test_a_folder_that_could_not_be_placed_on_the_host(self):
        folder = WorkingFolder(frontend_path="/var/emu", host_path=None, created_if_missing=True)

        assert read_start(_answer(_command(folder=folder))) == StartRefused(FOLDER_UNPLACED)


class TestARefusedCommand:
    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            (CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX, COMMAND_UNREAD),
            (CAVEAT_LAUNCH_COMMAND_ENV_OPTION, COMMAND_UNREAD),
            (CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID, COMMAND_UNREAD),
            (CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE, COMMAND_UNREAD),
            (CAVEAT_LAUNCH_COMMAND_INJECT_LOOP, COMMAND_UNREAD),
            (CAVEAT_LAUNCHER_SCRIPT_UNPINNED, COMMAND_UNREAD),
            (CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE, DESKTOP_FILE),
            (CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE, INJECTED_FILE_UNREAD),
            (CAVEAT_LAUNCH_COMMAND_NO_PROGRAM, NO_PROGRAM),
            (CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED, ROM_ROOT_UNKNOWN),
            (CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS, COMMAND_TOO_LONG),
            (CAVEAT_FRONTEND_BUILD_UNPINNED, FRONTEND_BUILD_UNREAD),
            (CAVEAT_FRONTEND_BUILD_SEALED, FRONTEND_BUILD_UNREAD),
            (CAVEAT_LAUNCH_COMMAND_LABEL_UNKNOWN, ENTRY_UNKNOWN),
        ],
    )
    def test_each_refusal_reads_as_its_reason(self, code, reason):
        assert read_start(_answer(None, _caveat(code))) == StartRefused(reason)

    def test_a_refusal_tender_has_no_word_for_reads_as_a_command_that_could_not_be_read(self):
        assert read_start(_answer(None, _caveat("a-code-of-a-later-release"))) == StartRefused(COMMAND_UNREAD)

    def test_an_entry_that_is_not_installed_says_so(self):
        entry = _entry(AVAILABILITY_NOT_INSTALLED)

        assert read_start(_answer(None, entry=entry)) == StartRefused(NOT_INSTALLED)

    def test_an_entry_whose_installation_is_not_established_says_why(self):
        entry = _entry(AVAILABILITY_UNESTABLISHED, (_caveat(CAVEAT_EMULATOR_RULES_MISSING),))

        assert read_start(_answer(None, entry=entry)) == StartRefused(NOT_ESTABLISHED_NO_FIND_RULE)


class TestWhatIsSaidBesideAStart:
    def test_a_placeholder_es_de_leaves_unfilled_is_named(self):
        answer = _answer(
            _command(), _caveat(CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN, {"placeholder": "%RPCS3_GAMEID%"})
        )

        result = read_start(answer)

        assert isinstance(result, StartCommand)
        assert result.notes == (StartNote(PLACEHOLDER_UNFILLED, "%RPCS3_GAMEID%"),)
        assert start_verdict(result) == {
            "starts": True,
            "reason": None,
            "remarks": [{"reason": PLACEHOLDER_UNFILLED, "placeholder": "%RPCS3_GAMEID%"}],
        }

    def test_a_value_es_de_would_break_is_passed_as_answered_and_nothing_is_said(self):
        result = read_start(
            _answer(_command(), _caveat(CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN, {"placeholder": "%ROM%"}))
        )

        assert isinstance(result, StartCommand)
        assert result.notes == ()
        assert result.argv[-1] == _GAME


class _Installation:
    def __init__(self, answer: LaunchCommandAnswer | Exception) -> None:
        self.kind = "retrodeck"
        self._answer = answer
        self.calls: list[tuple[str, str, str | None]] = []

    def launch_command(self, system: str, content_path: str, *, label: str | None = None) -> LaunchCommandAnswer:
        self.calls.append((system, content_path, label))
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _adapter(installations: list[Any], traces: list[str]) -> AtlasLaunchAdapter:
    return AtlasLaunchAdapter(
        sources=EmulatorSourcesAdapter(
            user_home="/home/deck",
            settings={},
            log_debug=traces.append,
            detect_installations=lambda home, machine: list(installations),
            machine=object(),
        ),
        log_debug=traces.append,
    )


class TestTheAdapter:
    def test_it_asks_the_answering_source_with_the_entry_tender_starts_the_game_through(self):
        installation = _Installation(_answer(_command()))
        traces: list[str] = []

        result = _adapter([installation], traces).start_command("gc", _GAME, label="Dolphin (Standalone)")

        assert isinstance(result, StartCommand)
        assert installation.calls == [("gc", _GAME, "Dolphin (Standalone)")]

    def test_one_reading_asks_each_question_once(self):
        installation = _Installation(_answer(_command()))
        adapter = _adapter([installation], [])
        reading = adapter._sources.read()

        adapter.start_command("gc", _GAME, label="Dolphin (Standalone)", reading=reading)
        adapter.start_command("gc", _GAME, label="Dolphin (Standalone)", reading=reading)

        assert len(installation.calls) == 1

    def test_with_no_source_answering_the_command_could_not_be_read(self):
        assert _adapter([], []).start_command("gc", _GAME, label="Dolphin (Standalone)") == StartRefused(COMMAND_UNREAD)

    def test_a_resolver_that_raises_is_a_command_that_could_not_be_read_and_is_traced(self):
        traces: list[str] = []

        result = _adapter([_Installation(RuntimeError("boom"))], traces).start_command("gc", _GAME, label="Dolphin")

        assert result == StartRefused(COMMAND_UNREAD)
        assert any("resolver failed on launch_command" in line for line in traces)
