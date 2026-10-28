"""Tests for services/library/_followed_keys.py — the followed keys laid over a registry and written by a commit."""

from __future__ import annotations

from fakes.fake_unit_of_work import FakeUnitOfWork

from domain.rom import Rom
from services.library._followed_keys import record_followed_keys, with_followed_keys


def _stored(uow: FakeUnitOfWork, rom_id: int, key: str, app_id: int | None) -> None:
    with uow:
        uow.roms.save(
            Rom(
                rom_id=rom_id,
                platform_slug="gba",
                name=f"Golf {rom_id}",
                fs_name=f"golf-{rom_id}.zip",
                shortcut_app_id=app_id,
                last_synced_at="2025-01-01T00:00:00+00:00",
                sibling_group_key=key,
                last_fetch_id="run-1",
            )
        )


class TestWithFollowedKeys:
    def test_a_followed_row_carries_its_new_key_and_the_rest_stay(self):
        registry = {
            "30": {"app_id": 6030, "sibling_group_key": "igdb:300:2"},
            "35": {"app_id": 6035, "sibling_group_key": "ss:8:2"},
        }

        assert with_followed_keys(registry, {30: "ss:7:2"}) == {
            "30": {"app_id": 6030, "sibling_group_key": "ss:7:2"},
            "35": {"app_id": 6035, "sibling_group_key": "ss:8:2"},
        }
        assert registry["30"]["sibling_group_key"] == "igdb:300:2"


class TestRecordFollowedKeys:
    def test_each_stored_row_takes_its_key_and_keeps_the_rest_of_its_row(self):
        uow = FakeUnitOfWork()
        _stored(uow, 30, "igdb:300:2", 6030)

        with uow:
            record_followed_keys(uow, {30: "ss:7:2"})

        with uow:
            row = uow.roms.get(30)
        assert row is not None
        assert (row.sibling_group_key, row.shortcut_app_id, row.last_fetch_id) == ("ss:7:2", 6030, "run-1")

    def test_a_row_no_longer_stored_is_skipped(self):
        uow = FakeUnitOfWork()
        _stored(uow, 31, "igdb:300:2", None)

        with uow:
            record_followed_keys(uow, {30: "ss:7:2", 31: "ss:7:2"})

        with uow:
            gone, kept = uow.roms.get(30), uow.roms.get(31)
        assert (gone, kept and kept.sibling_group_key) == (None, "ss:7:2")
