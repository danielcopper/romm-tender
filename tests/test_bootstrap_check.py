"""What a pre-install check builds on: the live database and settings, copied under the check's own roots."""

from __future__ import annotations

import logging
import sqlite3

from bootstrap import copy_live_data

from domain.app_directories import AppDirectories

LOGGER = logging.getLogger("test_bootstrap_check")


def _database(path, note: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    with db:
        db.execute("CREATE TABLE marker (note TEXT)")
        db.execute("INSERT INTO marker VALUES (?)", (note,))
    db.close()


def _notes(path) -> list[str]:
    db = sqlite3.connect(path)
    try:
        return [row[0] for row in db.execute("SELECT note FROM marker")]
    finally:
        db.close()


def _roots(tmp_path) -> AppDirectories:
    check = tmp_path / "check"
    return AppDirectories(
        config_dir=str(check / "config"),
        data_dir=str(check / "data"),
        cache_dir=str(check / "cache"),
        state_dir=str(check / "state"),
        runtime_dir=str(check / "run"),
        code_dir=str(tmp_path / "code"),
        bin_dir=str(check / "bin"),
    )


class TestCopyLiveData:
    def test_the_database_and_the_settings_land_under_the_check_s_roots(self, tmp_path, caplog):
        (tmp_path / "live" / "data").mkdir(parents=True)
        (tmp_path / "live" / "config").mkdir(parents=True)
        _database(tmp_path / "live" / "data" / "romm-tender.db", "the library")
        (tmp_path / "live" / "config" / "settings.json").write_text('{"version": 13}\n', encoding="utf-8")

        with caplog.at_level(logging.INFO, logger="test_bootstrap_check"):
            copy_live_data(
                data_from=str(tmp_path / "live" / "data"),
                config_from=str(tmp_path / "live" / "config"),
                directories=_roots(tmp_path),
                logger=LOGGER,
            )

        assert (tmp_path / "check" / "data" / "romm-tender.db").is_file()
        assert (tmp_path / "check" / "config" / "settings.json").read_text(encoding="utf-8") == '{"version": 13}\n'
        assert [record.getMessage() for record in caplog.records] == [
            f"check: copied the database from {tmp_path / 'live' / 'data'}",
            f"check: copied the settings from {tmp_path / 'live' / 'config'}",
        ]

    def test_a_first_install_copies_nothing_and_says_nothing(self, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="test_bootstrap_check"):
            copy_live_data(
                data_from=str(tmp_path / "live" / "data"),
                config_from=str(tmp_path / "live" / "config"),
                directories=_roots(tmp_path),
                logger=LOGGER,
            )

        assert not (tmp_path / "check").exists()
        assert caplog.records == []


class TestWhichDatabaseIsCopied:
    """The one a start would open, under the name it has, so the build renames the copy as a start would."""

    def test_only_the_old_name_is_copied_under_the_old_name(self, tmp_path):
        _database(tmp_path / "live" / "data" / "romm_sync.db", "the library")

        copy_live_data(
            data_from=str(tmp_path / "live" / "data"),
            config_from=str(tmp_path / "live" / "config"),
            directories=_roots(tmp_path),
            logger=LOGGER,
        )

        assert sorted(path.name for path in (tmp_path / "check" / "data").iterdir()) == ["romm_sync.db"]
        assert _notes(tmp_path / "check" / "data" / "romm_sync.db") == ["the library"]

    def test_with_both_names_only_the_current_one_is_copied(self, tmp_path):
        _database(tmp_path / "live" / "data" / "romm_sync.db", "left behind")
        _database(tmp_path / "live" / "data" / "romm-tender.db", "the library")

        copy_live_data(
            data_from=str(tmp_path / "live" / "data"),
            config_from=str(tmp_path / "live" / "config"),
            directories=_roots(tmp_path),
            logger=LOGGER,
        )

        assert sorted(path.name for path in (tmp_path / "check" / "data").iterdir()) == ["romm-tender.db"]
        assert _notes(tmp_path / "check" / "data" / "romm-tender.db") == ["the library"]
