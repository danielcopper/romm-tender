"""Tests for services/platform_switches.py — the one writer of the per-platform sync toggles."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fakes.fake_settings_persister import FakeSettingsPersister

from services.platform_switches import PlatformSwitches, PlatformSwitchesConfig


class _FailingPersister(FakeSettingsPersister):
    def save_settings(self) -> None:
        raise OSError("disk full")


def _switches(
    settings: dict[str, Any], persister: FakeSettingsPersister | None = None, lock: threading.Lock | None = None
) -> PlatformSwitches:
    return PlatformSwitches(
        config=PlatformSwitchesConfig(
            settings=settings,
            settings_persister=persister if persister is not None else FakeSettingsPersister(),
            lock=lock if lock is not None else threading.Lock(),
        )
    )


def _moved(by_id: Any, switches: Any) -> dict[str, bool]:
    return {**switches, **{f"folder-{pid}": on for pid, on in by_id.items()}}


class TestReplace:
    def test_the_built_map_takes_the_place_of_the_toggles_and_is_saved(self):
        settings: dict[str, Any] = {"enabled_platforms": {"snes": True}}
        persister = FakeSettingsPersister()

        _switches(settings, persister).replace(lambda switches: {**switches, "gba": False})

        assert settings["enabled_platforms"] == {"snes": True, "gba": False}
        assert persister.save_count == 1

    def test_the_map_in_place_is_left_as_it_was(self):
        before = {"snes": True}
        settings: dict[str, Any] = {"enabled_platforms": before}

        _switches(settings).replace(lambda switches: {})

        assert before == {"snes": True}
        assert settings["enabled_platforms"] == {}

    def test_a_value_that_is_no_map_is_handed_over_as_no_toggles(self):
        settings: dict[str, Any] = {"enabled_platforms": None}
        handed: list[Any] = []

        _switches(settings).replace(lambda switches: handed.append(switches) or {"snes": True})

        assert handed == [{}]

    def test_a_save_that_fails_keeps_the_new_map_and_raises(self):
        settings: dict[str, Any] = {"enabled_platforms": {"snes": True}}

        with pytest.raises(OSError, match="disk full"):
            _switches(settings, _FailingPersister()).replace(lambda switches: {"snes": False})

        assert settings["enabled_platforms"] == {"snes": False}

    def test_a_write_waits_for_a_writer_that_holds_the_lock(self):
        settings: dict[str, Any] = {"enabled_platforms": {}}
        lock = threading.Lock()
        writer = _switches(settings, lock=lock)

        with lock:
            worker = threading.Thread(target=writer.replace, args=(lambda switches: {"snes": True},))
            worker.start()
            worker.join(timeout=0.2)
            assert worker.is_alive()
            assert settings["enabled_platforms"] == {}
        worker.join(timeout=5)

        assert settings["enabled_platforms"] == {"snes": True}


class TestCarryOver:
    def test_the_moved_toggles_take_the_place_of_both_maps_and_are_saved(self):
        settings: dict[str, Any] = {"enabled_platforms": {"gba": False}, "enabled_platform_ids": {"1": True}}
        persister = FakeSettingsPersister()

        moved = _switches(settings, persister).carry_over(_moved)

        assert moved is True
        assert settings["enabled_platforms"] == {"gba": False, "folder-1": True}
        assert settings["enabled_platform_ids"] is None
        assert persister.save_count == 1

    def test_the_settings_keep_their_keys_so_a_save_walking_them_meanwhile_does_not_fail(self):
        settings: dict[str, Any] = {"enabled_platforms": {}, "enabled_platform_ids": {"1": True}, "log_level": "warn"}
        walking = iter(settings)
        next(walking)

        _switches(settings).carry_over(_moved)

        assert list(walking) == ["enabled_platform_ids", "log_level"]

    @pytest.mark.parametrize(
        "settings", [{"enabled_platforms": {}}, {"enabled_platforms": {}, "enabled_platform_ids": None}]
    )
    def test_nothing_kept_by_id_moves_nothing_and_saves_nothing(self, settings):
        persister = FakeSettingsPersister()
        handed: list[Any] = []

        moved = _switches(settings, persister).carry_over(lambda by_id, switches: handed.append(by_id) or {})

        assert moved is False
        assert handed == []
        assert settings["enabled_platforms"] == {}
        assert persister.save_count == 0

    def test_nothing_built_leaves_both_maps_and_saves_nothing(self):
        settings: dict[str, Any] = {"enabled_platforms": {}, "enabled_platform_ids": {"1": True}}
        persister = FakeSettingsPersister()

        moved = _switches(settings, persister).carry_over(lambda by_id, switches: None)

        assert moved is False
        assert settings == {"enabled_platforms": {}, "enabled_platform_ids": {"1": True}}
        assert persister.save_count == 0

    def test_a_save_that_fails_keeps_the_moved_toggles_and_raises(self):
        """#2452 D23: the run filters by the moved toggles; the next save writes them."""
        settings: dict[str, Any] = {"enabled_platforms": {}, "enabled_platform_ids": {"1": True}}

        with pytest.raises(OSError, match="disk full"):
            _switches(settings, _FailingPersister()).carry_over(_moved)

        assert settings == {"enabled_platforms": {"folder-1": True}, "enabled_platform_ids": None}

    def test_a_move_waits_for_a_writer_that_holds_the_lock(self):
        settings: dict[str, Any] = {"enabled_platforms": {}, "enabled_platform_ids": {"1": True}}
        lock = threading.Lock()
        writer = _switches(settings, lock=lock)

        with lock:
            worker = threading.Thread(target=writer.carry_over, args=(_moved,))
            worker.start()
            worker.join(timeout=0.2)
            assert worker.is_alive()
            assert settings["enabled_platform_ids"] == {"1": True}
        worker.join(timeout=5)

        assert settings["enabled_platforms"] == {"folder-1": True}
