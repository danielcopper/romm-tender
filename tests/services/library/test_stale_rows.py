"""Tests for services/library/_stale_rows.py — which stale rows a run removes, and kept rows' platform names."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from domain.sync_diff import StaleRow
from services.library._stale_rows import settle_stale_rows, with_kept_platform_names

_LOGGER = logging.getLogger("test_stale_rows")


class TestSettleStaleRows:
    @pytest.mark.asyncio
    async def test_a_row_reached_through_a_collection_is_removed_only_where_romm_returns_it(self):
        ask = AsyncMock(return_value={1: "present", 2: "gone"})
        rows = [StaleRow(1, 1001, "gba", "collection"), StaleRow(2, 1002, "gba", "collection")]

        remove, kept, _marks = await settle_stale_rows(
            rows,
            ask_romm=ask,
            logger=_LOGGER,
            read_platform_slugs=set(),
            listed_platform_slugs={"gba"},
            collection_listed_rom_ids=set(),
            synced_group_keys=frozenset(),
            synced_sibling_rom_ids=frozenset(),
        )

        assert [row.rom_id for row in remove] == [1]
        assert kept == [], (
            "a row RomM did not return is in neither list: being kept does not file it in its platform's collection"
        )
        ask.assert_awaited_once_with([1, 2])

    @pytest.mark.asyncio
    async def test_romm_is_not_asked_when_no_row_needs_it(self):
        ask = AsyncMock(return_value={})
        rows = [StaleRow(1, 1001, "n64", "platform")]

        remove, kept, _marks = await settle_stale_rows(
            rows,
            ask_romm=ask,
            logger=_LOGGER,
            read_platform_slugs={"n64"},
            listed_platform_slugs={"n64"},
            collection_listed_rom_ids=set(),
            synced_group_keys=frozenset(),
            synced_sibling_rom_ids=frozenset(),
        )

        assert (remove, [row.rom_id for row in kept]) == ([], [1])
        ask.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_row_whose_game_the_run_synced_is_asked_where_another_would_be_and_never_removed(self):
        ask = AsyncMock(return_value={1: "present", 3: "gone"})
        rows = [
            StaleRow(1, 1001, "gba", "collection", "igdb:1:1"),
            StaleRow(2, 1002, "gba", None, "igdb:1:1"),
            StaleRow(3, 1003, "gba", "collection", "igdb:9:1"),
        ]

        remove, kept, marks = await settle_stale_rows(
            rows,
            ask_romm=ask,
            logger=_LOGGER,
            read_platform_slugs=set(),
            listed_platform_slugs={"gba"},
            collection_listed_rom_ids=set(),
            synced_group_keys=frozenset({"igdb:1:1"}),
            synced_sibling_rom_ids=frozenset({3}),
        )

        assert (remove, kept, marks) == ([], [], {1: None, 3: "not_found"})
        ask.assert_awaited_once_with([1, 3])


class TestWithKeptPlatformNames:
    @pytest.mark.asyncio
    async def test_a_kept_row_outside_the_live_names_takes_its_cached_name(self):
        read = AsyncMock(return_value={"gba": "Game Boy Advance"})

        names = await with_kept_platform_names([StaleRow(1, 1001, "gba")], {"n64": "Nintendo 64"}, read_kept_names=read)

        assert names == {"gba": "Game Boy Advance", "n64": "Nintendo 64"}
        read.assert_awaited_once_with({"gba"})

    @pytest.mark.asyncio
    async def test_nothing_is_read_when_every_kept_row_has_a_live_name(self):
        read = AsyncMock(return_value={})

        names = await with_kept_platform_names([StaleRow(1, 1001, "n64")], {"n64": "Nintendo 64"}, read_kept_names=read)

        assert names == {"n64": "Nintendo 64"}
        read.assert_not_awaited()
