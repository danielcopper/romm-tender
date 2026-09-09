"""Tests for the start-up move that takes user data out of the plugin's reach.

Every case runs against real directories under ``tmp_path``: the whole point of
the adapter is what it does to a filesystem, and a faked one would prove nothing
about the staging rename that makes an interrupted copy recoverable.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from datetime import UTC, datetime

import pytest
from fakes.system_time import FakeClock

from adapters.user_data_migration import SourceLocation, UserDataMigrationAdapter

_OLD = "decky-romm-sync"
_NEW = "romm-tender"
_DB = "romm_sync.db"
_SETTINGS = "settings.json"


def _seed_library(data_dir, *, roms: int) -> None:
    """Write a database holding *roms* rows, the way a used install has one."""
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(data_dir / _DB))
    try:
        connection.execute("CREATE TABLE roms (id INTEGER PRIMARY KEY)")
        connection.executemany("INSERT INTO roms (id) VALUES (?)", [(n,) for n in range(1, roms + 1)])
        connection.commit()
    finally:
        connection.close()


def _seed_settings(settings_dir, *, body: str = '{"romm_url": "http://example"}') -> None:
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / _SETTINGS).write_text(body, encoding="utf-8")


def _make(tmp_path, *, clock: FakeClock | None = None) -> UserDataMigrationAdapter:
    """An adapter over a tmp_path layout that mirrors Decky's own."""
    return UserDataMigrationAdapter(
        settings_root=str(tmp_path / "home" / ".config" / "romm-tender"),
        data_root=str(tmp_path / "home" / ".local" / "share" / "romm-tender"),
        fallback_settings_dir=str(tmp_path / "settings" / _NEW),
        fallback_data_dir=str(tmp_path / "data" / _NEW),
        sources=[
            SourceLocation(
                name=name,
                settings_dir=str(tmp_path / "settings" / name),
                data_dir=str(tmp_path / "data" / name),
            )
            for name in (_OLD, _NEW)
        ],
        answer_path=str(tmp_path / "data" / _NEW / "data-location-choice.json"),
        db_filename=_DB,
        settings_filename=_SETTINGS,
        clock=clock if clock is not None else FakeClock(),
        logger=logging.getLogger("test"),
    )


class TestNothingToMigrate:
    def test_creates_both_roots_and_uses_them(self, tmp_path):
        locations = _make(tmp_path).migrate()

        assert locations.settings_dir == str(tmp_path / "home" / ".config" / "romm-tender")
        assert locations.data_dir == str(tmp_path / "home" / ".local" / "share" / "romm-tender")
        assert os.path.isdir(locations.settings_dir)
        assert os.path.isdir(locations.data_dir)
        assert locations.choice_required is False
        assert locations.failure is None

    def test_an_occupied_root_is_left_exactly_as_it_was(self, tmp_path):
        adapter = _make(tmp_path)
        adapter.migrate()
        (tmp_path / "home" / ".local" / "share" / "romm-tender" / _DB).write_text("ours", encoding="utf-8")
        _seed_library(tmp_path / "data" / _OLD, roms=3)

        locations = adapter.migrate()

        assert (tmp_path / "home" / ".local" / "share" / "romm-tender" / _DB).read_text(encoding="utf-8") == "ours"
        assert locations.data_dir == str(tmp_path / "home" / ".local" / "share" / "romm-tender")


class TestOneLibraryWins:
    def test_the_whole_directory_travels(self, tmp_path):
        _seed_library(tmp_path / "data" / _OLD, roms=2)
        (tmp_path / "data" / _OLD / "covers").mkdir()
        (tmp_path / "data" / _OLD / "covers" / "10.png").write_bytes(b"cover")
        (tmp_path / "data" / _OLD / "save_sync_state.json.bak").write_text("{}", encoding="utf-8")
        _seed_settings(tmp_path / "settings" / _OLD)

        locations = _make(tmp_path).migrate()

        root = tmp_path / "home" / ".local" / "share" / "romm-tender"
        assert (root / _DB).is_file()
        assert (root / "covers" / "10.png").read_bytes() == b"cover"
        assert (root / "save_sync_state.json.bak").is_file()
        assert (tmp_path / "home" / ".config" / "romm-tender" / _SETTINGS).is_file()
        assert locations.failure is None

    def test_the_source_is_left_untouched(self, tmp_path):
        _seed_library(tmp_path / "data" / _OLD, roms=1)

        _make(tmp_path).migrate()

        assert (tmp_path / "data" / _OLD / _DB).is_file()

    def test_a_note_is_left_in_each_directory_that_was_copied(self, tmp_path):
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        _seed_settings(tmp_path / "settings" / _OLD)

        _make(tmp_path, clock=FakeClock(now=datetime(2026, 9, 4, 12, 0, tzinfo=UTC))).migrate()

        note = (tmp_path / "data" / _OLD / "README.txt").read_text(encoding="utf-8")
        assert "2026-09-04" in note
        assert str(tmp_path / "home" / ".local" / "share" / "romm-tender") in note
        assert "safe to delete" in note
        assert (tmp_path / "settings" / _OLD / "README.txt").is_file()

    def test_a_note_already_in_the_source_is_left_alone(self, tmp_path):
        """A file of that name is the user's, and this move modifies a source nowhere."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        (tmp_path / "data" / _OLD / "README.txt").write_text("mine", encoding="utf-8")

        _make(tmp_path).migrate()

        assert (tmp_path / "data" / _OLD / "README.txt").read_text(encoding="utf-8") == "mine"
        assert (tmp_path / "home" / ".local" / "share" / "romm-tender" / _DB).is_file()

    def test_a_note_from_an_earlier_migration_does_not_travel(self, tmp_path):
        """In the live root its text would be a lie: that folder is not the spare."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        _seed_settings(tmp_path / "settings" / _OLD)
        (tmp_path / "data" / _OLD / "README.txt").write_text("Tender moved your data", encoding="utf-8")
        (tmp_path / "settings" / _OLD / "README.txt").write_text("Tender moved your data", encoding="utf-8")

        _make(tmp_path).migrate()

        assert not (tmp_path / "home" / ".local" / "share" / "romm-tender" / "README.txt").exists()
        assert not (tmp_path / "home" / ".config" / "romm-tender" / "README.txt").exists()
        assert (tmp_path / "data" / _OLD / "README.txt").is_file()
        assert (tmp_path / "settings" / _OLD / "README.txt").is_file()

    def test_a_readme_further_down_the_tree_is_the_user_s_and_travels(self, tmp_path):
        """Only the top level is where a note of ours can ever have been left."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        (tmp_path / "data" / _OLD / "covers").mkdir()
        (tmp_path / "data" / _OLD / "covers" / "README.txt").write_text("mine", encoding="utf-8")

        _make(tmp_path).migrate()

        moved = tmp_path / "home" / ".local" / "share" / "romm-tender" / "covers" / "README.txt"
        assert moved.read_text(encoding="utf-8") == "mine"

    def test_a_half_with_no_source_directory_still_gets_an_empty_root(self, tmp_path):
        """The chosen location's settings half may simply not exist."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)

        locations = _make(tmp_path).migrate()

        assert os.path.isdir(locations.settings_dir)
        assert os.listdir(locations.settings_dir) == []
        assert not (tmp_path / "settings" / _OLD).exists()

    def test_an_empty_database_is_not_a_library(self, tmp_path):
        """Bootstrap creates one on the first start, so existence proves nothing."""
        _seed_library(tmp_path / "data" / _OLD, roms=0)
        _seed_settings(tmp_path / "settings" / _NEW)

        _make(tmp_path).migrate()

        assert (tmp_path / "home" / ".config" / "romm-tender" / _SETTINGS).read_text(encoding="utf-8") == (
            '{"romm_url": "http://example"}'
        )


class TestTwoLibraries:
    def test_nothing_is_copied_and_the_decky_directories_stay_in_use(self, tmp_path):
        _seed_library(tmp_path / "data" / _OLD, roms=2)
        _seed_library(tmp_path / "data" / _NEW, roms=5)

        locations = _make(tmp_path).migrate()

        assert locations.choice_required is True
        assert locations.failure is None
        assert locations.settings_dir == str(tmp_path / "settings" / _NEW)
        assert locations.data_dir == str(tmp_path / "data" / _NEW)
        assert not (tmp_path / "home" / ".local" / "share" / "romm-tender").exists()

    def test_a_recorded_answer_is_acted_on_and_then_dropped(self, tmp_path):
        _seed_library(tmp_path / "data" / _OLD, roms=2)
        _seed_library(tmp_path / "data" / _NEW, roms=5)
        adapter = _make(tmp_path)
        adapter.record_answer(_OLD)

        locations = adapter.migrate()

        assert locations.choice_required is False
        assert locations.data_dir == str(tmp_path / "home" / ".local" / "share" / "romm-tender")
        moved = sqlite3.connect(str(tmp_path / "home" / ".local" / "share" / "romm-tender" / _DB))
        try:
            assert moved.execute("SELECT COUNT(*) FROM roms").fetchone()[0] == 2
        finally:
            moved.close()
        assert not (tmp_path / "data" / _NEW / "data-location-choice.json").exists()

    def test_an_answer_naming_something_else_is_refused(self, tmp_path):
        adapter = _make(tmp_path)
        with pytest.raises(ValueError, match="older data locations"):
            adapter.record_answer("some-other-plugin")

    def test_the_recorded_answer_names_the_folder_it_was_given(self, tmp_path):
        adapter = _make(tmp_path)
        adapter.record_answer(_NEW)
        payload = json.loads((tmp_path / "data" / _NEW / "data-location-choice.json").read_text(encoding="utf-8"))
        assert payload == {"source": _NEW}

    def test_an_answer_naming_a_folder_that_is_gone_migrates_the_surviving_library(self, tmp_path):
        """Obeying it would copy an empty directory into place and settle the
        question for the life of the install, with the real library stranded."""
        _seed_library(tmp_path / "data" / _OLD, roms=2)
        _seed_library(tmp_path / "data" / _NEW, roms=5)
        adapter = _make(tmp_path)
        adapter.record_answer(_NEW)
        shutil.rmtree(tmp_path / "data" / _NEW)

        locations = adapter.migrate()

        assert locations.choice_required is False
        assert locations.failure is None
        moved = sqlite3.connect(str(tmp_path / "home" / ".local" / "share" / "romm-tender" / _DB))
        try:
            assert moved.execute("SELECT COUNT(*) FROM roms").fetchone()[0] == 2
        finally:
            moved.close()

    def test_the_recorded_answer_is_not_carried_into_the_new_root(self, tmp_path):
        """It answers a question the new root's existence has already settled."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        _seed_library(tmp_path / "data" / _NEW, roms=1)
        adapter = _make(tmp_path)
        adapter.record_answer(_NEW)

        adapter.migrate()

        root = tmp_path / "home" / ".local" / "share" / "romm-tender"
        assert (root / _DB).is_file()
        assert not (root / "data-location-choice.json").exists()


class TestStaging:
    def test_an_interrupted_copy_never_looks_finished(self, tmp_path):
        """Debris under the staging name is discarded, not renamed into place."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        staging = tmp_path / "home" / ".local" / "share" / "romm-tender.migrating"
        staging.mkdir(parents=True)
        (staging / "half-written.png").write_bytes(b"torn")

        _make(tmp_path).migrate()

        root = tmp_path / "home" / ".local" / "share" / "romm-tender"
        assert (root / _DB).is_file()
        assert not (root / "half-written.png").exists()
        assert not staging.exists()

    def test_an_unreadable_root_takes_only_its_own_half_down(self, tmp_path):
        """A root that will not answer is one half's problem, not both halves'."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        _seed_settings(tmp_path / "settings" / _OLD)
        # A FILE where the data root belongs: asking it what it holds raises.
        (tmp_path / "home" / ".local" / "share").mkdir(parents=True)
        (tmp_path / "home" / ".local" / "share" / "romm-tender").write_text("not a directory", encoding="utf-8")

        locations = _make(tmp_path).migrate()

        assert locations.failure is not None
        assert locations.data_dir == str(tmp_path / "data" / _NEW)
        assert locations.settings_dir == str(tmp_path / "home" / ".config" / "romm-tender")
        assert (tmp_path / "home" / ".config" / "romm-tender" / _SETTINGS).is_file()

    def test_an_unreadable_root_cannot_unsettle_a_half_that_already_moved(self, tmp_path):
        """Otherwise the plugin silently reverts to the pre-migration settings file,
        and the next good start reads the new root again with those edits gone."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        _seed_settings(tmp_path / "settings" / _OLD)
        adapter = _make(tmp_path)
        adapter.migrate()
        data_root = tmp_path / "home" / ".local" / "share" / "romm-tender"
        shutil.rmtree(data_root)
        data_root.write_text("not a directory", encoding="utf-8")

        locations = adapter.migrate()

        assert locations.settings_dir == str(tmp_path / "home" / ".config" / "romm-tender")
        assert locations.data_dir == str(tmp_path / "data" / _NEW)
        assert locations.failure is not None

    def test_both_reasons_are_reported_when_both_halves_fail(self, tmp_path):
        """Naming what to fix is the notice's whole job, so neither reason is dropped."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        (tmp_path / "home" / ".config").mkdir(parents=True)
        (tmp_path / "home" / ".config" / "romm-tender").write_text("not a directory", encoding="utf-8")
        (tmp_path / "home" / ".local" / "share").mkdir(parents=True)
        (tmp_path / "home" / ".local" / "share" / "romm-tender").write_text("not a directory", encoding="utf-8")

        locations = _make(tmp_path).migrate()

        assert locations.failure is not None
        assert locations.failure.count("Not a directory") == 2
        assert locations.settings_dir == str(tmp_path / "settings" / _NEW)
        assert locations.data_dir == str(tmp_path / "data" / _NEW)

    def test_one_half_can_fail_while_the_other_lands(self, tmp_path):
        """A copy that dies leaves ITS half in Decky's tree, and says why."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        _seed_settings(tmp_path / "settings" / _OLD)
        # A named pipe is something the copy cannot carry, so the data half dies
        # mid-way while the settings half beside it finishes.
        os.mkfifo(tmp_path / "data" / _OLD / "pipe")

        locations = _make(tmp_path).migrate()

        assert locations.failure is not None
        assert locations.data_dir == str(tmp_path / "data" / _NEW)
        assert locations.settings_dir == str(tmp_path / "home" / ".config" / "romm-tender")
        assert not (tmp_path / "home" / ".local" / "share" / "romm-tender").exists()

    def test_a_failed_half_is_retried_on_the_next_start(self, tmp_path):
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        _seed_settings(tmp_path / "settings" / _OLD)
        os.mkfifo(tmp_path / "data" / _OLD / "pipe")
        adapter = _make(tmp_path)
        assert adapter.migrate().failure is not None

        os.unlink(tmp_path / "data" / _OLD / "pipe")
        locations = adapter.migrate()

        assert locations.failure is None
        assert locations.data_dir == str(tmp_path / "home" / ".local" / "share" / "romm-tender")
        assert (tmp_path / "home" / ".local" / "share" / "romm-tender" / _DB).is_file()


class TestDescribeSources:
    def test_reports_path_size_and_last_change_per_location(self, tmp_path):
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        (tmp_path / "data" / _OLD / "covers").mkdir()
        (tmp_path / "data" / _OLD / "covers" / "10.png").write_bytes(b"x" * 512)
        _seed_library(tmp_path / "data" / _NEW, roms=1)

        described = _make(tmp_path).describe_sources()

        by_name = {entry["source"]: entry for entry in described}
        assert set(by_name) == {_OLD, _NEW}
        assert by_name[_OLD]["path"] == str(tmp_path / "data" / _OLD)
        assert by_name[_OLD]["present"] is True
        old_size, new_size = by_name[_OLD]["size_bytes"], by_name[_NEW]["size_bytes"]
        assert old_size is not None
        assert new_size is not None
        assert old_size > new_size
        assert by_name[_OLD]["changed_at"] is not None

    def test_a_location_that_is_not_there_is_listed_as_absent(self, tmp_path):
        """A choice shown with one option is not the question that was asked."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)

        described = _make(tmp_path).describe_sources()

        by_name = {entry["source"]: entry for entry in described}
        assert [entry["source"] for entry in described] == [_OLD, _NEW]
        assert by_name[_NEW]["present"] is False
        assert by_name[_NEW]["size_bytes"] is None
        assert by_name[_NEW]["changed_at"] is None

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0o000 directory, so the walk cannot be broken")
    def test_a_reading_that_could_not_finish_reports_no_size(self, tmp_path):
        """``os.walk`` yields less and says nothing, so a smaller total would be
        indistinguishable from a smaller library — the one thing the reader is
        choosing on."""
        _seed_library(tmp_path / "data" / _OLD, roms=1)
        blocked = tmp_path / "data" / _OLD / "covers"
        blocked.mkdir()
        (blocked / "10.png").write_bytes(b"x" * 512)
        blocked.chmod(0o000)
        try:
            described = _make(tmp_path).describe_sources()
        finally:
            blocked.chmod(0o755)

        entry = next(item for item in described if item["source"] == _OLD)
        assert entry["present"] is True
        assert entry["size_bytes"] is None
        assert entry["changed_at"] is None
