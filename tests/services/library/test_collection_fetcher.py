"""Tests for CollectionFetcher — a collection unit's skip, its members and its walk.

Driven end-to-end through :class:`FakeRommApi` like the fetcher's own tests:
each test seeds platforms, ROMs and collections on the fake and asserts on what
the collection fetcher returns and on the fake's call log.
"""

import logging
from dataclasses import replace

import pytest

from domain.work_unit import WorkUnit
from lib.romm_paging import LIST_PAGE_SIZE
from tests.services.library._helpers import (
    _fetching_frames,
    _seed_collection_stamp,
    _seed_persisted_rom,
)


class TestFetchCollectionUnit:
    """Tests for fetch_collection_unit() — wrong-type guard, multi-page pagination."""

    @pytest.mark.asyncio
    async def test_raises_on_non_collection_unit(self, library):
        """fetch_collection_unit must reject platform units."""
        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=0)
        with pytest.raises(ValueError, match="non-collection unit"):
            await library.sync._collection_fetcher.fetch_collection_unit(unit, set())

    @pytest.mark.asyncio
    async def test_paginates_across_multiple_pages(self, make_library, fake_romm_api):
        """A full first page must trigger offset += limit and a second fetch."""
        library = make_library(romm_api=fake_romm_api)

        # One full page + a one-ROM tail at the 500-ROM page size => page 1 fills,
        # page 2 carries the tail.
        fake_romm_api.roms = {
            i: {
                "id": i,
                "platform_id": 1,
                "name": f"G{i}",
                "platform_name": "N64",
                "platform_slug": "n64",
                "collection_ids": [7],
            }
            for i in range(LIST_PAGE_SIZE)
        }
        fake_romm_api.roms[999] = {
            "id": 999,
            "platform_id": 1,
            "name": "G999",
            "platform_name": "N64",
            "platform_slug": "n64",
            "collection_ids": [7],
        }

        rom_count = LIST_PAGE_SIZE + 1
        unit = WorkUnit(
            type="collection",
            id=7,
            name="Coll",
            slug="",
            rom_count=rom_count,
            collection_kind="standard",
            member_rom_ids=tuple(fake_romm_api.roms),
            unwalked_platform_ids=(1,),
        )
        synced: set[int] = set()
        (
            new_roms,
            all_collection_rom_ids,
            skipped,
            _restamp_settles,
        ) = await library.sync._collection_fetcher.fetch_collection_unit(unit, synced)

        assert skipped is False
        assert len(new_roms) == rom_count
        assert len(all_collection_rom_ids) == rom_count
        assert 999 in synced

    @pytest.mark.asyncio
    async def test_dispatches_smart_collection_to_smart_endpoint(self, make_library, fake_romm_api):
        """collection_kind='smart' routes through list_roms_by_smart_collection."""
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {
            1: {"id": 1, "platform_id": 1, "platform_name": "N64", "smart_collection_ids": [9]},
            2: {"id": 2, "platform_id": 2, "platform_name": "SNES", "smart_collection_ids": [9]},
        }

        unit = WorkUnit(
            type="collection",
            id=9,
            name="Smart Filter",
            slug="",
            rom_count=2,
            collection_kind="smart",
            member_rom_ids=(1, 2),
            unwalked_platform_ids=(1, 2),
        )
        synced: set[int] = set()
        new_roms, ids, _skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit, synced
        )

        assert [r["id"] for r in new_roms] == [1, 2]
        assert ids == [1, 2]
        # Verify the smart endpoint was the one consulted, not the user/virtual ones.
        method_calls = [c[0] for c in fake_romm_api.call_log]
        assert "list_roms_by_smart_collection" in method_calls
        assert "list_roms_by_collection" not in method_calls
        assert "list_roms_by_virtual_collection" not in method_calls

    @pytest.mark.asyncio
    async def test_dispatches_virtual_collection_to_virtual_endpoint(self, make_library, fake_romm_api):
        """collection_kind='virtual' routes through list_roms_by_virtual_collection."""
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {
            1: {"id": 1, "platform_id": 1, "platform_name": "N64", "virtual_collection_ids": ["100"]},
        }

        unit = WorkUnit(
            type="collection",
            id="100",
            name="Mario",
            slug="",
            rom_count=1,
            collection_kind="virtual",
            member_rom_ids=(1,),
            unwalked_platform_ids=(1,),
        )
        synced: set[int] = set()
        new_roms, _ids, _skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit, synced
        )

        assert [r["id"] for r in new_roms] == [1]
        method_calls = [c[0] for c in fake_romm_api.call_log]
        assert "list_roms_by_virtual_collection" in method_calls
        assert "list_roms_by_smart_collection" not in method_calls


def _user_collection_unit(
    cid="7", *, rom_count=2, updated_at: str | None = "2026-01-01T00:00:00", member_rom_ids: tuple[int, ...] = ()
):
    return WorkUnit(
        type="collection",
        id=cid,
        name="Faves",
        slug="",
        rom_count=rom_count,
        collection_kind="standard",
        collection_updated_at=updated_at,
        member_rom_ids=member_rom_ids,
    )


def _virtual_unit(*, member_rom_ids, cid="fr-1", unwalked_platform_ids=()):
    return WorkUnit(
        type="collection",
        id=cid,
        name="Mario",
        slug="",
        rom_count=len(member_rom_ids),
        collection_kind="virtual",
        member_rom_ids=member_rom_ids,
        unwalked_platform_ids=unwalked_platform_ids,
    )


# A stamp's fetch start, and the probe reference it gives: the fetch start less
# the platform skip's ten-minute margin (#2477 D2).
_FETCH_START = "2026-06-01T00:10:00+00:00"
_REFERENCE = "2026-06-01T00:00:00+00:00"


def _seed_stamp(uow, cid="7", kind="standard", *, member_rom_ids, listing_rom_ids=None, rom_count=None):
    """Seed a stamp as an apply writes it: a fetch start and the checksum of the listing's ids.

    *listing_rom_ids* defaults to *member_rom_ids*: they differ only where a
    walk answered ids beyond the listing.
    """
    _seed_collection_stamp(
        uow,
        cid,
        kind,
        updated_at="" if kind == "virtual" else "2026-01-01T00:00:00",
        completed_at="2026-06-01T00:30:00+00:00",
        rom_count=len(member_rom_ids) if rom_count is None else rom_count,
        member_rom_ids=member_rom_ids,
        fetch_started_at=_FETCH_START,
        listing_rom_ids=member_rom_ids if listing_rom_ids is None else listing_rom_ids,
    )


def _seed_virtual_stamp(uow, *, member_rom_ids, cid="fr-1"):
    _seed_stamp(uow, cid, "virtual", member_rom_ids=member_rom_ids)


async def _skip_members(library, unit):
    """The members the collection skip replays for *unit*, or ``None`` where it fetches."""
    return (await library.sync._collection_fetcher._try_collection_incremental_skip(unit)).member_rom_ids


def _collection_walks(fake_romm_api):
    """Every collection walk in the fake's call log, as ``(method, platform_ids)``."""
    walks = ("list_roms_by_collection", "list_roms_by_smart_collection", "list_roms_by_virtual_collection")
    return [(name, kwargs.get("platform_ids")) for name, _args, kwargs in fake_romm_api.call_log if name in walks]


def _probes(fake_romm_api):
    """The positional arguments of every collection skip probe in the fake's call log."""
    return [args for name, args, _kwargs in fake_romm_api.call_log if name == "list_collection_roms_updated_after"]


class TestTryCollectionIncrementalSkip:
    """Decision tests for _try_collection_incremental_skip() — the collection skip gate (#742, #2477)."""

    @pytest.mark.asyncio
    async def test_skips_when_unchanged(self, make_library, fake_romm_api):
        """Stamp present, updated_at equal, the listing's checksum equal, the scoped probe empty → skip."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11))
        # Members exist but none updated after the reference → probe total 0.
        fake_romm_api.roms = {
            10: {"id": 10, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"},
            11: {"id": 11, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"},
        }
        unit = _user_collection_unit(rom_count=2, member_rom_ids=(11, 10))

        result = await _skip_members(library, unit)

        assert result == [10, 11]
        assert _probes(fake_romm_api) == [(7, "standard", _REFERENCE)]

    @pytest.mark.asyncio
    async def test_the_probe_s_reference_is_the_fetch_start_less_the_margin(self, make_library, fake_romm_api):
        """Not the stamp's completed_at: an edit between the fetch and the final commit is after the reference."""
        library = make_library(romm_api=fake_romm_api)
        _seed_collection_stamp(
            library.uow,
            "7",
            "standard",
            updated_at="2026-01-01T00:00:00",
            completed_at="2026-06-01T05:00:00+00:00",
            rom_count=1,
            member_rom_ids=(10,),
            fetch_started_at="2026-06-01T04:00:00+00:00",
            listing_rom_ids=(10,),
        )
        # Edited after the fetch started, before the run completed.
        fake_romm_api.roms = {10: {"id": 10, "collection_ids": [7], "updated_at": "2026-06-01T04:30:00+00:00"}}

        result = await _skip_members(library, _user_collection_unit(rom_count=1, member_rom_ids=(10,)))

        assert result is None
        assert _probes(fake_romm_api) == [(7, "standard", "2026-06-01T03:50:00+00:00")]

    @pytest.mark.asyncio
    async def test_no_skip_when_no_stamp(self, make_library, fake_romm_api):
        """First-ever sync: no stamp → fetch, no probe."""
        library = make_library(romm_api=fake_romm_api)
        result = await _skip_members(library, _user_collection_unit())
        assert result is None
        assert not [c for c in fake_romm_api.call_log if c[0] == "list_collection_roms_updated_after"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["standard", "smart", "virtual"])
    async def test_a_stamp_without_a_fetch_start_is_fetched_in_full(self, make_library, fake_romm_api, caplog, kind):
        """A stamp written before migration 032 records neither, so it never skips and nothing is probed."""
        library = make_library(romm_api=fake_romm_api)
        cid = "fr-1" if kind == "virtual" else "7"
        _seed_collection_stamp(
            library.uow,
            cid,
            kind,
            updated_at="" if kind == "virtual" else "2026-01-01T00:00:00",
            completed_at="2026-06-01T00:00:00",
            rom_count=2,
            member_rom_ids=(10, 11),
        )
        unit = replace(
            _user_collection_unit(cid, rom_count=2, member_rom_ids=(10, 11)),
            collection_kind=kind,
        )

        with caplog.at_level(logging.INFO):
            result = await _skip_members(library, unit)

        assert result is None
        assert _probes(fake_romm_api) == []
        assert "the stamp records no fetch start or checksum — fetch" in caplog.text

    @pytest.mark.asyncio
    async def test_no_skip_when_updated_at_changed(self, make_library, fake_romm_api):
        """A membership add/remove bumps the collection's updated_at → fetch, no probe."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11))
        unit = _user_collection_unit(rom_count=2, updated_at="2026-02-02T00:00:00", member_rom_ids=(10, 11))  # changed

        result = await _skip_members(library, unit)

        assert result is None
        assert not [c for c in fake_romm_api.call_log if c[0] == "list_collection_roms_updated_after"]

    @pytest.mark.asyncio
    async def test_no_skip_when_member_content_changed(self, make_library, fake_romm_api):
        """A member ROM updated after the reference (scoped probe > 0) → fetch."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11))
        fake_romm_api.roms = {
            10: {"id": 10, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"},
            11: {"id": 11, "collection_ids": [7], "updated_at": "2026-07-01T00:00:00"},  # after the reference
        }
        result = await _skip_members(library, _user_collection_unit(rom_count=2, member_rom_ids=(10, 11)))
        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["standard", "smart"])
    async def test_an_equal_count_change_to_the_listed_members_is_fetched_in_full(
        self, make_library, fake_romm_api, kind
    ):
        """One member hidden by an admin and another unhidden: same count and updated_at, other ids → fetch."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, "7", kind, member_rom_ids=(10, 11))
        unit = replace(_user_collection_unit(rom_count=2, member_rom_ids=(10, 12)), collection_kind=kind)

        result = await _skip_members(library, unit)

        assert result is None
        assert _probes(fake_romm_api) == []

    @pytest.mark.asyncio
    async def test_the_listing_s_rom_count_is_not_compared(self, make_library, fake_romm_api):
        """The listing's ids and updated_at match the stamp: a rom_count unlike the stamp's refuses nothing."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11), rom_count=2)

        result = await _skip_members(library, _user_collection_unit(rom_count=3, member_rom_ids=(10, 11)))

        assert result == [10, 11]

    @pytest.mark.asyncio
    async def test_an_own_smart_collection_whose_walk_returned_more_than_its_listing_skips(
        self, make_library, fake_romm_api
    ):
        """The stamp replays the walk's extra id, while the checksum covers only the listing's ids."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, "9", "smart", member_rom_ids=(20, 21, 22), listing_rom_ids=(20, 21))
        unit = WorkUnit(
            type="collection",
            id="9",
            name="Smart",
            slug="",
            rom_count=2,
            collection_kind="smart",
            collection_updated_at="2026-01-01T00:00:00",
            member_rom_ids=(20, 21),
        )

        result = await _skip_members(library, unit)

        assert result == [20, 21, 22]

    @pytest.mark.asyncio
    async def test_no_skip_when_no_updated_at_on_unit(self, make_library, fake_romm_api):
        """A listing that carried no updated_at (None) can't be compared → fetch."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11))
        unit = _user_collection_unit(rom_count=2, updated_at=None, member_rom_ids=(10, 11))
        result = await _skip_members(library, unit)
        assert result is None

    @pytest.mark.asyncio
    async def test_virtual_skips_when_listed_members_equal_the_stamp_s(self, make_library, fake_romm_api):
        """A virtual collection skips on the listing's checksum and a zero scoped probe."""
        library = make_library(romm_api=fake_romm_api)
        _seed_virtual_stamp(library.uow, member_rom_ids=(10, 11))
        fake_romm_api.roms = {
            10: {"id": 10, "virtual_collection_ids": ["fr-1"], "updated_at": "2026-01-01T00:00:00"},
            11: {"id": 11, "virtual_collection_ids": ["fr-1"], "updated_at": "2026-01-01T00:00:00"},
        }

        result = await _skip_members(library, _virtual_unit(member_rom_ids=(11, 10)))

        assert result == [10, 11]
        assert _probes(fake_romm_api) == [("fr-1", "virtual", _REFERENCE)]

    @pytest.mark.asyncio
    async def test_virtual_fetches_when_listed_members_differ_from_the_stamp_s(self, make_library, fake_romm_api):
        """A member added to or dropped from a virtual collection reaches the listing, not its updated_at."""
        library = make_library(romm_api=fake_romm_api)
        _seed_virtual_stamp(library.uow, member_rom_ids=(10, 11))

        result = await _skip_members(library, _virtual_unit(member_rom_ids=(10, 12)))

        assert result is None
        assert not [c for c in fake_romm_api.call_log if c[0] == "list_collection_roms_updated_after"]

    @pytest.mark.asyncio
    async def test_virtual_fetches_when_a_member_changed_since_the_stamp(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_virtual_stamp(library.uow, member_rom_ids=(10,))
        fake_romm_api.roms = {10: {"id": 10, "virtual_collection_ids": ["fr-1"], "updated_at": "2026-07-01T00:00:00"}}

        result = await _skip_members(library, _virtual_unit(member_rom_ids=(10,)))

        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_on_probe_exception(self, make_library, fake_romm_api):
        """A scoped-probe server error falls open to a fetch (warn + None)."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11))
        fake_romm_api.list_collection_roms_updated_after_side_effect = RuntimeError("probe boom")
        result = await _skip_members(library, _user_collection_unit(rom_count=2, member_rom_ids=(10, 11)))
        assert result is None
        assert len(_probes(fake_romm_api)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", [[], [{"id": 10}], "0", 0])
    async def test_a_probe_answer_that_is_not_a_json_object_is_fetched_in_full(
        self, make_library, fake_romm_api, caplog, answer
    ):
        """An answer that is not a JSON object establishes nothing."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11))
        fake_romm_api.list_collection_roms_updated_after_answer = answer

        with caplog.at_level(logging.INFO):
            result = await _skip_members(library, _user_collection_unit(rom_count=2, member_rom_ids=(10, 11)))

        assert result is None
        assert len(_probes(fake_romm_api)) == 1
        assert "RomM's answer could not be read — fetch" in caplog.text

    @pytest.mark.asyncio
    async def test_smart_collection_routes_smart_param(self, make_library, fake_romm_api):
        """A smart collection probes with kind='smart' and is keyed off smart_collection_ids."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, "9", "smart", member_rom_ids=(20,))
        fake_romm_api.roms = {20: {"id": 20, "smart_collection_ids": [9], "updated_at": "2026-01-01T00:00:00"}}
        unit = WorkUnit(
            type="collection",
            id="9",
            name="Smart",
            slug="",
            rom_count=1,
            collection_kind="smart",
            collection_updated_at="2026-01-01T00:00:00",
            member_rom_ids=(20,),
        )

        result = await _skip_members(library, unit)

        assert result == [20]
        assert _probes(fake_romm_api) == [(9, "smart", _REFERENCE)]


class TestWhetherAFreshStampSettlesTheRefusal:
    """The gate says whether a stamp the apply writes settles its refusal, which the preview counts by (#2477 D3)."""

    @staticmethod
    async def _skip(library, unit):
        return await library.sync._collection_fetcher._try_collection_incremental_skip(unit)

    @pytest.mark.asyncio
    async def test_no_stamp_is_settled_by_one(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)

        skip = await self._skip(library, _user_collection_unit(member_rom_ids=(10, 11)))

        assert (skip.member_rom_ids, skip.restamp_settles) == (None, True)

    @pytest.mark.asyncio
    async def test_a_stamp_without_a_fetch_start_is_settled_by_a_fresh_one(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_collection_stamp(
            library.uow,
            "7",
            "standard",
            updated_at="2026-01-01T00:00:00",
            completed_at="2026-06-01T00:00:00",
            rom_count=2,
            member_rom_ids=(10, 11),
        )

        skip = await self._skip(library, _user_collection_unit(member_rom_ids=(10, 11)))

        assert (skip.member_rom_ids, skip.restamp_settles) == (None, True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("updated_at", "listed"),
        [("2026-02-02T00:00:00", (10, 11)), ("2026-01-01T00:00:00", (10, 12))],
        ids=["updated_at", "listed ids"],
    )
    async def test_a_listing_unlike_the_stamp_is_settled_by_a_fresh_one(
        self, make_library, fake_romm_api, updated_at, listed
    ):
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10, 11))

        skip = await self._skip(library, _user_collection_unit(updated_at=updated_at, member_rom_ids=listed))

        assert (skip.member_rom_ids, skip.restamp_settles) == (None, True)

    @pytest.mark.asyncio
    async def test_a_member_updated_after_the_reference_is_settled_by_a_fresh_stamp(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10,))
        fake_romm_api.roms = {10: {"id": 10, "collection_ids": [7], "updated_at": "2026-07-01T00:00:00"}}

        skip = await self._skip(library, _user_collection_unit(member_rom_ids=(10,)))

        assert (skip.member_rom_ids, skip.restamp_settles) == (None, True)

    @pytest.mark.asyncio
    async def test_a_probe_that_raised_is_not_settled_by_a_fresh_stamp(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10,))
        fake_romm_api.list_collection_roms_updated_after_side_effect = RuntimeError("probe boom")

        skip = await self._skip(library, _user_collection_unit(member_rom_ids=(10,)))

        assert (skip.member_rom_ids, skip.restamp_settles) == (None, False)

    @pytest.mark.asyncio
    async def test_a_probe_answer_that_could_not_be_read_is_not_settled_by_a_fresh_stamp(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10,))
        fake_romm_api.list_collection_roms_updated_after_answer = []

        skip = await self._skip(library, _user_collection_unit(member_rom_ids=(10,)))

        assert (skip.member_rom_ids, skip.restamp_settles) == (None, False)

    @pytest.mark.asyncio
    async def test_a_skip_asks_for_no_stamp(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10,))

        skip = await self._skip(library, _user_collection_unit(member_rom_ids=(10,)))

        assert (skip.member_rom_ids, skip.restamp_settles) == ([10], False)

    @pytest.mark.asyncio
    async def test_the_unit_fetch_hands_the_answer_on(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10,))
        fake_romm_api.list_collection_roms_updated_after_side_effect = RuntimeError("probe boom")

        *_rest, skipped, restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            _user_collection_unit(member_rom_ids=(10,)), {10}
        )

        assert (skipped, restamp_settles) == (False, False)


class TestFetchCollectionUnitSkip:
    """End-to-end fetch_collection_unit() over the #742 skip — reconstruction + accounting."""

    @pytest.mark.asyncio
    async def test_skip_reconstructs_uncovered_members_and_marks_synced(self, make_library, fake_romm_api):
        """A skipped collection reconstructs its not-yet-covered bound members and adds
        every member to synced_rom_ids, without paginating."""
        library = make_library(romm_api=fake_romm_api)
        uow = library.uow
        _seed_stamp(uow, member_rom_ids=(10, 11))
        # Member 10 is on a platform NOT fetched this run (bound in the registry);
        # member 11 was already covered by a platform unit (in synced_rom_ids).
        _seed_persisted_rom(uow, 10, app_id=1010, group_key="igdb:10:1", platform_slug="gba")
        # No live members updated after the stamp → probe total 0.
        fake_romm_api.roms = {
            10: {"id": 10, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"},
            11: {"id": 11, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"},
        }
        synced = {11}
        unit = _user_collection_unit(rom_count=2, member_rom_ids=(10, 11))

        new_roms, all_ids, skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit, synced
        )

        assert skipped is True
        assert all_ids == [10, 11]
        assert synced == {10, 11}  # every member marked covered for stale cleanup
        # Only the not-already-synced bound member is reconstructed as a new row.
        assert [r["id"] for r in new_roms] == [10]
        assert new_roms[0]["platform_slug"] == "gba"
        assert new_roms[0]["sibling_group_key"] == "igdb:10:1"
        # No pagination endpoint was consulted.
        assert not [c for c in fake_romm_api.call_log if c[0] == "list_roms_by_collection"]

    @pytest.mark.asyncio
    async def test_skip_keeps_what_the_last_sync_stored_of_whether_romm_has_a_file(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        uow = library.uow
        _seed_stamp(uow, member_rom_ids=(10,))
        _seed_persisted_rom(uow, 10, app_id=1010, group_key="igdb:10:1", platform_slug="gba", has_file_on_disk=False)
        fake_romm_api.roms = {10: {"id": 10, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"}}
        unit = _user_collection_unit(rom_count=1, member_rom_ids=(10,))

        new_roms, _all_ids, skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit, set()
        )

        assert skipped is True
        assert [(r["id"], r["has_file_on_disk"]) for r in new_roms] == [(10, False)]

    @pytest.mark.asyncio
    async def test_skip_omits_unbound_member_from_reconstruction(self, make_library, fake_romm_api):
        """An unbound / absent member is not reconstructed (it is a sibling handled at
        finalize, not a shortcut to rebuild), but is still marked synced."""
        library = make_library(romm_api=fake_romm_api)
        uow = library.uow
        _seed_stamp(uow, member_rom_ids=(10, 12))
        _seed_persisted_rom(uow, 10, app_id=1010, group_key="igdb:10:1", platform_slug="gba")
        _seed_persisted_rom(uow, 12, app_id=None, group_key="igdb:10:1", platform_slug="gba")  # unbound sibling
        fake_romm_api.roms = {
            10: {"id": 10, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"},
            12: {"id": 12, "collection_ids": [7], "updated_at": "2026-01-01T00:00:00"},
        }
        synced: set[int] = set()

        new_roms, all_ids, skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit=_user_collection_unit(rom_count=2, member_rom_ids=(10, 12)), synced_rom_ids=synced
        )

        assert skipped is True
        assert [r["id"] for r in new_roms] == [10]  # 12 omitted (unbound)
        assert all_ids == [10, 12]
        assert synced == {10, 12}

    @pytest.mark.asyncio
    async def test_changed_collection_paginates_normally(self, make_library, fake_romm_api):
        """A changed collection (updated_at differs) falls through to the walk."""
        library = make_library(romm_api=fake_romm_api)
        _seed_stamp(library.uow, member_rom_ids=(10,))
        fake_romm_api.roms = {
            10: {"id": 10, "collection_ids": [7], "platform_id": 2, "platform_name": "GBA", "platform_slug": "gba"},
            11: {"id": 11, "collection_ids": [7], "platform_id": 2, "platform_name": "GBA", "platform_slug": "gba"},
        }
        unit = replace(
            _user_collection_unit(rom_count=2, updated_at="2026-05-05T00:00:00"),  # changed
            member_rom_ids=(10, 11),
            unwalked_platform_ids=(2,),
        )
        synced: set[int] = set()

        new_roms, all_ids, skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit, synced
        )

        assert skipped is False
        assert [r["id"] for r in new_roms] == [10, 11]  # the walk returns the live members
        assert all_ids == [10, 11]
        assert [c for c in fake_romm_api.call_log if c[0] == "list_roms_by_collection"]


class TestFetchCollectionUnitVirtualSkip:
    """A virtual collection takes the same skip as the other kinds, end to end."""

    @pytest.mark.asyncio
    async def test_an_unchanged_virtual_collection_is_skipped_without_a_walk(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_virtual_stamp(library.uow, member_rom_ids=(10, 11))
        fake_romm_api.roms = {
            10: {"id": 10, "platform_id": 2, "virtual_collection_ids": ["fr-1"], "updated_at": "2026-01-01T00:00:00"},
            11: {"id": 11, "platform_id": 2, "virtual_collection_ids": ["fr-1"], "updated_at": "2026-01-01T00:00:00"},
        }
        synced: set[int] = set()

        _new_roms, all_ids, skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            _virtual_unit(member_rom_ids=(10, 11), unwalked_platform_ids=(2,)), synced
        )

        assert skipped is True
        assert all_ids == [10, 11]
        assert synced == {10, 11}
        assert _collection_walks(fake_romm_api) == []

    @pytest.mark.asyncio
    async def test_a_virtual_collection_whose_members_changed_is_walked(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_virtual_stamp(library.uow, member_rom_ids=(10,))
        fake_romm_api.roms = {
            10: {"id": 10, "platform_id": 2, "virtual_collection_ids": ["fr-1"]},
            11: {"id": 11, "platform_id": 2, "virtual_collection_ids": ["fr-1"]},
        }

        new_roms, all_ids, skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            _virtual_unit(member_rom_ids=(10, 11), unwalked_platform_ids=(2,)), set()
        )

        assert skipped is False
        assert [r["id"] for r in new_roms] == [10, 11]
        assert all_ids == [10, 11]
        assert _collection_walks(fake_romm_api) == [("list_roms_by_virtual_collection", (2,))]


def _standard_unit(*, member_rom_ids, unwalked_platform_ids):
    return WorkUnit(
        type="collection",
        id="7",
        name="Faves",
        slug="",
        rom_count=len(member_rom_ids),
        collection_kind="standard",
        member_rom_ids=member_rom_ids,
        unwalked_platform_ids=unwalked_platform_ids,
    )


def _member(rom_id, platform_id, **membership):
    return {"id": rom_id, "platform_id": platform_id, "platform_name": f"P{platform_id}", **membership}


class TestCollectionMembersFromTheListing:
    """A collection unit fetches only the listed members no unit delivered (#2356)."""

    @pytest.mark.asyncio
    async def test_members_all_delivered_make_no_request(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {1: _member(1, 1, collection_ids=[7]), 2: _member(2, 1, collection_ids=[7])}
        synced = {1, 2}

        new_roms, all_ids, skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            _standard_unit(member_rom_ids=(1, 2), unwalked_platform_ids=(3,)), synced
        )

        assert fake_romm_api.call_log == []
        assert (new_roms, all_ids, skipped) == ([], [1, 2], False)
        assert synced == {1, 2}

    @pytest.mark.asyncio
    async def test_leftovers_are_fetched_by_one_walk_narrowed_to_the_platforms_left_off(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {
            1: _member(1, 1, collection_ids=[7]),
            2: _member(2, 2, collection_ids=[7]),
            3: _member(3, 3, collection_ids=[7]),
        }
        synced = {1}

        new_roms, all_ids, _skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            _standard_unit(member_rom_ids=(1, 2, 3), unwalked_platform_ids=(2, 3)), synced
        )

        assert _collection_walks(fake_romm_api) == [("list_roms_by_collection", (2, 3))]
        assert [r["id"] for r in new_roms] == [2, 3]
        assert {r["platform_name"] for r in new_roms} == {"P2", "P3"}
        assert all_ids == [1, 2, 3]
        assert synced == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_a_listed_member_the_walk_did_not_answer_is_logged_and_left_out(
        self, make_library, fake_romm_api, caplog
    ):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {2: _member(2, 2, collection_ids=[7])}
        synced: set[int] = set()

        with caplog.at_level(logging.INFO):
            (
                new_roms,
                all_ids,
                _skipped,
                _restamp_settles,
            ) = await library.sync._collection_fetcher.fetch_collection_unit(
                _standard_unit(member_rom_ids=(2, 9), unwalked_platform_ids=(2,)), synced
            )

        assert [r["id"] for r in new_roms] == [2]
        assert all_ids == [2, 9], "the membership map is the listing's ids"
        assert 9 not in synced
        assert "Per-unit fetch Faves: 1 listed member(s) no unit delivered — left out of this run" in caplog.messages

    @pytest.mark.asyncio
    async def test_leftovers_with_no_platform_left_off_make_no_request(self, make_library, fake_romm_api, caplog):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {2: _member(2, 1, collection_ids=[7])}

        with caplog.at_level(logging.INFO):
            (
                new_roms,
                all_ids,
                _skipped,
                _restamp_settles,
            ) = await library.sync._collection_fetcher.fetch_collection_unit(
                _standard_unit(member_rom_ids=(1, 2), unwalked_platform_ids=()), {1}
            )

        assert fake_romm_api.call_log == []
        assert (new_roms, all_ids) == ([], [1, 2])
        assert "Per-unit fetch Faves: 1 listed member(s) no unit delivered — left out of this run" in caplog.messages

    @pytest.mark.asyncio
    async def test_a_walk_answer_beyond_the_listing_is_taken(self, make_library, fake_romm_api):
        """An own smart collection's live walk may hold a member its stored listing does not yet."""
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {
            1: _member(1, 2, smart_collection_ids=[9]),
            2: _member(2, 2, smart_collection_ids=[9]),
        }
        unit = WorkUnit(
            type="collection",
            id="9",
            name="Recent",
            slug="",
            rom_count=1,
            collection_kind="smart",
            member_rom_ids=(1,),
            unwalked_platform_ids=(2,),
        )

        new_roms, all_ids, _skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit, set()
        )

        assert [r["id"] for r in new_roms] == [1, 2]
        assert all_ids == [1, 2]


def _own_smart_unit(*, member_rom_ids, unwalked_platform_ids):
    return WorkUnit(
        type="collection",
        id="9",
        name="Recent",
        slug="",
        rom_count=len(member_rom_ids),
        collection_kind="smart",
        member_rom_ids=member_rom_ids,
        unwalked_platform_ids=unwalked_platform_ids,
    )


class TestAnOwnSmartCollectionFollowsTheLiveView:
    """An own smart collection walks the platforms the sync leaves off even with no leftover (D5).

    Its listing is RomM's stored set, which a failed refresh can leave behind the
    live criteria; the narrowed walk answers live.
    """

    @pytest.mark.asyncio
    async def test_with_every_listed_member_covered_it_still_walks_the_platforms_left_off(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {
            1: _member(1, 1, smart_collection_ids=[9]),
            2: _member(2, 2, smart_collection_ids=[9]),
        }
        synced = {1}

        new_roms, all_ids, _skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            _own_smart_unit(member_rom_ids=(1,), unwalked_platform_ids=(2,)), synced
        )

        assert _collection_walks(fake_romm_api) == [("list_roms_by_smart_collection", (2,))]
        assert [r["id"] for r in new_roms] == [2]
        assert all_ids == [1, 2]
        assert synced == {1, 2}

    @pytest.mark.asyncio
    async def test_with_no_platform_left_off_it_makes_no_request(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {1: _member(1, 1, smart_collection_ids=[9])}

        new_roms, all_ids, _skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            _own_smart_unit(member_rom_ids=(1,), unwalked_platform_ids=()), {1}
        )

        assert fake_romm_api.call_log == []
        assert (new_roms, all_ids) == ([], [1])


class TestSmartCollectionOwnership:
    """A foreign smart collection walks whole; an own one takes the narrowed walk (D4)."""

    @staticmethod
    def _seed(library, fake_romm_api, *, romm_user_id):
        fake_romm_api.platforms = [
            {"id": 1, "name": "N64", "slug": "n64", "rom_count": 1},
            {"id": 2, "name": "GBA", "slug": "gba", "rom_count": 1},
        ]
        fake_romm_api.roms = {
            1: _member(1, 1, smart_collection_ids=[9]),
            2: _member(2, 2, smart_collection_ids=[9]),
        }
        fake_romm_api.smart_collections = [{"id": 9, "name": "Played", "rom_count": 2, "user_id": 5}]
        library.settings["enabled_platforms"] = {"1": True, "2": False}
        library.settings["enabled_collections"] = {"standard": {}, "smart": {"9": True}, "virtual": {}}
        library.settings["collection_owner_scope"] = "all"
        if romm_user_id is None:
            library.settings.pop("romm_user_id", None)
        else:
            library.settings["romm_user_id"] = romm_user_id

    async def _fetch_the_collection(self, library, fake_romm_api):
        units = await library.sync._fetcher.build_work_queue()
        [unit] = [u for u in units if u.type == "collection"]
        fake_romm_api.call_log.clear()
        new_roms, _all_ids, _skipped, _restamp_settles = await library.sync._collection_fetcher.fetch_collection_unit(
            unit, {1}
        )
        return new_roms

    @pytest.mark.asyncio
    async def test_a_foreign_smart_collection_walks_every_page(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, romm_user_id=3)

        new_roms = await self._fetch_the_collection(library, fake_romm_api)

        assert _collection_walks(fake_romm_api) == [("list_roms_by_smart_collection", None)]
        assert [r["id"] for r in new_roms] == [2]

    @pytest.mark.asyncio
    async def test_an_own_smart_collection_takes_the_narrowed_walk(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, romm_user_id=5)

        new_roms = await self._fetch_the_collection(library, fake_romm_api)

        assert _collection_walks(fake_romm_api) == [("list_roms_by_smart_collection", (2,))]
        assert [r["id"] for r in new_roms] == [2]

    @pytest.mark.asyncio
    async def test_with_identity_unknown_a_smart_collection_takes_the_narrowed_walk(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, romm_user_id=None)

        await self._fetch_the_collection(library, fake_romm_api)

        assert _collection_walks(fake_romm_api) == [("list_roms_by_smart_collection", (2,))]


class TestFetchProgressNarration:
    """Per-page ``fetching`` progress frames during a collection unit's walk (#1025)."""

    @pytest.mark.asyncio
    async def test_collection_fetch_emits_per_page_fetching_frames(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        # 1200 ROMs in collection 7 → 3 pages (500 + 500 + 200) at the 500-ROM
        # page size; every page emits a frame (interval 1).
        rom_count = 1200
        fake_romm_api.roms = {
            i: {"id": i, "platform_id": 1, "name": f"G{i}", "collection_ids": [7]} for i in range(rom_count)
        }

        unit = WorkUnit(
            type="collection",
            id=7,
            name="Favorites",
            slug="",
            rom_count=rom_count,
            collection_kind="standard",
            member_rom_ids=tuple(range(rom_count)),
            unwalked_platform_ids=(1,),
        )
        await library.sync._collection_fetcher.fetch_collection_unit(
            unit, set(), progress_step=2, progress_total_steps=8
        )

        frames = _fetching_frames(emit)
        assert [f["current"] for f in frames] == [1, 2, 3]
        assert frames[0]["message"] == "Fetching Favorites (page 1/3)"
        assert frames[0]["step"] == 2
        assert frames[0]["totalSteps"] == 8
        # A collection fetch narrates under the same ``fetch`` sub-stage (#1407).
        assert all(f["subStage"] == "fetch" for f in frames)
