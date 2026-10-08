import logging
import os
from typing import Any
from unittest.mock import patch

import pytest
from _vendor import vdf

from adapters.steam_config import SteamConfigAdapter
from lib.errors import SteamGridDirMissingError
from lib.steam_input_apply import SteamInputApply


@pytest.fixture
def adapter(tmp_path):
    return SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))


# ── find_steam_user_dir ─────────────────────────────────────


class TestFindSteamUserDir:
    def test_single_user_local_share(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata"
        user_dir = userdata / "12345"
        user_dir.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.find_steam_user_dir()
        assert result == str(user_dir)

    def test_single_user_dot_steam(self, tmp_path):
        userdata = tmp_path / ".steam" / "steam" / "userdata"
        user_dir = userdata / "67890"
        user_dir.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.find_steam_user_dir()
        assert result == str(user_dir)

    def test_multiple_users_returns_most_recent(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata"
        user1 = userdata / "111"
        user2 = userdata / "222"
        user1.mkdir(parents=True)
        user2.mkdir(parents=True)
        # Make user2 newer
        os.utime(str(user1), (1000, 1000))
        os.utime(str(user2), (2000, 2000))
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.find_steam_user_dir()
        assert result == str(user2)

    def test_no_steam_dir_returns_none(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.find_steam_user_dir() is None

    def test_no_numeric_dirs_returns_none(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata"
        (userdata / "not_numeric").mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.find_steam_user_dir() is None

    def test_prefers_local_share_over_dot_steam(self, tmp_path):
        # .local/share path is checked first
        path1 = tmp_path / ".local" / "share" / "Steam" / "userdata" / "111"
        path1.mkdir(parents=True)
        path2 = tmp_path / ".steam" / "steam" / "userdata" / "222"
        path2.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.find_steam_user_dir()
        assert result == str(path1)


# ── shortcuts_vdf_path ──────────────────────────────────────


class TestShortcutsVdfPath:
    def test_returns_path_when_user_dir_exists(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        userdata.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.shortcuts_vdf_path()
        assert result == os.path.join(str(userdata), "config", "shortcuts.vdf")

    def test_returns_none_when_no_user_dir(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.shortcuts_vdf_path() is None


# ── grid_dir ────────────────────────────────────────────────


class TestGridDir:
    def test_creates_and_returns_grid_dir(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        userdata.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.grid_dir()
        expected = os.path.join(str(userdata), "config", "grid")
        assert result == expected
        assert os.path.isdir(expected)

    def test_returns_none_when_no_user_dir(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.grid_dir() is None


# ── read_shortcuts / write_shortcuts ────────────────────────


class TestReadShortcuts:
    def test_returns_empty_when_no_path(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.read_shortcuts()
        assert result == {"shortcuts": {}}

    def test_returns_empty_when_file_missing(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        userdata.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.read_shortcuts()
        assert result == {"shortcuts": {}}

    def test_reads_existing_vdf(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        config_dir = userdata / "config"
        config_dir.mkdir(parents=True)
        vdf_data = {"shortcuts": {"0": {"appname": "Test"}}}
        vdf_path = config_dir / "shortcuts.vdf"
        with open(str(vdf_path), "wb") as f:
            f.write(vdf.binary_dumps(vdf_data))
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.read_shortcuts()
        assert result["shortcuts"]["0"]["appname"] == "Test"


class TestReadShortcutExes:
    """The reading the shortcut relocation is planned off."""

    @staticmethod
    def _write(tmp_path, entries: dict[str, Any]) -> None:
        config_dir = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(str(config_dir / "shortcuts.vdf"), "wb") as f:
            f.write(vdf.binary_dumps({"shortcuts": entries}))

    def test_reads_every_shortcut_app_id_and_exe(self, tmp_path):
        self._write(
            tmp_path,
            {
                "0": {"appid": 1, "AppName": "One", "Exe": "/a/bin/tender-rom-launcher"},
                "1": {"appid": 2, "AppName": "Two", "Exe": "/usr/bin/other"},
            },
        )
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() == {1: "/a/bin/tender-rom-launcher", 2: "/usr/bin/other"}

    def test_reads_the_keys_whatever_case_steam_wrote_them_in(self, tmp_path):
        """Steam has written both spellings; a case-sensitive read comes back empty on one."""
        self._write(tmp_path, {"0": {"AppId": 7, "exe": "/a/bin/tender-rom-launcher"}})
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() == {7: "/a/bin/tender-rom-launcher"}

    def test_converts_the_signed_app_id_the_file_stores(self, tmp_path):
        """Every SteamClient API takes the unsigned form; a negative id names no shortcut."""
        self._write(tmp_path, {"0": {"appid": -949288395, "Exe": "/a/bin/tender-rom-launcher"}})
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() == {3345678901: "/a/bin/tender-rom-launcher"}

    def test_skips_an_entry_missing_either_field(self, tmp_path):
        self._write(tmp_path, {"0": {"appid": 1}, "1": {"Exe": "/a/bin/tender-rom-launcher"}, "2": "not a dict"})
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() == {}

    def test_a_machine_with_no_shortcut_file_reads_as_no_shortcuts(self, tmp_path):
        """A finished reading of nothing — not the same answer as a failed one."""
        (tmp_path / ".local" / "share" / "Steam" / "userdata" / "123").mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() == {}

    def test_an_unlocatable_steam_directory_reads_as_nothing_established(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() is None

    def test_a_file_that_will_not_parse_reads_as_nothing_established(self, tmp_path):
        config_dir = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "shortcuts.vdf").write_bytes(b"not a vdf file at all")
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() is None

    def test_a_file_holding_no_shortcut_list_reads_as_nothing_established(self, tmp_path):
        config_dir = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123" / "config"
        config_dir.mkdir(parents=True)
        with open(str(config_dir / "shortcuts.vdf"), "wb") as f:
            f.write(vdf.binary_dumps({"something_else": {}}))
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_exes() is None


class TestWriteShortcuts:
    def test_writes_vdf_file(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        config_dir = userdata / "config"
        config_dir.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        data = {"shortcuts": {"0": {"appname": "Written"}}}
        adapter.write_shortcuts(data)
        vdf_path = config_dir / "shortcuts.vdf"
        assert vdf_path.exists()
        with open(str(vdf_path), "rb") as f:
            loaded = vdf.binary_loads(f.read())
        assert loaded["shortcuts"]["0"]["appname"] == "Written"

    def test_raises_when_no_user_dir(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        with pytest.raises(RuntimeError, match="Cannot find"):
            adapter.write_shortcuts({"shortcuts": {}})

    def test_atomic_write_via_replace(self, tmp_path):
        """Write uses tmp file + os.replace for atomicity."""
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        config_dir = userdata / "config"
        config_dir.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        adapter.write_shortcuts({"shortcuts": {}})
        # .tmp should not remain
        tmp_file = config_dir / "shortcuts.vdf.tmp"
        assert not tmp_file.exists()
        assert (config_dir / "shortcuts.vdf").exists()


# ── write_shortcut_icon ─────────────────────────────────────


class TestWriteShortcutIcon:
    def test_writes_png_to_grid_dir(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        userdata.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        result = adapter.write_shortcut_icon(12345, b"png-data")
        grid_dir = userdata / "config" / "grid"
        expected = grid_dir / "12345_icon.png"
        assert result == str(expected)
        assert expected.read_bytes() == b"png-data"

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        userdata.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        adapter.write_shortcut_icon(12345, b"data")
        grid_dir = userdata / "config" / "grid"
        assert not (grid_dir / "12345_icon.png.tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        userdata.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        adapter.write_shortcut_icon(12345, b"first")
        adapter.write_shortcut_icon(12345, b"second")
        grid_dir = userdata / "config" / "grid"
        assert (grid_dir / "12345_icon.png").read_bytes() == b"second"

    def test_raises_when_no_grid_dir(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        with pytest.raises(SteamGridDirMissingError, match="grid directory"):
            adapter.write_shortcut_icon(12345, b"data")

    def test_cleans_tmp_on_failure(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        userdata.mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        with (
            patch("adapters.steam_config.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError, match="boom"),
        ):
            adapter.write_shortcut_icon(12345, b"data")
        grid_dir = userdata / "config" / "grid"
        assert not (grid_dir / "12345_icon.png.tmp").exists()
        assert not (grid_dir / "12345_icon.png").exists()


class TestReadShortcutIcons:
    """The reading the shortcut icon job's worklist is derived from."""

    def test_reads_every_shortcut_icon_with_none_as_an_empty_path(self, tmp_path):
        TestReadShortcutExes._write(
            tmp_path,
            {
                "0": {"appid": -949288395, "AppName": "One", "icon": "/grid/3345678901_icon.png"},
                "1": {"AppId": 2, "AppName": "Two", "Icon": ""},
                "2": {"appid": 3, "AppName": "Three"},
                "3": "not a dict",
            },
        )
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_icons() == {3345678901: "/grid/3345678901_icon.png", 2: "", 3: ""}

    def test_an_unlocatable_steam_directory_reads_as_nothing_established(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        assert adapter.read_shortcut_icons() is None


class TestEnsureGridFile:
    @staticmethod
    def _grid(tmp_path):
        (tmp_path / ".local" / "share" / "Steam" / "userdata" / "123").mkdir(parents=True)
        return tmp_path / ".local" / "share" / "Steam" / "userdata" / "123" / "config" / "grid"

    def test_writes_a_missing_file_and_answers_its_path(self, tmp_path):
        grid = self._grid(tmp_path)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        path = adapter.ensure_grid_file("tender-icon-placeholder.png", b"pixel")

        assert path == str(grid / "tender-icon-placeholder.png")
        assert (grid / "tender-icon-placeholder.png").read_bytes() == b"pixel"
        assert not (grid / "tender-icon-placeholder.png.tmp").exists()

    def test_leaves_a_file_that_already_holds_the_bytes_alone(self, tmp_path):
        grid = self._grid(tmp_path)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        adapter.ensure_grid_file("tender-icon-placeholder.png", b"pixel")

        with patch("adapters.steam_config.os.replace") as replace:
            adapter.ensure_grid_file("tender-icon-placeholder.png", b"pixel")

        replace.assert_not_called()
        assert (grid / "tender-icon-placeholder.png").read_bytes() == b"pixel"

    def test_rewrites_a_file_that_holds_other_bytes(self, tmp_path):
        grid = self._grid(tmp_path)
        grid.mkdir(parents=True)
        (grid / "tender-icon.png").write_bytes(b"old logo")
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        adapter.ensure_grid_file("tender-icon.png", b"new logo")

        assert (grid / "tender-icon.png").read_bytes() == b"new logo"

    def test_raises_when_no_grid_dir(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        with pytest.raises(SteamGridDirMissingError, match="grid directory"):
            adapter.ensure_grid_file("tender-icon.png", b"data")

    def test_cleans_tmp_on_failure(self, tmp_path):
        grid = self._grid(tmp_path)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        with (
            patch("adapters.steam_config.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError, match="boom"),
        ):
            adapter.ensure_grid_file("tender-icon.png", b"data")
        assert not (grid / "tender-icon.png.tmp").exists()
        assert not (grid / "tender-icon.png").exists()


# ── set_steam_input_config ──────────────────────────────────


class TestSetSteamInputConfig:
    def _make_adapter_with_localconfig(self, tmp_path, localconfig_data):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        config_dir = userdata / "config"
        config_dir.mkdir(parents=True)
        localconfig_path = config_dir / "localconfig.vdf"
        with open(str(localconfig_path), "w", encoding="utf-8") as f:
            vdf.dump(localconfig_data, f, pretty=True)
        return SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

    def test_no_user_dir_answers_no_steam_user(self, tmp_path):
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.set_steam_input_config([12345], mode="force_on") is SteamInputApply.NO_STEAM_USER

    def test_no_localconfig_answers_no_localconfig(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        (userdata / "config").mkdir(parents=True)
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.set_steam_input_config([12345], mode="force_on") is SteamInputApply.NO_LOCALCONFIG
        assert not (userdata / "config" / "localconfig.vdf").exists()

    def test_force_on_sets_value_2(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {}}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        assert adapter.set_steam_input_config([12345], mode="force_on") is SteamInputApply.APPLIED
        # Re-read and verify
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        with open(str(userdata / "config" / "localconfig.vdf")) as f:
            result = vdf.load(f)
        assert result["UserLocalConfigStore"]["Apps"]["12345"]["UseSteamControllerConfig"] == "2"

    def test_force_off_sets_value_0(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {}}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        adapter.set_steam_input_config([99], mode="force_off")
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        with open(str(userdata / "config" / "localconfig.vdf")) as f:
            result = vdf.load(f)
        assert result["UserLocalConfigStore"]["Apps"]["99"]["UseSteamControllerConfig"] == "0"

    def test_default_removes_override(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {"42": {"UseSteamControllerConfig": "2", "other": "val"}}}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        adapter.set_steam_input_config([42], mode="default")
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        with open(str(userdata / "config" / "localconfig.vdf")) as f:
            result = vdf.load(f)
        # Key removed, but app entry remains because it has other keys
        assert "UseSteamControllerConfig" not in result["UserLocalConfigStore"]["Apps"]["42"]

    def test_default_removes_empty_app_entry(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {"42": {"UseSteamControllerConfig": "2"}}}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        adapter.set_steam_input_config([42], mode="default")
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        with open(str(userdata / "config" / "localconfig.vdf")) as f:
            result = vdf.load(f)
        assert "42" not in result["UserLocalConfigStore"]["Apps"]

    def test_default_mode_with_no_apps_key_has_nothing_to_change(self, tmp_path):
        data = {"UserLocalConfigStore": {}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        assert adapter.set_steam_input_config([42], mode="default") is SteamInputApply.APPLIED

    def test_force_on_creates_apps_key_if_missing(self, tmp_path):
        data = {"UserLocalConfigStore": {}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        adapter.set_steam_input_config([42], mode="force_on")
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        with open(str(userdata / "config" / "localconfig.vdf")) as f:
            result = vdf.load(f)
        assert result["UserLocalConfigStore"]["Apps"]["42"]["UseSteamControllerConfig"] == "2"

    def test_no_change_doesnt_write(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {}}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        lc_path = userdata / "config" / "localconfig.vdf"
        mtime_before = os.path.getmtime(str(lc_path))
        # default mode on non-existent app -> no change
        assert adapter.set_steam_input_config([999], mode="default") is SteamInputApply.APPLIED
        mtime_after = os.path.getmtime(str(lc_path))
        assert mtime_before == mtime_after

    def test_parse_error_answers_unreadable_and_writes_nothing(self, tmp_path):
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        config_dir = userdata / "config"
        config_dir.mkdir(parents=True)
        # An unclosed block: the parser accepts a bare ``key value {{{`` line as one pair.
        with open(str(config_dir / "localconfig.vdf"), "w") as f:
            f.write('"UserLocalConfigStore"\n{\n')
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.set_steam_input_config([42], mode="force_on") is SteamInputApply.UNREADABLE
        assert (config_dir / "localconfig.vdf").read_text() == '"UserLocalConfigStore"\n{\n'

    def test_multiple_app_ids(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {}}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        adapter.set_steam_input_config([100, 200, 300], mode="force_on")
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        with open(str(userdata / "config" / "localconfig.vdf")) as f:
            result = vdf.load(f)
        for app_id in ["100", "200", "300"]:
            assert result["UserLocalConfigStore"]["Apps"][app_id]["UseSteamControllerConfig"] == "2"

    def test_write_failure_answers_write_failed_and_leaves_the_file(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {}}}
        logger = logging.getLogger("test_write_fail")
        userdata = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123"
        config_dir = userdata / "config"
        config_dir.mkdir(parents=True)
        with open(str(config_dir / "localconfig.vdf"), "w", encoding="utf-8") as f:
            vdf.dump(data, f, pretty=True)
        before = (config_dir / "localconfig.vdf").read_bytes()
        adapter = SteamConfigAdapter(user_home=str(tmp_path), logger=logger)
        with patch("adapters.steam_config.vdf.dump", side_effect=OSError("disk full")):
            assert adapter.set_steam_input_config([42], mode="force_on") is SteamInputApply.WRITE_FAILED
        assert (config_dir / "localconfig.vdf").read_bytes() == before
        assert not (config_dir / "localconfig.vdf.tmp").exists()

    def test_a_failed_replace_answers_write_failed(self, tmp_path):
        data = {"UserLocalConfigStore": {"Apps": {}}}
        adapter = self._make_adapter_with_localconfig(tmp_path, data)
        config_dir = tmp_path / ".local" / "share" / "Steam" / "userdata" / "123" / "config"
        with patch("adapters.steam_config.os.replace", side_effect=PermissionError("read-only")):
            assert adapter.set_steam_input_config([42], mode="force_on") is SteamInputApply.WRITE_FAILED
        assert not (config_dir / "localconfig.vdf.tmp").exists()
