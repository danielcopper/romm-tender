"""Tests for domain/emulator_commands — the bakeability classification kernel.

The command strings are sampled verbatim from a live RetroDECK
``es_systems.xml`` (linux/ flavor) so the rules are pinned against the real
shapes Tender must classify: plain + env-prefixed emulators, the ``%INJECT%``
and OS-shell forms, the MAME quoting/``\\;`` templates, and the ``%STARTDIR%``
prefix. A handful of synthetic strings cover branches the current es_systems
does not exercise (an unknown ``%PLACEHOLDER%``, a lone double-quote).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any, ClassVar

import pytest

from domain import emulator_commands
from domain.emulator_commands import (
    CORE,
    CORE_UNLOCATED,
    FOREIGN_CORE,
    NOT_ESTABLISHED,
    NOT_ESTABLISHED_PATH_UNSEEN,
    NOT_INSTALLED,
    PROGRAM,
    START_SCRIPT_RUNS_NOTHING,
    EmulatorOption,
    classify_command,
    default_option,
    label_to_invocation,
    option_to_invocation,
    options_to_payload,
    resolve_firmware_option,
    resolve_platform_label,
    shipped_default,
)
from domain.shortcut_data import EmulatorInvocation

# --- Real command strings (sampled from a live es_systems.xml) --------------

PSX_SWANSTATION = "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/swanstation_libretro.so %ROM%"
GC_DOLPHIN_STANDALONE = "env QT_QPA_PLATFORM=xcb %EMULATOR_DOLPHIN% -b -e %ROM%"
PS2_PCSX2_BATCH = "%EMULATOR_PCSX2% -batch %ROM%"
PS3_RPCS3_DIRECTORY = "%EMULATOR_RPCS3% --no-gui %ROM%"
PS3_RPCS3_SHORTCUT = "%ENABLESHORTCUTS% %EMULATOR_OS-SHELL% %ROM%"
PS3_RPCS3_GAME_SERIAL = "%EMULATOR_RPCS3% --no-gui %RPCS3_GAMEID%:%INJECT%=%BASENAME%.ps3"
PS2_PCSX2_LIBRETRO = "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/pcsx2_libretro.so %ROM%"
PS2_PCEE2_LIBRETRO = "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/pcee2_libretro.so %ROM%"
SYMBIAN_EKA2L1_RH29 = '%EMULATOR_EKA2L1% --fullscreen --device RH-29 --run "%BASENAME%"'
SYMBIAN_EKA2L1_RM84 = '%EMULATOR_EKA2L1% --fullscreen --device RM-84 --run "%BASENAME%"'
SYMBIAN_EKA2L1_RM507 = '%EMULATOR_EKA2L1% --fullscreen --device RM-507 --run "%BASENAME%"'
SYMBIAN_EKA2L1_CUSTOM = '%EMULATOR_EKA2L1% --fullscreen --device %INJECT%=%BASENAME%.device --run "%BASENAME%"'
XEMU_INJECT_WITH_ROM = "%INJECT%=%BASENAME%.esprefix %EMULATOR_XEMU% -dvd_path %ROM%"
VITA3K_INJECT_NO_ROM = "%EMULATOR_VITA3K% -r %INJECT%=%BASENAME%.psvita"
ARCADE_MAME_STANDALONE = (
    "%EMULATOR_MAME% -inipath /var/config/mame/ini -rompath %GAMEDIR%\\;%ROMPATH%/arcade %BASENAME%"
)
APPLE2_MAME_LIBRETRO_QUOTED = (
    "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mame_libretro.so "
    '"apple2e -rompath \\"%GAMEDIRRAW%;%ROMPATH%/apple2\\" -gameio joy -flop1 \\"%GAMEDIRRAW%/%FILENAME%\\""'
)
ARCHIMEDES_MAME_BACKSLASH_SEMI = (
    "%EMULATOR_MAME% -inipath /var/config/mame/ini -rompath %GAMEDIR%\\;%ROMPATH%/archimedes aa4401 -flop1 %ROM%"
)
BBCMICRO_MAME_QUOTED = (
    "%STARTDIR%=~/.mame %EMULATOR_MAME% -rompath %GAMEDIR%\\;%ROMPATH%/bbcmicro bbcb "
    '-autoboot_delay "2" -autoboot_command "*cat\\n\\n*exec !boot\\n" -analogue acornjoy -flop1 %ROM%'
)
PC98_NEKOP2_STARTDIR = "%STARTDIR%=%GAMEDIR% %EMULATOR_RETROARCH% -L %CORE_RETROARCH%/nekop2_libretro.so %ROM%"
PICO8_STANDALONE = "%EMULATOR_PICO-8% -root_path %GAMEDIR% -run %ROM%"
NEOGEOCD_FBNEO = "%EMULATOR_RETROARCH% --subsystem neocd -L %CORE_RETROARCH%/fbneo_libretro.so %ROM%"

# --- What the resolver states for the entries above ------------------------

# Where the resolver states RetroDECK keeps its cores, spelled inside its sandbox.
CORES = "/var/config/retroarch/cores"

# The core the resolver states each core command loads; every other command is
# an emulator program of its own.
CORE_OF = {
    PSX_SWANSTATION: "swanstation_libretro",
    PS2_PCSX2_LIBRETRO: "pcsx2_libretro",
    PS2_PCEE2_LIBRETRO: "pcee2_libretro",
    APPLE2_MAME_LIBRETRO_QUOTED: "mame_libretro",
    PC98_NEKOP2_STARTDIR: "nekop2_libretro",
}


def _classify(label: str, text: str, **resolver: Any) -> EmulatorOption:
    """``classify_command`` handed what the resolver states for *text*'s entry: kind, core, core path."""
    core_so = CORE_OF.get(text)
    if core_so is None:
        return classify_command(label, text, kind=PROGRAM, **resolver)
    return classify_command(label, text, kind=CORE, core_so=core_so, core_path=f"{CORES}/{core_so}.so", **resolver)


def _declared(*options: EmulatorOption) -> list[EmulatorOption]:
    """*options* with the declared positions a catalogue that lists them in this order states."""
    return [dataclasses.replace(option, declared_index=index) for index, option in enumerate(options)]


def _core_invocation(core_so: str, label: str) -> EmulatorInvocation:
    return EmulatorInvocation.run_game(f"%EMULATOR_RETROARCH% -L {CORES}/{core_so}.so %ROM%", label, core_so=core_so)


# --- Synthetic strings for branches the live es_systems does not hit ---------

SYNTH_UNKNOWN_PLACEHOLDER = "%EMULATOR_FOO% --port %MYSTERY_TOKEN% %ROM%"
SYNTH_LONE_QUOTE = '%EMULATOR_FOO% --title "game" %ROM%'
SYNTH_TRAILING_DOT = "%EMULATOR_DREAMM% %GAMEDIR% ."


class TestClassifyCommand:
    """Each real/synthetic command classifies to exactly one status + reason."""

    @pytest.mark.parametrize(
        ("label", "text", "status", "reason", "kind", "core_so"),
        [
            ("SwanStation", PSX_SWANSTATION, "bakeable", None, CORE, "swanstation_libretro"),
            ("Dolphin (Standalone)", GC_DOLPHIN_STANDALONE, "bakeable", None, PROGRAM, None),
            ("PCSX2 (Standalone)", PS2_PCSX2_BATCH, "bakeable", None, PROGRAM, None),
            ("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, "bakeable", None, PROGRAM, None),
            ("PICO-8 (Standalone)", PICO8_STANDALONE, "bakeable", None, PROGRAM, None),
            ("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT, "unbakeable", "shortcut_script", PROGRAM, None),
            ("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL, "needs_setup", "inject", PROGRAM, None),
            ("xemu (Standalone)", XEMU_INJECT_WITH_ROM, "needs_setup", "inject", PROGRAM, None),
            ("Vita3K (Standalone)", VITA3K_INJECT_NO_ROM, "needs_setup", "inject", PROGRAM, None),
            ("MAME (Standalone)", ARCADE_MAME_STANDALONE, "unbakeable", "no_rom_target", PROGRAM, None),
            ("MAME - Current", APPLE2_MAME_LIBRETRO_QUOTED, "unbakeable", "no_rom_target", CORE, "mame_libretro"),
            ("MAME [A440/1]", ARCHIMEDES_MAME_BACKSLASH_SEMI, "unbakeable", "quoting", PROGRAM, None),
            ("MAME (Standalone)", BBCMICRO_MAME_QUOTED, "unbakeable", "quoting", PROGRAM, None),
            ("Neko Project II", PC98_NEKOP2_STARTDIR, "unbakeable", "startdir", CORE, "nekop2_libretro"),
            ("Foo", SYNTH_UNKNOWN_PLACEHOLDER, "unbakeable", "unknown_placeholder", PROGRAM, None),
            ("Foo", SYNTH_LONE_QUOTE, "unbakeable", "quoting", PROGRAM, None),
            ("DREAMM", SYNTH_TRAILING_DOT, "unbakeable", "no_rom_target", PROGRAM, None),
        ],
    )
    def test_classification(self, label, text, status, reason, kind, core_so):
        option = _classify(label, text)
        assert option.label == label
        assert option.status == status
        assert option.reason == reason
        assert option.kind == kind
        assert option.core_so == core_so
        assert option.command == text.strip()

    def test_inject_wins_over_missing_rom_target(self):
        """A ``%INJECT%`` command with no ``%ROM%`` is needs-setup, not no_rom_target."""
        option = _classify("Vita3K", VITA3K_INJECT_NO_ROM)
        assert option.status == "needs_setup"
        assert option.reason == "inject"

    def test_startdir_beats_unknown_placeholder(self):
        """``%STARTDIR%`` is a known placeholder — it surfaces its own reason."""
        option = _classify("Neko Project II", PC98_NEKOP2_STARTDIR)
        assert option.reason == "startdir"

    def test_env_prefixed_command_is_bakeable_standalone(self):
        option = _classify("Dolphin (Standalone)", GC_DOLPHIN_STANDALONE)
        assert option.status == "bakeable"
        assert option.kind == PROGRAM


class TestAnEntryTheResolverStatesNotInstalled:
    """``NOT_INSTALLED`` takes the place of the bake verdict, whatever the command."""

    def test_a_missing_standalone_is_withheld_with_its_reason(self):
        option = _classify("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        assert option.status == "needs_setup"
        assert option.reason == "not_installed"
        # Identity fields survive.
        assert option.label == "Ryubing (Standalone)"
        assert option.kind == PROGRAM
        assert option.command == "%EMULATOR_RYUBING% %ROM%"

    def test_a_startable_entry_keeps_its_bake_verdict(self):
        option = _classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH, installation=None)
        assert (option.status, option.not_established) == ("bakeable", None)

    def test_a_libretro_core_the_resolver_states_missing_is_withheld(self):
        option = _classify("SwanStation", PSX_SWANSTATION, installation=NOT_INSTALLED)
        assert (option.status, option.reason) == ("needs_setup", "not_installed")
        assert option.kind == CORE
        assert option.core_so == "swanstation_libretro"

    def test_the_installation_reason_stands_ahead_of_an_unbakeable_command(self):
        option = _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT, installation=NOT_INSTALLED)
        assert option.reason == "not_installed"

    def test_a_withheld_option_shipped_first_stays_the_default_and_bakes_nothing(self):
        (withheld,) = _declared(
            _classify("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        )
        assert default_option([withheld]) is withheld
        assert label_to_invocation([withheld], "Ryubing (Standalone)") is None

    def test_a_withheld_option_reads_disabled_with_reason_in_payload(self):
        withheld = _classify("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        payload = options_to_payload([withheld])
        assert payload[0]["bakeable"] is False
        assert payload[0]["reason"] == "not_installed"
        assert payload[0]["is_default"] is False


class TestAnEntryTheResolverCouldNotEstablish:
    """A selectable pick with a note, never the default by itself (#2195 D9)."""

    def _unseen(self) -> EmulatorOption:
        return _classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH, installation=NOT_ESTABLISHED_PATH_UNSEEN)

    def test_it_bakes_as_its_command_would_and_carries_the_note(self):
        option = self._unseen()
        assert (option.status, option.reason, option.not_established) == ("bakeable", None, "path_unseen")

    def test_it_is_never_the_default_by_itself(self):
        swanstation = _classify("SwanStation", PSX_SWANSTATION)
        assert default_option(_declared(self._unseen())) is None
        assert default_option(_declared(self._unseen(), swanstation)) is None

    def test_a_pick_of_it_stays(self):
        assert label_to_invocation([self._unseen()], "PCSX2 (Standalone)") == EmulatorInvocation.run_game(
            PS2_PCSX2_BATCH, "PCSX2 (Standalone)"
        )
        assert resolve_platform_label([self._unseen()], "PCSX2 (Standalone)") == "PCSX2 (Standalone)"

    def test_it_reads_selectable_with_its_note_in_payload(self):
        payload = options_to_payload([self._unseen()])
        assert (payload[0]["bakeable"], payload[0]["reason"], payload[0]["is_default"]) == (True, "path_unseen", False)

    def test_an_unbakeable_command_keeps_the_reason_it_cannot_bake(self):
        option = _classify("Shortcut", PS3_RPCS3_SHORTCUT, installation=NOT_ESTABLISHED)
        assert options_to_payload([option])[0]["reason"] == "shortcut_script"


class TestThePanelWordsEveryNotEstablishedNote:
    """The panel's map of not-established words is the domain's list, no more and no less."""

    _PANEL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "utils" / "emulatorInstallation.ts"

    def test_the_two_lists_are_equal(self):
        source = self._PANEL.read_text(encoding="utf-8")
        causes = source[source.index("const CAUSES") : source.index("]);", source.index("const CAUSES"))]
        panel = set(re.findall(r'^\s*\["(\w+)",', causes, flags=re.MULTILINE))
        domain = {
            value
            for name, value in vars(emulator_commands).items()
            if name == "NOT_ESTABLISHED" or name.startswith("NOT_ESTABLISHED_")
        }
        assert panel == domain


class TestThePanelWordsEveryRefusalOfTheResolvers:
    """Each bake refusal the resolver's statements cause has its own words in the panel's ``reasonCopy``."""

    _PANEL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "utils" / "emulatorMenu.tsx"

    @pytest.mark.parametrize("reason", [CORE_UNLOCATED, START_SCRIPT_RUNS_NOTHING])
    def test_the_panel_has_a_case_for_it(self, reason):
        source = self._PANEL.read_text(encoding="utf-8")
        copy = source[source.index("export function reasonCopy") : source.index("default:", source.index("reasonCopy"))]
        assert f'case "{reason}":' in copy


class TestWhatTheResolverStatesDecidesTheBake:
    """Kind, core and core path are the resolver's; Tender writes the core path in and bakes the rest as written."""

    def _fbneo(self, **resolver: Any) -> EmulatorOption:
        stated: dict[str, Any] = {"kind": CORE, "core_so": "fbneo_libretro", "core_path": f"{CORES}/fbneo_libretro.so"}
        stated |= resolver
        return classify_command("FinalBurn Neo", NEOGEOCD_FBNEO, **stated)

    def test_finalburn_neo_on_neo_geo_cd_reads_as_its_core_and_keeps_its_subsystem(self):
        option = self._fbneo()
        assert (option.kind, option.core_so, option.status) == (CORE, "fbneo_libretro", "bakeable")
        assert option_to_invocation(option) == EmulatorInvocation.run_game(
            f"%EMULATOR_RETROARCH% --subsystem neocd -L {CORES}/fbneo_libretro.so %ROM%",
            "FinalBurn Neo",
            core_so="fbneo_libretro",
        )

    def test_the_core_path_baked_is_the_resolvers(self):
        option = classify_command(
            "mGBA",
            "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%",
            kind=CORE,
            core_so="mgba_libretro",
            core_path="/var/config/retroarch/cores-elsewhere/mgba_libretro.so",
        )
        invocation = option_to_invocation(option)
        assert invocation is not None
        assert invocation.command == (
            "%EMULATOR_RETROARCH% -L /var/config/retroarch/cores-elsewhere/mgba_libretro.so %ROM%"
        )

    def test_a_core_the_resolver_gives_no_path_for_is_not_bakeable(self):
        option = self._fbneo(core_path=None, installation=NOT_ESTABLISHED_PATH_UNSEEN)
        assert (option.status, option.reason, option.not_established) == (
            "unbakeable",
            CORE_UNLOCATED,
            NOT_ESTABLISHED_PATH_UNSEEN,
        )
        assert option_to_invocation(option) is None
        assert default_option(_declared(option)) is None
        assert options_to_payload([option])[0]["reason"] == CORE_UNLOCATED

    def test_an_entry_the_start_script_runs_nothing_for_is_not_bakeable(self):
        option = classify_command("PICO-8 (Standalone)", PICO8_STANDALONE, kind=PROGRAM, start_script_runs_nothing=True)
        assert (option.status, option.reason) == ("unbakeable", START_SCRIPT_RUNS_NOTHING)
        assert label_to_invocation([option], "PICO-8 (Standalone)") is None

    def test_not_installed_stands_ahead_of_the_start_script(self):
        option = classify_command(
            "PICO-8 (Standalone)",
            PICO8_STANDALONE,
            kind=PROGRAM,
            installation=NOT_INSTALLED,
            start_script_runs_nothing=True,
        )
        assert option.reason == NOT_INSTALLED

    def test_a_program_carries_its_launcher_and_a_core_does_not(self):
        launcher = "/app/retrodeck/components/retroarch/retroarch"
        invocation = option_to_invocation(self._fbneo(launcher=launcher))
        assert invocation is not None
        assert invocation.launcher is None

    def test_a_foreign_core_is_its_own_kind_and_bakes_as_its_command_earns(self):
        option = classify_command(
            "Citra", "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/citra_libretro.dll %ROM%", kind=FOREIGN_CORE
        )
        assert (option.kind, option.core_so, option.status) == (FOREIGN_CORE, None, "bakeable")
        assert options_to_payload([option])[0]["kind"] == FOREIGN_CORE


class TestTheLauncher:
    """The resolver's launcher rides on the option into a standalone invocation."""

    _LAUNCHER = "/app/retrodeck/components/rpcs3/component_launcher.sh"

    def test_a_standalone_invocation_carries_the_launcher(self):
        option = _classify("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, launcher=self._LAUNCHER)
        invocation = option_to_invocation(option)
        assert invocation is not None
        assert invocation.launcher == self._LAUNCHER

    def test_a_withheld_entry_renders_no_invocation_whatever_its_launcher(self):
        option = _classify(
            "RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, installation=NOT_INSTALLED, launcher=self._LAUNCHER
        )
        assert option_to_invocation(option) is None


class TestShippedDefault:
    """The default is the entry the source ships first, read off its declared position (#2195 D7)."""

    def test_the_lowest_declared_position_is_the_default_whatever_its_state(self):
        """The ps3 chain: the Shortcut entry cannot be baked, and it is still the default."""
        shortcut, serial, directory = _declared(
            _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
            _classify("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL),
            _classify("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
        )
        assert shipped_default([shortcut, serial, directory]) is shortcut
        assert default_option([shortcut, serial, directory]) is shortcut

    def test_a_choice_made_in_es_de_listed_first_is_not_the_default(self):
        """The resolver lists the user's choice first; its declared position still says it ships later."""
        genesis, gearsystem = _declared(
            _classify("Genesis Plus GX", PSX_SWANSTATION), _classify("Gearsystem", PSX_SWANSTATION)
        )
        promoted = dataclasses.replace(gearsystem, chosen_in_frontend=True)
        assert shipped_default([promoted, genesis]) is genesis

    def test_the_numbering_may_start_above_zero_and_skip(self):
        first = dataclasses.replace(_classify("First", PSX_SWANSTATION), declared_index=1)
        third = dataclasses.replace(_classify("Third", PSX_SWANSTATION), declared_index=5)
        assert shipped_default([third, first]) is first

    def test_no_declared_entry_is_no_default(self):
        assert shipped_default([_classify("Derived", PSX_SWANSTATION)]) is None
        assert shipped_default([]) is None


class TestOptionToInvocation:
    def test_libretro_option_renders_libretro_invocation(self):
        option = _classify("SwanStation", PSX_SWANSTATION)
        assert option_to_invocation(option) == _core_invocation("swanstation_libretro", "SwanStation")

    def test_standalone_option_bakes_command_verbatim(self):
        option = _classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH)
        assert option_to_invocation(option) == EmulatorInvocation.run_game(PS2_PCSX2_BATCH, "PCSX2 (Standalone)")

    def test_unbakeable_option_returns_none(self):
        option = _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT)
        assert option_to_invocation(option) is None

    def test_needs_setup_option_returns_none(self):
        option = _classify("Vita3K", VITA3K_INJECT_NO_ROM)
        assert option_to_invocation(option) is None

    def test_none_option_returns_none(self):
        assert option_to_invocation(None) is None


class TestLabelToInvocation:
    OPTIONS: ClassVar[list[EmulatorOption]] = [
        _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
        _classify("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
    ]

    def test_bakeable_label_resolves(self):
        result = label_to_invocation(self.OPTIONS, "RPCS3 Directory (Standalone)")
        assert result == EmulatorInvocation.run_game(PS3_RPCS3_DIRECTORY, "RPCS3 Directory (Standalone)")

    def test_unbakeable_label_returns_none(self):
        assert label_to_invocation(self.OPTIONS, "RPCS3 Shortcut (Standalone)") is None

    def test_unknown_label_returns_none(self):
        assert label_to_invocation(self.OPTIONS, "Not A Real Emulator") is None


class TestResolveFirmwareOption:
    """The emulator a platform's BIOS answer is about: the pick, else the shipped default whatever its state."""

    def test_the_shipped_default_where_nothing_is_picked_even_one_that_cannot_be_baked(self):
        shortcut, directory = _declared(
            _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
            _classify("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
        )
        assert resolve_firmware_option([shortcut, directory], None) is shortcut

    def test_a_pick_stands_whatever_its_state_and_a_vanished_one_gives_way(self):
        first, current = _declared(
            _classify("MAME (Standalone)", ARCADE_MAME_STANDALONE, installation=NOT_INSTALLED),
            _classify("MAME - Current", APPLE2_MAME_LIBRETRO_QUOTED),
        )
        assert resolve_firmware_option([first, current], "MAME - Current") is current
        assert resolve_firmware_option([first, current], "Removed") is first

    def test_none_where_no_emulator_is_listed(self):
        assert resolve_firmware_option([], None) is None


class TestResolvePlatformLabel:
    """The platform-layer label two services render — one rule, one place."""

    OPTIONS: ClassVar[list[EmulatorOption]] = _declared(
        _classify("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
        _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
        _classify("RPCS3 ISO (Standalone)", PS3_RPCS3_DIRECTORY),
    )

    def test_no_override_falls_back_to_the_default(self):
        assert resolve_platform_label(self.OPTIONS, None) == "RPCS3 Directory (Standalone)"

    def test_a_resolvable_override_wins(self):
        assert resolve_platform_label(self.OPTIONS, "RPCS3 ISO (Standalone)") == "RPCS3 ISO (Standalone)"

    def test_an_override_naming_an_unbakeable_option_stands(self):
        # A pick stands whatever its state (#2195 D23): the platform starts
        # through the plain launch rather than with another emulator.
        assert resolve_platform_label(self.OPTIONS, "RPCS3 Shortcut (Standalone)") == "RPCS3 Shortcut (Standalone)"

    def test_an_override_naming_nothing_degrades(self):
        assert resolve_platform_label(self.OPTIONS, "Uninstalled Emulator") == "RPCS3 Directory (Standalone)"

    def test_a_default_that_cannot_be_baked_is_still_named(self):
        options = _declared(_classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT))
        assert resolve_platform_label(options, None) == "RPCS3 Shortcut (Standalone)"
        assert resolve_platform_label(options, "RPCS3 Shortcut (Standalone)") == "RPCS3 Shortcut (Standalone)"

    def test_empty_options_answer_no_label(self):
        assert resolve_platform_label([], "anything") is None


class TestOptionsToPayload:
    def test_marks_single_default_and_bakeability(self):
        options = _declared(
            _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
            _classify("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL),
            _classify("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
        )
        payload = options_to_payload(options)
        assert payload == [
            {
                "label": "RPCS3 Shortcut (Standalone)",
                "kind": PROGRAM,
                "core_so": None,
                "emulator": None,
                "declared_index": 0,
                "is_default": True,
                "chosen_in_frontend": False,
                "bakeable": False,
                "reason": "shortcut_script",
            },
            {
                "label": "RPCS3 Game Serial (Standalone)",
                "kind": PROGRAM,
                "core_so": None,
                "emulator": None,
                "declared_index": 1,
                "is_default": False,
                "chosen_in_frontend": False,
                "bakeable": False,
                "reason": "inject",
            },
            {
                "label": "RPCS3 Directory (Standalone)",
                "kind": PROGRAM,
                "core_so": None,
                "emulator": None,
                "declared_index": 2,
                "is_default": False,
                "chosen_in_frontend": False,
                "bakeable": True,
                "reason": None,
            },
        ]

    def test_libretro_payload_carries_core_so(self):
        payload = options_to_payload(_declared(_classify("SwanStation", PSX_SWANSTATION)))
        assert payload[0]["kind"] == CORE
        assert payload[0]["core_so"] == "swanstation_libretro"
        assert payload[0]["is_default"] is True

    def test_the_payload_carries_the_identity_beside_the_core(self):
        """The one field naming both kinds, so a surface can join a pick to a firmware row.

        ``core_so`` answers a different question and stays: a standalone
        emulator has an identity and no core, and neither field is derivable from
        the other.
        """
        payload = options_to_payload(
            [
                _classify("SwanStation", PSX_SWANSTATION, emulator="swanstation_libretro.so"),
                _classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH, emulator="PCSX2"),
            ]
        )

        assert [(e["core_so"], e["emulator"]) for e in payload] == [
            ("swanstation_libretro", "swanstation_libretro.so"),
            (None, "PCSX2"),
        ]

    def test_an_unidentified_row_carries_no_identity(self):
        """The resolver could not name the emulator, and the payload says so rather than guessing."""
        payload = options_to_payload([_classify("Citra", PSX_SWANSTATION)])

        assert payload[0]["emulator"] is None

    def test_no_default_when_none_bakeable(self):
        payload = options_to_payload([_classify("MAME (Standalone)", ARCADE_MAME_STANDALONE)])
        assert all(entry["is_default"] is False for entry in payload)

    def test_command_text_not_leaked(self):
        payload = options_to_payload([_classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH)])
        assert "command" not in payload[0]

    def test_labels_that_run_one_command_are_one_row_each(self):
        """RetroDECK's PlayStation 2 list, in its own order: LRPS2 and PCSX2 start the same core and stay two rows."""
        payload = options_to_payload(
            _declared(
                _classify("PCEE2", PS2_PCEE2_LIBRETRO, emulator="pcee2_libretro.so"),
                _classify("LRPS2", PS2_PCSX2_LIBRETRO, emulator="pcsx2_libretro.so"),
                _classify("PCSX2", PS2_PCSX2_LIBRETRO, emulator="pcsx2_libretro.so"),
                _classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH, emulator="PCSX2"),
            )
        )

        assert [(e["label"], e["emulator"], e["declared_index"]) for e in payload] == [
            ("PCEE2", "pcee2_libretro.so", 0),
            ("LRPS2", "pcsx2_libretro.so", 1),
            ("PCSX2", "pcsx2_libretro.so", 2),
            ("PCSX2 (Standalone)", "PCSX2", 3),
        ]

    def test_one_emulator_launched_different_ways_is_one_row_per_entry(self):
        """RPCS3's four PS3 labels are four rows, Directory and ISO apart though they run one command."""
        payload = options_to_payload(
            [
                _classify("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT, emulator="RPCS3"),
                _classify("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL, emulator="RPCS3"),
                _classify("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, emulator="RPCS3"),
                _classify("RPCS3 ISO (Standalone)", PS3_RPCS3_DIRECTORY, emulator="RPCS3"),
            ]
        )

        assert [e["label"] for e in payload] == [
            "RPCS3 Shortcut (Standalone)",
            "RPCS3 Game Serial (Standalone)",
            "RPCS3 Directory (Standalone)",
            "RPCS3 ISO (Standalone)",
        ]

    def test_one_emulator_s_device_variants_stay_apart(self):
        """EKA2L1's four Symbian labels each start a different device."""
        labels = [
            ("EKA2L1 [Nokia N-Gage] (Standalone)", SYMBIAN_EKA2L1_RH29),
            ("EKA2L1 [Nokia N70] (Standalone)", SYMBIAN_EKA2L1_RM84),
            ("EKA2L1 [Nokia N97] (Standalone)", SYMBIAN_EKA2L1_RM507),
            ("EKA2L1 [Custom device] (Standalone)", SYMBIAN_EKA2L1_CUSTOM),
        ]
        payload = options_to_payload([_classify(label, text, emulator="EKA2L1") for label, text in labels])

        assert [e["label"] for e in payload] == [label for label, _ in labels]

    def test_the_choice_made_in_es_de_is_marked_and_the_default_stays_shipped(self):
        genesis, gearsystem = _declared(
            _classify("Genesis Plus GX", PSX_SWANSTATION), _classify("Gearsystem", PSX_SWANSTATION)
        )
        payload = options_to_payload([dataclasses.replace(gearsystem, chosen_in_frontend=True), genesis])

        assert [(e["label"], e["is_default"], e["chosen_in_frontend"]) for e in payload] == [
            ("Gearsystem", False, True),
            ("Genesis Plus GX", True, False),
        ]


class TestClassificationInvariants:
    """Property-style checks over the sampled command corpus."""

    CORPUS: ClassVar[list[str]] = [
        PSX_SWANSTATION,
        GC_DOLPHIN_STANDALONE,
        PS2_PCSX2_BATCH,
        PS3_RPCS3_DIRECTORY,
        PS3_RPCS3_SHORTCUT,
        PS3_RPCS3_GAME_SERIAL,
        XEMU_INJECT_WITH_ROM,
        VITA3K_INJECT_NO_ROM,
        ARCADE_MAME_STANDALONE,
        APPLE2_MAME_LIBRETRO_QUOTED,
        ARCHIMEDES_MAME_BACKSLASH_SEMI,
        BBCMICRO_MAME_QUOTED,
        PC98_NEKOP2_STARTDIR,
        PICO8_STANDALONE,
        SYNTH_UNKNOWN_PLACEHOLDER,
        SYNTH_LONE_QUOTE,
        SYNTH_TRAILING_DOT,
    ]

    _VALID_STATUSES: ClassVar[set[str]] = {"bakeable", "needs_setup", "unbakeable"}
    _VALID_REASONS: ClassVar[set[str | None]] = {
        None,
        "inject",
        "shortcut_script",
        "no_rom_target",
        "quoting",
        "startdir",
        "unknown_placeholder",
    }

    @pytest.mark.parametrize("text", CORPUS)
    def test_exactly_one_valid_status_and_reason(self, text):
        option = _classify("L", text)
        assert option.status in self._VALID_STATUSES
        assert option.reason in self._VALID_REASONS
        # A bakeable option never carries a reason; a non-bakeable one always does.
        assert (option.reason is None) == (option.status == "bakeable")

    @pytest.mark.parametrize("text", CORPUS)
    def test_classification_is_deterministic(self, text):
        first = _classify("L", text)
        second = _classify("L", text)
        assert first == second


def test_emulator_option_is_frozen():
    option = EmulatorOption(
        label="x", kind=PROGRAM, core_so=None, command="%ROM%", status="bakeable", reason=None, emulator="RPCS3"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        option.label = "y"  # type: ignore[misc]
