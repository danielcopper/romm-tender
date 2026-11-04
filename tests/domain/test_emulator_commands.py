"""Tests for domain/emulator_commands — a system's emulator entries as Tender offers them.

The command strings are sampled verbatim from a live RetroDECK
``es_systems.xml`` (linux/ flavor); which of them loads a core is what the
resolver states for it (``CORE_OF``), so an entry is built the way the
catalogue reading builds it.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from domain import emulator_commands
from domain.emulator_commands import (
    CORE,
    FOREIGN_CORE,
    NOT_ESTABLISHED,
    NOT_ESTABLISHED_PATH_UNSEEN,
    NOT_INSTALLED,
    PROGRAM,
    EmulatorOption,
    default_option,
    emulator_option,
    options_to_payload,
    resolve_firmware_option,
    shipped_default,
)

# --- Real command strings (sampled from a live es_systems.xml) --------------

PSX_SWANSTATION = "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/swanstation_libretro.so %ROM%"
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
ARCADE_MAME_STANDALONE = (
    "%EMULATOR_MAME% -inipath /var/config/mame/ini -rompath %GAMEDIR%\\;%ROMPATH%/arcade %BASENAME%"
)
APPLE2_MAME_LIBRETRO_QUOTED = (
    "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mame_libretro.so "
    '"apple2e -rompath \\"%GAMEDIRRAW%;%ROMPATH%/apple2\\" -gameio joy -flop1 \\"%GAMEDIRRAW%/%FILENAME%\\""'
)
PC98_NEKOP2_STARTDIR = "%STARTDIR%=%GAMEDIR% %EMULATOR_RETROARCH% -L %CORE_RETROARCH%/nekop2_libretro.so %ROM%"

# --- What the resolver states for the entries above ------------------------

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
    """``emulator_option`` handed what the resolver states for *text*'s entry: its kind and its core."""
    core_so = CORE_OF.get(text)
    if core_so is None:
        return emulator_option(label, kind=PROGRAM, **resolver)
    return emulator_option(label, kind=CORE, core_so=core_so, **resolver)


def _declared(*options: EmulatorOption) -> list[EmulatorOption]:
    """*options* with the declared positions a catalogue that lists them in this order states."""
    return [dataclasses.replace(option, declared_index=index) for index, option in enumerate(options)]


class TestAnEntryTheResolverStatesNotInstalled:
    """``NOT_INSTALLED`` marks the entry not installed, whatever the command."""

    def test_a_missing_standalone_is_withheld_with_its_reason(self):
        option = _classify("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        assert option.not_installed is True
        # Identity fields survive.
        assert option.label == "Ryubing (Standalone)"
        assert option.kind == PROGRAM

    def test_a_startable_entry_is_neither_withheld_nor_noted(self):
        option = _classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH, installation=None)
        assert (option.not_installed, option.not_established) == (False, None)

    def test_a_libretro_core_the_resolver_states_missing_is_withheld(self):
        option = _classify("SwanStation", PSX_SWANSTATION, installation=NOT_INSTALLED)
        assert option.not_installed is True
        assert option.kind == CORE
        assert option.core_so == "swanstation_libretro"

    def test_a_withheld_option_shipped_first_stays_the_default(self):
        (withheld,) = _declared(
            _classify("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        )
        assert default_option([withheld]) is withheld

    def test_a_withheld_option_reads_disabled_with_reason_in_payload(self):
        withheld = _classify("Ryubing (Standalone)", "%EMULATOR_RYUBING% %ROM%", installation=NOT_INSTALLED)
        payload = options_to_payload([withheld])
        assert payload[0]["reason"] == "not_installed"
        assert payload[0]["is_default"] is False


class TestAnEntryTheResolverCouldNotEstablish:
    """A selectable pick with a note, never the default by itself (#2195 D9)."""

    def _unseen(self) -> EmulatorOption:
        return _classify("PCSX2 (Standalone)", PS2_PCSX2_BATCH, installation=NOT_ESTABLISHED_PATH_UNSEEN)

    def test_it_stays_a_pick_and_carries_the_note(self):
        option = self._unseen()
        assert (option.not_installed, option.not_established) == (False, "path_unseen")

    def test_it_is_never_the_default_by_itself(self):
        swanstation = _classify("SwanStation", PSX_SWANSTATION)
        assert default_option(_declared(self._unseen())) is None
        assert default_option(_declared(self._unseen(), swanstation)) is None

    def test_a_pick_of_it_stays(self):
        picked = resolve_firmware_option([self._unseen()], "PCSX2 (Standalone)")
        assert picked is not None
        assert picked.label == "PCSX2 (Standalone)"

    def test_it_reads_selectable_with_its_note_in_payload(self):
        payload = options_to_payload([self._unseen()])
        assert (payload[0]["reason"], payload[0]["is_default"]) == ("path_unseen", False)

    def test_a_shortcut_entry_whose_installation_is_not_established_sends_its_note(self):
        option = _classify("Shortcut", PS3_RPCS3_SHORTCUT, installation=NOT_ESTABLISHED)
        assert options_to_payload([option])[0]["reason"] == NOT_ESTABLISHED


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


class TestWhatTheResolverStatesAboutAnEntry:
    """Kind and core are the resolver's, carried as it states them."""

    def test_finalburn_neo_on_neo_geo_cd_reads_as_its_core(self):
        option = emulator_option("FinalBurn Neo", kind=CORE, core_so="fbneo_libretro")
        assert (option.kind, option.core_so) == (CORE, "fbneo_libretro")

    def test_a_foreign_core_is_its_own_kind(self):
        option = emulator_option("Citra", kind=FOREIGN_CORE)
        assert (option.kind, option.core_so) == (FOREIGN_CORE, None)
        assert options_to_payload([option])[0]["kind"] == FOREIGN_CORE


class TestShippedDefault:
    """The default is the entry the source ships first, read off its declared position (#2195 D7)."""

    def test_the_lowest_declared_position_is_the_default_whatever_its_state(self):
        """The ps3 chain: the Shortcut entry is the default, whatever starting with it comes to."""
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


class TestOptionsToPayload:
    def test_marks_single_default(self):
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
                "reason": None,
            },
            {
                "label": "RPCS3 Game Serial (Standalone)",
                "kind": PROGRAM,
                "core_so": None,
                "emulator": None,
                "declared_index": 1,
                "is_default": False,
                "chosen_in_frontend": False,
                "reason": None,
            },
            {
                "label": "RPCS3 Directory (Standalone)",
                "kind": PROGRAM,
                "core_so": None,
                "emulator": None,
                "declared_index": 2,
                "is_default": False,
                "chosen_in_frontend": False,
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

    def test_no_default_when_none_is_declared(self):
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


def test_emulator_option_is_frozen():
    option = EmulatorOption(label="x", kind=PROGRAM, core_so=None, emulator="RPCS3")
    with pytest.raises(dataclasses.FrozenInstanceError):
        option.label = "y"  # type: ignore[misc]
