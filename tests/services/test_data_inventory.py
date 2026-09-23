"""Tests for DataInventoryService."""

# conftest.py patches decky before this import
import decky  # noqa: F401
import pytest
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.uow_open_probe import record_uow_open
from models.prune import RecoveryBundleInventory

from domain.rom import Rom
from domain.rom_install import RomInstall
from services.data_inventory import DataInventoryService, DataInventoryServiceConfig


class FakeRecoveryInventory:
    """In-memory ``RecoveryBundleInventoryReader`` recording every reading asked for."""

    def __init__(self, count: int = 0, total_bytes: int = 0, root: str = "/home/deck/tender-recovery") -> None:
        self._answer: RecoveryBundleInventory = {"count": count, "total_bytes": total_bytes, "bundles": []}
        self._root = root
        self.calls = 0

    def root(self) -> str:
        return self._root

    def bundle_inventory(self) -> RecoveryBundleInventory:
        self.calls += 1
        return {
            "count": self._answer["count"],
            "total_bytes": self._answer["total_bytes"],
            "bundles": list(self._answer["bundles"]),
        }


def _seed_rom(uow, rom_id, *, fs_size_bytes, installed):
    """Put one ROM in the registry, optionally with an install record beside it."""
    with uow:
        uow.roms.save(
            Rom.synced(
                rom_id=rom_id,
                platform_slug="n64",
                name=f"Game {rom_id}",
                fs_name=f"game{rom_id}.z64",
                shortcut_app_id=1000 + rom_id,
                synced_at="2025-01-01T00:00:00",
                fs_size_bytes=fs_size_bytes,
            )
        )
        if installed:
            uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=rom_id,
                    file_path=f"/roms/n64/game{rom_id}.z64",
                    rom_dir=None,
                    platform_slug="n64",
                    system="n64",
                    installed_at="2025-01-01T00:00:00",
                )
            )


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def recovery() -> FakeRecoveryInventory:
    return FakeRecoveryInventory()


@pytest.fixture
def service(uow: FakeUnitOfWork, recovery: FakeRecoveryInventory) -> DataInventoryService:
    return DataInventoryService(
        config=DataInventoryServiceConfig(
            loop=running_loop(),
            uow_factory=FakeUnitOfWorkFactory(uow),
            recovery_inventory=recovery,
        )
    )


class TestTheInstalledPopulation:
    """The installed-ROM count and the size RomM reported for those rows."""

    @pytest.mark.asyncio
    async def test_it_counts_the_installed_rows_and_sums_their_server_size(self, service, uow):
        _seed_rom(uow, 1, fs_size_bytes=1000, installed=True)
        _seed_rom(uow, 2, fs_size_bytes=2500, installed=True)

        answer = await service.get_data_inventory()

        assert answer["installed_roms"] == 2
        assert answer["installed_bytes"] == 3500

    @pytest.mark.asyncio
    async def test_it_leaves_a_synced_but_uninstalled_rom_out_of_both_figures(self, service, uow):
        _seed_rom(uow, 1, fs_size_bytes=1000, installed=True)
        _seed_rom(uow, 2, fs_size_bytes=9_000_000, installed=False)

        answer = await service.get_data_inventory()

        assert answer["installed_roms"] == 1
        assert answer["installed_bytes"] == 1000

    @pytest.mark.asyncio
    async def test_an_installed_rom_whose_size_romm_never_reported_counts_and_adds_nothing(self, service, uow):
        _seed_rom(uow, 1, fs_size_bytes=None, installed=True)
        _seed_rom(uow, 2, fs_size_bytes=4096, installed=True)

        answer = await service.get_data_inventory()

        assert answer["installed_roms"] == 2
        assert answer["installed_bytes"] == 4096

    @pytest.mark.asyncio
    async def test_an_empty_library_reports_zero_rather_than_failing(self, service):
        answer = await service.get_data_inventory()

        assert answer["installed_roms"] == 0
        assert answer["installed_bytes"] == 0


class TestTheRecoveryBundlePopulation:
    """What the recovery root holds, taken from the reader and never recomputed."""

    @pytest.mark.asyncio
    async def test_it_reports_the_readers_count_and_total(self, uow, service, recovery):
        recovery._answer = {"count": 3, "total_bytes": 12_345, "bundles": []}

        answer = await service.get_data_inventory()

        assert answer["recovery_bundles"] == 3
        assert answer["recovery_bytes"] == 12_345
        assert recovery.calls == 1

    @pytest.mark.asyncio
    async def test_it_lists_each_bundle_the_reader_listed(self, uow, service, recovery):
        listed = [
            {"name": "Shenmue", "day": "2026-09-20", "bytes": 1_000},
            {"name": "hand-renamed", "day": None, "bytes": None},
        ]
        recovery._answer = {"count": 2, "total_bytes": 1_000, "bundles": listed}

        answer = await service.get_data_inventory()

        assert answer["recovery_bundle_list"] == listed

    @pytest.mark.asyncio
    async def test_a_recovery_root_that_holds_nothing_reports_zero(self, service):
        answer = await service.get_data_inventory()

        assert answer["recovery_bundles"] == 0
        assert answer["recovery_bytes"] == 0
        assert answer["recovery_bundle_list"] == []


class TestTheAnswerShape:
    """The six keys the page reads, and nothing else."""

    @pytest.mark.asyncio
    async def test_it_answers_exactly_the_population_figures_the_root_and_the_list(self, service):
        answer = await service.get_data_inventory()

        assert set(answer) == {
            "installed_roms",
            "installed_bytes",
            "recovery_bundles",
            "recovery_bytes",
            "recovery_root",
            "recovery_bundle_list",
        }

    @pytest.mark.asyncio
    async def test_it_names_the_root_the_bundles_were_counted_under(self, uow, service, recovery):
        """The folder is derived from the package name, so the panel is told rather than spelling it."""
        recovery._root = "/home/deck/somewhere-else-recovery"

        answer = await service.get_data_inventory()

        assert answer["recovery_root"] == "/home/deck/somewhere-else-recovery"

    @pytest.mark.asyncio
    async def test_the_bundle_reading_happens_outside_the_unit_of_work(self, uow, service, recovery):
        """A Unit of Work wraps database reads and writes, never file I/O.

        The reader is asked while no unit is open, so the recovery walk never
        runs under the write lock ``BEGIN IMMEDIATE`` takes.
        """
        observed = record_uow_open(uow, recovery, "bundle_inventory")

        await service.get_data_inventory()

        assert observed == [False]
