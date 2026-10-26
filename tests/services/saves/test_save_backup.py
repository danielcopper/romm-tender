"""Tests for SaveBackup — the one funnel a save file is moved aside through."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from fakes.system_time import FakeClock

from domain.save_backup import BACKUP_DIR_NAME, BACKUP_RETENTION, backup_name, backup_timestamp
from tests.services.saves._helpers import make_service

_NOW = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def _service(tmp_path):
    svc, _ = make_service(tmp_path, clock=FakeClock(now=_NOW))
    return svc


class TestTheClaimedBackupIsNamedByTheSameRule:
    """The removed-game cleanup's backup takes its name and time stamp where every other backup does."""

    def test_a_claimed_backup_is_named_with_the_backups_own_time_stamp(self, tmp_path):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)
        save = saves_dir / "game.srm"
        save.write_bytes(b"the only copy")
        store = svc._save_file_store
        claim = store.claim_source(str(save), str(tmp_path / "saves"))

        outcome = svc._save_backup.back_up_claimed_save_file(
            str(saves_dir), "game.srm", claim=claim, safe_root=str(tmp_path / "saves")
        )

        assert outcome["success"] is True
        expected = backup_name("game.srm", backup_timestamp(_NOW), set())
        assert expected == "game_20260304_050607.srm"
        assert (saves_dir / BACKUP_DIR_NAME / expected).read_bytes() == b"the only copy"
        assert not save.exists()

    def test_a_claimed_backup_beside_an_ordinary_one_of_the_same_second_takes_the_next_name(self, tmp_path):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)
        save = saves_dir / "game.srm"
        save.write_bytes(b"first")
        assert svc._save_backup.back_up_save_file(str(saves_dir), "game.srm", keep=10) is True
        save.write_bytes(b"second")
        claim = svc._save_file_store.claim_source(str(save), str(tmp_path / "saves"))

        svc._save_backup.back_up_claimed_save_file(
            str(saves_dir), "game.srm", claim=claim, safe_root=str(tmp_path / "saves")
        )

        backups = sorted(os.listdir(saves_dir / BACKUP_DIR_NAME))
        assert backups == ["game_20260304_050607.srm", "game_20260304_050607_1.srm"]
        assert (saves_dir / BACKUP_DIR_NAME / "game_20260304_050607_1.srm").read_bytes() == b"second"

    def test_a_claimed_backup_keeps_every_earlier_backup(self, tmp_path):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        backup_dir = saves_dir / BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True)
        earlier = [f"game_202601{day:02d}_000000.srm" for day in range(1, 13)]
        for name in earlier:
            (backup_dir / name).write_bytes(b"old")
        save = saves_dir / "game.srm"
        save.write_bytes(b"now")
        claim = svc._save_file_store.claim_source(str(save), str(tmp_path / "saves"))

        svc._save_backup.back_up_claimed_save_file(
            str(saves_dir), "game.srm", claim=claim, safe_root=str(tmp_path / "saves")
        )

        assert sorted(os.listdir(backup_dir)) == [*earlier, "game_20260304_050607.srm"]


class TestAnOrdinaryBackupKeepsWhatItIsTold:
    def test_the_retention_is_the_callers(self, tmp_path):
        clock = FakeClock(now=_NOW)
        svc, _ = make_service(tmp_path, clock=clock)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)
        for n in range(4):
            (saves_dir / "game.srm").write_bytes(f"v{n}".encode())
            clock.advance(1)
            assert svc._save_backup.back_up_save_file(str(saves_dir), "game.srm", keep=2) is True

        survivors = sorted(os.listdir(saves_dir / BACKUP_DIR_NAME))
        assert [(saves_dir / BACKUP_DIR_NAME / name).read_bytes() for name in survivors] == [b"v2", b"v3"]


class TestSaveBackupRetention:
    """back_up_save_file's collision-proof naming + retention pruning (#974).

    The default ``FakeClock(now=datetime(2026, 1, 1))`` stamps the same
    ``20260101_000000`` timestamp until a test calls ``clock.advance(...)``, so
    same-second collision tests run it fixed while ordering-sensitive retention
    tests advance it for distinct timestamps.
    """

    def test_same_second_collision_keeps_both_backups(self, tmp_path):
        """Two same-second backups of one file produce two distinct backups —
        the earlier copy is never overwritten by the ``_<n>`` counter."""
        svc, _ = make_service(tmp_path)
        backup = svc._save_backup
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True, exist_ok=True)
        save_path = saves_dir / "game.srm"

        save_path.write_bytes(b"first version")
        assert backup.back_up_save_file(str(saves_dir), "game.srm", keep=BACKUP_RETENTION) is True

        save_path.write_bytes(b"second version")
        assert backup.back_up_save_file(str(saves_dir), "game.srm", keep=BACKUP_RETENTION) is True

        backup_dir = saves_dir / ".romm-backup"
        first = backup_dir / "game_20260101_000000.srm"
        second = backup_dir / "game_20260101_000000_1.srm"
        assert first.exists()
        assert second.exists()
        # The earlier backup survived the same-second second backup.
        assert first.read_bytes() == b"first version"
        assert second.read_bytes() == b"second version"

    def test_retention_caps_backups_per_file_and_spares_others(self, tmp_path):
        """More than the retention limit of backups for one file leaves exactly
        ``BACKUP_RETENTION`` backups for its stem — the NEWEST versions — while a
        different file's backup is untouched.

        Uses an advancing clock so every backup has a distinct ``<ts>`` and the
        chronological prune ordering is unambiguous.
        """
        from datetime import UTC, datetime

        from fakes.system_time import FakeClock

        clock = FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC))
        svc, _ = make_service(tmp_path, clock=clock)
        backup = svc._save_backup
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = saves_dir / ".romm-backup"

        # A backup of a DIFFERENT save file that must survive game.srm pruning.
        (saves_dir / "mario.srm").write_bytes(b"mario save")
        assert backup.back_up_save_file(str(saves_dir), "mario.srm", keep=BACKUP_RETENTION) is True

        total = BACKUP_RETENTION + 5
        contents = [f"game v{i}".encode() for i in range(total)]
        for content in contents:
            clock.advance(1)  # distinct <ts> per backup → no same-second collisions
            (saves_dir / "game.srm").write_bytes(content)
            assert backup.back_up_save_file(str(saves_dir), "game.srm", keep=BACKUP_RETENTION) is True

        entries = os.listdir(backup_dir)
        game_backups = [n for n in entries if n.startswith("game_")]
        assert len(game_backups) == BACKUP_RETENTION
        # The survivors are exactly the newest BACKUP_RETENTION versions written…
        survivor_contents = {(backup_dir / n).read_bytes() for n in game_backups}
        assert survivor_contents == set(contents[-BACKUP_RETENTION:])
        # …and the oldest 5 are gone.
        assert all(c not in survivor_contents for c in contents[:5])
        # The unrelated file's single backup was never pruned.
        assert [n for n in entries if n.startswith("mario_")] == ["mario_20260101_000000.srm"]

    def test_same_second_over_cap_never_deletes_just_created(self, tmp_path):
        """Same-second churn past the cap must NEVER destroy the file it just backed up.

        Regression for the prune-deletes-its-own-backup bug (#974): once the dir is
        at cap a freed base name is reused for the newest file, and the base sorts
        oldest — so an unguarded prune would delete the live save it just
        backed up. After every backup the just-written content must still be
        recoverable somewhere in ``.romm-backup``; the folder stays bounded.
        """

        svc, _ = make_service(tmp_path)
        backup = svc._save_backup  # fixed clock → every backup is same-second
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = saves_dir / ".romm-backup"

        total = BACKUP_RETENTION + 5
        for i in range(total):
            content = f"game v{i}".encode()
            (saves_dir / "game.srm").write_bytes(content)
            assert backup.back_up_save_file(str(saves_dir), "game.srm", keep=BACKUP_RETENTION) is True
            # The file just backed up survived its own prune pass.
            present = {(backup_dir / n).read_bytes() for n in os.listdir(backup_dir)}
            assert content in present
            # Bounded: the cap plus at most the one just-created copy.
            assert len(os.listdir(backup_dir)) <= BACKUP_RETENTION + 1

        # The very last version written is still present at the end.
        final = {(backup_dir / n).read_bytes() for n in os.listdir(backup_dir)}
        assert f"game v{total - 1}".encode() in final

    def test_returns_false_when_nothing_to_back_up(self, tmp_path):
        """No file at *filename* → early return False, no backup dir churn."""
        svc, _ = make_service(tmp_path)
        backup = svc._save_backup
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True, exist_ok=True)

        assert backup.back_up_save_file(str(saves_dir), "absent.srm", keep=BACKUP_RETENTION) is False

    def test_symlinked_backup_directory_fails_closed(self, tmp_path):
        svc, _ = make_service(tmp_path)
        backup = svc._save_backup
        saves_dir = tmp_path / "saves" / "gba"
        outside = tmp_path / "outside"
        saves_dir.mkdir(parents=True, exist_ok=True)
        outside.mkdir()
        save = saves_dir / "game.srm"
        save.write_bytes(b"keep")
        (saves_dir / ".romm-backup").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="Unsafe save backup directory"):
            backup.back_up_save_file(str(saves_dir), "game.srm", keep=BACKUP_RETENTION)

        assert save.read_bytes() == b"keep"
        assert list(outside.iterdir()) == []


class TestARetentionRemovalThatFails:
    """A backup whose move went through answers moved even when removing an older backup fails afterwards."""

    def test_the_file_is_answered_as_moved_and_the_older_backup_is_kept(self, tmp_path, monkeypatch, caplog):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        backup_dir = saves_dir / BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True)
        older = [backup_dir / f"game_20250101_00000{i}.srm" for i in range(BACKUP_RETENTION)]
        for path in older:
            path.write_bytes(b"older")
        save = saves_dir / "game.srm"
        save.write_bytes(b"the only copy")

        def remove_file(_path: str) -> None:
            raise PermissionError("backup folder is read-only")

        monkeypatch.setattr(svc._save_file_store, "remove_file", remove_file)

        with caplog.at_level("WARNING", logger="test"):
            moved = svc._save_backup.back_up_save_file(str(saves_dir), "game.srm", keep=BACKUP_RETENTION)

        assert moved is True
        assert not save.exists()
        assert (backup_dir / backup_name("game.srm", backup_timestamp(_NOW), set())).read_bytes() == b"the only copy"
        assert all(path.exists() for path in older)
        assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [
            "Kept an older backup of game.srm past its retention: game_20250101_000000.srm (backup folder is read-only)"
        ]


class TestPreparingTheBackupFolder:
    """``prepare_save_backup_folder`` makes ``.romm-backup`` ready, or raises, and moves nothing."""

    def test_a_missing_folder_is_created_and_no_save_moves(self, tmp_path):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)
        save = saves_dir / "game.srm"
        save.write_bytes(b"keep")

        svc._save_backup.prepare_save_backup_folder(str(saves_dir))

        assert list((saves_dir / BACKUP_DIR_NAME).iterdir()) == []
        assert save.read_bytes() == b"keep"

    def test_a_folder_that_is_a_link_raises(self, tmp_path):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (saves_dir / BACKUP_DIR_NAME).symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="Unsafe save backup directory"):
            svc._save_backup.prepare_save_backup_folder(str(saves_dir))

    def test_a_folder_that_cannot_be_created_raises_the_stores_error(self, tmp_path, monkeypatch):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)

        def make_dirs(_path: str) -> None:
            raise PermissionError("saves folder is read-only")

        monkeypatch.setattr(svc._save_file_store, "make_dirs", make_dirs)

        with pytest.raises(PermissionError, match="read-only"):
            svc._save_backup.prepare_save_backup_folder(str(saves_dir))

    def test_a_folder_that_cannot_be_listed_raises_the_stores_error(self, tmp_path, monkeypatch):
        svc = _service(tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)

        def listdir(_path: str) -> list[str]:
            raise PermissionError("backup folder cannot be read")

        monkeypatch.setattr(svc._save_file_store, "listdir", listdir)

        with pytest.raises(PermissionError, match="cannot be read"):
            svc._save_backup.prepare_save_backup_folder(str(saves_dir))
