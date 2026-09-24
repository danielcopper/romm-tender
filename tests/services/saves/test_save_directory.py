"""Tests for following a game's save files when its answered save directory moves.

The follow compares today's answer with the directory recorded for the game and
carries the files where the two differ. What is under test is that comparison,
the file rule, the collision rule's refusal to overwrite or remove anything, and
that the record moves on only once every file arrived — plus the one-time pass
that records every installed game, and the entry points that follow before they
refuse.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import pytest

from domain.answered_save_directory import AnsweredSaveDirectory
from domain.rom_save_sync_state import RomSaveSyncState
from domain.save_answer import SaveAnswer, SaveComponent, unestablished_answer
from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _get_save_state,
    _install_rom,
    _seed_install,
    _seed_save_state,
    _uow,
    make_service,
)

if TYPE_CHECKING:
    from fakes.fake_save_location_reader import FakeSaveLocationReader

_ROM = 42


def _answer(
    directory: str,
    *,
    names: tuple[str, ...] = ("pokemon.srm",),
    state: str = "per_game_files",
    caveats: tuple[str, ...] = (),
    root_kind: str = "savefile_directory",
) -> SaveAnswer:
    return SaveAnswer(
        state=cast("Any", state),
        unestablished=None,
        emulator="mGBA",
        directory=directory,
        backing_directory=None,
        granularity="shared-card" if state == "shared" else "per-game-file",
        needs=(),
        components=tuple(
            SaveComponent(name=name, directory=directory, role="battery", granularity=None) for name in names
        ),
        caveats=caveats,
        content_installed=True,
        root_kind=root_kind,
    )


def _seed_answer(svc, answer: SaveAnswer) -> None:
    cast("FakeSaveLocationReader", svc._rom_info._save_locations).answer_with("gba", answer)


def _record(svc, directory: str, rom_id: int = _ROM) -> None:
    with _uow(svc) as uow:
        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=rom_id, directory=directory))


def _recorded(svc, rom_id: int = _ROM) -> str | None:
    with _uow(svc) as uow:
        record = uow.answered_save_directories.get(rom_id)
    return record.directory if record is not None else None


def _follow(svc, answer: SaveAnswer) -> None:
    svc._sync_engine._follower.do_follow(_ROM, answer)


@pytest.fixture
def dirs(tmp_path):
    """The directory a save used to be answered at, and the one it is answered at now."""
    old = tmp_path / "saves" / "gba"
    new = tmp_path / "saves" / "gba" / "mGBA"
    return old, new


class TestTheComparison:
    def test_a_first_sight_records_the_answer_and_moves_nothing(self, tmp_path, dirs):
        old, _new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        save = _create_save(tmp_path)

        _follow(svc, _answer(str(old)))

        assert _recorded(svc) == str(old)
        assert save.exists()

    def test_the_same_answer_again_does_nothing(self, tmp_path, dirs):
        old, _new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        save = _create_save(tmp_path)

        _follow(svc, _answer(str(old)))

        assert _recorded(svc) == str(old)
        assert save.exists()

    def test_an_answer_with_no_directory_is_never_recorded(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        _follow(svc, unestablished_answer(content_installed=True))

        assert _recorded(svc) is None

    def test_a_first_sight_creates_no_save_sync_state(self, tmp_path, dirs):
        # A save-sync state row means "tracked for save sync"; a game seen for
        # the first time never was.
        old, _new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        _follow(svc, _answer(str(old)))

        assert _recorded(svc) == str(old)
        assert _get_save_state(svc, _ROM) is None

    def test_an_answer_with_no_directory_leaves_the_record_alone(self, tmp_path, dirs):
        old, _new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))

        _follow(svc, unestablished_answer(content_installed=True))

        assert _recorded(svc) == str(old)


class TestAMovedDirectory:
    def test_the_files_are_carried_and_the_record_moves_on(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        new.mkdir(parents=True)
        save = _create_save(tmp_path, content=b"progress")

        _follow(svc, _answer(str(new)))

        assert not save.exists()
        assert (new / "pokemon.srm").read_bytes() == b"progress"
        assert _recorded(svc) == str(new)

    def test_every_file_the_answer_names_is_carried_configuration_included(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        _create_save(tmp_path, ext=".srm")
        _create_save(tmp_path, ext=".smpc")
        new.mkdir(parents=True)

        _follow(svc, _answer(str(new), names=("pokemon.srm", "pokemon.smpc")))

        assert sorted(os.listdir(new)) == ["pokemon.smpc", "pokemon.srm"]

    def test_a_sorted_directory_that_does_not_exist_yet_is_created(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        _create_save(tmp_path, content=b"progress")

        _follow(svc, _answer(str(new), caveats=("sorted-dir-missing",)))

        assert (new / "pokemon.srm").read_bytes() == b"progress"
        assert _recorded(svc) == str(new)

    def test_nothing_to_carry_still_moves_the_record_on(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))

        _follow(svc, _answer(str(new)))

        assert _recorded(svc) == str(new)
        # Nothing to carry, so nothing was created where RetroArch creates it itself.
        assert not new.exists()

    def test_two_spellings_of_one_directory_move_nothing(self, tmp_path):
        # A file "colliding" with itself would send the only copy to the backup.
        real = tmp_path / "saves" / "gba"
        link = tmp_path / "linked"
        real.mkdir(parents=True)
        link.symlink_to(real)
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(real))
        save = _create_save(tmp_path, content=b"progress")

        _follow(svc, _answer(str(link)))

        assert save.read_bytes() == b"progress"
        assert not (real / ".romm-backup").exists()
        assert _recorded(svc) == str(link)


class TestACollisionIsNeverOverwritten:
    def _both(self, tmp_path, old, new, *, old_mtime: float, new_mtime: float):
        old_file = _create_save(tmp_path, content=b"old-dir copy")
        new.mkdir(parents=True)
        new_file = new / "pokemon.srm"
        new_file.write_bytes(b"new-dir copy")
        os.utime(old_file, (old_mtime, old_mtime))
        os.utime(new_file, (new_mtime, new_mtime))
        return old_file, new_file

    def test_an_older_copy_where_the_emulator_looks_is_backed_up_and_replaced(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        old_file, new_file = self._both(tmp_path, old, new, old_mtime=2_000, new_mtime=1_000)

        _follow(svc, _answer(str(new)))

        assert new_file.read_bytes() == b"old-dir copy"
        assert not old_file.exists()
        backups = os.listdir(new / ".romm-backup")
        assert len(backups) == 1
        assert (new / ".romm-backup" / backups[0]).read_bytes() == b"new-dir copy"
        assert _recorded(svc) == str(new)

    def test_an_older_copy_left_behind_is_backed_up_where_it_sits(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        old_file, new_file = self._both(tmp_path, old, new, old_mtime=1_000, new_mtime=2_000)

        _follow(svc, _answer(str(new)))

        assert new_file.read_bytes() == b"new-dir copy"
        assert not old_file.exists()
        backups = os.listdir(old / ".romm-backup")
        assert len(backups) == 1
        assert (old / ".romm-backup" / backups[0]).read_bytes() == b"old-dir copy"

    def test_a_tie_keeps_the_copy_the_emulator_already_reads(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        _old_file, new_file = self._both(tmp_path, old, new, old_mtime=1_000, new_mtime=1_000)

        _follow(svc, _answer(str(new)))

        assert new_file.read_bytes() == b"new-dir copy"
        assert len(os.listdir(old / ".romm-backup")) == 1

    def test_a_folder_in_the_way_carries_nothing_and_keeps_the_record(self, tmp_path, dirs):
        # The funnel moves regular files only, and a move onto a folder would
        # put the save inside it. The next sync tries again.
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        save = _create_save(tmp_path, content=b"progress")
        (new / "pokemon.srm").mkdir(parents=True)

        _follow(svc, _answer(str(new)))

        assert save.read_bytes() == b"progress"
        assert _recorded(svc) == str(old)


class TestARefusingAnswerCarriesWhatIsNamedAfterTheGame:
    def test_files_named_after_the_game_move_and_another_games_stay(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path, file_name="Sonic.gba")
        _record(svc, str(old))
        _create_save(tmp_path, rom_name="Sonic", ext=".srm")
        _create_save(tmp_path, rom_name="Sonic", ext=".rtc")
        other = _create_save(tmp_path, rom_name="Sonic 2", ext=".srm")
        new.mkdir(parents=True)

        _follow(svc, _answer(str(new), names=(), state="shared"))

        assert sorted(name for name in os.listdir(new)) == ["Sonic.rtc", "Sonic.srm"]
        assert other.exists()
        assert _recorded(svc) == str(new)

    def test_never_carries_by_name_out_of_the_contents_own_directory(self, tmp_path):
        # There ``<stem>.*`` is the game itself and its disc images.
        rom_dir = tmp_path / "retrodeck" / "roms" / "gba"
        rom_dir.mkdir(parents=True)
        (rom_dir / "pokemon.gba").write_bytes(b"the game")
        (rom_dir / "pokemon.srm").write_bytes(b"progress")
        new = tmp_path / "saves" / "gba"
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(rom_dir))

        _follow(svc, _answer(str(new), names=(), state="shared"))

        assert sorted(os.listdir(rom_dir)) == ["pokemon.gba", "pokemon.srm"]

    def test_a_syncable_answer_carries_its_named_file_out_of_the_content_directory(self, tmp_path):
        # The names come from the emulator, so the game file itself is never among them.
        rom_dir = tmp_path / "retrodeck" / "roms" / "gba"
        rom_dir.mkdir(parents=True)
        (rom_dir / "pokemon.gba").write_bytes(b"the game")
        (rom_dir / "pokemon.srm").write_bytes(b"progress")
        new = tmp_path / "saves" / "gba"
        new.mkdir(parents=True)
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(rom_dir))

        _follow(svc, _answer(str(new)))

        assert os.listdir(rom_dir) == ["pokemon.gba"]
        assert (new / "pokemon.srm").read_bytes() == b"progress"


class TestNeverIntoTheContentDirectory:
    """Beside the content is a multi-file game's own folder, which an uninstall removes whole."""

    @pytest.fixture
    def rom_dir(self, tmp_path):
        return tmp_path / "retrodeck" / "roms" / "gba"

    def test_a_moved_answer_beside_the_content_moves_nothing_and_keeps_the_record(self, tmp_path, dirs, rom_dir):
        old, _new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        save = _create_save(tmp_path, content=b"progress")

        _follow(svc, _answer(str(rom_dir), root_kind="content_directory"))

        assert save.read_bytes() == b"progress"
        assert not rom_dir.exists()
        assert _recorded(svc) == str(old)

    def test_a_refusing_answer_beside_the_content_moves_nothing(self, tmp_path, dirs, rom_dir):
        # The by-name rule would otherwise carry every never-synced ``<stem>.*`` in.
        old, _new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        save = _create_save(tmp_path, content=b"progress")

        _follow(svc, _answer(str(rom_dir), names=(), state="shared", root_kind="content_directory"))

        assert save.read_bytes() == b"progress"
        assert _recorded(svc) == str(old)

    def test_a_first_sight_beside_the_content_records_nothing(self, tmp_path, rom_dir):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        _follow(svc, _answer(str(rom_dir), root_kind="content_directory"))

        assert _recorded(svc) is None

    def test_switching_the_option_back_finds_the_old_record_and_moves_nothing(self, tmp_path, dirs, rom_dir):
        old, _new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        save = _create_save(tmp_path, content=b"progress")

        _follow(svc, _answer(str(rom_dir), root_kind="content_directory"))
        _follow(svc, _answer(str(old)))

        assert save.read_bytes() == b"progress"
        assert sorted(os.listdir(old)) == ["pokemon.srm"]
        assert _recorded(svc) == str(old)

    @pytest.mark.asyncio
    async def test_the_backfill_records_nothing_beside_the_content(self, tmp_path, rom_dir):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _seed_answer(svc, _answer(str(rom_dir), root_kind="content_directory"))

        await svc.record_save_directories_once()

        assert _recorded(svc) is None


class _WatchedStore:
    """The real save-file store, failing any call made while a Unit of Work is open."""

    def __init__(self, inner: Any, uow: Any) -> None:
        self._inner = inner
        self._uow = uow

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            assert not self._uow.is_open, f"{name} called while a Unit of Work was open"
            return attribute(*args, **kwargs)

        return guarded


class TestNoUnitOfWorkSpansTheFiles:
    def test_every_file_operation_runs_outside_a_transaction(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        _create_save(tmp_path)
        follower = svc._sync_engine._follower
        follower._save_file_store = cast("Any", _WatchedStore(follower._save_file_store, _uow(svc)))

        _follow(svc, _answer(str(new), caveats=("sorted-dir-missing",)))

        assert (new / "pokemon.srm").exists()


class TestTheEntryPointsFollowBeforeTheyRefuse:
    @pytest.mark.asyncio
    async def test_a_refusing_answer_is_still_followed_before_the_skip(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        _create_save(tmp_path, content=b"progress")
        _seed_answer(svc, _answer(str(new), names=(), state="shared"))

        result = await svc.pre_launch_sync(_ROM)

        assert result["reason"] == "save_shape_unsupported"
        assert (new / "pokemon.srm").read_bytes() == b"progress"
        assert _recorded(svc) == str(new)

    @pytest.mark.asyncio
    async def test_the_sync_after_a_follow_finds_the_carried_file(self, tmp_path, dirs):
        old, new = dirs
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        _record(svc, str(old))
        _create_save(tmp_path, content=b"progress")
        _seed_answer(svc, _answer(str(new)))

        result = await svc.post_exit_sync(_ROM)

        assert result["success"] is True
        assert [call for call in fake.call_log if call[0] == "upload_save"]
        assert _recorded(svc) == str(new)

    @pytest.mark.asyncio
    async def test_the_sweep_follows_each_rom_it_syncs(self, tmp_path, dirs):
        old, new = dirs
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        state = RomSaveSyncState()
        state.confirm_slot("default")
        _seed_save_state(svc, _ROM, state)
        _record(svc, str(old))
        _create_save(tmp_path, content=b"progress")
        _seed_answer(svc, _answer(str(new)))

        await svc.sync_all_saves()

        assert (new / "pokemon.srm").read_bytes() == b"progress"
        assert _recorded(svc) == str(new)
        after = _get_save_state(svc, _ROM)
        assert after is not None
        assert after.slot_confirmed is True


class TestTheOneTimeBackfill:
    @pytest.mark.asyncio
    async def test_records_every_installed_rom_that_has_no_record(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path, rom_id=1, file_name="one.gba")
        _install_rom(svc, tmp_path, rom_id=2, file_name="two.gba")
        _record(svc, "/kept/as/it/was", rom_id=2)

        await svc.record_save_directories_once()

        assert _recorded(svc, 1) == str(tmp_path / "saves" / "gba")
        assert _recorded(svc, 2) == "/kept/as/it/was"
        # Recorded beside the save-sync state, never as one: neither game was synced.
        assert _get_save_state(svc, 1) is None
        assert _get_save_state(svc, 2) is None

    @pytest.mark.asyncio
    async def test_a_never_synced_game_still_lists_the_default_slot(self, tmp_path):
        # The listing reads "no save-sync state" as "never tracked" and offers the
        # default slot; a row there would read as the retired legacy mode.
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)

        await svc.record_save_directories_once()
        result = await svc.get_save_slots(_ROM)

        assert _recorded(svc) == str(tmp_path / "saves" / "gba")
        assert result["success"] is True
        assert result["active_slot"] == "autosave"

    @pytest.mark.asyncio
    async def test_runs_once(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        asked = cast("FakeSaveLocationReader", svc._rom_info._save_locations).calls

        await svc.record_save_directories_once()
        first = len(asked)
        with _uow(svc) as uow:
            uow.answered_save_directories.delete(_ROM)
        await svc.record_save_directories_once()

        assert first == 1
        assert len(asked) == first
        assert _recorded(svc) is None

    @pytest.mark.asyncio
    async def test_an_answer_with_no_directory_records_nothing(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        cast("FakeSaveLocationReader", svc._rom_info._save_locations).refuse("gba")

        await svc.record_save_directories_once()

        assert _recorded(svc) is None

    @pytest.mark.asyncio
    async def test_a_rom_with_no_usable_install_row_is_passed_over(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _seed_install(svc, _ROM, file_path="", system="", platform_slug="gba", allow_empty=True)

        await svc.record_save_directories_once()

        assert _recorded(svc) is None

    @pytest.mark.asyncio
    async def test_a_failed_pass_is_not_marked_done(self, tmp_path, caplog):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        def boom(rom_id: int) -> None:
            raise RuntimeError(f"database gone under rom {rom_id}")

        svc._sync_engine._follower.do_record_if_absent = boom

        await svc.record_save_directories_once()

        with _uow(svc) as uow:
            assert uow.kv_config.get("save_directories_recorded") is None
        assert any("save directories failed" in record.message for record in caplog.records)
