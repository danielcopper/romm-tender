"""Tests for domain.sgdb_artwork — SGDB asset-type and endpoint helpers."""

from __future__ import annotations

import pytest

from domain.sgdb_artwork import (
    SgdbCandidate,
    SgdbCandidatePage,
    asset_type_endpoint,
    asset_type_name,
    build_autocomplete_path,
    cached_artwork_name,
    cached_image_id,
    cached_slot_of,
    choose_candidate,
    classify_resolution,
    effective_sgdb_id,
    first_grid_url,
    is_cached_artwork,
    parse_autocomplete_results,
    parse_candidate_page,
    parse_game_name,
    sgdb_candidates_page_path,
    sgdb_endpoint_path,
    to_signed_app_id,
    to_unsigned_app_id,
)


class TestAssetTypeName:
    @pytest.mark.parametrize(
        ("type_num", "expected"),
        [
            (1, "hero"),
            (2, "logo"),
            (3, "grid"),
            (4, "icon"),
        ],
    )
    def test_known_codes(self, type_num, expected):
        assert asset_type_name(type_num) == expected

    def test_unknown_returns_none(self):
        assert asset_type_name(99) is None

    def test_zero_returns_none(self):
        assert asset_type_name(0) is None


class TestAssetTypeEndpoint:
    @pytest.mark.parametrize(
        ("name", "endpoint"),
        [
            ("hero", "heroes"),
            ("logo", "logos"),
            ("grid", "grids"),
            ("icon", "icons"),
            ("cover", "grids"),
        ],
    )
    def test_known_names(self, name, endpoint):
        assert asset_type_endpoint(name) == endpoint

    def test_unknown_returns_none(self):
        assert asset_type_endpoint("banner") is None

    def test_empty_returns_none(self):
        assert asset_type_endpoint("") is None


class TestSgdbEndpointPath:
    def test_hero_path(self):
        assert sgdb_endpoint_path("hero", 9999) == "/heroes/game/9999"

    def test_logo_path(self):
        assert sgdb_endpoint_path("logo", 42) == "/logos/game/42?types=static"

    def test_icon_path(self):
        assert sgdb_endpoint_path("icon", 1) == "/icons/game/1"

    def test_grid_path_appends_dimensions_query(self):
        assert sgdb_endpoint_path("grid", 7) == "/grids/game/7?dimensions=460x215,920x430&types=static"

    def test_unknown_asset_returns_none(self):
        assert sgdb_endpoint_path("banner", 1) is None

    def test_the_cover_is_asked_in_its_three_portrait_sizes(self):
        assert sgdb_endpoint_path("cover", 7) == "/grids/game/7?dimensions=600x900,660x930,342x482&types=static"


class TestToSignedAppId:
    def test_low_positive_unchanged(self):
        # Values below 2^31 round-trip as themselves.
        assert to_signed_app_id(100001) == 100001
        assert to_signed_app_id(0) == 0
        assert to_signed_app_id(1) == 1

    def test_high_bit_becomes_negative(self):
        # 3_000_000_000 has its high bit set in 32-bit space → negative as int32.
        # Confirmed in steamgrid tests: app_id 3000000000 → signed = -1294967296.
        assert to_signed_app_id(3000000000) == -1294967296

    def test_max_uint32_is_negative_one(self):
        assert to_signed_app_id(0xFFFFFFFF) == -1

    def test_2_pow_31_is_int32_min(self):
        assert to_signed_app_id(0x80000000) == -2147483648


class TestBuildAutocompletePath:
    def test_simple_term(self):
        assert build_autocomplete_path("zelda") == "/search/autocomplete/zelda"

    def test_spaces_are_encoded(self):
        assert build_autocomplete_path("super mario") == "/search/autocomplete/super%20mario"

    def test_reserved_chars_encoded(self):
        # quote() leaves '/' alone by default, but encodes other reserved chars.
        assert build_autocomplete_path("a&b?c") == "/search/autocomplete/a%26b%3Fc"

    def test_empty_term(self):
        assert build_autocomplete_path("") == "/search/autocomplete/"


class TestParseAutocompleteResults:
    def test_happy_path_with_release_year(self):
        # 1234567890 → 2009-02-13 UTC
        payload = {
            "success": True,
            "data": [
                {"id": 1, "name": "Zelda", "release_date": 1234567890},
                {"id": 2, "name": "Mario", "release_date": 0},
            ],
        }
        result = parse_autocomplete_results(payload)
        assert result == [
            {"id": 1, "name": "Zelda", "release_year": 2009},
            {"id": 2, "name": "Mario", "release_year": 1970},
        ]

    def test_missing_release_date_yields_none_year(self):
        payload = {"success": True, "data": [{"id": 5, "name": "Metroid"}]}
        assert parse_autocomplete_results(payload) == [{"id": 5, "name": "Metroid", "release_year": None}]

    def test_none_release_date_yields_none_year(self):
        payload = {"success": True, "data": [{"id": 5, "name": "Metroid", "release_date": None}]}
        assert parse_autocomplete_results(payload) == [{"id": 5, "name": "Metroid", "release_year": None}]

    def test_success_false_returns_empty(self):
        assert parse_autocomplete_results({"success": False, "data": [{"id": 1, "name": "x"}]}) == []

    def test_none_payload_returns_empty(self):
        assert parse_autocomplete_results(None) == []

    def test_empty_payload_returns_empty(self):
        assert parse_autocomplete_results({}) == []

    def test_non_list_data_returns_empty(self):
        assert parse_autocomplete_results({"success": True, "data": "nope"}) == []

    def test_malformed_entries_skipped(self):
        payload = {
            "success": True,
            "data": [
                {"id": 1, "name": "ok", "release_date": None},
                {"id": "bad", "name": "string id"},
                {"name": "no id"},
                {"id": 2},  # no name
                "not a dict",
                {"id": 3, "name": "also ok"},
            ],
        }
        result = parse_autocomplete_results(payload)
        assert result == [
            {"id": 1, "name": "ok", "release_year": None},
            {"id": 3, "name": "also ok", "release_year": None},
        ]

    def test_bool_release_date_ignored(self):
        # bool is a subclass of int — must not be treated as a timestamp.
        payload = {"success": True, "data": [{"id": 1, "name": "x", "release_date": True}]}
        assert parse_autocomplete_results(payload) == [{"id": 1, "name": "x", "release_year": None}]


class TestFirstGridUrl:
    def test_returns_thumb(self):
        payload = {"success": True, "data": [{"thumb": "t.png", "url": "u.png"}]}
        assert first_grid_url(payload) == "t.png"

    def test_falls_back_to_url_when_no_thumb(self):
        payload = {"success": True, "data": [{"url": "u.png"}]}
        assert first_grid_url(payload) == "u.png"

    def test_empty_thumb_falls_back_to_url(self):
        payload = {"success": True, "data": [{"thumb": "", "url": "u.png"}]}
        assert first_grid_url(payload) == "u.png"

    def test_empty_data_returns_none(self):
        assert first_grid_url({"success": True, "data": []}) is None

    def test_none_payload_returns_none(self):
        assert first_grid_url(None) is None

    def test_success_false_returns_none(self):
        assert first_grid_url({"success": False, "data": [{"thumb": "t.png"}]}) is None

    def test_first_entry_without_urls_returns_none(self):
        assert first_grid_url({"success": True, "data": [{"id": 1}]}) is None

    def test_non_dict_first_entry_returns_none(self):
        assert first_grid_url({"success": True, "data": ["nope"]}) is None


class TestClassifyResolution:
    def test_state_only_uses_state(self):
        assert classify_resolution(9999, None) == "use_state"

    def test_romm_only_uses_romm(self):
        assert classify_resolution(None, 7777) == "use_romm"

    def test_romm_wins_over_equal_state(self):
        assert classify_resolution(9999, 9999) == "use_romm"

    def test_romm_wins_over_differing_state(self):
        assert classify_resolution(9999, 7777) == "use_romm"

    def test_both_none_unresolved(self):
        assert classify_resolution(None, None) == "unresolved"


class TestEffectiveSgdbId:
    def test_romms_id_wins_over_the_users_choice(self):
        assert effective_sgdb_id(7777, 8888) == 7777

    def test_the_users_choice_stands_where_romm_gives_none(self):
        assert effective_sgdb_id(None, 8888) == 8888

    def test_neither_is_none(self):
        assert effective_sgdb_id(None, None) is None


class TestToUnsignedAppId:
    """The direction anything reading shortcuts.vdf needs."""

    def test_round_trips_a_shortcut_app_id(self):
        for app_id in (0x80000000, 0xC7654321, 0xFFFFFFFF):
            assert to_unsigned_app_id(to_signed_app_id(app_id)) == app_id

    def test_turns_the_signed_form_the_file_stores_back_into_steams_own_id(self):
        assert to_unsigned_app_id(-949288395) == 3345678901

    def test_leaves_an_id_that_was_never_negative_alone(self):
        assert to_unsigned_app_id(42) == 42


def _image(image_id: int, width: int, height: int) -> dict[str, object]:
    return {"id": image_id, "url": f"https://cdn.example/{image_id}.png", "width": width, "height": height}


def _chosen(asset_type: str, data: object) -> SgdbCandidate:
    candidate = choose_candidate(asset_type, data)
    assert candidate is not None
    return candidate


class TestOnlyTheWideGridAndTheLogoNarrowTheirQuery:
    @pytest.mark.parametrize("asset_type", ["hero", "icon"])
    def test_the_hero_and_the_icon_are_asked_as_before(self, asset_type):
        assert "?" not in (sgdb_endpoint_path(asset_type, 1) or "")


class TestChooseTheWideGrid:
    def test_920_by_430_wins_over_an_earlier_460_by_215(self):
        data = [_image(1, 460, 215), _image(2, 920, 430)]

        assert choose_candidate("grid", data) == SgdbCandidate(
            image_id=2, url="https://cdn.example/2.png", width=920, height=430
        )

    def test_460_by_215_wins_over_an_earlier_candidate_of_another_size(self):
        data = [_image(1, 600, 900), _image(2, 460, 215)]

        assert _chosen("grid", data).image_id == 2

    def test_without_either_size_the_first_candidate_is_taken(self):
        data = [_image(1, 1024, 1024), _image(2, 600, 900)]

        assert _chosen("grid", data).image_id == 1

    def test_among_two_920_by_430_the_first_is_taken(self):
        data = [_image(1, 460, 215), _image(2, 920, 430), _image(3, 920, 430)]

        assert _chosen("grid", data).image_id == 2


class TestChooseTheLogo:
    def test_the_first_candidate_from_640_to_2048_wide_is_taken(self):
        data = [_image(1, 300, 100), _image(2, 4000, 1000), _image(3, 800, 300), _image(4, 1000, 400)]

        assert _chosen("logo", data).image_id == 3

    @pytest.mark.parametrize("width", [640, 2048])
    def test_both_ends_of_the_band_count(self, width):
        data = [_image(1, 639, 100), _image(2, width, 100)]

        assert _chosen("logo", data).image_id == 2

    @pytest.mark.parametrize("width", [639, 2049])
    def test_just_outside_the_band_falls_back_to_the_first(self, width):
        data = [_image(1, 300, 100), _image(2, width, 100)]

        assert _chosen("logo", data).image_id == 1

    def test_a_candidate_with_no_stated_width_is_only_a_fallback(self):
        data = [{"id": 1, "url": "https://cdn.example/1.png"}, _image(2, 900, 300)]

        assert _chosen("logo", data).image_id == 2


class TestWhatCountsAsACandidate:
    def test_no_data_is_no_candidate(self):
        assert choose_candidate("grid", []) is None
        assert choose_candidate("grid", None) is None

    @pytest.mark.parametrize("asset_type", ["grid", "logo", "hero", "icon"])
    def test_an_image_without_an_id_is_no_candidate(self, asset_type):
        data = [{"url": "https://cdn.example/a.png", "width": 920, "height": 430}, _image(2, 460, 215)]

        assert _chosen(asset_type, data).image_id == 2

    def test_an_id_that_is_a_bool_is_no_id(self):
        assert choose_candidate("grid", [{"id": True, "url": "https://cdn.example/a.png"}]) is None

    def test_a_hero_without_an_id_is_no_candidate(self):
        assert choose_candidate("hero", [{"url": "https://cdn.example/hero.png"}]) is None

    def test_an_entry_without_a_url_is_skipped(self):
        data = [{"id": 1, "url": ""}, {"id": 2}, "junk", _image(3, 460, 215)]

        assert _chosen("grid", data).image_id == 3

    def test_the_hero_takes_the_first_candidate_whatever_its_size(self):
        data = [_image(1, 100, 100), _image(2, 1920, 620)]

        assert _chosen("hero", data).image_id == 1


class TestCachedArtworkNames:
    @pytest.mark.parametrize("asset_type", ["grid", "logo", "hero", "icon"])
    def test_each_image_is_named_by_its_image_id(self, asset_type):
        name = cached_artwork_name(42, asset_type, 12345)

        assert name == f"42_{asset_type}_12345.png"
        assert cached_image_id(name, 42, asset_type) == 12345

    def test_a_hero_named_without_an_id_carries_none(self):
        assert cached_image_id("42_hero.png", 42, "hero") is None

    def test_a_wide_grid_without_an_image_id_has_no_name(self):
        with pytest.raises(ValueError, match="image id"):
            cached_artwork_name(42, "grid", None)

    @pytest.mark.parametrize(
        "name",
        ["42_grid.png", "42_grid_12345.png.tmp", "142_grid_12345.png", "42_logo_12345.png", "42_grid_abc.png"],
    )
    def test_no_other_name_carries_rom_42s_wide_grid_id(self, name):
        assert cached_image_id(name, 42, "grid") is None

    def test_both_names_a_wide_grid_has_had_are_its_cache_files(self):
        assert is_cached_artwork("42_grid.png", 42, "grid")
        assert is_cached_artwork("42_grid_7.png", 42, "grid")
        assert not is_cached_artwork("42_logo_7.png", 42, "grid")
        assert not is_cached_artwork("421_grid.png", 42, "grid")

    @pytest.mark.parametrize(
        ("name", "slot"),
        [
            ("42_grid_7.png", (42, "grid")),
            ("42_logo.png", (42, "logo")),
            ("421_logo_7.png", (421, "logo")),
            ("42_icon.png", (42, "icon")),
            ("42_icon_7.png", (42, "icon")),
            ("42_hero.png", (42, "hero")),
            ("42_hero_7.png", (42, "hero")),
            ("42_cover_7.png", (42, "cover")),
        ],
    )
    def test_a_cache_file_names_its_rom_and_asset_type(self, name, slot):
        assert cached_slot_of(name) == slot

    @pytest.mark.parametrize(
        "name", ["42_cover.png", "042_grid_7.png", "42_grid_7.png.tmp", "42_grid_abc.png", "x.png"]
    )
    def test_no_other_name_is_a_cache_file(self, name):
        assert cached_slot_of(name) is None


class TestChooseTheCover:
    def test_600_by_900_wins_over_an_earlier_660_by_930(self):
        data = [_image(1, 660, 930), _image(2, 600, 900)]

        assert _chosen("cover", data).image_id == 2

    def test_660_by_930_wins_over_an_earlier_342_by_482(self):
        data = [_image(1, 342, 482), _image(2, 660, 930)]

        assert _chosen("cover", data).image_id == 2

    def test_342_by_482_wins_over_an_earlier_candidate_of_another_size(self):
        data = [_image(1, 1024, 1024), _image(2, 342, 482)]

        assert _chosen("cover", data).image_id == 2

    def test_without_any_of_the_three_the_first_candidate_is_taken(self):
        data = [_image(1, 1024, 1024), _image(2, 920, 430)]

        assert _chosen("cover", data).image_id == 1


class TestCandidatePages:
    def test_page_zero_is_the_automatic_query_with_its_page(self):
        assert sgdb_candidates_page_path("grid", 7, 0) == "/grids/game/7?dimensions=460x215,920x430&types=static&page=0"

    def test_an_asset_without_a_query_gets_one(self):
        assert sgdb_candidates_page_path("hero", 7, 2) == "/heroes/game/7?page=2"

    def test_an_unknown_asset_has_no_page(self):
        assert sgdb_candidates_page_path("banner", 7, 0) is None

    def test_the_candidates_carry_their_thumbnail_in_steamgriddbs_order(self):
        payload = {
            "success": True,
            "data": [
                {**_image(2, 920, 430), "thumb": "https://cdn.example/t2.jpg"},
                _image(1, 460, 215),
            ],
        }

        page = parse_candidate_page(payload, 0)

        assert page == SgdbCandidatePage(
            candidates=[
                SgdbCandidate(
                    image_id=2,
                    url="https://cdn.example/2.png",
                    width=920,
                    height=430,
                    thumb="https://cdn.example/t2.jpg",
                ),
                SgdbCandidate(image_id=1, url="https://cdn.example/1.png", width=460, height=215),
            ],
            has_more=True,
        )

    @pytest.mark.parametrize(
        ("page", "total", "has_more"),
        [(0, 51, True), (0, 50, False), (1, 101, True), (1, 100, False)],
    )
    def test_a_stated_total_and_limit_decide_whether_more_follow(self, page, total, has_more):
        payload = {"success": True, "page": page, "total": total, "limit": 50, "data": [_image(1, 1, 1)]}

        answer = parse_candidate_page(payload, page)

        assert answer is not None
        assert answer.has_more is has_more

    @pytest.mark.parametrize(("listed", "has_more"), [(50, True), (49, False)])
    def test_a_limit_alone_says_whether_the_page_was_full(self, listed, has_more):
        payload = {"success": True, "limit": 50, "data": [_image(i, 1, 1) for i in range(1, listed + 1)]}

        answer = parse_candidate_page(payload, 0)

        assert answer is not None
        assert answer.has_more is has_more

    @pytest.mark.parametrize(("data", "has_more"), [([_image(1, 1, 1)], True), ([], False)])
    def test_without_either_an_empty_page_ends_the_list(self, data, has_more):
        answer = parse_candidate_page({"success": True, "data": data}, 3)

        assert answer is not None
        assert answer.has_more is has_more

    @pytest.mark.parametrize("payload", [None, {"success": False}, "junk"])
    def test_an_unsuccessful_answer_is_no_page(self, payload):
        assert parse_candidate_page(payload, 0) is None

    def test_an_empty_thumb_is_none(self):
        page = parse_candidate_page({"success": True, "data": [{**_image(1, 1, 1), "thumb": ""}]}, 0)

        assert page is not None
        assert page.candidates[0].thumb is None


class TestParseGameName:
    def test_the_games_name(self):
        assert parse_game_name({"success": True, "data": {"id": 7, "name": "Mario Golf"}}) == "Mario Golf"

    @pytest.mark.parametrize(
        "payload",
        [None, {"success": False}, {"success": True, "data": []}, {"success": True, "data": {"name": ""}}],
    )
    def test_no_name_without_a_successful_named_answer(self, payload):
        assert parse_game_name(payload) is None
