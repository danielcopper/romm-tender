"""Tests for the SAVES tab's read with save sync off: this device's save files, or the refusal, asking RomM nothing."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, cast

import pytest
from fakes.fake_save_location_reader import FakeSaveLocationReader

from domain.answered_save_directory import AnsweredSaveDirectory
from domain.iso_time import epoch_to_iso
from domain.save_answer import SaveAnswer, SaveComponent, saves_root_missing_answer
from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _install_rom,
    _seed_rom,
    _uow,
    make_service,
)

if TYPE_CHECKING:
    from pathlib import Path

_ROM = 42
_MISSING_ROOT = "/run/media/deck/sd/retrodeck/saves"
_SHARED_CARD = SaveAnswer(
    state="shared",
    unestablished=None,
    emulator="PCSX2 (Standalone)",
    directory="/saves/ps2/pcsx2/memcards",
    backing_directory=None,
    granularity="shared-card",
    needs=(),
    components=(),
    caveats=(),
    content_installed=True,
)


def _reader(svc) -> FakeSaveLocationReader:
    return cast("FakeSaveLocationReader", svc._rom_info._save_locations)


def _per_game_answer(directory: str) -> SaveAnswer:
    return SaveAnswer(
        state="per_game_files",
        unestablished=None,
        emulator="mGBA",
        directory=directory,
        backing_directory=None,
        granularity="per-game-file",
        needs=(),
        components=(SaveComponent(name="pokemon.srm", directory=directory, role="battery", granularity=None),),
        caveats=(),
        content_installed=True,
    )


def _unsyncable(tmp_path, kind: str):
    """A service whose installed rom 42 answers *kind*, with a save on disk where a syncing answer would look."""
    svc, fake = make_service(tmp_path, save_locations=FakeSaveLocationReader(beside_content=kind == "beside_content"))
    _install_rom(svc, tmp_path)
    _create_save(tmp_path)
    if kind == "shared":
        _reader(svc).answer_with("gba", _SHARED_CARD)
    elif kind == "saves_root_missing":
        _reader(svc).answer_with(
            "gba", saves_root_missing_answer(path=_MISSING_ROOT, emulator="mGBA", content_installed=True)
        )
    return svc, fake


_UNSYNCABLE = [
    pytest.param(
        "shared",
        "save_shape_unsupported",
        "Save sync is unavailable: this emulator keeps one save card that all games share. (PCSX2 (Standalone))",
        id="shared-card",
    ),
    pytest.param(
        "beside_content",
        "savefiles_in_content_dir",
        "Save sync is unavailable: saves are written to the game's content directory.",
        id="beside-the-game-file",
    ),
    pytest.param(
        "saves_root_missing",
        "saves_root_missing",
        f"Save sync is unavailable: the saves folder {_MISSING_ROOT} does not exist.",
        id="saves-folder-missing",
    ),
]


class TestTheFilesThisDeviceHolds:
    @pytest.mark.asyncio
    async def test_lists_each_save_file_with_its_folder_size_and_last_change_asking_romm_nothing(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        save = _create_save(tmp_path, content=b"\x01" * 2048)
        os.utime(save, (1_767_225_600, 1_767_225_600))

        result = await svc.get_local_save_files(_ROM)

        assert result == {
            "available": True,
            "installed": True,
            "files": [
                {
                    "filename": "pokemon.srm",
                    "directory": str(tmp_path / "saves" / "gba"),
                    "size": 2048,
                    "modified_at": epoch_to_iso(1_767_225_600),
                }
            ],
        }
        assert fake.call_log == []

    @pytest.mark.asyncio
    async def test_a_game_with_no_save_yet_lists_nothing(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        result = await svc.get_local_save_files(_ROM)

        assert result == {"available": True, "installed": True, "files": []}
        assert fake.call_log == []

    @pytest.mark.asyncio
    async def test_asks_romm_nothing_with_save_sync_on_and_a_device_to_register(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)

        result = await svc.get_local_save_files(_ROM)

        assert [f["filename"] for f in result["files"]] == ["pokemon.srm"]
        assert fake.call_log == []


class TestAGameWhoseSavesCannotSync:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("kind", "reason", "message"), _UNSYNCABLE)
    async def test_answers_the_refusal_and_lists_no_files(self, tmp_path, kind, reason, message):
        svc, fake = _unsyncable(tmp_path, kind)

        result = await svc.get_local_save_files(_ROM)

        assert result["available"] is False
        assert result["installed"] is True
        assert result["reason"] == reason
        assert result["message"] == message
        assert result["files"] == []
        assert fake.call_log == []
        assert len(_reader(svc).calls) == 1

    @pytest.mark.asyncio
    async def test_carries_the_finding_the_panel_words_the_refusal_from(self, tmp_path):
        svc, _fake = _unsyncable(tmp_path, "saves_root_missing")

        result = await svc.get_local_save_files(_ROM)

        assert result["finding"] == {"code": "saves-root-missing", "data": {"path": _MISSING_ROOT}}
        assert result["emulator"] == "mGBA"


class TestAGameNotDownloaded:
    @pytest.mark.asyncio
    async def test_is_asked_about_the_path_it_would_be_downloaded_to_and_lists_no_files(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _seed_rom(svc, _ROM, platform_slug="gba", fs_name="pokemon.gba")
        _create_save(tmp_path)

        result = await svc.get_local_save_files(_ROM)

        assert result == {"available": True, "installed": False, "files": []}
        assert _reader(svc).calls == [("gba", str(tmp_path / "retrodeck" / "roms" / "gba" / "pokemon.gba"), None)]
        assert fake.call_log == []

    @pytest.mark.asyncio
    async def test_whose_saves_would_not_sync_answers_the_refusal(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _seed_rom(svc, _ROM, platform_slug="gba", fs_name="pokemon.gba")
        _reader(svc).answer_with("gba", _SHARED_CARD)

        result = await svc.get_local_save_files(_ROM)

        assert result["available"] is False
        assert result["installed"] is False
        assert result["reason"] == "save_shape_unsupported"
        assert result["files"] == []
        assert fake.call_log == []


class TestTheRomsLock:
    @pytest.mark.asyncio
    async def test_the_read_waits_for_the_roms_sync_lock(self, tmp_path):
        """It may move files (following a moved directory), so it never runs beside another holder of the lock."""
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)

        async with svc._sync_engine.rom_lock(_ROM):
            read = asyncio.ensure_future(svc.get_local_save_files(_ROM))
            await asyncio.sleep(0.05)
            assert not read.done()
        result = await read

        assert [f["filename"] for f in result["files"]] == ["pokemon.srm"]


class TestAMovedSaveDirectory:
    @staticmethod
    def _moved(tmp_path, svc) -> tuple[Path, Path]:
        old = tmp_path / "saves" / "gba"
        new = tmp_path / "saves" / "gba" / "mGBA"
        with _uow(svc) as uow:
            uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=_ROM, directory=str(old)))
        _reader(svc).answer_with("gba", _per_game_answer(str(new)))
        return old, new

    @pytest.mark.asyncio
    async def test_is_followed_before_the_files_are_looked_for(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        _create_save(tmp_path, content=b"progress")
        _old, new = self._moved(tmp_path, svc)

        result = await svc.get_local_save_files(_ROM)

        assert [(f["filename"], f["directory"]) for f in result["files"]] == [("pokemon.srm", str(new))]
        assert (new / "pokemon.srm").read_bytes() == b"progress"
        assert fake.call_log == []

    @pytest.mark.asyncio
    async def test_is_not_followed_for_a_game_not_downloaded(self, tmp_path):
        # Its answer is about the path it would be downloaded to: a prediction,
        # never recorded as the directory its saves lie in.
        svc, _fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _seed_rom(svc, _ROM, platform_slug="gba", fs_name="pokemon.gba")

        result = await svc.get_local_save_files(_ROM)

        assert result == {"available": True, "installed": False, "files": []}
        with _uow(svc) as uow:
            assert uow.answered_save_directories.get(_ROM) is None

    @pytest.mark.asyncio
    async def test_is_not_followed_while_save_sync_is_off(self, tmp_path):
        svc, _fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        save = _create_save(tmp_path, content=b"progress")
        self._moved(tmp_path, svc)

        result = await svc.get_local_save_files(_ROM)

        assert result["files"] == []
        assert save.read_bytes() == b"progress"
