"""The one-time step naming PS3 folder installs as ES-DE scans them (#2195 D11, D27)."""

from __future__ import annotations

import logging

from fakes.fake_download_file_store import FakeDownloadFileStore
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.rom import Rom
from domain.rom_install import RomInstall
from services.ps3_folder_naming import KV_PS3_FOLDERS_NAMED, Ps3FolderNamingService, Ps3FolderNamingServiceConfig

_EBOOT = "PS3_GAME/USRDIR/EBOOT.BIN"


class _Harness:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.store = FakeDownloadFileStore()
        self.service = Ps3FolderNamingService(
            config=Ps3FolderNamingServiceConfig(
                uow_factory=FakeUnitOfWorkFactory(self.uow),
                file_store=self.store,
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
