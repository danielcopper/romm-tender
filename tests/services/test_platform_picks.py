"""Tests for services/platform_picks.py — the one writer of the per-platform emulator picks."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fakes.fake_settings_persister import FakeSettingsPersister

from services.platform_picks import PlatformPicks, PlatformPicksConfig


class _FailingPersister(FakeSettingsPersister):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def save_settings(self) -> None:
        raise self.error


def _picks(
    settings: dict[str, Any], persister: FakeSettingsPersister | None = None, lock: threading.Lock | None = None
) -> PlatformPicks:
    return PlatformPicks(
        config=PlatformPicksConfig(
            settings=settings,
            settings_persister=persister if persister is not None else FakeSettingsPersister(),
            lock=lock if lock is not None else threading.Lock(),
        )
    )


class TestReplace:
    def test_the_built_map_takes_the_place_of_the_picks_and_is_saved(self):
        settings: dict[str, Any] = {"platform_cores": {"ps": {"label": "SwanStation"}}}
        persister = FakeSettingsPersister()

        changed = _picks(settings, persister).replace(lambda picks: {**picks, "gc": {"label": "Dolphin"}})

        assert changed is True
        assert settings["platform_cores"] == {"ps": {"label": "SwanStation"}, "gc": {"label": "Dolphin"}}
        assert persister.save_count == 1

    def test_the_map_in_place_is_left_as_it_was(self):
        before = {"ps": {"label": "SwanStation"}}
        settings: dict[str, Any] = {"platform_cores": before}

        _picks(settings).replace(lambda picks: {})

        assert before == {"ps": {"label": "SwanStation"}}
        assert settings["platform_cores"] == {}

    def test_nothing_built_changes_and_saves_nothing(self):
        settings: dict[str, Any] = {"platform_cores": {"ps": {"label": "SwanStation"}}}
        persister = FakeSettingsPersister()

        changed = _picks(settings, persister).replace(lambda picks: None)

        assert changed is False
        assert settings["platform_cores"] == {"ps": {"label": "SwanStation"}}
        assert persister.save_count == 0

    @pytest.mark.parametrize("error", [OSError("disk full"), RuntimeError("not serialisable"), KeyboardInterrupt()])
    def test_a_save_that_raises_puts_the_old_picks_back_and_raises(self, error):
        before = {"ps": {"label": "SwanStation"}}
        settings: dict[str, Any] = {"platform_cores": before}
        picks = _picks(settings, _FailingPersister(error))

        with pytest.raises(type(error)):
            picks.replace(lambda current: {})

        assert settings["platform_cores"] is before

    def test_settings_without_picks_get_an_empty_map_before_any_replace(self):
        settings: dict[str, Any] = {"other": True}

        _picks(settings)

        assert settings == {"other": True, "platform_cores": {}}

    def test_a_save_of_the_whole_settings_iterating_them_meanwhile_does_not_fail(self):
        """A save on another thread may be walking the settings while the picks are replaced."""
        settings: dict[str, Any] = {"platform_cores": {"ps": {"label": "SwanStation"}, "gc": {}}, "other": True}
        picks = _picks(settings)
        walking_settings = iter(settings)
        walking_picks = iter(settings["platform_cores"])
        next(walking_settings)
        next(walking_picks)

        picks.replace(lambda current: {**current, "ngc": {}})

        assert list(walking_settings) == ["other"]
        assert list(walking_picks) == ["gc"]

    def test_a_replace_waits_for_whoever_holds_the_lock(self):
        lock = threading.Lock()
        settings: dict[str, Any] = {"platform_cores": {}}
        picks = _picks(settings, lock=lock)

        with lock:
            worker = threading.Thread(target=picks.replace, args=(lambda current: {"ps": {}},))
            worker.start()
            worker.join(timeout=0.2)
            assert worker.is_alive()
            assert settings["platform_cores"] == {}
        worker.join(timeout=5)

        assert not worker.is_alive()
        assert settings["platform_cores"] == {"ps": {}}
