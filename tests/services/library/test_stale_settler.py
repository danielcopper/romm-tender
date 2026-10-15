"""Tests for services/library/stale_settler.py — which stale rows a run removes."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.sync_diff import StaleRow
from services.library.stale_settler import StaleSettler, StaleSettlerConfig


def _settler(*, answers=None, kept_names=None) -> tuple[StaleSettler, AsyncMock, MagicMock]:
    fetcher = MagicMock()
    fetcher.ask_romm_for_roms = AsyncMock(return_value=answers or {})
    reader = MagicMock()
    reader.do_read_kept_platform_names = MagicMock(return_value=kept_names or {})
    settler = StaleSettler(
        config=StaleSettlerConfig(
            loop=asyncio.get_running_loop(),
            logger=logging.getLogger("test_stale_settler"),
            fetcher=fetcher,
            local_library_reader=reader,
        )
    )
    return settler, fetcher.ask_romm_for_roms, reader.do_read_kept_platform_names


class TestSettle:
    @pytest.mark.asyncio
    async def test_a_row_reached_through_a_collection_is_removed_only_where_romm_returns_it(self):
        settler, ask, _read = _settler(answers={1: "present", 2: "gone"})
        rows = [StaleRow(1, 1001, "gba", "collection"), StaleRow(2, 1002, "gba", "collection")]

        remove, _kept = await settler.settle(
            rows, read_platform_slugs=set(), listed_platform_slugs={"gba"}, collection_listed_rom_ids=set()
        )

        assert [row.rom_id for row in remove] == [1]
        ask.assert_awaited_once_with([1, 2])

    @pytest.mark.asyncio
    async def test_romm_is_not_asked_when_no_row_needs_it(self):
        settler, ask, _read = _settler()
        rows = [StaleRow(1, 1001, "n64", "platform")]

        remove, kept = await settler.settle(
            rows, read_platform_slugs={"n64"}, listed_platform_slugs={"n64"}, collection_listed_rom_ids=set()
        )

        assert (remove, [row.rom_id for row in kept]) == ([], [1])
        ask.assert_not_awaited()


class TestWithKeptPlatformNames:
    @pytest.mark.asyncio
    async def test_a_kept_row_outside_the_live_names_takes_its_cached_name(self):
        settler, _ask, read = _settler(kept_names={"gba": "Game Boy Advance"})

        names = await settler.with_kept_platform_names([StaleRow(1, 1001, "gba")], {"n64": "Nintendo 64"})

        assert names == {"gba": "Game Boy Advance", "n64": "Nintendo 64"}
        read.assert_called_once_with({"gba"})

    @pytest.mark.asyncio
    async def test_nothing_is_read_when_every_kept_row_has_a_live_name(self):
        settler, _ask, read = _settler()

        names = await settler.with_kept_platform_names([StaleRow(1, 1001, "n64")], {"n64": "Nintendo 64"})

        assert names == {"n64": "Nintendo 64"}
        read.assert_not_called()
