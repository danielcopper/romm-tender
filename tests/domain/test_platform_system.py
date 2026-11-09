"""Tests for domain/platform_system.py — which system a RomM platform is, decided from the resolver's answers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from _romm_platforms import LISTED_GAMECUBE

from domain.emulator_sources import ALL_SOURCES_SWITCHED_OFF, CATALOGUE_UNAVAILABLE, NO_SOURCE_DETECTED, ArrangedSource
from domain.platform_system import (
    FOUND,
    NO_SYSTEM,
    SWITCHED_OFF,
    UNASKED,
    IdAnswer,
    KeptPlatform,
    PlatformIds,
    PlatformSystem,
    SystemMatch,
    SystemPick,
    SystemStatus,
    decode_platform_ids,
    encode_platform_ids,
    kept_by_slug,
    kept_platform_of,
    kept_platforms,
    pick_system,
    platform_ids_of,
)
from domain.retrodeck_folders import GAME_DOWNLOAD, EveryFolderRefused, FolderRefused

if TYPE_CHECKING:
    from collections.abc import Iterator

RETRODECK = ArrangedSource(kind="retrodeck", enabled=True, starts_games=True)


def _on(system: str, *tags: str) -> SystemMatch:
    return SystemMatch(system=system, status=SystemStatus.ON, tags=tags or (system,))


def _off(system: str, *tags: str) -> SystemMatch:
    return SystemMatch(system=system, status=SystemStatus.OFF, tags=tags or (system,))


def _absent(system: str, *tags: str) -> SystemMatch:
    return SystemMatch(system=system, status=SystemStatus.NOT_HERE, tags=tags or (system,))


def _answer(*matches: SystemMatch, platforms: tuple[str, ...] = (), systems: tuple[str, ...] = ()) -> IdAnswer:
    return IdAnswer(platforms=platforms, systems=systems, matches=matches)


_UNMAPPED = _answer()


class TestPlatformIds:
    def test_the_ids_are_asked_in_the_decided_order(self):
        ids = PlatformIds(igdb_id=19, libretro_slug="Nintendo - SNES", ss_id=4, tgdb_id=6)

        assert ids.questions() == (
            ("igdb_id", "19"),
            ("libretro_slug", "Nintendo - SNES"),
            ("ss_id", "4"),
            ("tgdb_id", "6"),
        )

    def test_an_id_rom_m_does_not_hold_is_not_asked(self):
        assert PlatformIds(ss_id=57).questions() == (("ss_id", "57"),)
        assert PlatformIds().questions() == ()

    def test_rom_ms_platform_is_read_for_the_four_ids_and_nothing_else(self):
        platform = {
            "slug": "snes",
            "igdb_id": 19,
            "libretro_slug": "Nintendo - Super Nintendo Entertainment System",
            "ss_id": "4",
            "tgdb_id": 6,
            "moby_id": 15,
            "ra_id": 3,
        }

        assert platform_ids_of(platform) == PlatformIds(
            igdb_id=19, libretro_slug="Nintendo - Super Nintendo Entertainment System", ss_id=4, tgdb_id=6
        )

    def test_rom_ms_display_name_is_kept_beside_the_ids_and_never_asked(self):
        ids = platform_ids_of({"name": "Super Nintendo", "igdb_id": 19})

        assert ids.name == "Super Nintendo"
        assert ids.questions() == (("igdb_id", "19"),)
        assert platform_ids_of({"display_name": "SNES"}).name == "SNES"

    @pytest.mark.parametrize("value", [None, True, "", "abc", "-4", 1.5, [19]])
    def test_a_number_of_the_wrong_kind_counts_as_none(self, value):
        assert platform_ids_of({"igdb_id": value, "ss_id": value, "tgdb_id": value}) == PlatformIds()

    @pytest.mark.parametrize("value", [None, True, "", "  ", 4, [19]])
    def test_a_name_of_the_wrong_kind_counts_as_none(self, value):
        assert platform_ids_of({"libretro_slug": value}) == PlatformIds()

    def test_the_kept_value_reads_back_as_it_was_kept(self):
        kept = {
            "sfc": KeptPlatform(
                slug="snes",
                fs_slug="sfc",
                platform_id=3,
                name="Super Nintendo",
                is_identified=True,
                romm_version="5.4.1",
                ids={"igdb_id": 19, "tgdb_id": 6, "moby_slug": None},
            ),
            "psx": KeptPlatform(slug="psx", ids={"libretro_slug": "Sony - PlayStation"}),
        }

        assert decode_platform_ids(encode_platform_ids(kept)) == kept

    @pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", '"snes"'])
    def test_nothing_usable_kept_is_none_kept(self, raw):
        assert decode_platform_ids(raw) is None

    def test_an_entry_that_is_not_an_object_is_dropped(self):
        assert decode_platform_ids('{"snes": {"igdb_id": 19}, "psx": 7}') == {
            "snes": KeptPlatform(slug="snes", ids={"igdb_id": 19})
        }


class TestKeptPlatform:
    def test_a_listed_platform_is_kept_with_its_named_fields_and_every_identifier_as_sent(self):
        assert kept_platforms([LISTED_GAMECUBE], "5.4.1") == {
            "gc": KeptPlatform(
                slug="ngc",
                fs_slug="gc",
                platform_id=12,
                name="Nintendo GameCube",
                is_identified=True,
                romm_version="5.4.1",
                ids={
                    "fs_slug": "gc",
                    "igdb_slug": "ngc",
                    "moby_slug": "gamecube",
                    "hltb_slug": None,
                    "libretro_slug": "Nintendo - GameCube",
                    "igdb_id": 21,
                    "sgdb_id": None,
                    "moby_id": 14,
                    "launchbox_id": 30,
                    "ss_id": 13,
                    "ra_id": 16,
                    "hasheous_id": None,
                    "tgdb_id": 2,
                    "flashpoint_id": None,
                    "family_slug": "nintendo",
                },
            )
        }

    def test_an_identifier_rom_m_adds_later_is_kept_with_no_change(self):
        kept = kept_platform_of({**LISTED_GAMECUBE, "newdb_id": 77, "newdb_slug": "gcn"}, "5.5.0")

        assert (kept.ids["newdb_id"], kept.ids["newdb_slug"]) == (77, "gcn")

    def test_a_listed_platform_without_a_folder_is_left_out(self):
        assert kept_platforms([{**LISTED_GAMECUBE, "fs_slug": None}, {"slug": "psx", "fs_slug": "psx"}], None) == {
            "psx": KeptPlatform(slug="psx", fs_slug="psx", ids={"fs_slug": "psx"})
        }

    def test_two_folders_that_share_a_slug_are_both_kept(self):
        kept = kept_platforms([LISTED_GAMECUBE, {**LISTED_GAMECUBE, "id": 13, "fs_slug": "gamecube"}], "5.4.1")

        assert list(kept) == ["gc", "gamecube"]

    def test_of_folders_that_share_a_slug_the_last_kept_answers_it(self):
        kept = kept_platforms([LISTED_GAMECUBE, {**LISTED_GAMECUBE, "id": 13, "fs_slug": "gamecube"}], "5.4.1")

        found = kept_by_slug(kept, "ngc")

        assert found is not None
        assert found.fs_slug == "gamecube"
        assert kept_by_slug(kept, "gc") is None

    def test_the_question_is_asked_with_the_four_ids_and_the_name_alone(self):
        kept = kept_platform_of(LISTED_GAMECUBE, "5.4.1")

        assert kept.question_ids() == PlatformIds(
            igdb_id=21, libretro_slug="Nintendo - GameCube", ss_id=13, tgdb_id=2, name="Nintendo GameCube"
        )

    def test_a_record_kept_before_the_folder_was_reads_as_the_slug_it_was_kept_under(self):
        raw = '{"snes": {"igdb_id": 19, "libretro_slug": null, "ss_id": null, "tgdb_id": 6, "name": "Super Nintendo"}}'

        assert decode_platform_ids(raw) == {
            "snes": KeptPlatform(
                slug="snes",
                name="Super Nintendo",
                ids={"igdb_id": 19, "libretro_slug": None, "ss_id": None, "tgdb_id": 6},
            )
        }

    def test_a_record_kept_before_asks_the_question_it_asked_before(self):
        before = {"igdb_id": 19, "libretro_slug": None, "ss_id": 4, "tgdb_id": 6, "name": "Super Nintendo"}
        [kept] = (decode_platform_ids(json.dumps({"snes": before})) or {}).values()

        assert kept.question_ids() == platform_ids_of(before)


class TestOneMatch:
    def test_one_switched_on_system_is_taken_as_it_is(self):
        assert pick_system([_answer(_on("psx"), platforms=("psx",))], "ps") == SystemPick(FOUND, "psx")

    def test_an_id_that_stands_for_one_system_alone_gives_that_system(self):
        # ScreenScraper 6 is the CPS1 board, which no platform row can carry.
        answer = _answer(_on("cps1", "arcade"), systems=("cps1",))

        assert pick_system([answer], "cps1") == SystemPick(FOUND, "cps1")


class TestSeveralMatches:
    def test_the_system_named_like_the_platform_is_taken_not_a_regional_one(self):
        answer = _answer(_on("sfc", "snes"), _on("snes"), _on("snesna", "snes"), platforms=("snes",))

        pick = pick_system([answer], "snes")

        assert (pick.state, pick.system) == (FOUND, "snes")

    def test_without_a_system_named_like_the_platform_the_first_in_the_resolvers_order(self):
        answer = _answer(_on("mark3", "mastersystem"), _on("sms", "mastersystem"), platforms=("mastersystem",))

        assert pick_system([answer], "sms").system == "mark3"

    def test_a_switched_off_system_is_never_taken_beside_a_switched_on_one(self):
        answer = _answer(_on("sfc", "snes"), _off("snes"), platforms=("snes",))

        assert pick_system([answer], "snes").system == "sfc"


class TestTheIdsInTurn:
    def test_the_first_id_with_a_switched_on_system_decides(self):
        answers = [_UNMAPPED, _answer(_on("atari800"), platforms=("atari800",))]

        assert pick_system(answers, "atari8bit").system == "atari800"

    def test_an_id_after_the_deciding_one_is_never_asked(self):
        asked: list[int] = []

        def answers() -> Iterator[IdAnswer]:
            for index, answer in enumerate(
                [_answer(_on("psx"), platforms=("psx",)), _answer(_on("other"), platforms=("other",))]
            ):
                asked.append(index)
                yield answer

        pick_system(answers(), "ps")

        assert asked == [0]

    def test_an_id_giving_only_a_switched_off_system_does_not_decide(self):
        answers = [_answer(_off("xbox360"), platforms=("xbox360",)), _answer(_on("xbox"), platforms=("xbox",))]

        assert pick_system(answers, "xbox360").system == "xbox"

    def test_an_id_giving_only_absent_systems_matches_nothing(self):
        answers = [_answer(_absent("n3ds"), platforms=("n3ds",)), _answer(_on("3ds"), platforms=("3ds",))]

        assert pick_system(answers, "3ds").system == "3ds"


class TestAnAnswerNamingSeveralPlatforms:
    """The three shapes measured on the resolver's crosswalk (D10)."""

    @pytest.mark.parametrize(
        ("platforms", "matches", "romm_slug", "taken"),
        [
            (("famicom", "nes"), (_on("famicom"), _on("nes")), "nes", "nes"),
            (
                ("satellaview", "snes", "sufami"),
                (_on("satellaview"), _on("sfc", "snes"), _on("snes"), _on("sufami")),
                "snes",
                "snes",
            ),
            (("amiga", "amigacd32"), (_on("amiga"), _on("amigacd32")), "amiga-cd32", "amigacd32"),
        ],
        ids=["libretro-nes", "thegamesdb-snes", "libretro-amiga"],
    )
    def test_the_platform_equal_to_rom_ms_slug_without_hyphens_is_taken(self, platforms, matches, romm_slug, taken):
        pick = pick_system([_answer(*matches, platforms=platforms)], romm_slug)

        assert (pick.state, pick.system) == (FOUND, taken)

    def test_where_none_equals_the_slug_the_next_id_is_asked(self):
        several = _answer(_on("famicom"), _on("nes"), platforms=("famicom", "nes"))
        one = _answer(_on("nes"), platforms=("nes",))

        assert pick_system([several, one], "nintendo-entertainment-system").system == "nes"

    def test_another_platform_is_never_taken_in_its_place(self):
        # The slug's platform names no system here, and the answer's other
        # platforms are passed over rather than taken instead.
        several = _answer(_on("famicom"), _on("satellaview"), platforms=("famicom", "satellaview"))

        assert pick_system([several], "nintendo-entertainment-system") == SystemPick(NO_SYSTEM)

    def test_a_switched_off_slug_platform_is_named_beside_a_switched_on_other(self):
        answer = _answer(_off("satellaview"), _on("snes"), _on("sufami"), platforms=("satellaview", "snes", "sufami"))

        assert pick_system([answer], "satellaview") == SystemPick(SWITCHED_OFF, "satellaview")

    def test_a_slug_platform_with_no_system_here_has_no_system(self):
        answer = _answer(_on("amiga"), platforms=("amiga", "amigacd32"))

        assert pick_system([answer], "amiga-cd32") == SystemPick(NO_SYSTEM)

    def test_rom_ms_slug_is_never_a_system_of_its_own(self):
        # The slug's platform is there with its only system switched off: that
        # one is named, and famicom, switched on, is not taken instead.
        answer = _answer(_on("famicom"), _off("nes"), platforms=("famicom", "nes"))

        assert pick_system([answer], "nes") == SystemPick(SWITCHED_OFF, "nes")

    def test_a_later_id_naming_the_slug_platform_still_decides(self):
        several = _answer(_on("famicom"), platforms=("famicom", "nes"))
        later = _answer(_on("nes"), platforms=("famicom", "nes"))

        assert pick_system([several, later], "nes") == SystemPick(FOUND, "nes")


class TestNothingSwitchedOn:
    def test_a_system_only_switched_off_is_named(self):
        pick = pick_system([_answer(_off("xbox360"), platforms=("xbox360",))], "xbox360")

        assert (pick.state, pick.system) == (SWITCHED_OFF, "xbox360")

    def test_the_first_switched_off_system_is_named(self):
        answers = [_answer(_off("atarijaguarcd"), platforms=("atarijaguarcd",)), _answer(_off("jaguar"))]

        assert pick_system(answers, "jaguar-cd").system == "atarijaguarcd"

    @pytest.mark.parametrize(
        "answers",
        [[], [_UNMAPPED], [_answer(_absent("n3ds"), platforms=("n3ds",))], [_answer(platforms=("vic20",))]],
        ids=["no-ids", "unmapped", "only-absent", "real-platform-nothing-here"],
    )
    def test_no_system_at_all_is_no_system(self, answers):
        assert pick_system(answers, "vic-20") == SystemPick(NO_SYSTEM)


def _platform(
    state: str, *, system: str | None = None, unasked: str | None = None, source: ArrangedSource | None = RETRODECK
) -> PlatformSystem:
    return PlatformSystem(state, "vic-20", "Commodore VIC-20", system=system, source=source, unasked=unasked)


class TestPlatformSystem:
    def test_only_a_found_system_is_taken(self):
        assert _platform(FOUND, system="c64").taken == "c64"
        assert _platform(SWITCHED_OFF, system="xbox360").taken is None
        assert _platform(NO_SYSTEM).taken is None

    def test_no_system_refuses_with_the_facts_the_sentence_is_worded_from(self):
        refusal = _platform(NO_SYSTEM).refusal(GAME_DOWNLOAD)

        assert isinstance(refusal, FolderRefused)
        assert refusal.reason == "no_platform_system"
        assert refusal.details == {"source": "retrodeck", "platform": "Commodore VIC-20", "system": None}

    def test_a_switched_off_system_refuses_naming_it(self):
        refusal = _platform(SWITCHED_OFF, system="xbox360").refusal(GAME_DOWNLOAD)

        assert refusal.reason == "platform_system_off"
        assert refusal.details == {"source": "retrodeck", "platform": "Commodore VIC-20", "system": "xbox360"}

    @pytest.mark.parametrize(
        ("unasked", "reason"),
        [
            (NO_SOURCE_DETECTED, "retrodeck_not_installed"),
            (ALL_SOURCES_SWITCHED_OFF, "retrodeck_switched_off"),
            (CATALOGUE_UNAVAILABLE, "retrodeck_unanswered"),
        ],
    )
    def test_with_no_source_asked_the_folders_refusal_stands(self, unasked, reason):
        refusal = _platform(UNASKED, unasked=unasked, source=None).refusal(GAME_DOWNLOAD)

        assert refusal.reason == reason

    def test_a_rom_m_that_could_not_be_read_refuses_with_its_reason_and_message(self):
        platform = PlatformSystem(
            UNASKED, "vic-20", "VIC-20", source=RETRODECK, unasked="server_unreachable", unasked_message="offline"
        )

        refusal = platform.refusal(GAME_DOWNLOAD)

        assert (refusal.reason, refusal.message) == ("server_unreachable", "offline")
        assert platform.unavailable_options() == {
            "available": False,
            "options": [],
            "reason": "server_unreachable",
            "source": {"kind": "retrodeck", "starts_games": True},
        }
        assert platform.payload() is None

    def test_a_question_that_raised_refuses_every_folder(self):
        refusal = _platform(UNASKED, unasked=CATALOGUE_UNAVAILABLE).refusal(GAME_DOWNLOAD)

        assert isinstance(refusal, EveryFolderRefused)

    def test_a_found_system_is_not_refused(self):
        found = _platform(FOUND, system="c64")
        with pytest.raises(ValueError, match="not refused"):
            found.refusal(GAME_DOWNLOAD)

    @pytest.mark.parametrize(
        ("platform", "reason", "source"),
        [
            (_platform(NO_SYSTEM), "no_platform_system", {"kind": "retrodeck", "starts_games": True}),
            (_platform(SWITCHED_OFF, system="x"), "platform_system_off", {"kind": "retrodeck", "starts_games": True}),
            (_platform(UNASKED, unasked=NO_SOURCE_DETECTED, source=None), "no_source", None),
        ],
    )
    def test_the_emulator_list_is_unavailable_for_why(self, platform, reason, source):
        assert platform.unavailable_options() == {"available": False, "options": [], "reason": reason, "source": source}

    def test_the_pages_read_the_state_the_source_the_system_and_the_name(self):
        assert _platform(SWITCHED_OFF, system="xbox360").payload() == {
            "state": "switched_off",
            "source": "retrodeck",
            "system": "xbox360",
            "platform": "Commodore VIC-20",
        }

    def test_with_no_source_asked_the_pages_read_nothing(self):
        assert _platform(UNASKED, unasked=NO_SOURCE_DETECTED, source=None).payload() is None
        assert _platform(FOUND, system="c64", source=None).payload() is None
