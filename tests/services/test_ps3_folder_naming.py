"""The one-time step naming PS3 folder installs as ES-DE scans them (#2195 D11, D27)."""

from __future__ import annotations

import logging

from fakes.fake_download_file_store import FakeDownloadFileStore
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.rom import Rom
from domain.rom_install import RomInstall
from services.ps3_folder_naming import (
    KV_PS3_FOLDERS_LIFTED,
    KV_PS3_FOLDERS_NAMED,
    KV_PS3_FOLDERS_TO_LIFT,
    KV_PS3_FOLDERS_TO_NAME,
    Ps3FolderNamingService,
    Ps3FolderNamingServiceConfig,
)

_EBOOT = "PS3_GAME/USRDIR/EBOOT.BIN"

# ES-DE's ps3 accept-list as RetroDECK ships it.
_PS3_ACCEPTED = frozenset({".desktop", ".iso", ".ISO", ".ps3", ".PS3", ".ps3dir", ".PS3DIR"})


class _Harness:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.store = FakeDownloadFileStore()
        self.accepted: dict[str, frozenset[str]] = {"ps3": _PS3_ACCEPTED}
        self.service = Ps3FolderNamingService(
            config=Ps3FolderNamingServiceConfig(
                uow_factory=FakeUnitOfWorkFactory(self.uow),
                file_store=self.store,
                system_extensions=lambda system_name, reading=None: self.accepted.get(system_name, frozenset()),
                logger=logging.getLogger("test_ps3_folder_naming"),
            )
        )

    def install(self, rom_id: int, *, rom_dir: str | None, file_path: str, on_disk: bool = True) -> None:
        with self.uow:
            self.uow.roms.save(
                Rom(
                    rom_id=rom_id,
                    platform_slug="ps3",
                    name=f"Game {rom_id}",
                    fs_name=f"game_{rom_id}",
                    shortcut_app_id=1000 + rom_id,
                    last_synced_at="2026-01-01T00:00:00+00:00",
                )
            )
            self.uow.rom_installs.save(
                RomInstall(
                    rom_id=rom_id,
                    file_path=file_path,
                    rom_dir=rom_dir,
                    platform_slug="ps3",
                    system="ps3",
                    installed_at="2026-01-01T00:00:00+00:00",
                )
            )
        if on_disk:
            self.store.files[file_path] = b"x"

    def rom_dir(self, rom_id: int) -> str | None:
        install = self.uow.rom_installs.get(rom_id)
        assert install is not None
        return install.rom_dir

    def stamped(self) -> bool:
        return self.uow.kv_config.get(KV_PS3_FOLDERS_NAMED) == "1"

    def lifted(self) -> bool:
        return self.uow.kv_config.get(KV_PS3_FOLDERS_LIFTED) == "1"

    def file_path(self, rom_id: int) -> str:
        install = self.uow.rom_installs.get(rom_id)
        assert install is not None
        return install.file_path


class TestAPs3FolderIsRenamedOnce:
    def test_the_folder_and_its_install_record_take_the_name_es_de_scans(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}")

        h.service.name_ps3_folders()

        assert f"/roms/ps3/MyGame.ps3/{_EBOOT}" in h.store.files
        assert f"/roms/ps3/MyGame/{_EBOOT}" not in h.store.files
        install = h.uow.rom_installs.get(1)
        assert install is not None
        assert (install.rom_dir, install.file_path) == ("/roms/ps3/MyGame.ps3", f"/roms/ps3/MyGame.ps3/{_EBOOT}")
        assert h.stamped()

    def test_a_folder_already_named_so_and_every_other_install_are_left_alone(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/Named.ps3", file_path=f"/roms/ps3/Named.ps3/{_EBOOT}")
        h.install(2, rom_dir="/roms/ps3/Puppeteer", file_path="/roms/ps3/Puppeteer/Puppeteer.pkg")
        h.install(3, rom_dir=None, file_path="/roms/ps3/Game.iso")
        before = dict(h.store.files)

        h.service.name_ps3_folders()

        assert h.store.files == before
        assert h.rom_dir(2) == "/roms/ps3/Puppeteer"
        assert h.stamped()

    def test_a_stamped_step_runs_no_more(self):
        h = _Harness()
        h.uow.kv_config.set(KV_PS3_FOLDERS_NAMED, "1")
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}")

        h.service.name_ps3_folders()

        assert f"/roms/ps3/MyGame/{_EBOOT}" in h.store.files

    def test_running_it_again_renames_nothing_twice(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}")
        h.service.name_ps3_folders()
        h.uow.kv_config.set(KV_PS3_FOLDERS_NAMED, "")

        h.service.name_ps3_folders()

        assert f"/roms/ps3/MyGame.ps3/{_EBOOT}" in h.store.files
        assert h.rom_dir(1) == "/roms/ps3/MyGame.ps3"


class TestWhatIsLeftForTheNextStart:
    def test_a_folder_that_is_not_there_keeps_its_record_and_the_step_unstamped(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}", on_disk=False)

        h.service.name_ps3_folders()

        assert h.rom_dir(1) == "/roms/ps3/MyGame"
        assert not h.stamped()

    def test_a_taken_name_keeps_the_folder_as_it_is_and_counts_as_done(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}")
        h.store.files["/roms/ps3/MyGame.ps3/other.txt"] = b"y"

        h.service.name_ps3_folders()

        assert f"/roms/ps3/MyGame/{_EBOOT}" in h.store.files
        assert h.rom_dir(1) == "/roms/ps3/MyGame"
        assert h.stamped()

    def test_a_move_that_fails_keeps_the_record_and_the_step_unstamped(self, monkeypatch):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}")

        def refuse(src: str, dst: str) -> None:
            raise PermissionError(src)

        monkeypatch.setattr(h.store, "move_dir", refuse)

        h.service.name_ps3_folders()

        assert h.rom_dir(1) == "/roms/ps3/MyGame"
        assert not h.stamped()

    def test_a_folder_moved_whose_record_still_names_its_old_place_gets_its_record_moved(self):
        # A pass stopped between the move and the record, or a database the
        # installer's rollback put back: the folder is at its new name already.
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}", on_disk=False)
        h.store.files[f"/roms/ps3/MyGame.ps3/{_EBOOT}"] = b"x"

        h.service.name_ps3_folders()

        install = h.uow.rom_installs.get(1)
        assert install is not None
        assert (install.rom_dir, install.file_path) == ("/roms/ps3/MyGame.ps3", f"/roms/ps3/MyGame.ps3/{_EBOOT}")
        assert h.stamped()

    def test_a_record_that_cannot_be_moved_stops_neither_the_others_nor_the_next_start(self, monkeypatch):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/First", file_path=f"/roms/ps3/First/{_EBOOT}")
        h.install(2, rom_dir="/roms/ps3/Second", file_path=f"/roms/ps3/Second/{_EBOOT}")
        save = h.uow.rom_installs.save

        def refuse_the_first(install: RomInstall) -> None:
            if install.rom_id == 1:
                raise RuntimeError("database is locked")
            save(install)

        monkeypatch.setattr(h.uow.rom_installs, "save", refuse_the_first)

        h.service.name_ps3_folders()

        assert h.rom_dir(1) == "/roms/ps3/First"
        assert h.rom_dir(2) == "/roms/ps3/Second.ps3"
        assert not h.stamped()

        monkeypatch.setattr(h.uow.rom_installs, "save", save)
        h.service.name_ps3_folders()

        assert h.rom_dir(1) == "/roms/ps3/First.ps3"
        assert h.stamped()


class TestTheStepsSubjectIsFixedAtItsFirstPass:
    def test_a_folder_adopted_after_the_first_pass_is_left_as_it_stands(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/Missing", file_path=f"/roms/ps3/Missing/{_EBOOT}", on_disk=False)
        h.service.name_ps3_folders()
        assert not h.stamped()

        h.install(2, rom_dir="/roms/ps3/Adopted", file_path=f"/roms/ps3/Adopted/{_EBOOT}")
        h.service.name_ps3_folders()

        assert h.rom_dir(2) == "/roms/ps3/Adopted"
        assert f"/roms/ps3/Adopted/{_EBOOT}" in h.store.files

    def test_the_first_pass_records_which_installs_it_renames(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/{_EBOOT}", on_disk=False)
        h.install(2, rom_dir="/roms/ps3/Named.ps3", file_path=f"/roms/ps3/Named.ps3/{_EBOOT}")

        h.service.name_ps3_folders()

        assert h.uow.kv_config.get(KV_PS3_FOLDERS_TO_NAME) == "[1]"


class TestANameEsDeAlreadyScans:
    def test_a_folder_named_with_a_suffix_the_accept_list_carries_is_left_alone(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.PS3", file_path=f"/roms/ps3/MyGame.PS3/{_EBOOT}")
        before = dict(h.store.files)

        h.service.name_ps3_folders()

        assert h.store.files == before
        assert h.rom_dir(1) == "/roms/ps3/MyGame.PS3"
        assert h.stamped()


class TestAGameFolderOneLevelDeeperIsLiftedOnce:
    """A game folder one level inside its install's folder is placed as that folder (#2195 D39)."""

    def test_the_game_folder_takes_the_install_folder_s_place_and_the_record_follows(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}")
        h.store.files["/roms/ps3/MyGame.ps3/Inner/PS3_DISC.SFB"] = b"s"

        h.service.name_ps3_folders()

        assert sorted(h.store.files) == [
            "/roms/ps3/MyGame.ps3/PS3_DISC.SFB",
            f"/roms/ps3/MyGame.ps3/{_EBOOT}",
        ]
        assert (h.rom_dir(1), h.file_path(1)) == ("/roms/ps3/MyGame.ps3", f"/roms/ps3/MyGame.ps3/{_EBOOT}")
        assert h.lifted()

    def test_an_install_named_neither_way_is_named_and_lifted_at_one_start(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/Inner/{_EBOOT}")

        h.service.name_ps3_folders()

        assert (h.rom_dir(1), h.file_path(1)) == ("/roms/ps3/MyGame.ps3", f"/roms/ps3/MyGame.ps3/{_EBOOT}")
        assert list(h.store.files) == [f"/roms/ps3/MyGame.ps3/{_EBOOT}"]
        assert h.stamped() and h.lifted()

    def test_a_game_folder_with_anything_beside_it_is_left_where_it_is_and_counts_as_done(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}")
        h.store.files["/roms/ps3/MyGame.ps3/readme.txt"] = b"kept"
        before = dict(h.store.files)

        h.service.name_ps3_folders()

        assert h.store.files == before
        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}"
        assert h.lifted()

    def test_a_stamped_lift_runs_no_more_while_the_naming_step_still_runs(self):
        h = _Harness()
        h.uow.kv_config.set(KV_PS3_FOLDERS_LIFTED, "1")
        h.install(1, rom_dir="/roms/ps3/MyGame", file_path=f"/roms/ps3/MyGame/Inner/{_EBOOT}")

        h.service.name_ps3_folders()

        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}"

    def test_a_stamped_naming_step_does_not_stop_the_lift(self):
        h = _Harness()
        h.uow.kv_config.set(KV_PS3_FOLDERS_NAMED, "1")
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}")

        h.service.name_ps3_folders()

        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/{_EBOOT}"

    def test_running_it_again_lifts_nothing_twice(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}")
        h.service.name_ps3_folders()
        h.uow.kv_config.set(KV_PS3_FOLDERS_LIFTED, "")

        h.service.name_ps3_folders()

        assert list(h.store.files) == [f"/roms/ps3/MyGame.ps3/{_EBOOT}"]
        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/{_EBOOT}"

    def test_the_first_pass_records_which_installs_it_lifts(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/A.ps3", file_path=f"/roms/ps3/A.ps3/Inner/{_EBOOT}", on_disk=False)
        h.install(2, rom_dir="/roms/ps3/B.ps3", file_path=f"/roms/ps3/B.ps3/{_EBOOT}")

        h.service.name_ps3_folders()

        assert h.uow.kv_config.get(KV_PS3_FOLDERS_TO_LIFT) == "[1]"
        assert not h.lifted()

    def test_a_folder_adopted_after_the_first_pass_is_left_as_it_stands(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/A.ps3", file_path=f"/roms/ps3/A.ps3/Inner/{_EBOOT}", on_disk=False)
        h.service.name_ps3_folders()

        h.install(2, rom_dir="/roms/ps3/B.ps3", file_path=f"/roms/ps3/B.ps3/Inner/{_EBOOT}")
        h.service.name_ps3_folders()

        assert h.file_path(2) == f"/roms/ps3/B.ps3/Inner/{_EBOOT}"


class TestALiftStoppedPartWayIsFinishedAtTheNextStart:
    def test_a_game_folder_waiting_beside_an_emptied_install_folder_is_put_in_place(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}", on_disk=False)
        h.store.files[f"/roms/ps3/MyGame.ps3.tender-lift/{_EBOOT}"] = b"x"
        h.store.dirs.add("/roms/ps3/MyGame.ps3")

        h.service.name_ps3_folders()

        assert list(h.store.files) == [f"/roms/ps3/MyGame.ps3/{_EBOOT}"]
        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/{_EBOOT}"
        assert h.lifted()

    def test_a_game_folder_waiting_where_the_install_folder_is_gone_is_put_in_place(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}", on_disk=False)
        h.store.files[f"/roms/ps3/MyGame.ps3.tender-lift/{_EBOOT}"] = b"x"

        h.service.name_ps3_folders()

        assert list(h.store.files) == [f"/roms/ps3/MyGame.ps3/{_EBOOT}"]
        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/{_EBOOT}"

    def test_a_lifted_folder_whose_record_still_names_the_folder_inside_gets_its_record_moved(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}", on_disk=False)
        h.store.files[f"/roms/ps3/MyGame.ps3/{_EBOOT}"] = b"x"

        h.service.name_ps3_folders()

        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/{_EBOOT}"
        assert h.lifted()

    def test_a_folder_that_is_not_there_keeps_its_record_and_the_lift_unstamped(self):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}", on_disk=False)

        h.service.name_ps3_folders()

        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}"
        assert not h.lifted()

    def test_a_move_that_fails_keeps_the_record_and_the_lift_unstamped(self, monkeypatch):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}")

        def refuse(src: str, dst: str) -> None:
            raise PermissionError(src)

        monkeypatch.setattr(h.store, "move_dir", refuse)

        h.service.name_ps3_folders()

        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}"
        assert f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}" in h.store.files
        assert not h.lifted()

    def test_an_install_folder_that_cannot_be_emptied_keeps_the_game_waiting_for_the_next_start(self, monkeypatch):
        h = _Harness()
        h.install(1, rom_dir="/roms/ps3/MyGame.ps3", file_path=f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}")
        # The folder stays on disk once emptied, which the fake models only as a known directory.
        h.store.dirs.add("/roms/ps3/MyGame.ps3")

        def refuse(path: str) -> None:
            raise OSError(path)

        monkeypatch.setattr(h.store, "remove_empty_dir", refuse)

        h.service.name_ps3_folders()

        assert f"/roms/ps3/MyGame.ps3.tender-lift/{_EBOOT}" in h.store.files
        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/Inner/{_EBOOT}"
        assert not h.lifted()

        monkeypatch.undo()
        h.service.name_ps3_folders()

        assert list(h.store.files) == [f"/roms/ps3/MyGame.ps3/{_EBOOT}"]
        assert h.file_path(1) == f"/roms/ps3/MyGame.ps3/{_EBOOT}"
        assert h.lifted()
