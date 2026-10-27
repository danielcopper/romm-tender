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
from typing import ClassVar

import pytest

from domain import emulator_commands
from domain.emulator_commands import (
    NOT_ESTABLISHED,
    NOT_ESTABLISHED_PATH_UNSEEN,
    NOT_INSTALLED,
    EmulatorOption,
    classify_command,
    label_to_invocation,
    option_to_invocation,
    options_to_payload,
    resolve_firmware_option,
    resolve_platform_label,
    select_default_option,
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

# --- Synthetic strings for branches the live es_systems does not hit ---------

SYNTH_UNKNOWN_PLACEHOLDER = "%EMULATOR_FOO% --port %MYSTERY_TOKEN% %ROM%"
SYNTH_LONE_QUOTE = '%EMULATOR_FOO% --title "game" %ROM%'
SYNTH_TRAILING_DOT = "%EMULATOR_DREAMM% %GAMEDIR% ."


class TestClassifyCommand:
    """Each real/synthetic command classifies to exactly one status + reason."""

    @pytest.mark.parametrize(
        ("label", "text", "status", "reason", "kind", "core_so"),
        [
            ("SwanStation", PSX_SWANSTATION, "bakeable", None, "libretro", "swanstation_libretro"),
            ("Dolphin (Standalone)", GC_DOLPHIN_STANDALONE, "bakeable", None, "standalone", None),
            ("PCSX2 (Standalone)", PS2_PCSX2_BATCH, "bakeable", None, "standalone", None),
            ("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, "bakeable", None, "standalone", None),
            ("PICO-8 (Standalone)", PICO8_STANDALONE, "bakeable", None, "standalone", None),
            ("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT, "unbakeable", "shortcut_script", "standalone", None),
            ("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL, "needs_setup", "inject", "standalone", None),
            ("xemu (Standalone)", XEMU_INJECT_WITH_ROM, "needs_setup", "inject", "standalone", None),
            ("Vita3K (Standalone)", VITA3K_INJECT_NO_ROM, "needs_setup", "inject", "standalone", None),
            ("MAME (Standalone)", ARCADE_MAME_STANDALONE, "unbakeable", "no_rom_target", "standalone", None),
            ("MAME - Current", APPLE2_MAME_LIBRETRO_QUOTED, "unbakeable", "no_rom_target", "standalone", None),
            ("MAME [A440/1]", ARCHIMEDES_MAME_BACKSLASH_SEMI, "unbakeable", "quoting", "standalone", None),
            ("MAME (Standalone)", BBCMICRO_MAME_QUOTED, "unbakeable", "quoting", "standalone", None),
            ("Neko Project II", PC98_NEKOP2_STARTDIR, "unbakeable", "startdir", "standalone", None),
            ("Foo", SYNTH_UNKNOWN_PLACEHOLDER, "unbakeable", "unknown_placeholder", "standalone", None),
            ("Foo", SYNTH_LONE_QUOTE, "unbakeable", "quoting", "standalone", None),
            ("DREAMM", SYNTH_TRAILING_DOT, "unbakeable", "no_rom_target", "standalone", None),
        ],
    )
    def test_classification(self, label, text, status, reason, kind, core_so):
        option = classify_command(label, text)
        assert option.label == label
        assert option.status == status
        assert option.reason == reason
        assert option.kind == kind
        assert option.core_so == core_so
        assert option.command == text.strip()

    def test_inject_wins_over_missing_rom_target(self):
        """A ``%INJECT%`` command with no ``%ROM%`` is needs-setup, not no_rom_target."""
        option = classify_command("Vita3K", VITA3K_INJECT_NO_ROM)
        assert option.status == "needs_setup"
        assert option.reason == "inject"

    def test_startdir_beats_unknown_placeholder(self):
        """``%STARTDIR%`` is a known placeholder — it surfaces its own reason."""
        option = classify_command("Neko Project II", PC98_NEKOP2_STARTDIR)
        assert option.reason == "startdir"

    def test_env_prefixed_command_is_bakeable_standalone(self):
        option = classify_command("Dolphin (Standalone)", GC_DOLPHIN_STANDALONE)
        assert option.status == "bakeable"
        assert option.kind == "standalone"


class TestAnEntryTheResolverStatesNotInstalled:
    """``NOT_INSTALLED`` takes the place of the bake verdict, whatever the command."""

    def test_a_missing_standalone_is_withheld_with_its_reason(self):
        option = classify_command("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        assert option.status == "needs_setup"
        assert option.reason == "not_installed"
        # Identity fields survive.
        assert option.label == "Ryubing (Standalone)"
        assert option.kind == "standalone"
        assert option.command == "%EMULATOR_RYUBING% %ROM%"

    def test_a_startable_entry_keeps_its_bake_verdict(self):
        option = classify_command("PCSX2 (Standalone)", PS2_PCSX2_BATCH, installation=None)
        assert (option.status, option.not_established) == ("bakeable", None)

    def test_a_libretro_core_the_resolver_states_missing_is_withheld(self):
        option = classify_command("SwanStation", PSX_SWANSTATION, installation=NOT_INSTALLED)
        assert (option.status, option.reason) == ("needs_setup", "not_installed")
        assert option.kind == "libretro"
        assert option.core_so == "swanstation_libretro"

    def test_the_installation_reason_stands_ahead_of_an_unbakeable_command(self):
        option = classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT, installation=NOT_INSTALLED)
        assert option.reason == "not_installed"

    def test_a_withheld_option_drops_out_of_default_selection_and_its_pin_no_longer_resolves(self):
        withheld = classify_command("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        assert select_default_option([withheld]) is None
        assert label_to_invocation([withheld], "Ryubing (Standalone)") is None

    def test_a_withheld_option_reads_disabled_with_reason_in_payload(self):
        withheld = classify_command("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        payload = options_to_payload([withheld])
        assert payload[0]["bakeable"] is False
        assert payload[0]["reason"] == "not_installed"
        assert payload[0]["is_default"] is False


class TestAnEntryTheResolverCouldNotEstablish:
    """A selectable pick with a note, never the default by itself (#2195 D9)."""

    def _paprium(self) -> EmulatorOption:
        return classify_command("Paprium", PSX_SWANSTATION, installation=NOT_ESTABLISHED_PATH_UNSEEN)

    def test_it_bakes_as_its_command_would_and_carries_the_note(self):
        option = self._paprium()
        assert (option.status, option.reason, option.not_established) == ("bakeable", None, "path_unseen")

    def test_it_is_never_the_default_by_itself(self):
        swanstation = classify_command("SwanStation", PSX_SWANSTATION)
        assert select_default_option([self._paprium()]) is None
        assert select_default_option([self._paprium(), swanstation]) is swanstation

    def test_a_pick_of_it_stays(self):
        assert label_to_invocation([self._paprium()], "Paprium") == EmulatorInvocation.libretro(
            "swanstation_libretro", "Paprium"
        )
        assert resolve_platform_label([self._paprium()], "Paprium") == "Paprium"

    def test_it_reads_selectable_with_its_note_in_payload(self):
        payload = options_to_payload([self._paprium()])
        assert (payload[0]["bakeable"], payload[0]["reason"], payload[0]["is_default"]) == (True, "path_unseen", False)

    def test_an_unbakeable_command_keeps_the_reason_it_cannot_bake(self):
        option = classify_command("Shortcut", PS3_RPCS3_SHORTCUT, installation=NOT_ESTABLISHED)
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


class TestTheLauncher:
    """The resolver's launcher rides on the option into a standalone invocation."""

    _LAUNCHER = "/app/retrodeck/components/rpcs3/component_launcher.sh"

    def test_a_standalone_invocation_carries_the_launcher(self):
        option = classify_command("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, launcher=self._LAUNCHER)
        invocation = option_to_invocation(option)
        assert invocation is not None
        assert invocation.launcher == self._LAUNCHER

    def test_a_withheld_entry_renders_no_invocation_whatever_its_launcher(self):
        option = classify_command(
            "RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, installation=NOT_INSTALLED, launcher=self._LAUNCHER
        )
        assert option_to_invocation(option) is None


class TestSelectDefaultOption:
    def test_first_bakeable_wins_over_earlier_unbakeable(self):
        """The ps3 chain: Shortcut (script) → Game Serial (inject) → Directory (bakeable)."""
        options = [
            classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
            classify_command("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL),
            classify_command("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
            classify_command("RPCS3 ISO (Standalone)", PS3_RPCS3_DIRECTORY),
        ]
        default = select_default_option(options)
        assert default is not None
        assert default.label == "RPCS3 Directory (Standalone)"

    def test_libretro_default_when_first_in_order(self):
        options = [classify_command("SwanStation", PSX_SWANSTATION)]
        default = select_default_option(options)
        assert default is not None
        assert default.core_so == "swanstation_libretro"

    def test_none_when_nothing_bakeable(self):
        options = [
            classify_command("MAME - Current", APPLE2_MAME_LIBRETRO_QUOTED),
            classify_command("MAME (Standalone)", ARCADE_MAME_STANDALONE),
        ]
        assert select_default_option(options) is None

    def test_empty_options_returns_none(self):
        assert select_default_option([]) is None


class TestOptionToInvocation:
    def test_libretro_option_renders_libretro_invocation(self):
        option = classify_command("SwanStation", PSX_SWANSTATION)
        assert option_to_invocation(option) == EmulatorInvocation.libretro("swanstation_libretro", "SwanStation")

    def test_standalone_option_bakes_command_verbatim(self):
        option = classify_command("PCSX2 (Standalone)", PS2_PCSX2_BATCH)
        assert option_to_invocation(option) == EmulatorInvocation.standalone(PS2_PCSX2_BATCH, "PCSX2 (Standalone)")

    def test_unbakeable_option_returns_none(self):
        option = classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT)
        assert option_to_invocation(option) is None

    def test_needs_setup_option_returns_none(self):
        option = classify_command("Vita3K", VITA3K_INJECT_NO_ROM)
        assert option_to_invocation(option) is None

    def test_none_option_returns_none(self):
        assert option_to_invocation(None) is None


class TestLabelToInvocation:
    OPTIONS: ClassVar[list[EmulatorOption]] = [
        classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
        classify_command("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
    ]

    def test_bakeable_label_resolves(self):
        result = label_to_invocation(self.OPTIONS, "RPCS3 Directory (Standalone)")
        assert result == EmulatorInvocation.standalone(PS3_RPCS3_DIRECTORY, "RPCS3 Directory (Standalone)")

    def test_unbakeable_label_returns_none(self):
        assert label_to_invocation(self.OPTIONS, "RPCS3 Shortcut (Standalone)") is None

    def test_unknown_label_returns_none(self):
        assert label_to_invocation(self.OPTIONS, "Not A Real Emulator") is None


class TestResolveFirmwareOption:
    """The emulator a platform's BIOS answer is about."""

    def test_the_pick_where_there_is_one(self):
        options = [
            classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
            classify_command("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
        ]
        option = resolve_firmware_option(options, None)
        assert option is not None
        assert option.label == "RPCS3 Directory (Standalone)"

    def test_the_emulator_retrodeck_declares_first_where_nothing_bakes(self):
        """RetroDECK's plain launch starts its first declared emulator, installed or not."""
        first = classify_command("MAME (Standalone)", ARCADE_MAME_STANDALONE, installation=NOT_INSTALLED)
        options = [first, classify_command("MAME - Current", APPLE2_MAME_LIBRETRO_QUOTED)]
        assert resolve_firmware_option(options, "MAME - Current") is first

    def test_none_where_no_emulator_is_listed(self):
        assert resolve_firmware_option([], None) is None


class TestResolvePlatformLabel:
    """The platform-layer label two services render — one rule, one place."""

    OPTIONS: ClassVar[list[EmulatorOption]] = [
        classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
        classify_command("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
        classify_command("RPCS3 ISO (Standalone)", PS3_RPCS3_DIRECTORY),
    ]

    def test_no_override_falls_back_to_the_default(self):
        assert resolve_platform_label(self.OPTIONS, None) == "RPCS3 Directory (Standalone)"

    def test_a_resolvable_override_wins(self):
        assert resolve_platform_label(self.OPTIONS, "RPCS3 ISO (Standalone)") == "RPCS3 ISO (Standalone)"

    def test_an_override_naming_an_unbakeable_option_degrades(self):
        # The label resolves to no invocation, so naming it would promise a
        # launch that cannot happen.
        assert resolve_platform_label(self.OPTIONS, "RPCS3 Shortcut (Standalone)") == "RPCS3 Directory (Standalone)"

    def test_an_override_naming_nothing_degrades(self):
        assert resolve_platform_label(self.OPTIONS, "Uninstalled Emulator") == "RPCS3 Directory (Standalone)"

    def test_nothing_bakeable_answers_no_label(self):
        options = [classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT)]
        assert resolve_platform_label(options, None) is None
        assert resolve_platform_label(options, "RPCS3 Shortcut (Standalone)") is None

    def test_empty_options_answer_no_label(self):
        assert resolve_platform_label([], "anything") is None


class TestOptionsToPayload:
    def test_marks_single_default_and_bakeability(self):
        options = [
            classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT),
            classify_command("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL),
            classify_command("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY),
        ]
        payload = options_to_payload(options)
        assert payload == [
            {
                "label": "RPCS3 Shortcut (Standalone)",
                "labels": ["RPCS3 Shortcut (Standalone)"],
                "name": "RPCS3 Shortcut (Standalone)",
                "kind": "standalone",
                "core_so": None,
                "emulator": None,
                "is_default": False,
                "bakeable": False,
                "reason": "shortcut_script",
            },
            {
                "label": "RPCS3 Game Serial (Standalone)",
                "labels": ["RPCS3 Game Serial (Standalone)"],
                "name": "RPCS3 Game Serial (Standalone)",
                "kind": "standalone",
                "core_so": None,
                "emulator": None,
                "is_default": False,
                "bakeable": False,
                "reason": "inject",
            },
            {
                "label": "RPCS3 Directory (Standalone)",
                "labels": ["RPCS3 Directory (Standalone)"],
                "name": "RPCS3 Directory (Standalone)",
                "kind": "standalone",
                "core_so": None,
                "emulator": None,
                "is_default": True,
                "bakeable": True,
                "reason": None,
            },
        ]

    def test_libretro_payload_carries_core_so(self):
        payload = options_to_payload([classify_command("SwanStation", PSX_SWANSTATION)])
        assert payload[0]["kind"] == "libretro"
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
                classify_command("SwanStation", PSX_SWANSTATION, emulator="swanstation_libretro.so"),
                classify_command("PCSX2 (Standalone)", PS2_PCSX2_BATCH, emulator="PCSX2"),
            ]
        )

        assert [(e["core_so"], e["emulator"]) for e in payload] == [
            ("swanstation_libretro", "swanstation_libretro.so"),
            (None, "PCSX2"),
        ]

    def test_an_unidentified_row_carries_no_identity(self):
        """The resolver could not name the emulator, and the payload says so rather than guessing."""
        payload = options_to_payload([classify_command("Citra", PSX_SWANSTATION)])

        assert payload[0]["emulator"] is None

    def test_no_default_when_none_bakeable(self):
        payload = options_to_payload([classify_command("MAME (Standalone)", ARCADE_MAME_STANDALONE)])
        assert all(entry["is_default"] is False for entry in payload)

    def test_command_text_not_leaked(self):
        payload = options_to_payload([classify_command("PCSX2 (Standalone)", PS2_PCSX2_BATCH)])
        assert "command" not in payload[0]

    def test_labels_that_run_one_command_are_one_entry_named_by_every_label(self):
        """RetroDECK's PlayStation 2 list, in its own order: LRPS2 and PCSX2 start the same core."""
        payload = options_to_payload(
            [
                classify_command("PCEE2", PS2_PCEE2_LIBRETRO, emulator="pcee2_libretro.so"),
                classify_command("LRPS2", PS2_PCSX2_LIBRETRO, emulator="pcsx2_libretro.so"),
                classify_command("PCSX2", PS2_PCSX2_LIBRETRO, emulator="pcsx2_libretro.so"),
                classify_command("PCSX2 (Standalone)", PS2_PCSX2_BATCH, emulator="PCSX2"),
            ]
        )

        assert [(e["label"], e["labels"], e["name"]) for e in payload] == [
            ("PCEE2", ["PCEE2"], "PCEE2"),
            ("LRPS2", ["LRPS2", "PCSX2"], "LRPS2 / PCSX2"),
            ("PCSX2 (Standalone)", ["PCSX2 (Standalone)"], "PCSX2 (Standalone)"),
        ]

    def test_the_merged_entry_is_the_default_where_any_of_its_labels_is(self):
        payload = options_to_payload(
            [
                classify_command("LRPS2", PS2_PCSX2_LIBRETRO, emulator="pcsx2_libretro.so"),
                classify_command("PCSX2", PS2_PCSX2_LIBRETRO, emulator="pcsx2_libretro.so"),
            ]
        )

        assert [(e["name"], e["is_default"], e["bakeable"]) for e in payload] == [("LRPS2 / PCSX2", True, True)]

    def test_one_emulator_launched_different_ways_stays_one_entry_per_way(self):
        """RPCS3's four PS3 labels run three commands: only Directory and ISO are one choice."""
        payload = options_to_payload(
            [
                classify_command("RPCS3 Shortcut (Standalone)", PS3_RPCS3_SHORTCUT, emulator="RPCS3"),
                classify_command("RPCS3 Game Serial (Standalone)", PS3_RPCS3_GAME_SERIAL, emulator="RPCS3"),
                classify_command("RPCS3 Directory (Standalone)", PS3_RPCS3_DIRECTORY, emulator="RPCS3"),
                classify_command("RPCS3 ISO (Standalone)", PS3_RPCS3_DIRECTORY, emulator="RPCS3"),
            ]
        )

        assert [e["name"] for e in payload] == [
            "RPCS3 Shortcut (Standalone)",
            "RPCS3 Game Serial (Standalone)",
            "RPCS3 Directory (Standalone) / RPCS3 ISO (Standalone)",
        ]

    def test_one_emulator_s_device_variants_stay_apart(self):
        """EKA2L1's four Symbian labels each start a different device."""
        labels = [
            ("EKA2L1 [Nokia N-Gage] (Standalone)", SYMBIAN_EKA2L1_RH29),
            ("EKA2L1 [Nokia N70] (Standalone)", SYMBIAN_EKA2L1_RM84),
            ("EKA2L1 [Nokia N97] (Standalone)", SYMBIAN_EKA2L1_RM507),
            ("EKA2L1 [Custom device] (Standalone)", SYMBIAN_EKA2L1_CUSTOM),
        ]
        payload = options_to_payload([classify_command(label, text, emulator="EKA2L1") for label, text in labels])

        assert [e["labels"] for e in payload] == [[label] for label, _ in labels]


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
        option = classify_command("L", text)
        assert option.status in self._VALID_STATUSES
        assert option.reason in self._VALID_REASONS
        # A bakeable option never carries a reason; a non-bakeable one always does.
        assert (option.reason is None) == (option.status == "bakeable")

    @pytest.mark.parametrize("text", CORPUS)
    def test_classification_is_deterministic(self, text):
        first = classify_command("L", text)
        second = classify_command("L", text)
        assert first == second

    def test_select_default_returns_none_or_bakeable(self):
        options = [classify_command(f"L{i}", text) for i, text in enumerate(self.CORPUS)]
        default = select_default_option(options)
        assert default is None or default.status == "bakeable"

    def test_selected_default_renders_an_invocation(self):
        options = [classify_command("SwanStation", PSX_SWANSTATION), classify_command("Q", SYNTH_LONE_QUOTE)]
        default = select_default_option(options)
        assert isinstance(option_to_invocation(default), EmulatorInvocation)


def test_emulator_option_is_frozen():
    option = EmulatorOption(
        label="x", kind="standalone", core_so=None, command="%ROM%", status="bakeable", reason=None, emulator="RPCS3"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        option.label = "y"  # type: ignore[misc]
