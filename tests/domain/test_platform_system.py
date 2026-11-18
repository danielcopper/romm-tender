"""Tests for domain/platform_system.py — which system a RomM platform is, decided from the resolver's answers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _romm_platforms import LISTED_GAMECUBE

from domain.emulator_sources import ALL_SOURCES_SWITCHED_OFF, CATALOGUE_UNAVAILABLE, NO_SOURCE_DETECTED, ArrangedSource
from domain.platform_system import (
    CONTRADICTED,
    FOUND,
    NO_SYSTEM,
    NOT_HERE,
    SEVERAL,
    SWITCHED_OFF,
    UNASKED,
    UNMATCHED,
    KeptPlatform,
    PlatformKeys,
    PlatformRefusal,
    PlatformState,
    PlatformSystem,
    SystemPair,
    SystemStatus,
    decode_platform_ids,
    encode_platform_ids,
    kept_by_slug,
    kept_entry,
    kept_folders_of_slug,
    kept_platform_of,
    kept_platforms,
    matched_state,
    system_key,
)
from domain.retrodeck_folders import GAME_DOWNLOAD, EveryFolderRefused, FolderRefused

RETRODECK = ArrangedSource(kind="retrodeck", enabled=True, starts_games=True)
_RETRODECK_KIND = {"kind": "retrodeck", "starts_games": True}


class TestTheKeptValue:
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

    def test_a_display_name_without_a_name_does_not_name_the_kept_record(self):
        """#2564 D2: RomM's display name never names a platform."""
        kept = kept_platform_of({k: v for k, v in LISTED_GAMECUBE.items() if k != "name"}, "5.4.1")

        assert kept.name is None

    def test_a_slug_that_is_only_whitespace_is_kept_as_no_slug(self):
        """#2761 D7."""
        assert kept_platform_of({**LISTED_GAMECUBE, "slug": "  "}, "5.4.1").slug is None

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

    def test_a_record_kept_before_the_folder_was_reads_as_the_slug_it_was_kept_under(self):
        raw = '{"snes": {"igdb_id": 19, "libretro_slug": null, "ss_id": null, "tgdb_id": 6, "name": "Super Nintendo"}}'

        assert decode_platform_ids(raw) == {
            "snes": KeptPlatform(
                slug="snes",
                name="Super Nintendo",
                ids={"igdb_id": 19, "libretro_slug": None, "ss_id": None, "tgdb_id": 6},
            )
        }


class TestPlatformKeys:
    """#2217 D16: a platform is asked with the folder the caller names — the game's, or the page's."""

    def test_a_game_is_asked_with_the_folder_rom_m_puts_it_in(self):
        detail = {"platform_slug": "genesis", "platform_fs_slug": "megadrivejp"}

        assert PlatformKeys.of_detail(detail) == PlatformKeys("genesis", "megadrivejp")

    def test_it_is_kept_and_named_under_its_folder_else_its_slug(self):
        assert (PlatformKeys("genesis", "megadrivejp").kept_as, PlatformKeys("genesis").kept_as) == (
            "megadrivejp",
            "genesis",
        )

    def test_the_record_of_its_folder_is_what_is_kept_for_it_never_another_of_its_slug(self):
        kept = {
            "megadrive": KeptPlatform(slug="genesis", fs_slug="megadrive"),
            "megadrivejp": KeptPlatform(slug="genesis", fs_slug="megadrivejp"),
        }

        assert kept_entry(kept, PlatformKeys("genesis", "megadrive")) == kept["megadrive"]
        assert kept_entry(kept, PlatformKeys("genesis", "sega32")) is None
        assert kept_entry(kept, PlatformKeys("genesis")) == kept["megadrivejp"]

    @pytest.mark.parametrize("folder", [None, ""])
    def test_without_a_folder_it_is_asked_with_its_slug_alone(self, folder):
        assert PlatformKeys.of("snes", folder) == PlatformKeys("snes")


_SNES = SystemPair("esde", "snes")
_XBOX360 = SystemPair("esde", "xbox360")
_SNESNA = SystemPair("esde", "snesna")
_MARK3 = SystemPair("esde", "mark3")
_MASTERSYSTEM = SystemPair("esde", "mastersystem")
_SMS_PAIRS = (SystemPair("romm", "sms"), SystemPair("folder", "sms"))


class TestMatchedState:
    """#2751, #2225 D8: what the source's matches come to; Tender picks none of several."""

    def test_one_switched_on_system_is_found(self):
        assert matched_state([(_SNES, SystemStatus.ON)]) == (FOUND, (_SNES,))

    def test_several_switched_on_systems_are_several_and_none_is_picked(self):
        matches = [(_MARK3, SystemStatus.ON), (_MASTERSYSTEM, SystemStatus.ON)]

        assert matched_state(matches) == (SEVERAL, (_MARK3, _MASTERSYSTEM))

    def test_a_switched_off_or_absent_system_beside_a_switched_on_one_changes_nothing(self):
        matches = [(_SNES, SystemStatus.ON), (_SNESNA, SystemStatus.OFF), (_MARK3, SystemStatus.NOT_HERE)]

        assert matched_state(matches) == (FOUND, (_SNES,))

    def test_with_none_switched_on_every_switched_off_one_is_named(self):
        matches = [(_SNES, SystemStatus.OFF), (_SNESNA, SystemStatus.OFF), (_MARK3, SystemStatus.NOT_HERE)]

        assert matched_state(matches) == (SWITCHED_OFF, (_SNES, _SNESNA))

    def test_with_only_systems_this_machine_has_not_set_up_they_are_not_here(self):
        assert matched_state([(_MARK3, SystemStatus.NOT_HERE)]) == (NOT_HERE, (_MARK3,))

    def test_no_match_at_all_is_no_system(self):
        assert matched_state([]) == (NO_SYSTEM, ())


class TestSystemKey:
    def test_a_recorded_name_is_its_own_key(self):
        assert system_key("snes") == "snes"

    def test_a_pair_is_named_by_its_key(self):
        assert system_key(_SNES) == "snes"

    def test_several_pairs_name_no_one_key(self):
        assert system_key(_SMS_PAIRS) is None


def _platform(
    state: PlatformState,
    *,
    named: tuple[SystemPair, ...] = (),
    asked: tuple[SystemPair, ...] = (),
    recorded: str | None = None,
    unasked: str | None = None,
    source: ArrangedSource | None = RETRODECK,
) -> PlatformSystem:
    return PlatformSystem(
        state,
        PlatformKeys("vic-20"),
        "Commodore VIC-20",
        source=source,
        named=named,
        asked=asked,
        recorded=recorded,
        unasked=unasked,
    )


class TestPlatformSystem:
    def test_only_a_found_system_is_taken(self):
        assert _platform(FOUND, recorded="c64").taken == "c64"
        assert _platform(SWITCHED_OFF, named=(_XBOX360,)).taken is None
        assert _platform(NO_SYSTEM).taken is None

    def test_a_found_system_is_taken_as_the_answer_named_it(self):
        """#2217 D16: a later question names the system as the source's answer named it."""
        found = _platform(FOUND, named=(_SNES,))

        assert (found.taken, found.one_system) == (_SNES, _SNES)

    def test_several_systems_are_asked_with_the_platforms_pairs_and_land_nowhere(self):
        """#2225 D8: questions go to the resolver with the pairs, so it answers alike or not at all; no folder."""
        several = _platform(SEVERAL, named=(_MARK3, _MASTERSYSTEM), asked=_SMS_PAIRS)

        assert (several.taken, several.one_system) == (_SMS_PAIRS, None)

    @pytest.mark.parametrize("state", [NO_SYSTEM, UNMATCHED, NOT_HERE, SWITCHED_OFF, UNASKED])
    def test_a_platform_with_no_system_to_use_takes_none(self, state):
        assert _platform(state, named=(_SNES,)).taken is None

    def test_a_download_lands_under_the_one_system_found_as_named_and_as_recorded(self):
        """The one rule downloads and adoption share."""
        assert _platform(FOUND, named=(_SNES,)).download_system(GAME_DOWNLOAD) == (_SNES, "snes")
        assert _platform(FOUND, recorded="c64").download_system(GAME_DOWNLOAD) == ("c64", "c64")

    def test_a_download_of_several_systems_is_the_platforms_refusal(self):
        several = _platform(SEVERAL, named=(_MARK3, _MASTERSYSTEM), asked=_SMS_PAIRS)

        refusal = several.download_system(GAME_DOWNLOAD)

        assert isinstance(refusal, FolderRefused)
        assert refusal.reason == "platform_systems_several"

    @pytest.mark.parametrize("state", [SWITCHED_OFF, NOT_HERE])
    def test_every_system_a_platform_without_one_to_use_names_is_asked_on_its_own(self, state):
        """Never the pairs it was asked with, whatever the count."""
        platform = _platform(state, named=(_SNES, _SNESNA), asked=(SystemPair("romm", "snes"),))

        assert platform.wanting_systems() == (_SNES, _SNESNA)
        assert platform.taken is None

    def test_a_platform_with_no_system_wants_nothing(self):
        assert _platform(NO_SYSTEM).wanting_systems() == ()

    def test_no_system_refuses_with_the_facts_the_sentence_is_worded_from(self):
        refusal = _platform(NO_SYSTEM).refusal(GAME_DOWNLOAD)

        assert isinstance(refusal, FolderRefused)
        assert refusal.reason == "no_platform_system"
        assert refusal.details == {
            "source": "retrodeck",
            "platform": "Commodore VIC-20",
            "systems": [],
            "slug": "vic-20",
            "folder": None,
        }

    def test_a_switched_off_system_refuses_naming_it(self):
        refusal = _platform(SWITCHED_OFF, named=(_XBOX360,)).refusal(GAME_DOWNLOAD)

        assert refusal.reason == "platform_system_off"
        assert refusal.details == {
            "source": "retrodeck",
            "platform": "Commodore VIC-20",
            "systems": ["xbox360"],
            "slug": "vic-20",
            "folder": None,
        }

    @pytest.mark.parametrize(
        ("state", "named", "reason", "systems"),
        [
            (UNMATCHED, (), "platform_unmatched", []),
            (NOT_HERE, (_MARK3,), "platform_system_not_here", ["mark3"]),
            (SEVERAL, (_MARK3, _MASTERSYSTEM), "platform_systems_several", ["mark3", "mastersystem"]),
            (SWITCHED_OFF, (_SNES, _SNESNA), "platform_system_off", ["snes", "snesna"]),
            (CONTRADICTED, (), "platform_pairs_contradict", []),
        ],
    )
    def test_each_case_with_no_system_to_use_refuses_with_its_own_reason(self, state, named, reason, systems):
        """#2751 D2: each case of a platform with no system says which it is."""
        refusal = _platform(state, named=named).refusal(GAME_DOWNLOAD)

        assert refusal.reason == reason
        assert refusal.details == {
            "source": "retrodeck",
            "platform": "Commodore VIC-20",
            "systems": systems,
            "slug": "vic-20",
            "folder": None,
        }

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
            UNASKED,
            PlatformKeys("vic-20"),
            "VIC-20",
            source=RETRODECK,
            unasked="server_unreachable",
            unasked_message="offline",
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
        found = _platform(FOUND, recorded="c64")
        with pytest.raises(ValueError, match="not refused"):
            found.refusal(GAME_DOWNLOAD)

    @pytest.mark.parametrize(
        ("platform", "reason", "source"),
        [
            (_platform(NO_SYSTEM), "no_platform_system", {"kind": "retrodeck", "starts_games": True}),
            (
                _platform(SWITCHED_OFF, named=(_XBOX360,)),
                "platform_system_off",
                {"kind": "retrodeck", "starts_games": True},
            ),
            (_platform(UNMATCHED), "platform_unmatched", {"kind": "retrodeck", "starts_games": True}),
            (_platform(NOT_HERE), "platform_system_not_here", {"kind": "retrodeck", "starts_games": True}),
            (_platform(SEVERAL, named=(_MARK3, _MASTERSYSTEM)), "platform_systems_several", _RETRODECK_KIND),
            (_platform(UNASKED, unasked=NO_SOURCE_DETECTED, source=None), "no_source", None),
        ],
    )
    def test_the_emulator_list_is_unavailable_for_why(self, platform, reason, source):
        assert platform.unavailable_options() == {"available": False, "options": [], "reason": reason, "source": source}

    def test_the_pages_read_the_state_the_source_the_system_and_the_name(self):
        assert _platform(SWITCHED_OFF, named=(_XBOX360,)).payload() == {
            "state": "switched_off",
            "source": "retrodeck",
            "systems": ["xbox360"],
            "slug": "vic-20",
            "folder": None,
            "platform": "Commodore VIC-20",
        }

    def test_a_contradiction_names_rom_ms_slug_and_folder(self):
        """#2751 D2 (changed): the sentence names both, so the user can rename the folder."""
        contradicted = PlatformSystem(CONTRADICTED, PlatformKeys("snes", "xbox360"), "SNES", source=RETRODECK)

        assert contradicted.refusal(GAME_DOWNLOAD).details["folder"] == "xbox360"
        assert (contradicted.payload() or {})["slug"] == "snes"
        assert (contradicted.payload() or {})["folder"] == "xbox360"
        assert contradicted.unavailable_options()["reason"] == "platform_pairs_contradict"

    def test_the_pages_read_every_system_of_several_and_none_as_the_one(self):
        """#2225 D8: the page says why a platform of several systems gets no link, naming them."""
        assert _platform(SEVERAL, named=(_MARK3, _MASTERSYSTEM), asked=_SMS_PAIRS).payload() == {
            "state": "several",
            "source": "retrodeck",
            "systems": ["mark3", "mastersystem"],
            "slug": "vic-20",
            "folder": None,
            "platform": "Commodore VIC-20",
        }

    def test_with_no_source_asked_the_pages_read_nothing(self):
        assert _platform(UNASKED, unasked=NO_SOURCE_DETECTED, source=None).payload() is None
        assert _platform(FOUND, recorded="c64", source=None).payload() is None


class TestKeptFoldersOfSlug:
    def test_every_kept_platform_of_the_slug_by_its_folder(self):
        kept = {
            "gc": KeptPlatform(slug="gamecube", fs_slug="gc"),
            "ngc": KeptPlatform(slug="gamecube", fs_slug="ngc"),
            "ps": KeptPlatform(slug="psx", fs_slug="ps"),
            "gamecube": KeptPlatform(slug="gamecube"),
        }

        assert kept_folders_of_slug(encode_platform_ids(kept), "gamecube") == frozenset({"gc", "ngc"})

    def test_nothing_kept_is_no_folder(self):
        assert kept_folders_of_slug(None, "gamecube") == frozenset()


class TestThePanelWordsEveryPlatformRefusal:
    """The panel's ``PlatformSystemReason`` is ``PlatformRefusal``, no more and no less."""

    _TYPES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "emulatorSources.ts"

    def test_the_two_lists_are_equal(self):
        source = self._TYPES.read_text(encoding="utf-8")
        start = source.index("export type PlatformSystemReason =")
        panel = set(re.findall(r'"(\w+)"', source[start : source.index(";", start)]))
        assert panel == {refusal.value for refusal in PlatformRefusal}
