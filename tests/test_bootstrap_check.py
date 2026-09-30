"""What a pre-install check builds on: the live database and settings, copied under the check's own roots."""

from __future__ import annotations

import logging
import sqlite3

from bootstrap import copy_live_data

from domain.app_directories import AppDirectories

LOGGER = logging.getLogger("test_bootstrap_check")


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
        db = sqlite3.connect(tmp_path / "live" / "data" / "romm_sync.db")
        with db:
            db.execute("CREATE TABLE marker (note TEXT)")
        db.close()
        (tmp_path / "live" / "config" / "settings.json").write_text('{"version": 13}\n', encoding="utf-8")

        with caplog.at_level(logging.INFO, logger="test_bootstrap_check"):
            copy_live_data(
                data_from=str(tmp_path / "live" / "data"),
                config_from=str(tmp_path / "live" / "config"),
                directories=_roots(tmp_path),
                logger=LOGGER,
            )

        assert (tmp_path / "check" / "data" / "romm_sync.db").is_file()
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

    def test_the_save_sync_state_settings_older_than_their_fourth_version_fold_in_is_copied_too(self, tmp_path, caplog):
        (tmp_path / "live" / "data").mkdir(parents=True)
        (tmp_path / "live" / "data" / "save_sync_state.json").write_text('{"device_name": "deck"}\n', encoding="utf-8")

        with caplog.at_level(logging.INFO, logger="test_bootstrap_check"):
            copy_live_data(
                data_from=str(tmp_path / "live" / "data"),
                config_from=str(tmp_path / "live" / "config"),
                directories=_roots(tmp_path),
                logger=LOGGER,
            )

        assert (tmp_path / "check" / "data" / "save_sync_state.json").read_text(encoding="utf-8") == (
            '{"device_name": "deck"}\n'
        )
        assert [record.getMessage() for record in caplog.records] == [
            f"check: copied the save-sync state from {tmp_path / 'live' / 'data'}"
        ]
