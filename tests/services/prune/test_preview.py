from __future__ import annotations

from typing import Any, cast

from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.platform_sync_state import PlatformSyncState
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.version_metadata import VersionMetadata
from services.prune.preview import PreviewBuilder, PreviewBuilderConfig


class _Recovery:
    def free_bytes(self) -> int:
        return 1000

    def root(self) -> str:
        return "/recovery"


class _Paths:
    def roms_path(self) -> str:
        return "/roms"


def _rom(rom_id: int, *, name: str, group: str, fetch_id: str | None) -> Rom:
    row = Rom.synced(
        rom_id=rom_id,
        platform_slug="dc",
        name=name,
        fs_name=f"{name}.chd",
        shortcut_app_id=None,
        synced_at="now",
        version=VersionMetadata(sibling_group_key=group),
    )
    if fetch_id is not None:
        row.record_fetch_generation(fetch_id)
    return row


def _builder(uow: FakeUnitOfWork) -> PreviewBuilder:
    return PreviewBuilder(
        config=PreviewBuilderConfig(
            uow_factory=FakeUnitOfWorkFactory(uow),
            recovery_store=cast("Any", _Recovery()),
            retrodeck_paths=cast("Any", _Paths()),
            settings={},
        )
    )


def _two_groups() -> FakeUnitOfWork:
    """Two sibling groups, each a dropped row beside a generation-current one."""
    uow = FakeUnitOfWork()
    with uow:
        uow.roms.save(_rom(4375, name="Game A", group="igdb:1217:53", fetch_id="old"))
        uow.roms.save(_rom(25135, name="Game A", group="igdb:1217:53", fetch_id="current"))
        uow.roms.save(_rom(4376, name="Game B", group="igdb:1218:53", fetch_id="old"))
        uow.roms.save(_rom(25136, name="Game B", group="igdb:1218:53", fetch_id="current"))
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(platform_slug="dc", at="now", rom_count=2, fetch_id="current")
        )
    return uow


def test_page_counts_only_removable_rows_as_candidates() -> None:
    builder = _builder(_two_groups())

    preview = builder.build("preview", "bulk", None)
    page = builder.page(preview, 0, 50)

    # Every group member stays disclosed — a fresh probe, not the local fetch
    # generation, decides whole-game removal, so a generation-current row can
    # still be taken and may never be deleted unseen.
    assert page["total"] == 4
    # ...but only the dropped rows are what this run can remove on its own.
    assert page["candidate_total"] == 2
    assert {item["rom_id"] for item in page["items"] if item["candidate"]} == {4375, 4376}
    assert {item["rom_id"] for item in page["items"] if not item["candidate"]} == {25135, 25136}


def test_a_platform_whose_skip_was_revoked_still_discovers_its_dropped_rows() -> None:
    """A platform turned off, or one whose shortcuts were removed, still gets its removed games found."""
    uow = _two_groups()
    with uow:
        uow.platform_sync_state.revoke_skip("dc")
    builder = _builder(uow)

    preview = builder.build("preview", "bulk", None)
    page = builder.page(preview, 0, 50)

    assert page["candidate_total"] == 2
    assert {item["rom_id"] for item in page["items"] if item["candidate"]} == {4375, 4376}


def test_page_orders_candidates_ahead_of_disclosed_siblings() -> None:
    builder = _builder(_two_groups())

    preview = builder.build("preview", "bulk", None)
    page = builder.page(preview, 0, 50)

    assert [item["rom_id"] for item in page["items"]] == [4375, 4376, 25135, 25136]


def test_page_reports_a_candidate_total_the_first_window_cannot_see() -> None:
    builder = _builder(_two_groups())

    preview = builder.build("preview", "bulk", None)
    first = builder.page(preview, 0, 1)

    # The headline count must be right before the list has been paged through.
    assert len(first["items"]) == 1
    assert (first["total"], first["candidate_total"]) == (4, 2)


def test_empty_page_still_carries_both_counts() -> None:
    builder = _builder(_two_groups())

    preview = builder.build("preview", "bulk", None)
    refreshed = builder.page(preview, 0, 0)

    # The free-space refresh asks for limit=0 and must not blank the counts.
    assert refreshed["items"] == []
    assert (refreshed["total"], refreshed["candidate_total"]) == (4, 2)


def test_a_row_carries_its_text_whole_and_no_truncation_flag() -> None:
    long = 10_000
    uow = FakeUnitOfWork()
    with uow:
        row = Rom.synced(
            rom_id=1,
            platform_slug="dc",
            name="n" * long,
            fs_name="f" * long,
            shortcut_app_id=None,
            synced_at="now",
            version=VersionMetadata(sibling_group_key="g" * long),
        )
        row.record_fetch_generation("old")
        uow.roms.save(row)
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=1,
                file_path="/roms/dc/1.chd",
                rom_dir=None,
                platform_slug="dc",
                system="dc",
                installed_at="now",
            )
        )
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(platform_slug="dc", at="now", rom_count=1, fetch_id="current")
        )

    class _UnmeasurableRecovery(_Recovery):
        def measure_path(self, path: str, roms_root: str) -> int:
            raise OSError("w" * long)

    builder = PreviewBuilder(
        config=PreviewBuilderConfig(
            uow_factory=FakeUnitOfWorkFactory(uow),
            recovery_store=cast("Any", _UnmeasurableRecovery()),
            retrodeck_paths=cast("Any", _Paths()),
            settings={},
        )
    )

    page = builder.page(builder.build("preview", "bulk", None), 0, 50)

    [item] = page["items"]
    assert (item["name"], item["fs_name"], item["group_id"], item["warning"]) == (
        "n" * long,
        "f" * long,
        "g" * long,
        "w" * long,
    )
    assert [key for key in item if key.endswith("_truncated")] == []
