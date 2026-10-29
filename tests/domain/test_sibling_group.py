"""Tests for domain/sibling_group.py — client-side sibling-group key derivation."""

from __future__ import annotations

import itertools
from typing import Any

from domain.sibling_group import (
    GroupKeys,
    compute_component_group_keys,
    compute_group_keys_with_unfetched,
    compute_sibling_group_key,
    target_bound_elsewhere,
    target_in_sibling_group,
)


def _rom(rom_id: int, *, platform_id: int = 57, siblings: tuple[int, ...] = (), **ids: int) -> dict[str, Any]:
    """Build a raw RomM ROM dict with ``sibling_roms`` edges and metadata ids."""
    rom: dict[str, Any] = {"id": rom_id, "platform_id": platform_id, "sibling_roms": [{"id": s} for s in siblings]}
    rom.update(ids)
    return rom


class TestCoalesceOrder:
    """The metadata ids coalesce in RomM's fixed order; the first non-null wins."""

    def test_igdb_wins_over_all_others(self):
        rom = {
            "id": 1,
            "platform_id": 57,
            "igdb_id": 3404,
            "ss_id": 9,
            "moby_id": 8,
            "ra_id": 7,
            "hasheous_id": 6,
            "launchbox_id": 5,
            "tgdb_id": 4,
            "flashpoint_id": 3,
        }
        assert compute_sibling_group_key(rom) == "igdb:3404:57"

    def test_ss_wins_when_igdb_absent(self):
        rom = {"id": 1, "platform_id": 57, "ss_id": 22, "moby_id": 8, "ra_id": 7}
        assert compute_sibling_group_key(rom) == "ss:22:57"

    def test_moby_wins_over_ra(self):
        rom = {"id": 1, "platform_id": 57, "moby_id": 88, "ra_id": 7}
        assert compute_sibling_group_key(rom) == "moby:88:57"

    def test_ra_wins_over_hasheous(self):
        rom = {"id": 1, "platform_id": 57, "ra_id": 70, "hasheous_id": 6}
        assert compute_sibling_group_key(rom) == "ra:70:57"

    def test_hasheous_wins_over_launchbox(self):
        rom = {"id": 1, "platform_id": 57, "hasheous_id": 60, "launchbox_id": 5}
        assert compute_sibling_group_key(rom) == "hasheous:60:57"

    def test_launchbox_wins_over_tgdb(self):
        rom = {"id": 1, "platform_id": 57, "launchbox_id": 50, "tgdb_id": 4}
        assert compute_sibling_group_key(rom) == "launchbox:50:57"

    def test_tgdb_wins_over_flashpoint(self):
        rom = {"id": 1, "platform_id": 57, "tgdb_id": 40, "flashpoint_id": 3}
        assert compute_sibling_group_key(rom) == "tgdb:40:57"

    def test_flashpoint_is_last_matched_source(self):
        rom = {"id": 1, "platform_id": 57, "flashpoint_id": 30}
        assert compute_sibling_group_key(rom) == "flashpoint:30:57"


class TestPlatformScoping:
    """The same metadata id on two platforms yields two distinct groups."""

    def test_same_igdb_different_platform_is_different_key(self):
        base = {"id": 1, "igdb_id": 3404}
        key_a = compute_sibling_group_key({**base, "platform_id": 57})
        key_b = compute_sibling_group_key({**base, "platform_id": 12})
        assert key_a == "igdb:3404:57"
        assert key_b == "igdb:3404:12"
        assert key_a != key_b

    def test_same_igdb_same_platform_is_same_key(self):
        a = compute_sibling_group_key({"id": 1, "platform_id": 57, "igdb_id": 3404})
        b = compute_sibling_group_key({"id": 2, "platform_id": 57, "igdb_id": 3404})
        assert a == b == "igdb:3404:57"


class TestUnmatchedFallback:
    """An unmatched ROM falls back to its own id — a solo group."""

    def test_no_metadata_ids_falls_back_to_rom_id(self):
        rom = {"id": 4409, "platform_id": 57}
        assert compute_sibling_group_key(rom) == "romm:4409:57"

    def test_all_metadata_ids_none_falls_back(self):
        rom = {
            "id": 4409,
            "platform_id": 57,
            "igdb_id": None,
            "ss_id": None,
            "moby_id": None,
            "ra_id": None,
            "hasheous_id": None,
            "launchbox_id": None,
            "tgdb_id": None,
            "flashpoint_id": None,
        }
        assert compute_sibling_group_key(rom) == "romm:4409:57"

    def test_two_unmatched_roms_are_distinct_solo_groups(self):
        a = compute_sibling_group_key({"id": 100, "platform_id": 57})
        b = compute_sibling_group_key({"id": 200, "platform_id": 57})
        assert a == "romm:100:57"
        assert b == "romm:200:57"
        assert a != b


class TestMissingAndNoneIds:
    """A None id is skipped as if absent; a later non-null id still wins."""

    def test_none_igdb_skips_to_next_present_source(self):
        rom = {"id": 1, "platform_id": 57, "igdb_id": None, "moby_id": 88}
        assert compute_sibling_group_key(rom) == "moby:88:57"

    def test_missing_igdb_key_skips_to_next(self):
        rom = {"id": 1, "platform_id": 57, "ra_id": 70}
        assert compute_sibling_group_key(rom) == "ra:70:57"

    def test_zero_id_is_treated_as_present(self):
        # 0 is a legitimate (if unusual) id — only None means "unmatched".
        rom = {"id": 1, "platform_id": 57, "igdb_id": 0}
        assert compute_sibling_group_key(rom) == "igdb:0:57"


class TestTargetInSiblingGroupLocal:
    """A LOCAL target is judged by key equality — the component keys encode group
    membership, so a differing persisted key is a different group (a NULL bound key
    accepts any eligible target)."""

    def test_local_target_same_group_is_member(self):
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:100:57",
                target_group_key="igdb:100:57",
                target_is_local=True,
                target_is_server_sibling=True,
            )
            is True
        )

    def test_local_target_different_group_is_not_member(self):
        # The #1359 bug: a RomM sibling that is locally synced under a DIFFERENT
        # key (a conflicting metadata match) is not switchable.
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:1156:57",
                target_group_key="ss:19274:57",
                target_is_local=True,
                target_is_server_sibling=True,
            )
            is False
        )

    def test_null_bound_key_accepts_any_local_target(self):
        # An unbackfilled / solo bound row (NULL key) never blocks a local target.
        assert (
            target_in_sibling_group(
                bound_group_key=None,
                target_group_key="ss:19274:57",
                target_is_local=True,
                target_is_server_sibling=False,
            )
            is True
        )

    def test_local_target_group_check_ignores_server_sibling_flag(self):
        # For a LOCAL target the server-sibling flag is irrelevant — the local key
        # decides. A cross-group local target stays a non-member even if RomM lists
        # it as a sibling.
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:1156:57",
                target_group_key="ss:19274:57",
                target_is_local=True,
                target_is_server_sibling=True,
            )
            is False
        )


class TestTargetBoundElsewhere:
    """A target is bound elsewhere when another shortcut of the game is on it."""

    def test_an_unbound_target_is_not_bound_elsewhere(self):
        assert target_bound_elsewhere(target_app_id=None, app_id=100) is False

    def test_a_target_on_the_asking_shortcut_is_not_bound_elsewhere(self):
        assert target_bound_elsewhere(target_app_id=100, app_id=100) is False

    def test_a_target_on_another_shortcut_is_bound_elsewhere(self):
        assert target_bound_elsewhere(target_app_id=777, app_id=100) is True


class TestTargetInSiblingGroupServerOnly:
    """A SERVER-ONLY target (no local row yet) is judged by canonical compatibility:
    RomM must list it as a sibling AND its id at the bound key's canonical source
    must be absent-or-equal — the persisted key doubles as the group's canonical
    summary (#1360, #1368)."""

    def test_matching_canonical_value_is_member(self):
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:100:57",
                target_ids={"igdb_id": 100, "ss_id": 22},
                target_is_local=False,
                target_is_server_sibling=True,
            )
            is True
        )

    def test_absent_canonical_value_is_member(self):
        # #1368 uneven coverage: the bound group keys on igdb; the sibling lacks an
        # igdb id (matched only on ss/hasheous) → absent-at-canonical → in-group. It
        # joins under the bound key and the next sync re-canonicalizes the component.
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:1001:57",
                target_ids={"ss_id": 2002, "hasheous_id": 3003, "launchbox_id": 4005},
                target_is_local=False,
                target_is_server_sibling=True,
            )
            is True
        )

    def test_conflicting_canonical_value_is_not_member(self):
        # #1360: the sibling carries a DIFFERENT id at the canonical source (a
        # genuine cross-game bridge) → rejected.
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:100:57",
                target_ids={"igdb_id": 999, "ss_id": 22},
                target_is_local=False,
                target_is_server_sibling=True,
            )
            is False
        )

    def test_higher_priority_source_is_member(self):
        # The bound group keys on ss (no igdb). The sibling agrees on ss AND carries
        # a higher-priority igdb — in-group; the switch persists the bound key and
        # the next sync re-canonicalizes the whole component onto igdb.
        assert (
            target_in_sibling_group(
                bound_group_key="ss:2002:57",
                target_ids={"igdb_id": 100, "ss_id": 2002},
                target_is_local=False,
                target_is_server_sibling=True,
            )
            is True
        )

    def test_romm_fallback_bound_key_admits_no_server_target(self):
        # A ``romm:`` bound key has no metadata source to compare against, so no
        # server-only target can be proven compatible (preserves today's blocking).
        assert (
            target_in_sibling_group(
                bound_group_key="romm:4409:57",
                target_ids={"igdb_id": 100},
                target_is_local=False,
                target_is_server_sibling=True,
            )
            is False
        )

    def test_unfetched_detail_is_not_member(self):
        # ``target_ids`` None (a transient detail-fetch miss) can't be judged →
        # non-switchable, exactly as a missing would-be key blocked before.
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:100:57",
                target_ids=None,
                target_is_local=False,
                target_is_server_sibling=True,
            )
            is False
        )

    def test_null_bound_key_accepts_any_server_only_target(self):
        # An unbackfilled / solo bound row can't discriminate — any RomM sibling is
        # accepted, even before its detail is fetched.
        assert (
            target_in_sibling_group(
                bound_group_key=None,
                target_ids=None,
                target_is_local=False,
                target_is_server_sibling=True,
            )
            is True
        )

    def test_not_a_sibling_is_not_member(self):
        assert (
            target_in_sibling_group(
                bound_group_key="igdb:100:57",
                target_ids={"igdb_id": 100},
                target_is_local=False,
                target_is_server_sibling=False,
            )
            is False
        )


class TestComputeComponentGroupKeys:
    """The component kernel: connected components over ``sibling_roms`` edges keyed
    by canonical-source agreement, seeded by resident (persisted) keys."""

    def test_two_fresh_siblings_share_the_canonical_key(self):
        roms = [_rom(1, igdb_id=100, siblings=(2,)), _rom(2, igdb_id=100, siblings=(1,))]
        assert compute_component_group_keys(roms, {}) == {1: "igdb:100:57", 2: "igdb:100:57"}

    def test_uneven_coverage_merges_on_igdb_despite_launchbox_divergence(self):
        # rom A: igdb+ss+hasheous+launchbox; rom B: ss+hasheous+launchbox with the
        # SAME ss/hasheous but a DIFFERENT launchbox and NO igdb. Both key on the
        # canonical igdb — the low-priority launchbox divergence never blocks (#1368).
        a = _rom(1, igdb_id=1001, ss_id=2002, hasheous_id=3003, launchbox_id=4004, siblings=(2,))
        b = _rom(2, ss_id=2002, hasheous_id=3003, launchbox_id=4005, siblings=(1,))
        assert compute_component_group_keys([a, b], {}) == {1: "igdb:1001:57", 2: "igdb:1001:57"}

    def test_chain_smuggled_canonical_conflict_falls_back(self):
        # A igdb:1+ss:5, B ss:5+moby:9, C igdb:2+moby:9 — A-B (ss) and B-C (moby)
        # chain into one component whose canonical (igdb) holds two values {1,2}
        # → no assumption-merge, every member keeps its own coalesce-first key.
        a = _rom(1, igdb_id=1, ss_id=5, siblings=(2,))
        b = _rom(2, ss_id=5, moby_id=9, siblings=(1, 3))
        c = _rom(3, igdb_id=2, moby_id=9, siblings=(2,))
        assert compute_component_group_keys([a, b, c], {}) == {1: "igdb:1:57", 2: "ss:5:57", 3: "igdb:2:57"}

    def test_no_edge_matched_rom_keys_solo_by_own_top_source(self):
        assert compute_component_group_keys([_rom(1, ss_id=22)], {}) == {1: "ss:22:57"}

    def test_no_edge_no_ids_falls_back_to_romm(self):
        assert compute_component_group_keys([_rom(1)], {}) == {1: "romm:1:57"}

    def test_in_unit_resident_preserved_and_seeds_fresh_member(self):
        # A already carries a key (an incremental-reconstructed resident); B is fresh
        # and edges to A but has no igdb → B adopts A's canonical summary, and A is
        # NOT re-keyed (absent from the result).
        a = {"id": 1, "platform_id": 57, "sibling_group_key": "igdb:100:57", "sibling_roms": [{"id": 2}]}
        b = _rom(2, ss_id=22, siblings=(1,))
        assert compute_component_group_keys([a, b], {}) == {2: "igdb:100:57"}

    def test_explicit_null_key_is_fresh_not_resident(self):
        # A skipped collection reconstructs its bound rows verbatim, NULL key
        # included (collection_fetcher._reconstruct_collection_members) — carrying the field
        # as NULL is not residency, so the member is fresh and gets a computed key.
        a = {"id": 1, "platform_id": 57, "igdb_id": 100, "sibling_group_key": None, "sibling_roms": []}
        assert compute_component_group_keys([a], {}) == {1: "igdb:100:57"}

    def test_db_resident_key_seeds_fresh_member(self):
        # B is fresh and edges to a DB-resident sibling (rom 1, not in the unit)
        # whose persisted key is igdb:100:57 → B adopts it.
        assert compute_component_group_keys([_rom(2, ss_id=22, siblings=(1,))], {1: "igdb:100:57"}) == {
            2: "igdb:100:57"
        }

    def test_romm_fallback_resident_contributes_nothing(self):
        # A DB-resident whose key is a romm: fallback offers no canonical candidate,
        # so B keys off its own id.
        assert compute_component_group_keys([_rom(2, ss_id=22, siblings=(1,))], {1: "romm:1:57"}) == {2: "ss:22:57"}

    def test_fresh_member_edging_conflicting_resident_falls_back(self):
        # B (igdb:999) edges to a DB-resident keyed igdb:100 → the component's
        # canonical (igdb) holds {100, 999} → B keeps its own key, no merge.
        assert compute_component_group_keys([_rom(2, igdb_id=999, ss_id=22, siblings=(1,))], {1: "igdb:100:57"}) == {
            2: "igdb:999:57"
        }

    def test_platform_scopes_the_merged_key(self):
        # A carries igdb, B has no ids but edges to A — both key on A's igdb, each
        # scoped by its own platform id (siblings share a platform).
        a = _rom(1, platform_id=12, igdb_id=100, siblings=(2,))
        b = _rom(2, platform_id=12, siblings=(1,))
        assert compute_component_group_keys([a, b], {}) == {1: "igdb:100:12", 2: "igdb:100:12"}

    def test_edge_to_unknown_rom_is_ignored(self):
        # An edge to a rom that is neither fresh nor resident (never fetched, not in
        # the DB) unions nothing and offers no candidate.
        assert compute_component_group_keys([_rom(2, ss_id=22, siblings=(99,))], {}) == {2: "ss:22:57"}

    def test_deterministic_under_input_permutation(self):
        a = _rom(1, igdb_id=1001, ss_id=2002, siblings=(2, 3))
        b = _rom(2, ss_id=2002, siblings=(1, 3))
        c = _rom(3, ss_id=2002, siblings=(1, 2))
        results = [compute_component_group_keys(list(p), {}) for p in itertools.permutations([a, b, c])]
        assert all(r == results[0] for r in results)
        assert results[0] == {1: "igdb:1001:57", 2: "igdb:1001:57", 3: "igdb:1001:57"}


def _key(source: str, value: int) -> str:
    return f"{source}:{value}:57"


def _keys(
    roms: list[dict[str, Any]],
    resident_keys: dict[int, str],
    *,
    complete: set[int] | None = None,
    unfetched_bound: tuple[int, ...] = (),
) -> GroupKeys:
    """Key *roms* as a complete view of every fresh ROM in it, unless *complete* names fewer."""
    complete_ids = {rom["id"] for rom in roms} if complete is None else complete
    return compute_group_keys_with_unfetched(
        roms, resident_keys, complete_rom_ids=complete_ids, unfetched_bound_ids=unfetched_bound
    )


class TestComputeGroupKeysWithUnfetched:
    """A complete view keys the stored rows it did not fetch with the group they follow (#2646 D1-D4)."""

    def test_a_hidden_row_an_edge_reaches_takes_the_component_key(self):
        # 40 is hidden in RomM: left out of the listing, still in 41's siblings. 41
        # gains an igdb id, so the game's key moves to it, and 40 moves with it.
        keys = _keys([_rom(41, igdb_id=1, ss_id=5, siblings=(40,))], {40: _key("ss", 5), 41: _key("ss", 5)})
        assert keys == GroupKeys({41: _key("igdb", 1)}, {40: _key("igdb", 1)})

    def test_a_partial_view_keys_as_compute_component_group_keys_and_follows_nothing(self):
        roms = [_rom(41, igdb_id=1, ss_id=5, siblings=(40,))]
        resident = {40: _key("ss", 5), 41: _key("ss", 5)}
        keys = _keys(roms, resident, complete=set(), unfetched_bound=(40,))
        assert keys == GroupKeys(compute_component_group_keys(roms, resident), {})

    def test_a_row_reached_under_its_own_key_is_not_answered(self):
        keys = _keys([_rom(41, ss_id=5, siblings=(40,))], {40: _key("ss", 5)}, unfetched_bound=(40,))
        assert keys.unfetched == {}

    def test_a_row_two_components_reach_under_two_keys_keeps_its_key(self):
        roms = [_rom(41, igdb_id=1, siblings=(40,)), _rom(42, igdb_id=2, siblings=(40,))]
        keys = _keys(roms, {40: _key("ss", 5)}, unfetched_bound=(40,))
        assert keys == GroupKeys({41: _key("igdb", 1), 42: _key("igdb", 2)}, {})

    def test_a_deleted_bound_row_follows_the_component_that_held_its_key(self):
        # 1 alone carried the igdb id and is deleted; 2 is keyed by its own ss id now.
        keys = _keys([_rom(2, ss_id=5)], {1: _key("igdb", 1), 2: _key("igdb", 1)}, unfetched_bound=(1,))
        assert keys == GroupKeys({2: _key("ss", 5)}, {1: _key("ss", 5)})

    def test_a_member_with_no_prior_key_does_not_stop_the_follow(self):
        roms = [_rom(2, ss_id=5, siblings=(3,)), _rom(3, ss_id=5, siblings=(2,))]
        keys = _keys(roms, {1: _key("igdb", 1), 2: _key("igdb", 1)}, unfetched_bound=(1,))
        assert keys.unfetched == {1: _key("ss", 5)}

    def test_a_row_not_bound_is_not_followed_without_an_edge(self):
        keys = _keys([_rom(2, ss_id=5)], {1: _key("igdb", 1), 2: _key("igdb", 1)})
        assert keys.unfetched == {}

    def test_a_game_split_in_two_is_not_followed(self):
        # 2 and 3 both held 1's key and are separate games now: neither is the one.
        roms = [_rom(2, ss_id=5), _rom(3, moby_id=4)]
        keys = _keys(roms, {1: _key("igdb", 1), 2: _key("igdb", 1), 3: _key("igdb", 1)}, unfetched_bound=(1,))
        assert keys.unfetched == {}

    def test_a_partial_carrier_is_not_followed(self):
        keys = _keys([_rom(2, ss_id=5)], {1: _key("igdb", 1), 2: _key("igdb", 1)}, complete=set(), unfetched_bound=(1,))
        assert keys.unfetched == {}

    def test_a_carrier_the_gate_splits_is_not_followed(self):
        roms = [_rom(2, igdb_id=7, ss_id=5, siblings=(3,)), _rom(3, igdb_id=8, ss_id=5, siblings=(2,))]
        keys = _keys(roms, {1: _key("igdb", 1), 2: _key("igdb", 1), 3: _key("igdb", 1)}, unfetched_bound=(1,))
        assert keys == GroupKeys({2: _key("igdb", 7), 3: _key("igdb", 8)}, {})

    def test_a_version_moved_into_another_game_is_not_followed(self):
        # 2 held 1's key and is now a version of 5's game: 5 held another key.
        roms = [_rom(2, ss_id=8, siblings=(5,)), _rom(5, ss_id=8, siblings=(2,))]
        keys = _keys(roms, {1: _key("igdb", 1), 2: _key("igdb", 1), 5: _key("ss", 8)}, unfetched_bound=(1,))
        assert keys.unfetched == {}

    def test_a_carrier_holding_another_value_at_the_stored_source_is_not_followed(self):
        # RomM re-matched 2 to another IGDB game: 1's igdb:1 is not that game's.
        keys = _keys([_rom(2, igdb_id=9, ss_id=5)], {1: _key("igdb", 1), 2: _key("igdb", 1)}, unfetched_bound=(1,))
        assert keys == GroupKeys({2: _key("igdb", 9)}, {})

    def test_a_romm_key_is_never_followed(self):
        keys = _keys([_rom(2, ss_id=5)], {1: "romm:1:57", 2: "romm:1:57"}, unfetched_bound=(1,))
        assert keys.unfetched == {}

    def test_a_stale_summary_does_not_split_its_game(self):
        # 30 is re-matched to igdb 2; hidden 32 still holds the igdb:1 summary the
        # whole game held at the last sync. It is not counted, and 32 follows.
        roms = [
            _rom(30, igdb_id=2, ss_id=5, siblings=(31, 32)),
            _rom(31, ss_id=5, siblings=(30, 32)),
        ]
        resident = {30: _key("igdb", 1), 31: _key("igdb", 1), 32: _key("igdb", 1)}
        keys = _keys(roms, resident)
        assert keys == GroupKeys({30: _key("igdb", 2), 31: _key("igdb", 2)}, {32: _key("igdb", 2)})

    def test_a_stale_summary_a_new_version_reaches_is_not_counted(self):
        # 33 is new since the last sync and holds no stored key; its edge reaches
        # hidden 32, whose igdb:1 is the key 30, the one member holding a key, held.
        roms = [_rom(30, igdb_id=2, ss_id=5, siblings=(33,)), _rom(33, ss_id=5, siblings=(30, 32))]
        keys = _keys(roms, {30: _key("igdb", 1), 32: _key("igdb", 1)})
        assert keys == GroupKeys({30: _key("igdb", 2), 33: _key("igdb", 2)}, {32: _key("igdb", 2)})

    def test_a_stale_summary_still_splits_a_partial_view(self):
        roms = [
            _rom(30, igdb_id=2, ss_id=5, siblings=(31, 32)),
            _rom(31, ss_id=5, siblings=(30, 32)),
        ]
        resident = {30: _key("igdb", 1), 31: _key("igdb", 1), 32: _key("igdb", 1)}
        keys = _keys(roms, resident, complete=set())
        assert keys == GroupKeys({30: _key("igdb", 2), 31: _key("ss", 5)}, {})

    def test_a_stored_rows_own_other_key_still_splits_its_game(self):
        # 32's igdb:1 is not the summary 30 held (igdb:2): it may be 32's own id.
        keys = _keys([_rom(30, igdb_id=2, ss_id=5, siblings=(32,))], {30: _key("igdb", 2), 32: _key("igdb", 1)})
        assert keys == GroupKeys({30: _key("igdb", 2)}, {})

    def test_two_games_a_new_shared_id_links_keep_their_hidden_versions_apart(self):
        # 10 and 20 are hidden and each still holds its own game's key; RomM now
        # links 11 and 21 through a shared ss id. Neither summary is the
        # component's own, so both are counted and the gate splits it.
        roms = [_rom(11, ss_id=5, siblings=(10, 21)), _rom(21, ss_id=5, siblings=(20, 11))]
        resident = {10: _key("igdb", 1), 11: _key("igdb", 1), 20: _key("igdb", 2), 21: _key("igdb", 2)}
        keys = _keys(roms, resident, unfetched_bound=(10, 20))
        assert keys == GroupKeys(compute_component_group_keys(roms, resident), {})

    def test_a_bridge_with_one_end_hidden_still_splits_its_game(self):
        # 11 held igdb:1 with hidden 10; it now also links 21, which holds igdb 2
        # and held its own game's key.
        roms = [_rom(11, ss_id=5, siblings=(10, 21)), _rom(21, igdb_id=2, ss_id=5, siblings=(11,))]
        resident = {10: _key("igdb", 1), 11: _key("igdb", 1), 21: _key("igdb", 2)}
        keys = _keys(roms, resident, unfetched_bound=(10,))
        assert keys == GroupKeys({11: _key("ss", 5), 21: _key("igdb", 2)}, {})

    def test_two_values_among_the_fetched_members_still_split_their_game(self):
        roms = [_rom(20, igdb_id=1, ss_id=5, siblings=(21,)), _rom(21, igdb_id=2, ss_id=5, siblings=(20,))]
        resident = {20: _key("igdb", 1), 21: _key("igdb", 1)}
        assert _keys(roms, resident) == GroupKeys({20: _key("igdb", 1), 21: _key("igdb", 2)}, {})

    def test_a_bridge_through_a_fetched_member_still_splits_its_game(self):
        roms = [
            _rom(20, igdb_id=1, ss_id=5, siblings=(21,)),
            _rom(21, ss_id=5, moby_id=8, siblings=(20, 22)),
            _rom(22, igdb_id=2, moby_id=8, siblings=(21,)),
        ]
        resident = {20: _key("igdb", 1), 21: _key("igdb", 1)}
        assert _keys(roms, resident) == GroupKeys({20: _key("igdb", 1), 21: _key("ss", 5), 22: _key("igdb", 2)}, {})

    def test_deterministic_under_input_permutation(self):
        roms = [
            _rom(30, igdb_id=2, ss_id=5, siblings=(31, 32)),
            _rom(31, ss_id=5, siblings=(30, 32)),
        ]
        resident = {30: _key("igdb", 1), 31: _key("igdb", 1), 32: _key("igdb", 1)}
        results = [_keys(list(p), resident) for p in itertools.permutations(roms)]
        assert all(r == results[0] for r in results)
        assert results[0].unfetched == {32: _key("igdb", 2)}
