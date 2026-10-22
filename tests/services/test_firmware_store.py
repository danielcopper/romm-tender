"""Tender's firmware store, end to end over a real folder tree: downloads into it, the links out of it, and removal.

Each test builds a home under ``tmp_path`` with a RetroDECK BIOS folder, Tender's
folder and its data folder, and states the emulators' demand through the fake
resolver, whose places lie in that tree and whose presence is read off it — so
a link Tender places is a real symlink, and the resolver reads the bytes behind
it the way it does on a machine.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from _factories import _make_conflict_rules
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_core_info_provider import FakeCoreInfoProvider, libretro_option, standalone_option
from fakes.fake_firmware_resolver import FakeFirmwareResolver
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_platform_systems import FakePlatformSystems
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock

from adapters.firmware_file import FirmwareFileAdapter
from domain.bios_file import BiosFile
from domain.firmware_wants import IDENTITY_ARCHIVE, IDENTITY_FILE, FolderFile
from domain.platform_system import NO_SYSTEM, PlatformSystem
from domain.source_roots import SourceRoot, SourceRoots
from lib.errors import Refused
from services.firmware import FirmwareService, FirmwareServiceConfig
from services.firmware.store import FirmwareStore, FirmwareStoreConfig

if TYPE_CHECKING:
    from domain.firmware_store import FirmwareLink

_YABA = "yabasanshiro_libretro.so"
_KRONOS = "kronos_libretro.so"


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class _Romm:
    """RomM's firmware surface over a fixed library: the platform list, and each file's bytes."""

    def __init__(self) -> None:
        self.files: dict[int, tuple[str, str, bytes, str | None]] = {}
        self.served: dict[int, bytes] = {}
        self.downloads: list[int] = []

    def add(self, firmware_id: int, platform: str, name: str, data: bytes, *, md5: str | None = "") -> None:
        self.files[firmware_id] = (platform, name, data, _md5(data) if md5 == "" else md5)

    def list_platforms(self) -> list[dict[str, Any]]:
        platforms: dict[str, dict[str, Any]] = {}
        for firmware_id, (platform, name, data, md5) in self.files.items():
            entry = platforms.setdefault(
                platform, {"id": len(platforms) + 1, "slug": platform, "fs_slug": platform, "firmware": []}
            )
            entry["firmware"].append(
                {
                    "id": firmware_id,
                    "file_name": name,
                    "file_size_bytes": len(data),
                    "md5_hash": md5,
                    "platform_id": entry["id"],
                }
            )
        return list(platforms.values())

    def get_firmware(self, firmware_id: int) -> dict[str, Any]:
        raise AssertionError("a download looks the file up in the listing")

    def download_firmware(self, firmware_id: int, filename: str, dest: str) -> None:
        self.downloads.append(firmware_id)
        Path(dest).write_bytes(self.served.get(firmware_id, self.files[firmware_id][2]))


@dataclass
class _Machine:
    """One home under ``tmp_path`` and the service over it."""

    service: FirmwareService
    store: FirmwareStore
    resolver: FakeFirmwareResolver
    romm: _Romm
    uow: FakeUnitOfWork
    core_info: FakeCoreInfoProvider
    platform_systems: FakePlatformSystems
    bios: Path
    tender: Path
    data: Path

    def store_path(self, platform: str, name: str) -> Path:
        return self.tender / "bios" / platform / name

    def links(self) -> dict[str, FirmwareLink]:
        return {link.place: link for link in self.uow.firmware_links.iter_all()}


def _roots(bios: Path, provenance: str = "read") -> SourceRoots:
    return SourceRoots(
        roots=(SourceRoot(name="bios", path=str(bios), provenance=provenance, settings_file=None),), findings=()
    )


@pytest.fixture
def machine(tmp_path) -> _Machine:
    bios = tmp_path / "retrodeck" / "bios"
    bios.mkdir(parents=True)
    tender = tmp_path / "Tender"
    tender.mkdir()
    data = tmp_path / "data"
    files = FirmwareFileAdapter()
    resolver = FakeFirmwareResolver(bios_root=str(bios), present_probe=files.exists, roots=_roots(bios))
    romm = _Romm()
    uow = FakeUnitOfWork()
    uows = FakeUnitOfWorkFactory(uow)
    clock = FakeClock(now=datetime(2026, 10, 21, 20, 0, 0, tzinfo=UTC))
    logger = logging.getLogger("test")
    store = FirmwareStore(
        config=FirmwareStoreConfig(
            romm_api=romm,
            firmware_file_store=files,
            platform_firmware_resolver=resolver,
            prepare_folder=lambda: str(tender),
            uow_factory=uows,
            clock=clock,
            data_dir=str(data),
            logger=logger,
        )
    )
    core_info = FakeCoreInfoProvider(options=[libretro_option("yabasanshiro_libretro", "YabaSanshiro")])
    platform_systems = FakePlatformSystems()
    service = FirmwareService(
        config=FirmwareServiceConfig(
            romm_api=romm,
            loop=running_loop(),
            logger=logger,
            clock=clock,
            firmware_file_store=files,
            platform_firmware_resolver=resolver,
            firmware_identifier=lambda md5: ("gb_bios.bin",) if md5 == "1" * 32 else (),
            store=store,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios)),
            core_info=core_info,
            platform_systems=platform_systems,
            platform_core_reader=FakePlatformCoreReader(),
            active_core=FakeActiveCoreResolver(),
            uow_factory=uows,
            conflict_rules=_make_conflict_rules(),
            settings={},
            settings_persister=FakeSettingsPersister(),
        )
    )
    return _Machine(service, store, resolver, romm, uow, core_info, platform_systems, bios, tender, data)


_SATURN = b"saturn bios bytes"


def _saturn(machine: _Machine) -> None:
    """Saturn's BIOS, wanted by YabaSanshiro in the BIOS folder and by Kronos in ``kronos/``."""
    (machine.bios / "kronos").mkdir()
    for emulator, place in (
        (_YABA, machine.bios / "saturn_bios.bin"),
        (_KRONOS, machine.bios / "kronos" / "saturn_bios.bin"),
    ):
        machine.resolver.declare(
            "saturn_bios.bin",
            required_by=[emulator],
            place=str(place),
            identity_md5=_md5(_SATURN),
            identity_kind=IDENTITY_FILE,
        )


class TestADownloadLandsInTheStore:
    async def test_a_file_lands_once_in_its_platforms_store_folder_and_is_linked_at_every_emulators_place(
        self, machine
    ):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)

        result = await machine.service.download_all_firmware("saturn")

        stored = machine.store_path("saturn", "sega_101.bin")
        assert stored.read_bytes() == _SATURN
        assert os.readlink(machine.bios / "saturn_bios.bin") == str(stored)
        assert os.readlink(machine.bios / "kronos" / "saturn_bios.bin") == str(stored)
        assert set(machine.links()) == {
            str(machine.bios / "saturn_bios.bin"),
            str(machine.bios / "kronos" / "saturn_bios.bin"),
        }
        assert (result["downloaded"], result["placed"], result["left_in_place"]) == (1, 2, 0)

    async def test_nothing_is_written_into_retrodecks_bios_folder_but_links(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)

        await machine.service.download_all_firmware("saturn")

        written = [path for path in machine.bios.rglob("*") if path.is_file() and not path.is_symlink()]
        assert written == []

    async def test_bytes_that_do_not_match_romms_checksum_are_discarded_and_nothing_changes(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        machine.romm.served[1] = b"damaged"

        with pytest.raises(Refused) as refused:
            await machine.service.download_platform_firmware_file("saturn", "sega_101.bin")

        assert refused.value.reason == "bios_download_damaged"
        assert refused.value.message == "The download was damaged. Try again."
        assert list((machine.tender / "bios").rglob("*.*")) == []
        assert not (machine.bios / "saturn_bios.bin").exists()
        assert list(machine.uow.firmware_store.iter_all()) == []

    async def test_a_file_romm_states_no_checksum_for_is_not_downloaded(self, machine):
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN, md5=None)

        with pytest.raises(Refused) as refused:
            await machine.service.download_platform_firmware_file("saturn", "sega_101.bin")

        assert refused.value.reason == "no_checksum"
        assert refused.value.message == "RomM states no checksum for sega_101.bin, so Tender cannot check a download."
        assert machine.romm.downloads == []

    async def test_a_name_that_reaches_another_platforms_folder_is_refused(self, machine):
        machine.romm.add(1, "saturn", "../dc/dc_boot.bin", _SATURN)

        with pytest.raises(Refused) as refused:
            await machine.service.download_platform_firmware_file("saturn", "../dc/dc_boot.bin")

        assert refused.value.reason == "path_traversal"
        assert machine.romm.downloads == []
        assert not (machine.tender / "bios" / "dc").exists()

    async def test_a_platform_that_leaves_the_store_is_refused(self, machine):
        machine.romm.add(1, "../roms", "sega_101.bin", _SATURN)

        with pytest.raises(Refused) as refused:
            await machine.service.download_platform_firmware_file("../roms", "sega_101.bin")

        assert refused.value.reason == "path_traversal"
        assert machine.romm.downloads == []
        assert not (machine.tender / "roms").exists()

    async def test_a_second_download_of_a_stored_file_fetches_nothing(self, machine):
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_platform_firmware_file("saturn", "sega_101.bin")

        again = await machine.service.download_platform_firmware_file("saturn", "sega_101.bin")

        assert machine.romm.downloads == [1]
        assert again["downloaded"] == 0


class TestByContent:
    async def test_a_file_under_an_alias_fills_the_requirement_it_is_identified_for(self, machine):
        machine.resolver.declare(
            "saturn_bios.bin",
            required_by=[_YABA],
            place=str(machine.bios / "saturn_bios.bin"),
            identity_md5=_md5(_SATURN),
            identity_kind=IDENTITY_FILE,
        )
        machine.romm.add(1, "saturn", "sega_100.bin", _SATURN)

        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]

        (row,) = [f for f in entry["files"] if f["declared_by"] == _YABA]
        assert row["file_name"] == "saturn_bios.bin"
        assert row["library"]["file_name"] == "sega_100.bin"
        assert row["library"]["matched"] == "content"
        assert row["on_server"] is True

    async def test_a_name_in_other_letters_is_linked_under_the_declared_name(self, machine):
        psp = b"psp bios"
        machine.core_info.options = [libretro_option("pcsx_rearmed_libretro", "PCSX ReARMed")]
        machine.resolver.declare(
            "psxonpsp660.bin",
            required_by=["pcsx_rearmed_libretro.so"],
            place=str(machine.bios / "psxonpsp660.bin"),
            identity_md5=_md5(psp),
            identity_kind=IDENTITY_FILE,
        )
        machine.romm.add(1, "psx", "PSXONPSP660.BIN", psp)

        await machine.service.download_all_firmware("psx")

        assert os.readlink(machine.bios / "psxonpsp660.bin") == str(machine.store_path("psx", "PSXONPSP660.BIN"))

    async def test_a_library_file_the_resolver_does_not_know_fills_no_requirement(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "mystery.bin", b"nobody knows these")

        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]

        claimed = [f for f in entry["files"] if f["declared_by"] is not None]
        assert all(f["library"] is None for f in claimed)
        (unclaimed,) = [f for f in entry["files"] if f["declared_by"] is None]
        assert unclaimed["file_name"] == "mystery.bin"
        assert unclaimed["md5"] == _md5(b"nobody knows these")

    async def test_two_files_with_the_same_bytes_both_lie_in_the_store_and_the_link_points_to_the_first(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        machine.romm.add(2, "saturn", "Sega_100.bin", _SATURN)

        await machine.service.download_all_firmware("saturn")

        assert machine.store_path("saturn", "sega_101.bin").exists()
        assert machine.store_path("saturn", "Sega_100.bin").exists()
        assert os.readlink(machine.bios / "saturn_bios.bin") == str(machine.store_path("saturn", "Sega_100.bin"))

    async def test_an_archive_and_a_file_with_no_known_checksum_are_matched_by_name_and_say_so(self, machine):
        machine.resolver.declare(
            "neogeo.zip",
            required_by=[_YABA],
            place=str(machine.bios / "neogeo.zip"),
            identity_md5="a" * 32,
            identity_kind=IDENTITY_ARCHIVE,
        )
        machine.resolver.declare("keys.txt", required_by=[_YABA], place=str(machine.bios / "keys.txt"))
        machine.romm.add(1, "saturn", "NEOGEO.ZIP", b"repacked")
        machine.romm.add(2, "saturn", "keys.txt", b"keys")

        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]

        matched = {f["file_name"]: f["library"]["matched"] for f in entry["files"] if f["library"]}
        assert matched == {"neogeo.zip": "archive_name", "keys.txt": "no_checksum_name"}


class TestAPlaceOutsideTheBiosFolder:
    async def test_cemus_keys_are_linked_where_the_resolver_names_them_and_read_present_only_then(
        self, machine, tmp_path
    ):
        cemu = tmp_path / "data" / "Cemu"
        cemu.mkdir(parents=True)
        machine.core_info.options = [standalone_option("CEMU", "Cemu", emulator="CEMU")]
        machine.resolver.declare("keys.txt", required_by=["CEMU"], place=str(cemu / "keys.txt"))
        machine.romm.add(1, "wiiu", "keys.txt", b"keys")

        before = (await machine.service.get_platform_firmware_status("wiiu"))["platform"]
        await machine.service.download_all_firmware("wiiu")
        after = (await machine.service.get_platform_firmware_status("wiiu"))["platform"]

        assert os.readlink(cemu / "keys.txt") == str(machine.store_path("wiiu", "keys.txt"))
        assert [f["downloaded"] for f in before["files"] if f["file_name"] == "keys.txt"] == [False]
        assert [f["downloaded"] for f in after["files"] if f["file_name"] == "keys.txt"] == [True]

    async def test_a_folder_that_does_not_exist_is_not_created_and_the_row_names_it(self, machine, tmp_path):
        cemu = tmp_path / "data" / "Cemu"
        machine.core_info.options = [standalone_option("CEMU", "Cemu", emulator="CEMU")]
        machine.resolver.declare("keys.txt", required_by=["CEMU"], place=str(cemu / "keys.txt"))
        machine.romm.add(1, "wiiu", "keys.txt", b"keys")

        await machine.service.download_all_firmware("wiiu")
        entry = (await machine.service.get_platform_firmware_status("wiiu"))["platform"]

        assert not cemu.exists()
        (row,) = [f for f in entry["files"] if f["file_name"] == "keys.txt"]
        assert row["place_refusal"] == {"reason": "folder_missing", "folder": str(cemu)}


class TestASourceWhoseRootsCouldNotBeAsked:
    @pytest.mark.parametrize(
        ("press", "args"),
        [
            ("download_all_firmware", ("saturn",)),
            ("download_required_firmware", ("saturn",)),
            ("download_platform_firmware_file", ("saturn", "sega_101.bin")),
            ("update_bios_links", ("saturn",)),
        ],
    )
    async def test_no_bios_press_places_a_link_there(self, machine, press, args):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_platform_firmware_file("saturn", "sega_101.bin")
        for place in machine.links():
            os.unlink(place)
        machine.resolver.roots = None

        await getattr(machine.service, press)(*args)
        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]

        assert not os.path.lexists(machine.bios / "saturn_bios.bin")
        assert not os.path.lexists(machine.bios / "kronos" / "saturn_bios.bin")
        (row,) = [f for f in entry["files"] if f["declared_by"] == _YABA]
        assert row["place_refusal"]["reason"] == "root_assumed"


class TestTwoSources:
    async def test_a_file_two_sources_need_lies_once_and_is_linked_into_both(self, machine, tmp_path):
        other_bios = tmp_path / "emudeck" / "bios"
        other_bios.mkdir(parents=True)
        other = FakeFirmwareResolver(
            bios_root=str(other_bios), present_probe=os.path.exists, source="emudeck", roots=_roots(other_bios)
        )
        other.declare(
            "saturn_bios.bin",
            required_by=[_YABA],
            place=str(other_bios / "saturn_bios.bin"),
            identity_md5=_md5(_SATURN),
            identity_kind=IDENTITY_FILE,
        )
        machine.resolver.other_sources.append(other)
        machine.resolver.declare(
            "saturn_bios.bin",
            required_by=[_YABA],
            place=str(machine.bios / "saturn_bios.bin"),
            identity_md5=_md5(_SATURN),
            identity_kind=IDENTITY_FILE,
        )
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)

        result = await machine.service.download_all_firmware("saturn")

        stored = str(machine.store_path("saturn", "sega_101.bin"))
        assert os.readlink(machine.bios / "saturn_bios.bin") == stored
        assert os.readlink(other_bios / "saturn_bios.bin") == stored
        assert [s.file_name for s in machine.uow.firmware_store.iter_all()] == ["sega_101.bin"]
        assert result["placed"] == 2


class TestAPlaceSomethingElseLiesAt:
    async def test_no_link_is_placed_over_it_and_the_result_counts_it(self, machine):
        _saturn(machine)
        (machine.bios / "saturn_bios.bin").write_bytes(b"the user's own copy")
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)

        result = await machine.service.download_all_firmware("saturn")

        assert (machine.bios / "saturn_bios.bin").read_bytes() == b"the user's own copy"
        assert not (machine.bios / "saturn_bios.bin").is_symlink()
        assert (result["placed"], result["left_in_place"]) == (1, 1)

    async def test_the_row_says_another_file_lies_there(self, machine):
        _saturn(machine)
        (machine.bios / "saturn_bios.bin").write_bytes(b"the user's own copy")

        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]

        (row,) = [f for f in entry["files"] if f["declared_by"] == _YABA]
        assert row["link"] == "occupied"


class TestReplacingAWrongFile:
    def _wrong(self, machine) -> Path:
        place = machine.bios / "saturn_bios.bin"
        place.write_bytes(b"a wrong image")
        machine.resolver.declare(
            "saturn_bios.bin",
            required_by=[_YABA],
            place=str(place),
            identity_md5=_md5(_SATURN),
            identity_kind=IDENTITY_FILE,
            checked="mismatch",
        )
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        return place

    async def test_download_required_leaves_it_and_the_row_offers_replace(self, machine):
        place = self._wrong(machine)

        await machine.service.download_required_firmware("saturn")
        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]

        assert place.read_bytes() == b"a wrong image"
        (row,) = [f for f in entry["files"] if f["declared_by"] == _YABA]
        assert row["replace_offered"] is True
        assert entry["backup_root"] == str(machine.data / "backups" / "bios")

    async def test_replace_moves_it_to_the_backup_folder_and_links_tenders_copy(self, machine):
        place = self._wrong(machine)

        result = await machine.service.replace_bios_file("saturn", str(place))

        backup = machine.data / "backups" / "bios" / "2026-10-21_20-00-00" / str(place).lstrip("/")
        assert result["backup_path"] == str(backup)
        assert backup.read_bytes() == b"a wrong image"
        assert os.readlink(place) == str(machine.store_path("saturn", "sega_101.bin"))

    async def test_no_replace_without_a_library_copy_proven_by_its_checksum(self, machine):
        place = machine.bios / "keys.txt"
        place.write_bytes(b"wrong")
        machine.resolver.declare("keys.txt", required_by=[_YABA], place=str(place), checked="mismatch")
        machine.romm.add(1, "saturn", "keys.txt", b"keys")

        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]
        with pytest.raises(Refused) as refused:
            await machine.service.replace_bios_file("saturn", str(place))

        (row,) = [f for f in entry["files"] if f["file_name"] == "keys.txt"]
        assert row["replace_offered"] is False
        assert refused.value.reason == "not_replaceable"
        assert place.read_bytes() == b"wrong"

    async def test_a_file_the_resolver_reads_as_met_is_never_replaced(self, machine):
        place = machine.bios / "saturn_bios.bin"
        place.write_bytes(_SATURN)
        machine.resolver.declare(
            "saturn_bios.bin",
            required_by=[_YABA],
            place=str(place),
            identity_md5=_md5(_SATURN),
            identity_kind=IDENTITY_FILE,
            checked="verified",
        )
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)

        with pytest.raises(Refused):
            await machine.service.replace_bios_file("saturn", str(place))

        assert not place.is_symlink()


class TestBringingLinksUpToDate:
    async def test_a_link_deleted_by_hand_is_placed_again(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_all_firmware("saturn")
        (machine.bios / "saturn_bios.bin").unlink()

        before = (await machine.service.get_platform_firmware_status("saturn"))["platform"]
        result = await machine.service.update_bios_links("saturn")

        (row,) = [f for f in before["files"] if f["declared_by"] == _YABA]
        assert row["link"] == "not_linked"
        assert os.readlink(machine.bios / "saturn_bios.bin") == str(machine.store_path("saturn", "sega_101.bin"))
        assert result["placed"] == 1

    async def test_switching_the_emulator_places_its_links_from_the_store_and_downloads_nothing(self, machine):
        machine.resolver.declare(
            "saturn_bios.bin",
            required_by=[_KRONOS],
            place=str(machine.bios / "kronos_only.bin"),
            identity_md5=_md5(_SATURN),
            identity_kind=IDENTITY_FILE,
        )
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        machine.romm.add(2, "saturn", "unrelated.bin", b"x")
        await machine.service.download_platform_firmware_file("saturn", "sega_101.bin")
        machine.romm.downloads.clear()
        (machine.bios / "kronos_only.bin").unlink()

        await machine.service.update_bios_links("saturn")

        assert machine.romm.downloads == []
        assert (machine.bios / "kronos_only.bin").is_symlink()

    async def test_a_link_of_tenders_with_no_store_copy_left_is_removed(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_all_firmware("saturn")
        machine.store_path("saturn", "sega_101.bin").unlink()

        await machine.service.update_bios_links("saturn")

        assert not os.path.lexists(machine.bios / "saturn_bios.bin")
        assert machine.links() == {}

    async def test_a_link_that_moved_with_its_folder_keeps_its_record(self, machine, tmp_path):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_all_firmware("saturn")
        moved = tmp_path / "sd" / "bios"
        moved.parent.mkdir()
        os.rename(machine.bios, moved)
        machine.resolver.placements = [
            type(p)(**{**p.__dict__, "place": p.place.replace(str(machine.bios), str(moved))})
            for p in machine.resolver.placements
        ]
        machine.resolver.roots = _roots(moved)

        await machine.service.update_bios_links("saturn")

        assert set(machine.links()) == {str(moved / "saturn_bios.bin"), str(moved / "kronos" / "saturn_bios.bin")}


class TestAPlaceAnotherPlatformsLinkServes:
    async def test_it_reads_linked_for_that_platform_and_its_own_copy_takes_over_once_that_goes(self, machine):
        boot = b"gb boot rom"
        for platform in ("gb", "gbc"):
            machine.romm.add({"gb": 1, "gbc": 2}[platform], platform, "gb_bios.bin", boot)
        machine.resolver.declare(
            "gb_bios.bin",
            required_by=[_YABA],
            place=str(machine.bios / "gb_bios.bin"),
            identity_md5=_md5(boot),
            identity_kind=IDENTITY_FILE,
        )
        await machine.service.download_all_firmware("gb")
        await machine.service.download_all_firmware("gbc")

        gbc = (await machine.service.get_platform_firmware_status("gbc"))["platform"]
        (row,) = [f for f in gbc["files"] if f["declared_by"] == _YABA]
        assert (row["link"], row["linked_for"]) == ("linked_elsewhere", "gb")

        await machine.service.delete_platform_bios("gb")
        await machine.service.update_bios_links("gbc")

        assert os.readlink(machine.bios / "gb_bios.bin") == str(machine.store_path("gbc", "gb_bios.bin"))


class TestDeleting:
    async def test_a_rows_delete_removes_the_store_file_and_every_link_to_it(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_all_firmware("saturn")

        result = await machine.service.delete_bios_file("saturn", "sega_101.bin")

        assert not machine.store_path("saturn", "sega_101.bin").exists()
        assert not os.path.lexists(machine.bios / "saturn_bios.bin")
        assert not os.path.lexists(machine.bios / "kronos" / "saturn_bios.bin")
        assert machine.links() == {}
        assert result["deleted_count"] == 1

    async def test_the_platform_count_takes_every_store_copy(self, machine):
        """Two copies of the same bytes, and a copy only another emulator asks for, all count and all go."""
        _saturn(machine)
        other = b"kronos only"
        machine.resolver.declare(
            "kronos_extra.bin",
            required_by=[_KRONOS],
            place=str(machine.bios / "kronos" / "kronos_extra.bin"),
            identity_md5=_md5(other),
            identity_kind=IDENTITY_FILE,
        )
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        machine.romm.add(2, "saturn", "Sega_100.bin", _SATURN)
        machine.romm.add(3, "saturn", "kronos_extra.bin", other)
        await machine.service.download_all_firmware("saturn")

        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]
        deleted = await machine.service.delete_platform_bios("saturn")

        assert entry["deletable_count"] == 3
        assert deleted["deleted_count"] == 3

    async def test_a_file_the_user_placed_stays(self, machine):
        _saturn(machine)
        (machine.bios / "saturn_bios.bin").write_bytes(b"mine")
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_all_firmware("saturn")

        await machine.service.delete_platform_bios("saturn")

        assert (machine.bios / "saturn_bios.bin").read_bytes() == b"mine"

    async def test_a_link_under_an_assumed_root_stays_with_its_record_and_goes_once_the_root_is_read(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_all_firmware("saturn")
        machine.resolver.roots = _roots(machine.bios, provenance="assumed")

        result = await machine.service.delete_bios_file("saturn", "sega_101.bin")

        assert not machine.store_path("saturn", "sega_101.bin").exists()
        assert os.path.lexists(machine.bios / "saturn_bios.bin")
        assert {(kept["kind"], kept["source"]) for kept in result["kept"]} == {("link", "retrodeck")}
        assert {kept["place"] for kept in result["kept"]} == {
            str(machine.bios / "saturn_bios.bin"),
            str(machine.bios / "kronos" / "saturn_bios.bin"),
        }
        assert len(machine.links()) == 2

        machine.resolver.roots = _roots(machine.bios)
        await machine.service.update_bios_links("saturn")

        assert not os.path.lexists(machine.bios / "saturn_bios.bin")
        assert machine.links() == {}

    async def test_a_bios_file_downloaded_before_the_store_stays_and_its_delete_removes_it_there(self, machine):
        older = machine.bios / "saturn_bios.bin"
        older.write_bytes(_SATURN)
        machine.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="saturn",
                file_name="sega_101.bin",
                file_path=str(older),
                downloaded_at="2026-01-01T00:00:00",
            )
        )
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)

        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]
        assert older.read_bytes() == _SATURN
        (row,) = [f for f in entry["files"] if f["declared_by"] == _YABA]
        assert row["delete_name"] == "sega_101.bin"

        await machine.service.delete_bios_file("saturn", "sega_101.bin")

        assert not older.exists()
        assert list(machine.uow.bios_files.iter_all()) == []


class TestAFolderTheEmulatorLists:
    async def test_every_recognised_ps2_bios_is_linked_into_the_folder_under_its_name_and_has_a_row(self, machine):
        folder = machine.bios / "pcsx2" / "bios"
        folder.mkdir(parents=True)
        images = {"SCPH-39001.bin": b"usa", "SCPH-70004.bin": b"europe"}
        machine.core_info.options = [libretro_option("pcsx2_libretro", "LRPS2")]
        machine.resolver.declare(
            "bios",
            required_by=["pcsx2_libretro.so"],
            place=str(folder),
            relative_path="pcsx2/bios",
            declares_directory=True,
            folder_md5s=frozenset(_md5(data) for data in images.values()),
        )
        for firmware_id, (name, data) in enumerate(images.items(), start=1):
            machine.romm.add(firmware_id, "ps2", name, data)

        await machine.service.download_all_firmware("ps2")
        entry = (await machine.service.get_platform_firmware_status("ps2"))["platform"]

        for name in images:
            assert os.readlink(folder / name) == str(machine.store_path("ps2", name))
        rows = {f["file_name"]: f for f in entry["files"] if f["in_folder"] == "pcsx2/bios"}
        assert set(rows) == set(images)
        assert all(row["declared_kind"] == "file" for row in rows.values())

    async def test_a_file_in_the_folder_that_is_not_tenders_has_a_row_and_no_delete(self, machine):
        folder = machine.bios / "pcsx2" / "bios"
        folder.mkdir(parents=True)
        (folder / "mine.bin").write_bytes(b"mine")
        machine.core_info.options = [libretro_option("pcsx2_libretro", "LRPS2")]
        machine.resolver.declare(
            "bios",
            required_by=["pcsx2_libretro.so"],
            place=str(folder),
            relative_path="pcsx2/bios",
            declares_directory=True,
            folder_files=(FolderFile(path=str(folder / "mine.bin"), satisfied=True, description="Europe"),),
        )

        entry = (await machine.service.get_platform_firmware_status("ps2"))["platform"]

        (row,) = [f for f in entry["files"] if f["file_name"] == "mine.bin"]
        assert (row["satisfied"], list(row["images"]), row["deletable_count"]) == (True, ["Europe"], 0)


class TestOnlyTheStore:
    async def test_for_an_emulator_the_resolver_does_not_cover_a_download_lands_in_the_store_only(self, machine):
        machine.core_info.options = [standalone_option("RYUBING", "Ryubing", emulator="RYUBING")]
        machine.resolver.describe_emulator("RYUBING", declaration="unsupported")
        machine.romm.add(1, "switch", "prod.keys", b"keys")

        await machine.service.download_all_firmware("switch")
        entry = (await machine.service.get_platform_firmware_status("switch"))["platform"]

        assert machine.store_path("switch", "prod.keys").exists()
        assert list(machine.bios.rglob("*")) == []
        assert entry["store_only"] == "unsupported"

    async def test_for_a_platform_no_source_answers_for_a_download_lands_in_the_store_only(self, machine):
        machine.platform_systems.answers["switch"] = PlatformSystem(NO_SYSTEM, "switch", "switch")
        machine.romm.add(1, "switch", "prod.keys", b"keys")

        result = await machine.service.download_all_firmware("switch")
        entry = (await machine.service.get_platform_firmware_status("switch"))["platform"]

        assert result["downloaded"] == 1
        assert machine.store_path("switch", "prod.keys").exists()
        assert entry["store_only"] == "no_source"

    async def test_download_all_fetches_a_file_no_emulator_asks_for_and_lists_it_as_used_by_none(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "extra.bin", b"nobody asks")

        await machine.service.download_all_firmware("saturn")
        entry = (await machine.service.get_platform_firmware_status("saturn"))["platform"]

        (row,) = [f for f in entry["files"] if f["file_name"] == "extra.bin"]
        assert (row["declared_by"], row["store_file"], row["delete_name"]) == (None, "extra.bin", "extra.bin")


class TestAStoreFileWithoutARecord:
    async def test_matching_bytes_are_taken_over_without_a_download(self, machine):
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        path = machine.store_path("saturn", "sega_101.bin")
        path.parent.mkdir(parents=True)
        path.write_bytes(_SATURN)

        result = await machine.service.download_all_firmware("saturn")

        assert machine.romm.downloads == []
        assert [s.file_name for s in machine.uow.firmware_store.iter_all()] == ["sega_101.bin"]
        assert result["backups"] == []

    async def test_other_bytes_go_to_the_backup_folder_before_the_download(self, machine):
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        path = machine.store_path("saturn", "sega_101.bin")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"something else")

        result = await machine.service.download_all_firmware("saturn")

        (backup,) = result["backups"]
        assert Path(backup).read_bytes() == b"something else"
        assert path.read_bytes() == _SATURN


class TestTendersFolderRecords:
    async def test_the_store_names_the_folder_its_files_lie_under_and_forgetting_removes_the_links(self, machine):
        _saturn(machine)
        machine.romm.add(1, "saturn", "sega_101.bin", _SATURN)
        await machine.service.download_all_firmware("saturn")

        assert machine.store.recorded_folders() == frozenset({str(machine.tender)})
        machine.store.forget_records_under(str(machine.tender))

        assert machine.store.recorded_folders() == frozenset()
        assert not os.path.lexists(machine.bios / "saturn_bios.bin")
        assert machine.store_path("saturn", "sega_101.bin").exists()
        assert machine.links() == {}


class TestIdentifying:
    async def test_answers_every_name_the_resolver_knows(self, machine):
        assert await machine.service.identify_library_firmware("1" * 32) == {
            "success": True,
            "known_as": ["gb_bios.bin"],
        }

    async def test_refuses_anything_but_a_checksum(self, machine):
        with pytest.raises(Refused):
            await machine.service.identify_library_firmware("../etc")


class TestFolderRule:
    @pytest.mark.parametrize(
        ("call", "args"),
        [
            ("download_all_firmware", ("saturn",)),
            ("download_required_firmware", ("saturn",)),
            ("download_platform_firmware_file", ("saturn", "sega_101.bin")),
            ("update_bios_links", ("saturn",)),
            ("replace_bios_file", ("saturn", "/x")),
            ("delete_bios_file", ("saturn", "sega_101.bin")),
            ("delete_platform_bios", ("saturn",)),
        ],
    )
    async def test_each_use_case_names_the_folder_rule(self, machine, call, args):
        refusal = Refused("folder_unreachable", "Tender's folder cannot be reached.")
        machine.service._rules = _make_conflict_rules(folder_unreachable=refusal)

        with pytest.raises(Refused) as refused:
            await getattr(machine.service, call)(*args)

        assert refused.value.reason == "folder_unreachable"
