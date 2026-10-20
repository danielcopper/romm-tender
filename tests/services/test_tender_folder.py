"""Tests for services/tender_folder.py — the place, its choice, reachability and what is recorded under a folder.

The service runs over the real folder adapter on a temporary home, with
drives that count as mounted only where a test connects them, and over test
stores that record what a test names: no store of Tender's records anything
under the folder yet, so these stand in for the first one that does.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from _factories import _make_conflict_rules, _make_prune_conflicts
from fakes.fake_emulator_sources import FakeEmulatorSources
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeUuidGen

from adapters.tender_folder import TenderFolderAdapter
from domain.refusal import DomainRefused
from domain.tender_folder import SourceRomFolders
from lib.errors import Refused
from services.tender_folder import FOLDER_ID_KEY, TenderFolderService, TenderFolderServiceConfig


class _Records:
    """A store that records under the folders a test names, and forgets them when asked."""

    def __init__(self, *folders: str) -> None:
        self.folders = set(folders)
        self.forgotten: list[str] = []

    def recorded_folders(self) -> frozenset[str]:
        return frozenset(self.folders)

    def forget_records_under(self, folder: str) -> None:
        self.forgotten.append(folder)
        self.folders.discard(folder)


class _Rig:
    def __init__(
        self,
        tmp_path,
        *records: _Records,
        sources: tuple[SourceRomFolders, ...] | None = (),
        cleanup: bool = False,
        folder_unreachable: Refused | None = None,
    ) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.media = tmp_path / "media"
        self.media.mkdir()
        self.mounts: set[str] = set()
        self.settings: dict[str, Any] = {}
        self.persister = FakeSettingsPersister()
        self.uow_factory = FakeUnitOfWorkFactory()
        self.sources = FakeEmulatorSources(rom_folders=sources)
        # The store a test records under later, where it names none up front.
        self.records = records[0] if records else _Records()
        self.store = TenderFolderAdapter(
            settings=self.settings,
            user_home=str(self.home),
            drives_root=str(self.media),
            is_mount=lambda path: path in self.mounts or os.path.ismount(path),
        )
        conflicts = _make_prune_conflicts()
        if cleanup:
            conflicts.register_run("held-run")
        self.service = TenderFolderService(
            config=TenderFolderServiceConfig(
                store=self.store,
                sources=self.sources,
                records=records or (self.records,),
                settings=self.settings,
                settings_persister=self.persister,
                uow_factory=self.uow_factory,
                uuid_gen=FakeUuidGen(["the-folder-id"]),
                conflict_rules=_make_conflict_rules(prune_conflicts=conflicts, folder_unreachable=folder_unreachable),
                loop=running_loop(),
                log_debug=lambda msg: None,
            )
        )

    def drive(self, name: str, *, connected: bool = True):
        path = self.media / name
        path.mkdir(exist_ok=True)
        if connected:
            self.mounts.add(str(path))
        return path

    def on_drive(self, drive) -> None:
        """Point the setting at *drive* as a choice of it would have."""
        self.settings["tender_folder"] = {"place": str(drive), "mount_point": str(drive)}


def _id_file(folder) -> str:
    return (folder / ".tender-folder").read_text()


class TestTheState:
    async def test_the_default_place_its_folder_and_the_writable_drives(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD")
        rig.drive("gone", connected=False)

        state = await rig.service.get_tender_folder()

        assert state == {
            "place": str(rig.home),
            "folder": str(rig.home / "Tender"),
            "default": True,
            "condition": "folder_missing",
            "unreachable": None,
            "elsewhere": [],
            "drives": [
                {
                    "name": "SD",
                    "mount_point": str(sd),
                    "folder": str(sd / "Tender"),
                    "free_bytes": state["drives"][0]["free_bytes"],
                }
            ],
        }
        assert state["drives"][0]["free_bytes"] > 0

    async def test_with_nothing_recorded_a_drive_that_is_gone_is_a_condition_and_never_unreachable(self, tmp_path):
        rig = _Rig(tmp_path, _Records())
        rig.on_drive(rig.drive("SD", connected=False))

        state = await rig.service.get_tender_folder()

        assert state["condition"] == "drive_not_mounted"
        assert state["unreachable"] is None

    @pytest.mark.parametrize("state", ["drive_not_mounted", "folder_missing", "folder_not_writable"])
    async def test_with_something_recorded_under_the_folder_it_is_unreachable_for_its_reason(self, tmp_path, state):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD", connected=state != "drive_not_mounted")
        rig.on_drive(sd)
        if state == "folder_not_writable":
            (sd / "Tender").mkdir()
            (sd / "Tender").chmod(0o500)
        rig.records.folders.add(str(sd / "Tender"))
        try:
            answer = await rig.service.get_tender_folder()
        finally:
            if state == "folder_not_writable":
                (sd / "Tender").chmod(0o700)

        assert answer["unreachable"] == state
        assert answer["folder"] == str(sd / "Tender")

    async def test_an_unreachable_folder_has_nothing_removed(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD")
        (sd / "Tender" / "bios").mkdir(parents=True)
        (sd / "Tender" / "bios" / "scph1001.bin").write_bytes(b"bios")
        rig.mounts.discard(str(sd))
        rig.on_drive(sd)
        rig.records.folders.add(str(sd / "Tender"))

        assert (await rig.service.get_tender_folder())["unreachable"] == "drive_not_mounted"
        rig.service.create_at_start()

        assert (sd / "Tender" / "bios" / "scph1001.bin").read_bytes() == b"bios"

    async def test_the_folders_something_is_recorded_under_besides_the_setting_are_listed_as_a_store_gave_them(
        self, tmp_path
    ):
        sd = str(tmp_path / "media" / "SD" / "Tender")
        rig = _Rig(tmp_path, _Records(sd, str(tmp_path / "home" / "Tender")), _Records(sd))

        state = await rig.service.get_tender_folder()

        assert state["elsewhere"] == [sd]
        assert state["default"] is True


class TestCreatingTheFolder:
    def test_the_first_start_creates_the_default_folder_with_its_id(self, tmp_path):
        rig = _Rig(tmp_path)

        rig.service.create_at_start()

        assert _id_file(rig.home / "Tender") == "the-folder-id\n"
        assert rig.uow_factory.uow.kv_config.get(FOLDER_ID_KEY) == "the-folder-id"

    def test_a_start_leaves_a_folder_that_is_there_alone(self, tmp_path):
        rig = _Rig(tmp_path)
        (rig.home / "Tender").mkdir()

        rig.service.create_at_start()

        assert os.listdir(rig.home / "Tender") == []

    def test_a_start_creates_no_folder_on_a_drive_that_is_gone(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD", connected=False)
        rig.on_drive(sd)

        rig.service.create_at_start()

        assert not (sd / "Tender").exists()

    def test_a_start_creates_no_folder_where_something_is_recorded_under_it(self, tmp_path):
        rig = _Rig(tmp_path, _Records(str(tmp_path / "home" / "Tender")))

        rig.service.create_at_start()

        assert not (rig.home / "Tender").exists()

    def test_every_folder_carries_the_one_id_recorded_once(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.service.create_at_start()
        sd = rig.drive("SD")
        rig.on_drive(sd)

        assert rig.service.do_prepare_for_write() == str(sd / "Tender")

        assert _id_file(sd / "Tender") == _id_file(rig.home / "Tender") == "the-folder-id\n"


class TestPreparingAWrite:
    def test_with_nothing_recorded_a_missing_folder_is_created_again_by_the_next_write(self, tmp_path):
        rig = _Rig(tmp_path, _Records())

        assert rig.service.do_prepare_for_write() == str(rig.home / "Tender")

        assert (rig.home / "Tender").is_dir()

    def test_a_write_to_a_drive_that_is_gone_is_refused_with_the_reason(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD", connected=False)
        rig.on_drive(sd)

        with pytest.raises(Refused) as refused:
            rig.service.do_prepare_for_write()

        assert refused.value.reason == "folder_unreachable"
        assert refused.value.message == (
            f"Tender's folder at {sd / 'Tender'} cannot be reached: the drive is not connected."
        )
        assert refused.value.details == {"folder": str(sd / "Tender"), "state": "drive_not_mounted"}
        assert not (sd / "Tender").exists()

    def test_a_write_to_a_place_that_cannot_be_written_is_refused_with_the_reason(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.home.chmod(0o500)
        try:
            with pytest.raises(Refused) as refused:
                rig.service.do_prepare_for_write()
        finally:
            rig.home.chmod(0o700)

        assert refused.value.details == {"folder": str(rig.home / "Tender"), "state": "folder_not_writable"}
        assert refused.value.message == (
            f"Tender's folder at {rig.home / 'Tender'} cannot be reached: the folder cannot be written to."
        )

    def test_a_missing_folder_something_is_recorded_under_is_not_created_again(self, tmp_path):
        rig = _Rig(tmp_path, _Records(str(tmp_path / "home" / "Tender")))

        with pytest.raises(Refused) as refused:
            rig.service.do_prepare_for_write()

        assert refused.value.details["state"] == "folder_missing"
        assert not (rig.home / "Tender").exists()


class TestTheFolderRule:
    def test_with_nothing_recorded_the_rule_refuses_nothing(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.on_drive(rig.drive("SD", connected=False))

        assert rig.service.unreachable_refusal() is None

    def test_with_something_recorded_under_an_unreachable_folder_the_rule_refuses_naming_it(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD", connected=False)
        rig.on_drive(sd)
        rig.records.folders.add(str(sd / "Tender"))

        refusal = rig.service.unreachable_refusal()

        assert refusal is not None
        assert refusal.reason == "folder_unreachable"
        assert refusal.message.startswith(f"Tender's folder at {sd / 'Tender'} cannot be reached")

    def test_a_reachable_folder_something_is_recorded_under_is_refused_nothing(self, tmp_path):
        rig = _Rig(tmp_path, _Records(str(tmp_path / "home" / "Tender")))
        (rig.home / "Tender").mkdir()

        assert rig.service.unreachable_refusal() is None


class TestCheckingATypedPlace:
    async def test_a_place_whose_last_folder_is_named_tender_offers_its_parent(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD")

        answer = await rig.service.check_tender_place(str(sd / "tender"))

        assert answer["tender_parent"] == str(sd)
        assert answer["folder"] == str(sd / "tender" / "Tender")

    async def test_the_folders_still_missing_are_named(self, tmp_path):
        rig = _Rig(tmp_path)

        answer = await rig.service.check_tender_place("~/Games/Library")

        assert answer == {
            "place": str(rig.home / "Games" / "Library"),
            "folder": str(rig.home / "Games" / "Library" / "Tender"),
            "tender_parent": None,
            "existing": str(rig.home),
            "missing": os.path.join("Games", "Library"),
        }
        assert not (rig.home / "Games").exists()

    async def test_a_place_on_a_drive_that_is_not_connected_is_refused_saying_so(self, tmp_path):
        rig = _Rig(tmp_path)

        with pytest.raises(DomainRefused) as refused:
            await rig.service.check_tender_place(str(rig.media / "SD" / "Games"))

        assert refused.value.reason == "place_drive_not_mounted"
        assert refused.value.message == "The drive SD is not connected."

    async def test_a_relative_path_is_refused(self, tmp_path):
        with pytest.raises(DomainRefused) as refused:
            await _Rig(tmp_path).service.check_tender_place("Games")

        assert refused.value.reason == "place_not_absolute"

    async def test_a_value_that_is_no_path_is_refused(self, tmp_path):
        with pytest.raises(Refused) as refused:
            await _Rig(tmp_path).service.check_tender_place(42)

        assert refused.value.reason == "invalid_value"

    async def test_a_place_while_the_sources_cannot_be_read_is_refused(self, tmp_path):
        with pytest.raises(DomainRefused) as refused:
            await _Rig(tmp_path, sources=None).service.check_tender_place("~/Games")

        assert refused.value.reason == "sources_unreadable"
        assert refused.value.message == "Tender could not read the emulator sources, so it cannot check this place."


class TestChoosingAPlace:
    async def test_choosing_a_drive_creates_its_folder_and_stores_the_place_with_its_mount_point(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD")

        state = await rig.service.choose_tender_place(str(sd))

        assert rig.settings["tender_folder"] == {"place": str(sd), "mount_point": str(sd)}
        assert rig.persister.save_count == 1
        assert state["folder"] == str(sd / "Tender")
        assert state["condition"] is None
        assert _id_file(sd / "Tender") == "the-folder-id\n"

    async def test_choosing_a_typed_place_creates_its_missing_folders(self, tmp_path):
        rig = _Rig(tmp_path)

        await rig.service.choose_tender_place("~/Games/Library")

        assert (rig.home / "Games" / "Library" / "Tender" / ".tender-folder").is_file()
        assert rig.settings["tender_folder"]["place"] == str(rig.home / "Games" / "Library")

    async def test_choosing_the_default_stores_no_choice(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.on_drive(rig.drive("SD"))

        state = await rig.service.choose_tender_place(None)

        assert rig.settings["tender_folder"] is None
        assert state["default"] is True
        assert (rig.home / "Tender").is_dir()

    async def test_a_place_that_cannot_be_written_is_refused_when_it_is_chosen(self, tmp_path):
        rig = _Rig(tmp_path)
        locked = rig.home / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            with pytest.raises(DomainRefused) as refused:
                await rig.service.choose_tender_place(str(locked))
        finally:
            locked.chmod(0o700)

        assert refused.value.reason == "place_not_writable"
        assert refused.value.message == f"{locked} cannot be written to."
        assert "tender_folder" not in rig.settings

    async def test_a_place_inside_a_rom_folder_is_refused_and_nothing_is_created(self, tmp_path):
        roms = tmp_path / "home" / "Emulation" / "roms"
        source = SourceRomFolders(kind="emudeck", rom_root=str(roms), system_folders=())
        rig = _Rig(tmp_path, sources=(source,))
        roms.mkdir(parents=True)

        with pytest.raises(DomainRefused) as refused:
            await rig.service.choose_tender_place(str(roms / "mine"))

        assert refused.value.reason == "place_in_rom_folder"
        assert refused.value.details["source"] == "emudeck"
        assert refused.value.details["path"] == f"{roms / 'mine'}/Tender"
        assert not (roms / "mine").exists()
        assert "tender_folder" not in rig.settings

    async def test_a_refusal_names_the_place_as_typed_and_the_rom_folder_resolved(self, tmp_path):
        rig = _Rig(tmp_path)
        roms = rig.home / "Emulation" / "roms"
        roms.mkdir(parents=True)
        (rig.home / "linked").symlink_to(roms)
        rig.sources.answered_rom_folders = (SourceRomFolders(kind="emudeck", rom_root=str(roms), system_folders=()),)

        with pytest.raises(DomainRefused) as refused:
            await rig.service.check_tender_place("~/linked/")

        assert (
            refused.value.message
            == f"~/linked/Tender lies inside emudeck's ROM folder ({roms}). Choose a place outside it."
        )

    async def test_a_typed_place_that_cannot_be_written_is_named_as_typed(self, tmp_path):
        rig = _Rig(tmp_path)
        locked = rig.home / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            with pytest.raises(DomainRefused) as refused:
                await rig.service.choose_tender_place("~/locked")
        finally:
            locked.chmod(0o700)

        assert refused.value.message == "~/locked cannot be written to."

    async def test_a_place_above_a_rom_folder_is_refused(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD")
        rig.sources.answered_rom_folders = (
            SourceRomFolders(kind="retrodeck", rom_root=str(sd / "Tender" / "roms"), system_folders=()),
        )

        with pytest.raises(DomainRefused) as refused:
            await rig.service.choose_tender_place(str(sd))

        assert refused.value.reason == "place_above_rom_folder"

    async def test_a_place_inside_a_system_folder_linked_to_another_drive_is_refused(self, tmp_path):
        rig = _Rig(tmp_path)
        big = rig.drive("BIG")
        (big / "ps2").mkdir()
        rig.sources.answered_rom_folders = (
            SourceRomFolders(
                kind="retrodeck", rom_root=str(rig.home / "roms"), system_folders=(("ps2", str(big / "ps2")),)
            ),
        )

        with pytest.raises(DomainRefused) as refused:
            await rig.service.choose_tender_place(str(big / "ps2"))

        assert refused.value.reason == "place_in_rom_folder"
        assert refused.value.details["system"] == "ps2"

    async def test_the_default_place_is_not_checked_against_the_rom_folders(self, tmp_path):
        source = SourceRomFolders(
            kind="retrodeck", rom_root=str(tmp_path / "home" / "Tender" / "roms"), system_folders=()
        )
        rig = _Rig(tmp_path, sources=(source,))

        state = await rig.service.choose_tender_place(None)

        assert state["default"] is True

    async def test_switching_removes_an_old_folder_holding_nothing_but_its_id_file(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.service.create_at_start()

        await rig.service.choose_tender_place(str(rig.drive("SD")))

        assert not (rig.home / "Tender").exists()

    async def test_switching_leaves_an_old_folder_that_holds_anything_else(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.service.create_at_start()
        (rig.home / "Tender" / "recovery").mkdir()

        await rig.service.choose_tender_place(str(rig.drive("SD")))

        assert (rig.home / "Tender" / "recovery").is_dir()

    async def test_choosing_switches_the_setting_and_moves_nothing(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.service.create_at_start()
        (rig.home / "Tender" / "recovery").mkdir()

        await rig.service.choose_tender_place(str(rig.drive("SD")))

        assert os.listdir(tmp_path / "media" / "SD" / "Tender") == [".tender-folder"]

    async def test_a_running_cleanup_refuses_a_choice(self, tmp_path):
        rig = _Rig(tmp_path, cleanup=True)

        with pytest.raises(Refused) as refused:
            await rig.service.choose_tender_place(None)

        assert refused.value.reason == "prune_active"

    async def test_another_place_is_a_way_out_of_an_unreachable_folder(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD", connected=False)
        rig.on_drive(sd)
        rig.records.folders.add(str(sd / "Tender"))

        state = await rig.service.choose_tender_place(None)

        assert state["unreachable"] is None
        assert state["elsewhere"] == [str(sd / "Tender")]


class TestWhatIsRecordedElsewhere:
    async def test_after_a_reset_using_that_folder_puts_the_setting_back_and_copies_nothing(self, tmp_path):
        sd_folder = tmp_path / "media" / "SD" / "Tender"
        rig = _Rig(tmp_path, _Records(str(sd_folder)))
        sd = rig.drive("SD")
        (sd / "Tender").mkdir()

        state = await rig.service.use_recorded_tender_folder(str(sd_folder))

        assert rig.settings["tender_folder"] == {"place": str(sd), "mount_point": str(sd)}
        assert state["elsewhere"] == []
        assert state["unreachable"] is None
        assert not (rig.home / "Tender").exists()

    async def test_using_that_folder_on_a_drive_that_is_gone_keeps_its_drive_as_the_mount_point(self, tmp_path):
        sd_folder = tmp_path / "media" / "SD" / "Tender"
        rig = _Rig(tmp_path, _Records(str(sd_folder)))

        state = await rig.service.use_recorded_tender_folder(str(sd_folder))

        assert rig.settings["tender_folder"]["mount_point"] == str(tmp_path / "media" / "SD")
        assert state["unreachable"] == "drive_not_mounted"

    async def test_a_folder_nothing_is_recorded_under_is_refused(self, tmp_path):
        rig = _Rig(tmp_path, _Records())

        with pytest.raises(Refused) as refused:
            await rig.service.use_recorded_tender_folder(str(tmp_path / "elsewhere" / "Tender"))

        assert refused.value.reason == "invalid_value"
        assert refused.value.message == (
            f"Tender has nothing recorded under {tmp_path / 'elsewhere' / 'Tender'} any more."
        )
        assert "tender_folder" not in rig.settings

    async def test_going_back_to_the_folder_that_is_already_the_setting_is_refused_saying_so(self, tmp_path):
        rig = _Rig(tmp_path, _Records(str(tmp_path / "home" / "Tender")))

        with pytest.raises(Refused) as refused:
            await rig.service.use_recorded_tender_folder(str(tmp_path / "home" / "Tender"))

        assert refused.value.message == f"{tmp_path / 'home' / 'Tender'} is already Tender's folder."

    async def test_forgetting_asks_every_store_and_touches_no_file(self, tmp_path):
        sd_folder = str(tmp_path / "media" / "SD" / "Tender")
        first, second = _Records(sd_folder), _Records(sd_folder, str(tmp_path / "other" / "Tender"))
        rig = _Rig(tmp_path, first, second)
        sd = rig.drive("SD")
        (sd / "Tender").mkdir()
        (sd / "Tender" / "kept.bin").write_bytes(b"x")

        state = await rig.service.forget_tender_folder_records(sd_folder)

        assert first.forgotten == second.forgotten == [sd_folder]
        assert state["elsewhere"] == [str(tmp_path / "other" / "Tender")]
        assert (sd / "Tender" / "kept.bin").read_bytes() == b"x"

    async def test_forgetting_ends_an_unreachable_folders_notice(self, tmp_path):
        rig = _Rig(tmp_path)
        sd = rig.drive("SD", connected=False)
        rig.on_drive(sd)
        rig.records.folders.add(str(sd / "Tender"))

        state = await rig.service.forget_tender_folder_records(str(sd / "Tender"))

        assert state["unreachable"] is None

    async def test_forgetting_a_folder_nothing_is_recorded_under_is_refused(self, tmp_path):
        rig = _Rig(tmp_path, _Records())

        with pytest.raises(Refused) as refused:
            await rig.service.forget_tender_folder_records("/nowhere/Tender")

        assert refused.value.reason == "invalid_value"
        assert refused.value.message == "Tender has nothing recorded under /nowhere/Tender any more."

    async def test_a_running_cleanup_refuses_forgetting(self, tmp_path):
        rig = _Rig(tmp_path, _Records(str(tmp_path / "home" / "Tender")), cleanup=True)

        with pytest.raises(Refused) as refused:
            await rig.service.forget_tender_folder_records(str(tmp_path / "home" / "Tender"))

        assert refused.value.reason == "prune_active"
        assert rig.records.forgotten == []

    async def test_forgetting_is_not_refused_by_the_folder_rule_it_is_the_way_out_of(self, tmp_path):
        rig = _Rig(tmp_path, folder_unreachable=Refused("folder_unreachable", "unreachable"))
        sd = rig.drive("SD", connected=False)
        rig.on_drive(sd)
        rig.records.folders.add(str(sd / "Tender"))

        state = await rig.service.forget_tender_folder_records(str(sd / "Tender"))

        assert state["unreachable"] is None
