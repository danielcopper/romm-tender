"""Tests for the firmware-want vocabulary and its one classification."""

from __future__ import annotations

import pytest

from domain.firmware_wants import (
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_OPEN,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    WANTED_NEEDED,
    WANTED_NOT_NEEDED,
    WANTED_OPTIONAL,
    WANTED_UNKNOWN,
    CoreFirmwareVerdict,
    FirmwareCatalogue,
    FirmwarePlacement,
    FirmwareWant,
    classify_wanted,
)


def _placement(file_name: str, *wants: FirmwareWant, relative_path: str | None = None) -> FirmwarePlacement:
    return FirmwarePlacement(
        file_name=file_name,
        relative_path=relative_path if relative_path is not None else file_name,
        description=f"{file_name} (BIOS)",
        wants=wants,
    )


def _catalogue(
    *placements: FirmwarePlacement,
    unread: frozenset[str] = frozenset(),
    resolved: bool = True,
) -> FirmwareCatalogue:
    return FirmwareCatalogue(placements=placements, unread_emulators=unread, resolved=resolved)


class TestRequiredByAny:
    def test_required_by_one_core_is_required(self):
        placement = _placement(
            "scph5501.bin",
            FirmwareWant(emulator="swanstation_libretro", required=False),
            FirmwareWant(emulator="mednafen_psx_libretro", required=True),
        )
        assert placement.required_by_any is True

    def test_optional_everywhere_is_not_required(self):
        placement = _placement(
            "dc_boot.bin",
            FirmwareWant(emulator="flycast_libretro", required=False),
        )
        assert placement.required_by_any is False


class TestClassifyWanted:
    def test_required_by_any_core_is_needed(self):
        placement = _placement("codehandler.bin", FirmwareWant(emulator="dolphin_libretro", required=True))
        assert classify_wanted(placement, complete=True) == WANTED_NEEDED

    def test_declared_but_never_required_is_optional(self):
        placement = _placement("dc_boot.bin", FirmwareWant(emulator="flycast_libretro", required=False))
        assert classify_wanted(placement, complete=True) == WANTED_OPTIONAL

    def test_a_needed_file_stays_needed_on_an_incomplete_reading(self):
        """A match is a match — the reading state only ever decides an ABSENCE."""
        placement = _placement("codehandler.bin", FirmwareWant(emulator="dolphin_libretro", required=True))
        assert classify_wanted(placement, complete=False) == WANTED_NEEDED

    def test_no_placement_on_a_complete_reading_is_not_needed(self):
        assert classify_wanted(None, complete=True) == WANTED_NOT_NEEDED

    def test_no_placement_on_an_incomplete_reading_is_unknown(self):
        """The distinction the collapsed boolean could not express."""
        assert classify_wanted(None, complete=False) == WANTED_UNKNOWN


class TestReadingCompleteFor:
    def test_a_launching_emulator_that_was_read_is_complete(self):
        catalogue = _catalogue(unread=frozenset({"fbalpha_libretro.so"}))
        assert catalogue.reading_complete_for("mgba_libretro.so") is True

    def test_an_unread_launching_emulator_blocks_completeness(self):
        catalogue = _catalogue(unread=frozenset({"fbalpha_libretro.so"}))
        assert catalogue.reading_complete_for("fbalpha_libretro.so") is False

    def test_another_unread_emulator_of_the_platform_does_not(self):
        """The doubt is the launch's, and an emulator nobody is running is not in it.

        A platform offering four emulators used to lose its whole answer to any
        one of them being unreadable, whichever one the game launches with.
        """
        catalogue = _catalogue(unread=frozenset({"gearlynx_libretro.so"}))
        assert catalogue.reading_complete_for("mednafen_psx_libretro.so") is True

    def test_a_standalone_emulator_is_asked_about_like_any_other(self):
        catalogue = _catalogue(unread=frozenset({"PCSX2"}))

        assert catalogue.reading_complete_for("DUCKSTATION") is True
        assert catalogue.reading_complete_for("PCSX2") is False

    def test_no_emulator_to_name_is_never_complete(self):
        """Nothing was resolved or nothing could be identified — the same silence either way.

        Answering ``True`` would let every server file classify ``not_needed``
        and the platform read a green "Nothing required" off asking nobody, which
        is the collapse the four-valued vocabulary exists to prevent.
        """
        catalogue = _catalogue()
        assert catalogue.reading_complete_for(None) is False

    def test_an_unresolved_reading_is_never_complete(self):
        """An emulator that would otherwise be complete, so the ``resolved`` gate is what answers."""
        catalogue = _catalogue(resolved=False)
        assert catalogue.reading_complete_for("mgba_libretro.so") is False


class TestCoresNeedingASystemImage:
    """Which cores declare for a console that will not start without an image.

    The per-core half read over every core at once, for a surface that lists
    several emulators beside one file and has to say which of them are in that
    state. ``verdict_for`` answers the same question one core at a time, and the
    two must not be able to disagree about a core.
    """

    @staticmethod
    def _with(**verdicts: str | None) -> FirmwareCatalogue:
        return FirmwareCatalogue(
            placements=(),
            unread_emulators=frozenset(),
            resolved=True,
            emulator_verdicts={
                emulator: CoreFirmwareVerdict(system_firmware=state) for emulator, state in verdicts.items()
            },
        )

    def test_only_the_console_that_will_not_start_is_named(self):
        catalogue = self._with(
            swanstation_libretro=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            pcsx_rearmed_libretro=SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
            snes9x_libretro=SYSTEM_FIRMWARE_RUNS_WITHOUT,
            mgba_libretro=SYSTEM_FIRMWARE_OPEN,
        )

        assert catalogue.emulators_needing_a_system_image() == frozenset({"swanstation_libretro"})

    def test_a_core_the_table_says_nothing_about_is_left_out(self):
        """An absent entry is an unasked question, and this set answers only where something was recorded."""
        catalogue = self._with(swanstation_libretro=None)

        assert catalogue.emulators_needing_a_system_image() == frozenset()
        assert catalogue.verdict_for("gpsp_libretro") is None

    def test_a_reading_that_did_not_happen_names_nobody(self):
        assert _catalogue(resolved=False).emulators_needing_a_system_image() == frozenset()

    def test_it_agrees_with_the_per_core_answer_for_every_core(self):
        """One question, two shapes — asked over all cores or one at a time."""
        catalogue = self._with(
            swanstation_libretro=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            pcsx_rearmed_libretro=SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
        )

        named = catalogue.emulators_needing_a_system_image()

        for emulator in ("swanstation_libretro", "pcsx_rearmed_libretro"):
            verdict = catalogue.verdict_for(emulator)
            assert verdict is not None
            assert (emulator in named) is verdict.system_needs_an_image


class TestCoresNeedingOneOfTheirFiles:
    """Which cores state a DISJUNCTION, and over how many files.

    The narrower half of :meth:`emulators_needing_a_system_image`: a core is here
    only where its console needs an image AND the core marks nothing required,
    because that is the only shape in which "one of these" is the whole of what
    the core says. The corpus is the deployed PlayStation as it was measured on
    the reference device — SwanStation declares five images and marks all five
    optional, Beetle PSX declares the same five and marks three of them
    required, PCSX ReARMed declares them and carries its own substitute.
    """

    _IMAGES = ("ps1_rom.bin", "psxonpsp660.bin", "scph5500.bin", "scph5501.bin", "scph5502.bin")

    @classmethod
    def _psx(cls, *, beetle_required: bool = True) -> FirmwareCatalogue:
        """Three cores over five images, each want stated per file.

        Per file because that is how the resolver answers, and because "marks
        nothing required" is a statement about a core's WHOLE declaration: a
        catalogue that stated one want per core could not tell the two apart.
        """
        placements = tuple(
            _placement(
                name,
                FirmwareWant(emulator="swanstation_libretro", required=False),
                FirmwareWant(emulator="mednafen_psx_libretro", required=beetle_required and name.startswith("scph")),
                FirmwareWant(emulator="pcsx_rearmed_libretro", required=False),
            )
            for name in cls._IMAGES
        )
        return FirmwareCatalogue(
            placements=placements,
            unread_emulators=frozenset(),
            resolved=True,
            emulator_verdicts={
                "swanstation_libretro": CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT),
                "mednafen_psx_libretro": CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT),
                "pcsx_rearmed_libretro": CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CORE_ALTERNATIVE),
            },
        )

    def test_a_core_that_marks_nothing_required_carries_its_whole_declaration(self):
        """Five, because SwanStation declares five — not because a platform lists five."""
        assert self._psx().emulators_needing_one_of_their_files()["swanstation_libretro"] == 5

    def test_a_core_that_does_mark_something_required_is_left_out(self):
        """The Beetle PSX shape, and the reason the two answers differ.

        Its console needs an image too, so the wider answer names it — and its
        three required rows already carry that demand as their own requirement.
        Naming it here as well would put one requirement on the page twice, in
        two vocabularies.
        """
        catalogue = self._psx()

        assert "mednafen_psx_libretro" in catalogue.emulators_needing_a_system_image()
        assert "mednafen_psx_libretro" not in catalogue.emulators_needing_one_of_their_files()

    def test_one_required_file_anywhere_silences_the_core_on_every_file(self):
        """It is the core's whole declaration that decides, not the file in hand.

        Beetle PSX marks ``scph5500.bin`` required and ``ps1_rom.bin`` optional.
        Read per file it would state a disjunction over the second, which is the
        annotation that put "the console will not start without one" under a
        core that hard-requires three other images.
        """
        assert "mednafen_psx_libretro" not in self._psx(beetle_required=True).emulators_needing_one_of_their_files()
        assert self._psx(beetle_required=False).emulators_needing_one_of_their_files()["mednafen_psx_libretro"] == 5

    def test_a_core_carrying_its_own_substitute_is_left_out(self):
        """PCSX ReARMed marks nothing required either — its console makes the difference."""
        assert "pcsx_rearmed_libretro" not in self._psx().emulators_needing_one_of_their_files()

    def test_a_core_the_table_says_nothing_about_is_left_out(self):
        """An absent entry is an unasked question, never a demand."""
        catalogue = _catalogue(_placement("gba_bios.bin", FirmwareWant(emulator="gpsp_libretro", required=False)))

        assert catalogue.emulators_needing_one_of_their_files() == {}

    def test_a_reading_that_did_not_happen_names_nobody(self):
        assert _catalogue(resolved=False).emulators_needing_one_of_their_files() == {}

    def test_an_emulator_with_no_core_of_its_own_is_not_counted(self):
        """A standalone emulator names no ``.so``, so there is no key to answer under."""
        catalogue = FirmwareCatalogue(
            placements=(_placement("scph5501.bin", FirmwareWant(emulator=None, required=False)),),
            unread_emulators=frozenset(),
            resolved=True,
            emulator_verdicts={"swanstation_libretro": CoreFirmwareVerdict(SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT)},
        )

        assert catalogue.emulators_needing_one_of_their_files() == {}


class TestByFileName:
    def test_indexes_every_placement(self):
        catalogue = _catalogue(
            _placement("a.bin", FirmwareWant(emulator="one_libretro", required=True)),
            _placement("b.bin", FirmwareWant(emulator="two_libretro", required=False)),
        )
        index = catalogue.by_file_name()
        assert set(index) == {"a.bin", "b.bin"}
        assert index["a.bin"].required_by_any is True

    def test_empty_catalogue_indexes_to_nothing(self):
        assert _catalogue().by_file_name() == {}


@pytest.mark.parametrize(
    ("complete", "expected"),
    [(True, WANTED_NOT_NEEDED), (False, WANTED_UNKNOWN)],
)
def test_absence_is_classified_by_the_reading_state_alone(complete, expected):
    assert classify_wanted(None, complete=complete) == expected
