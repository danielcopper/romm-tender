"""Tests for the verdict over a one-of group and the console regions a game maps onto.

Most fixtures are a PlayStation's because that is the shape a real machine
answers with, and one class deliberately is not: nothing in the verdict may
know which console it is judging, so an invented system with invented regions
is judged by exactly the same rules.
"""

from __future__ import annotations

import pytest

from domain.firmware_groups import (
    GROUP_MET,
    GROUP_PARTIAL,
    GROUP_UNKNOWN,
    GROUP_UNMET,
    REGION_NTSC_J,
    REGION_NTSC_U,
    REGION_PAL,
    console_regions_of,
    fetched_as_required,
    judge_group,
    judge_group_for_game,
    judge_groups,
)
from domain.firmware_wants import FirmwareGroup, FirmwareOption, FirmwarePlacement, FirmwareWant

_BEETLE = "mednafen_psx_libretro.so"


def _beetle(
    *, ntsc_j: bool | None = False, ntsc_u: bool | None = False, pal: bool | None = False, **regions
) -> FirmwareGroup:
    """Beetle PSX's group: one named image per region, each serving its own region only."""
    return FirmwareGroup(
        emulator=_BEETLE,
        options=(
            FirmwareOption("scph5500.bin", (REGION_NTSC_J,), ntsc_j),
            FirmwareOption("scph5501.bin", (REGION_NTSC_U,), ntsc_u),
            FirmwareOption("scph5502.bin", (REGION_PAL,), pal),
        ),
        **regions,
    )


def _found(satisfied: bool | None = True) -> FirmwareGroup:
    """SwanStation's group: the image its search found, serving every region."""
    return FirmwareGroup(
        emulator="swanstation_libretro.so",
        options=(FirmwareOption("scph1001.bin", (REGION_NTSC_J, REGION_NTSC_U, REGION_PAL), satisfied),),
    )


class TestJudgeGroup:
    def test_an_image_serving_every_region_meets_the_group(self):
        verdict = judge_group(_found())

        assert verdict.state == GROUP_MET
        assert verdict.covered == (REGION_NTSC_J, REGION_NTSC_U, REGION_PAL)
        assert (verdict.missing, verdict.unchecked) == ((), ())

    def test_one_regions_image_is_partial_and_names_what_is_left(self):
        verdict = judge_group(_beetle(ntsc_u=True))

        assert verdict.state == GROUP_PARTIAL
        assert verdict.covered == (REGION_NTSC_U,)
        assert verdict.missing == (REGION_NTSC_J, REGION_PAL)

    def test_every_regions_own_image_meets_the_group(self):
        assert judge_group(_beetle(ntsc_j=True, ntsc_u=True, pal=True)).state == GROUP_MET

    def test_nothing_in_place_is_unmet(self):
        verdict = judge_group(_beetle())

        assert verdict.state == GROUP_UNMET
        assert verdict.covered == ()
        assert verdict.missing == (REGION_NTSC_J, REGION_NTSC_U, REGION_PAL)

    def test_an_option_there_and_unread_is_unknown_never_unmet(self):
        """Something is in place; whether the emulator takes it is what nobody established."""
        verdict = judge_group(_beetle(ntsc_u=None))

        assert verdict.state == GROUP_UNKNOWN
        assert verdict.unchecked == (REGION_NTSC_U,)
        assert verdict.missing == (REGION_NTSC_J, REGION_PAL)

    def test_an_unread_image_serving_every_region_is_unknown(self):
        assert judge_group(_found(None)).state == GROUP_UNKNOWN

    def test_a_region_nobody_checked_keeps_a_covered_group_from_reading_green(self):
        verdict = judge_group(
            FirmwareGroup(
                emulator="swanstation_libretro.so",
                options=(FirmwareOption("scph1001.bin", (REGION_NTSC_U,), True),),
                unchecked_regions=(REGION_NTSC_J, REGION_PAL),
            )
        )

        assert verdict.state == GROUP_UNKNOWN
        assert verdict.covered == (REGION_NTSC_U,)
        assert verdict.unchecked == (REGION_NTSC_J, REGION_PAL)

    def test_a_region_stated_uncovered_stays_red_beside_an_unchecked_one(self):
        verdict = judge_group(
            FirmwareGroup(
                emulator="swanstation_libretro.so",
                options=(FirmwareOption("scph1001.bin", (REGION_NTSC_U,), True),),
                unchecked_regions=(REGION_NTSC_J,),
                absent_regions=(REGION_PAL,),
            )
        )

        assert verdict.state == GROUP_PARTIAL
        assert verdict.missing == (REGION_PAL,)
        assert verdict.unchecked == (REGION_NTSC_J,)

    def test_nothing_in_place_stays_unmet_whatever_else_went_unchecked(self):
        verdict = judge_group(_beetle(unchecked_regions=("ntsc-k",)))

        assert verdict.state == GROUP_UNMET
        assert verdict.unchecked == ("ntsc-k",)

    def test_a_region_stated_to_boot_nothing_is_missing(self):
        verdict = judge_group(
            FirmwareGroup(
                emulator="swanstation_libretro.so",
                options=(FirmwareOption("scph1001.bin", (REGION_NTSC_U,), True),),
                absent_regions=(REGION_NTSC_J, REGION_PAL),
            )
        )

        assert verdict.state == GROUP_PARTIAL
        assert verdict.missing == (REGION_NTSC_J, REGION_PAL)

    def test_one_file_under_two_options_covers_both_regions(self):
        group = FirmwareGroup(
            emulator=_BEETLE,
            options=(
                FirmwareOption("psxonpsp660.bin", (REGION_NTSC_J,), True),
                FirmwareOption("scph5501.bin", (REGION_NTSC_U,), False),
                FirmwareOption("psxonpsp660.bin", (REGION_PAL,), True),
            ),
        )

        verdict = judge_group(group)
        assert verdict.covered == (REGION_NTSC_J, REGION_PAL)
        assert verdict.missing == (REGION_NTSC_U,)
        assert group.regions_of("psxonpsp660.bin") == (REGION_NTSC_J, REGION_PAL)


class TestTheVerdictCarriesWhatAPageListsUnderIt:
    """A page lists the group's options under its verdict, so the verdict carries them whole."""

    def test_the_platforms_verdict_carries_every_option_and_every_region(self):
        group = _beetle(ntsc_u=True, unchecked_regions=("ntsc-k",))

        verdict = judge_group(group)
        assert verdict.options == group.options
        assert verdict.regions == (REGION_NTSC_J, REGION_NTSC_U, REGION_PAL, "ntsc-k")

    def test_a_covered_games_verdict_still_carries_every_option(self):
        group = _beetle(ntsc_u=True)

        verdict = judge_group_for_game(group, (REGION_NTSC_U,))
        assert verdict.state == GROUP_MET
        assert verdict.options == group.options
        assert verdict.regions == group.regions

    def test_a_games_verdict_still_carries_every_option(self):
        group = _beetle(ntsc_u=True)

        verdict = judge_group_for_game(group, (REGION_NTSC_J,))
        assert verdict.options == group.options
        assert verdict.regions == group.regions


class TestAnyConsoleIsJudgedTheSameWay:
    """An invented system, emulator and region vocabulary — the verdict knows none of them."""

    def _group(self, **verdicts: bool | None) -> FirmwareGroup:
        return FirmwareGroup(
            emulator="ARCADIA",
            options=(
                FirmwareOption("north.rom", ("north",), verdicts.get("north", False)),
                FirmwareOption("south.rom", ("south", "east"), verdicts.get("south", False)),
            ),
        )

    @pytest.mark.parametrize(
        ("verdicts", "state"),
        [
            ({"north": True, "south": True}, GROUP_MET),
            ({"south": True}, GROUP_PARTIAL),
            ({}, GROUP_UNMET),
            ({"north": None}, GROUP_UNKNOWN),
        ],
    )
    def test_the_four_states(self, verdicts, state):
        assert judge_group(self._group(**verdicts)).state == state

    def test_the_regions_are_the_ones_the_group_names(self):
        verdict = judge_group(self._group(south=True))

        assert verdict.covered == ("south", "east")
        assert verdict.missing == ("north",)

    def test_a_game_of_an_invented_region_is_judged_over_it(self):
        assert judge_group_for_game(self._group(south=True), ("east",)).state == GROUP_MET
        assert judge_group_for_game(self._group(south=True), ("north",)).state == GROUP_UNMET


class TestJudgeGroupForGame:
    def test_a_game_whose_region_is_covered_is_met_over_a_partial_platform(self):
        verdict = judge_group_for_game(_beetle(ntsc_u=True), (REGION_NTSC_U,))

        assert verdict.state == GROUP_MET
        assert verdict.covered == (REGION_NTSC_U,)
        assert verdict.game_regions == (REGION_NTSC_U,)

    def test_a_game_whose_region_is_not_covered_is_unmet_however_much_else_is(self):
        verdict = judge_group_for_game(_beetle(ntsc_u=True, pal=True), (REGION_NTSC_J,))

        assert verdict.state == GROUP_UNMET
        assert verdict.missing == (REGION_NTSC_J,)

    def test_a_game_of_several_regions_is_covered_where_one_is(self):
        verdict = judge_group_for_game(_beetle(pal=True), (REGION_NTSC_U, REGION_PAL))

        assert verdict.state == GROUP_MET
        assert verdict.covered == (REGION_PAL,)

    def test_a_game_whose_region_nobody_checked_is_unknown(self):
        verdict = judge_group_for_game(_beetle(ntsc_u=None), (REGION_NTSC_U,))

        assert verdict.state == GROUP_UNKNOWN
        assert verdict.unchecked == (REGION_NTSC_U,)

    def test_a_region_the_group_says_nothing_about_is_unknown_never_missing(self):
        group = FirmwareGroup(emulator=_BEETLE, options=(FirmwareOption("scph5501.bin", (REGION_NTSC_U,), True),))

        verdict = judge_group_for_game(group, (REGION_PAL,))
        assert verdict.state == GROUP_UNKNOWN
        assert verdict.missing == ()

    def test_judging_with_no_game_regions_is_the_platforms_verdict(self):
        assert judge_groups((_beetle(ntsc_u=True),)) == (judge_group(_beetle(ntsc_u=True)),)
        assert judge_groups((_beetle(ntsc_u=True),), (REGION_NTSC_U,))[0].state == GROUP_MET


class TestConsoleRegionsOf:
    @pytest.mark.parametrize(
        ("rom_regions", "console"),
        [
            (["USA"], (REGION_NTSC_U,)),
            (["Canada"], (REGION_NTSC_U,)),
            (["Japan"], (REGION_NTSC_J,)),
            (["Europe"], (REGION_PAL,)),
            (["Australia"], (REGION_PAL,)),
            (["UK"], (REGION_PAL,)),
            (["Germany"], (REGION_PAL,)),
            (["France"], (REGION_PAL,)),
            (["Spain"], (REGION_PAL,)),
            (["Italy"], (REGION_PAL,)),
            (["Netherlands"], (REGION_PAL,)),
            (["Sweden"], (REGION_PAL,)),
        ],
    )
    def test_romms_region_names_map_onto_console_regions(self, rom_regions, console):
        assert console_regions_of(rom_regions) == console

    @pytest.mark.parametrize("rom_regions", [["World"], ["Asia"], ["Korea"], ["Brazil"], ["Unknown"], [], [""]])
    def test_a_region_with_no_console_of_its_own_maps_to_nothing(self, rom_regions):
        assert console_regions_of(rom_regions) == ()

    def test_the_names_are_read_whatever_their_case(self):
        assert console_regions_of(["usa", "JAPAN", " Europe "]) == (REGION_NTSC_U, REGION_NTSC_J, REGION_PAL)

    def test_several_names_of_one_console_map_onto_it_once(self):
        assert console_regions_of(["Germany", "France", "World", "USA"]) == (REGION_PAL, REGION_NTSC_U)


def _placement(file_name: str, *wants: FirmwareWant) -> FirmwarePlacement:
    return FirmwarePlacement(file_name=file_name, relative_path=file_name, description=file_name, wants=wants)


class TestFetchedAsRequired:
    """One rule for the count on "Download required" and for what the download fetches."""

    def test_a_file_the_emulator_requires_is_fetched(self):
        placement = _placement("gba_bios.bin", FirmwareWant("mgba_libretro.so", required=True))

        assert fetched_as_required(placement, "mgba_libretro.so", ()) is True

    def test_an_option_of_an_uncovered_region_is_fetched(self):
        placement = _placement("scph5500.bin", FirmwareWant(_BEETLE, required=False))

        assert fetched_as_required(placement, _BEETLE, (_beetle(ntsc_u=True),)) is True

    def test_the_option_already_covering_its_region_is_not(self):
        placement = _placement("scph5501.bin", FirmwareWant(_BEETLE, required=False))

        assert fetched_as_required(placement, _BEETLE, (_beetle(ntsc_u=True),)) is False

    def test_nothing_of_a_covered_group_is_fetched(self):
        placement = _placement("scph5500.bin", FirmwareWant(_BEETLE, required=False))

        assert fetched_as_required(placement, _BEETLE, (_beetle(ntsc_j=True, ntsc_u=True, pal=True),)) is False

    def test_another_emulators_requirement_is_not_this_launchs(self):
        placement = _placement("scph5501.bin", FirmwareWant("DUCKSTATION", required=True))

        assert fetched_as_required(placement, _BEETLE, ()) is False

    def test_an_unidentified_pick_falls_back_to_any_emulator_requiring_it(self):
        placement = _placement("scph5501.bin", FirmwareWant("DUCKSTATION", required=True))

        assert fetched_as_required(placement, None, ()) is True

    def test_a_file_nothing_declares_is_never_fetched(self):
        assert fetched_as_required(None, _BEETLE, (_beetle(),)) is False
