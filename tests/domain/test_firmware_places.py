"""Unit tests for judging link places and planning a link update."""

from __future__ import annotations

from domain.firmware_places import (
    LINK_ELSEWHERE,
    LINK_FREE,
    LINK_LINKED,
    LINK_NOT_LINKED,
    LINK_OCCUPIED,
    REFUSED_FOLDER_MISSING,
    REFUSED_NO_PLACE,
    REFUSED_ROOT_ASSUMED,
    REFUSED_UNREADABLE,
    PlaceLook,
    PlaceRefusal,
    WantedLink,
    is_own_link,
    link_state,
    place_refusal,
    plan_link_update,
    root_over,
)
from domain.firmware_store import FirmwareLink
from domain.source_roots import SourceRoot, SourceRoots

_BIOS = "/home/deck/retrodeck/bios"
_STORE = "/home/deck/Tender/bios"
_GB_COPY = f"{_STORE}/gb/gb_bios.bin"
_GBC_COPY = f"{_STORE}/gbc/gb_bios.bin"
_FREE = PlaceLook(lexists=False)


def _roots(*, bios: str = "read", home: str = "read") -> SourceRoots:
    return SourceRoots(
        roots=(
            SourceRoot(name="installation", path="/home/deck/retrodeck", provenance=home, settings_file=None),
            SourceRoot(name="bios", path=_BIOS, provenance=bios, settings_file=None),
        ),
        findings=(),
    )


def _want(place: str, target: str | None, platform: str = "gbc", **extra) -> WantedLink:
    return WantedLink(
        place=place,
        target=target,
        platform_slug=platform,
        file_name=target.rsplit("/", 1)[-1] if target else None,
        source="retrodeck",
        **extra,
    )


def _record(place: str, target: str, platform: str = "gbc") -> FirmwareLink:
    return FirmwareLink.mark_placed(
        place=place,
        target=target,
        platform_slug=platform,
        file_name=target.rsplit("/", 1)[-1],
        source="retrodeck",
        placed_at="2026-10-21T20:00:00",
    )


class TestWhichRootAPlaceLiesUnder:
    def test_the_deepest_root_answers(self):
        root = root_over(f"{_BIOS}/gb_bios.bin", _roots())
        assert root is not None
        assert root.name == "bios"

    def test_a_place_outside_every_root_lies_under_none(self):
        assert root_over("/home/deck/.var/app/net.retrodeck.retrodeck/data/Cemu/keys.txt", _roots()) is None

    def test_a_folder_that_only_shares_a_prefix_is_no_root_of_it(self):
        root = root_over("/home/deck/retrodeck/bios-old/x.bin", _roots())
        assert root is not None
        assert root.name == "installation"


class TestWhetherAPlaceMayBeUsed:
    def test_a_place_under_an_assumed_root_is_refused_naming_that_root(self):
        refusal = place_refusal(f"{_BIOS}/gb_bios.bin", _FREE, _roots(bios="assumed"), unreadable=False)

        assert refusal == PlaceRefusal(REFUSED_ROOT_ASSUMED, _BIOS)

    def test_the_deepest_root_decides_even_under_an_assumed_parent(self):
        assert place_refusal(f"{_BIOS}/gb_bios.bin", _FREE, _roots(home="assumed"), unreadable=False) is None

    def test_a_place_under_no_root_may_be_used(self):
        cemu = "/home/deck/.var/app/net.retrodeck.retrodeck/data/Cemu/keys.txt"

        assert place_refusal(cemu, _FREE, _roots(bios="assumed", home="assumed"), unreadable=False) is None

    def test_a_missing_folder_is_refused_naming_it(self):
        look = PlaceLook(lexists=False, folder_exists=False)

        refusal = place_refusal("/x/data/Cemu/keys.txt", look, SourceRoots(roots=(), findings=()), unreadable=False)

        assert refusal == PlaceRefusal(REFUSED_FOLDER_MISSING, "/x/data/Cemu")

    def test_a_source_whose_roots_could_not_be_asked_gets_no_link(self):
        """Nothing tells an assumed root from a read one there, so no place of that source is used."""
        refusal = place_refusal(f"{_BIOS}/a.bin", _FREE, None, unreadable=False)

        assert refusal == PlaceRefusal(REFUSED_ROOT_ASSUMED, _BIOS)

    def test_a_place_the_resolver_could_not_look_at_is_refused(self):
        assert place_refusal(f"{_BIOS}/a.bin", _FREE, _roots(), unreadable=True) == PlaceRefusal(REFUSED_UNREADABLE)

    def test_no_place_is_refused(self):
        assert place_refusal(None, None, _roots(), unreadable=False) == PlaceRefusal(REFUSED_NO_PLACE)


class TestWhoseLinkItIs:
    def test_a_link_to_a_recorded_store_file_is_tenders_wherever_it_lies(self):
        look = PlaceLook(lexists=True, link_target=_GB_COPY)

        assert is_own_link("/anywhere/gb_bios.bin", look, {_GB_COPY: "gb"}, {})

    def test_a_link_its_record_names_is_tenders_after_its_store_file_went(self):
        place = f"{_BIOS}/gb_bios.bin"
        look = PlaceLook(lexists=True, link_target=_GB_COPY)

        assert is_own_link(place, look, {}, {place: _record(place, _GB_COPY)})

    def test_a_link_elsewhere_and_a_plain_file_are_not(self):
        assert not is_own_link("/p", PlaceLook(lexists=True, link_target="/home/deck/elsewhere.bin"), {}, {})
        assert not is_own_link("/p", PlaceLook(lexists=True), {_GB_COPY: "gb"}, {})


class TestWhatARowSaysLiesThere:
    def _state(self, look, target: str | None = _GBC_COPY, present=frozenset({_GB_COPY, _GBC_COPY}), records=None):
        return link_state(
            _want(f"{_BIOS}/gb_bios.bin", target),
            look,
            store_targets={_GB_COPY: "gb", _GBC_COPY: "gbc"},
            present=present,
            records=records or {},
        )

    def test_a_free_place_with_a_store_copy_is_not_linked_yet(self):
        assert self._state(_FREE) == (LINK_NOT_LINKED, None)

    def test_a_free_place_without_a_store_copy_is_free(self):
        assert self._state(_FREE, target=None) == (LINK_FREE, None)

    def test_tenders_link_to_this_platforms_copy_is_linked(self):
        assert self._state(PlaceLook(lexists=True, link_target=_GBC_COPY)) == (LINK_LINKED, "gbc")

    def test_tenders_link_to_another_platforms_copy_is_linked_for_that_platform(self):
        assert self._state(PlaceLook(lexists=True, link_target=_GB_COPY)) == (LINK_ELSEWHERE, "gb")

    def test_a_file_that_is_not_tenders_occupies_the_place(self):
        assert self._state(PlaceLook(lexists=True)) == (LINK_OCCUPIED, None)

    def test_tenders_link_that_points_at_nothing_is_not_linked_yet(self):
        look = PlaceLook(lexists=True, link_target=_GB_COPY)

        assert self._state(look, present=frozenset({_GBC_COPY})) == (LINK_NOT_LINKED, None)


class TestBringingTheLinksUpToDate:
    def _plan(self, wanted, looks, *, records=(), present=frozenset({_GBC_COPY}), blocked=frozenset()):
        return plan_link_update(
            "gbc",
            wanted,
            looks,
            store_targets={_GB_COPY: "gb", _GBC_COPY: "gbc"},
            present=present,
            records={record.place: record for record in records},
            blocked=blocked,
        )

    def test_a_free_place_with_a_store_copy_gets_its_link(self):
        want = _want(f"{_BIOS}/gb_bios.bin", _GBC_COPY)

        plan = self._plan([want], {want.place: _FREE})

        assert plan.place == (want,)
        assert plan.left_in_place == 0

    def test_a_place_another_file_lies_at_is_left_and_counted(self):
        want = _want(f"{_BIOS}/gb_bios.bin", _GBC_COPY)

        plan = self._plan([want], {want.place: PlaceLook(lexists=True)})

        assert (plan.place, plan.replace, plan.remove, plan.left_in_place) == ((), (), (), 1)

    def test_a_refused_place_is_left_alone_and_not_counted(self):
        want = _want(f"{_BIOS}/gb_bios.bin", _GBC_COPY, refusal=PlaceRefusal(REFUSED_ROOT_ASSUMED, _BIOS))

        plan = self._plan([want], {})

        assert plan == plan_link_update(
            "gbc", [], {}, store_targets={}, present=frozenset(), records={}, blocked=frozenset()
        )

    def test_a_dangling_link_of_tenders_is_placed_again_where_a_store_copy_exists(self):
        want = _want(f"{_BIOS}/gb_bios.bin", _GBC_COPY)
        look = PlaceLook(lexists=True, link_target=_GB_COPY)

        plan = self._plan([want], {want.place: look})

        assert plan.replace == (want,)

    def test_a_dangling_link_of_tenders_is_removed_where_no_store_copy_exists(self):
        place = f"{_BIOS}/gb_bios.bin"
        record = _record(place, _GBC_COPY)

        plan = self._plan(
            [_want(place, None)],
            {place: PlaceLook(lexists=True, link_target=_GBC_COPY)},
            records=[record],
            present=frozenset(),
        )

        assert plan.remove == (place,)
        assert plan.drop == (place,)

    def test_a_link_of_another_platforms_copy_stays_and_is_counted_as_nothing(self):
        want = _want(f"{_BIOS}/gb_bios.bin", _GBC_COPY)

        plan = self._plan(
            [want], {want.place: PlaceLook(lexists=True, link_target=_GB_COPY)}, present=frozenset({_GB_COPY})
        )

        assert (plan.place, plan.replace, plan.left_in_place) == ((), (), 0)

    def test_a_link_that_moved_with_its_folder_gets_its_record_and_the_old_one_goes(self):
        old = "/home/deck/retrodeck/bios/gb_bios.bin"
        new = "/run/media/deck/SD/retrodeck/bios/gb_bios.bin"

        plan = self._plan(
            [_want(new, _GBC_COPY)],
            {new: PlaceLook(lexists=True, link_target=_GBC_COPY), old: _FREE},
            records=[_record(old, _GBC_COPY)],
        )

        assert [link.place for link in plan.record] == [new]
        assert plan.record[0].platform_slug == "gbc"
        assert plan.drop == (old,)

    def test_a_blocked_record_is_kept(self):
        place = f"{_BIOS}/gb_bios.bin"

        plan = self._plan([], {place: _FREE}, records=[_record(place, _GBC_COPY)], blocked=frozenset({place}))

        assert plan.drop == ()

    def test_a_recorded_link_left_behind_by_a_delete_goes_once_its_place_may_be_touched(self):
        place = f"{_BIOS}/gb_bios.bin"
        record = _record(place, _GBC_COPY)

        plan = self._plan(
            [], {place: PlaceLook(lexists=True, link_target=_GBC_COPY)}, records=[record], present=frozenset()
        )

        assert (plan.remove, plan.drop) == ((place,), (place,))

    def test_another_platforms_records_are_not_this_ones_to_drop(self):
        place = f"{_BIOS}/other.bin"

        plan = self._plan([], {place: _FREE}, records=[_record(place, _GB_COPY, platform="gb")])

        assert plan.drop == ()

    def test_a_place_two_emulators_want_is_one_place(self):
        want = _want(f"{_BIOS}/gb_bios.bin", _GBC_COPY)

        plan = self._plan([want, want], {want.place: _FREE})

        assert plan.place == (want,)
