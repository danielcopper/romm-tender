"""Contract test for the Data Management page's inventory read.

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``getDataInventory = callable<[], DataInventory>``, taking no arguments and
answering the four population figures the page draws its rows from, the
recovery root, and one entry per counted bundle.

What this tier adds over ``tests/services/test_data_inventory.py`` is the
composition: the real ``bootstrap()`` hands the service the real SQLite Unit of
Work and the real ``RecoveryBundleAdapter``, so the size a seeded row carries
and the bytes a sealed bundle takes are read the way the page reads them.
"""

from __future__ import annotations

from ._seed import seed_install, seed_rom


async def test_an_untouched_install_reports_every_population_as_empty(harness):
    result = await harness.plugin.get_data_inventory()

    assert result == {
        "installed_roms": 0,
        "installed_bytes": 0,
        "recovery_bundles": 0,
        "recovery_bytes": 0,
        "recovery_root": harness.plugin._data_inventory_service._recovery_inventory.root(),
        "recovery_bundle_list": [],
    }
    # The real adapter's root, derived from the package name rather than spelled
    # by either side of the wire.
    assert result["recovery_root"].endswith("-recovery")


async def test_it_counts_the_installed_rows_and_sums_the_size_romm_reported(harness):
    seed_install(harness, 1, file_name="one.gba")
    seed_install(harness, 2, file_name="two.gba")
    # A synced ROM nobody downloaded belongs to neither figure.
    seed_rom(harness, 3)
    with harness.uow_factory() as uow:
        uow.roms.set_fs_size_bytes(1, 4_000_000)
        uow.roms.set_fs_size_bytes(2, 2_500_000)
        uow.roms.set_fs_size_bytes(3, 900_000_000)

    result = await harness.plugin.get_data_inventory()

    assert result["installed_roms"] == 2
    assert result["installed_bytes"] == 6_500_000


async def test_an_installed_row_whose_size_romm_never_reported_counts_and_adds_nothing(harness):
    seed_install(harness, 1, file_name="sized.gba")
    seed_install(harness, 2, file_name="unsized.gba")
    with harness.uow_factory() as uow:
        uow.roms.set_fs_size_bytes(1, 1_024)

    result = await harness.plugin.get_data_inventory()

    assert result["installed_roms"] == 2
    assert result["installed_bytes"] == 1_024


async def test_a_sealed_bundle_is_counted_and_measured_on_disk(harness):
    source_root = harness.tmp_path / "sources"
    source_root.mkdir()
    (source_root / "rom.gba").write_bytes(b"r" * 2048)
    # The very adapter the read is wired to, so the bundle it counts is the
    # bundle this test sealed.
    recovery = harness.plugin._data_inventory_service._recovery_inventory
    sealed = recovery.seal_bundle(
        "Game_2026-07-24_abc123",
        {"roms": [], "installs": [], "metadata": [], "save_sync": [], "playtime": [], "warnings": []},
        [
            {
                "source_path": str(source_root / "rom.gba"),
                "safe_root": str(source_root),
                "kind": "rom",
                "rom_id": 1,
            }
        ],
        {
            "bundle_id": "Game_2026-07-24_abc123",
            "created_at": "2026-07-24T12:00:00+00:00",
            "games": [],
            "playtime_lines": [],
        },
        "none\n",
    )

    result = await harness.plugin.get_data_inventory()

    assert result["recovery_bundles"] == 1
    # The bundle holds the copied ROM plus its own seal, checksum and README,
    # so the total is bounded below by the bytes that were copied into it.
    assert result["recovery_bytes"] > 2048
    assert result["recovery_bundle_list"] == [
        {"name": "Game", "day": "2026-07-24", "bytes": result["recovery_bytes"]},
    ]
    assert recovery.validate_sources(sealed) is True
