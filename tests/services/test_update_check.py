"""Tests for UpdateCheckService — the plugin's only channel for announcing a release."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest
from fakes.fake_latest_release import FakeLatestRelease
from fakes.fake_plugin_metadata_reader import FakePluginMetadataReader
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory
from fakes.system_time import FakeClock

from domain.update_release import DOWNLOAD_URL, LatestRelease, UpdateCheck, encode_update_check
from services.update_check import (
    DISMISSED_KEY,
    ENABLED_KEY,
    LAST_CHECK_KEY,
    UpdateCheckService,
    UpdateCheckServiceConfig,
)

_PLUGIN_DIR = "/home/deck/homebrew/plugins/romm-tender"
_A_DAY = 24 * 60 * 60


class _RecordingPersister:
    def __init__(self) -> None:
        self.saves = 0

    def save_settings(self) -> None:
        self.saves += 1


def _make(
    *,
    latest: LatestRelease | None = None,
    raises: Exception | None = None,
    settings: dict[str, Any] | None = None,
    current_version: str = "0.32.0",
    decky_name: str = "Tender",
    clock: FakeClock | None = None,
    uow_factory: FakeUnitOfWorkFactory | None = None,
):
    releases = FakeLatestRelease(answer=latest, raises=raises)
    used_settings = settings if settings is not None else {}
    persister = _RecordingPersister()
    service = UpdateCheckService(
        config=UpdateCheckServiceConfig(
            latest_release=releases,
            plugin_metadata=FakePluginMetadataReader(version=current_version, decky_name=decky_name),
            plugin_dir=_PLUGIN_DIR,
            clock=clock if clock is not None else FakeClock(),
            uow_factory=uow_factory if uow_factory is not None else FakeUnitOfWorkFactory(),
            settings=used_settings,
            settings_persister=persister,
            loop=asyncio.get_event_loop(),
            logger=logging.getLogger("test_update_check"),
        ),
    )
    return service, releases, used_settings, persister


def _stored(uow_factory: FakeUnitOfWorkFactory) -> dict[str, Any]:
    """The stamp the service persisted. Fails the test where nothing was stored."""
    with uow_factory() as uow:
        raw = uow.kv_config.get(LAST_CHECK_KEY)
    assert raw is not None
    return json.loads(raw)


def _nothing_stored(uow_factory: FakeUnitOfWorkFactory) -> bool:
    with uow_factory() as uow:
        return uow.kv_config.get(LAST_CHECK_KEY) is None


class TestWhetherAnUpdateIsAvailable:
    async def test_a_newer_release_is_available(self):
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest="ab33cd"))

        notice = await service.get_update_notice()

        assert notice["available"] is True
        assert notice["latest_version"] == "0.33.0"
        assert notice["current_version"] == "0.32.0"

    async def test_the_running_release_is_not_an_update(self):
        service, _, _, _ = _make(latest=LatestRelease(version="0.32.0", digest="ab33cd"))

        notice = await service.get_update_notice()

        assert notice["available"] is False
        assert notice["latest_version"] == "0.32.0"

    async def test_an_older_release_is_not_an_update(self):
        """A development build ahead of the last release must not be told to downgrade."""
        service, _, _, _ = _make(latest=LatestRelease(version="0.31.0", digest=None), current_version="0.32.0")

        assert (await service.get_update_notice())["available"] is False

    async def test_the_digest_travels_without_its_algorithm_prefix(self):
        """Decky compares what it is handed against ``sha256(zip).hexdigest()``."""
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest="ab33cd"))

        assert (await service.get_update_notice())["digest"] == "ab33cd"

    async def test_a_release_without_a_digest_is_still_announced(self):
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest=None))

        notice = await service.get_update_notice()

        assert notice["available"] is True
        assert notice["digest"] is None

    async def test_the_download_address_is_the_fixed_one(self):
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest=None))

        assert (await service.get_update_notice())["download_url"] == DOWNLOAD_URL

    async def test_the_plugin_name_is_the_one_decky_matches_on(self):
        """package.json's name is a DIFFERENT string; handing Decky that one duplicates the install."""
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest=None), decky_name="Tender")

        notice = await service.get_update_notice()

        assert notice["plugin_name"] == "Tender"
        assert notice["plugin_name"] != FakePluginMetadataReader().name


class TestEveryFailureIsSilent:
    async def test_a_check_that_reached_nothing_says_nothing(self):
        service, releases, _, _ = _make(latest=None)

        notice = await service.get_update_notice()

        assert releases.calls == 1
        assert notice["available"] is False
        assert notice["latest_version"] is None
        assert notice["digest"] is None

    async def test_a_raising_seam_is_still_silent(self):
        """The seam promises never to raise; the promise the USER sees is this service's."""
        service, _, _, _ = _make(raises=TimeoutError("timed out"))

        notice = await service.get_update_notice()

        assert notice["available"] is False
        assert notice["latest_version"] is None

    async def test_a_failed_check_keeps_the_answer_it_already_had(self):
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        service, _, _, _ = _make(
            latest=LatestRelease(version="0.33.0", digest="ab33cd"), clock=clock, uow_factory=uow_factory
        )
        assert (await service.get_update_notice())["available"] is True

        clock.advance(_A_DAY + 1)
        offline, _, _, _ = _make(latest=None, clock=clock, uow_factory=uow_factory)
        notice = await offline.get_update_notice()

        assert notice["available"] is True
        assert notice["latest_version"] == "0.33.0"
        assert notice["digest"] == "ab33cd"

    async def test_a_failed_check_still_holds_the_next_one_off(self):
        """An offline Deck must not pay a request timeout on every panel open."""
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        service, releases, _, _ = _make(latest=None, clock=clock, uow_factory=uow_factory)

        await service.get_update_notice()
        await service.get_update_notice()

        assert releases.calls == 1
        assert _stored(uow_factory)["checked_at"] == clock.time()

    async def test_an_unusable_stored_stamp_is_simply_checked_again(self):
        uow_factory = FakeUnitOfWorkFactory()
        with uow_factory() as uow:
            uow.kv_config.set(LAST_CHECK_KEY, "{not json")
        service, releases, _, _ = _make(latest=LatestRelease(version="0.33.0", digest=None), uow_factory=uow_factory)

        assert (await service.get_update_notice())["latest_version"] == "0.33.0"
        assert releases.calls == 1


class TestTheDailyThrottle:
    async def test_a_second_ask_inside_the_day_reads_no_release(self):
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        service, releases, _, _ = _make(
            latest=LatestRelease(version="0.33.0", digest="ab33cd"), clock=clock, uow_factory=uow_factory
        )

        first = await service.get_update_notice()
        clock.advance(_A_DAY - 1)
        second = await service.get_update_notice()

        assert releases.calls == 1
        assert second == first

    async def test_a_restart_inside_the_day_still_shows_the_card(self):
        """The answer is persisted, so a reload neither loses the card nor asks again."""
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        first, releases, _, _ = _make(
            latest=LatestRelease(version="0.33.0", digest="ab33cd"), clock=clock, uow_factory=uow_factory
        )
        await first.get_update_notice()

        clock.advance(60)
        restarted, restarted_releases, _, _ = _make(latest=None, clock=clock, uow_factory=uow_factory)
        notice = await restarted.get_update_notice()

        assert restarted_releases.calls == 0
        assert releases.calls == 1
        assert notice["available"] is True
        assert notice["latest_version"] == "0.33.0"

    async def test_a_day_later_the_release_is_read_again(self):
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        service, releases, _, _ = _make(
            latest=LatestRelease(version="0.33.0", digest=None), clock=clock, uow_factory=uow_factory
        )

        await service.get_update_notice()
        clock.advance(_A_DAY)
        await service.get_update_notice()

        assert releases.calls == 2

    async def test_a_stamp_dated_in_the_future_is_due_now(self):
        """A backwards clock jump must not hold the check off for as long as the jump."""
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        with uow_factory() as uow:
            uow.kv_config.set(
                LAST_CHECK_KEY,
                encode_update_check(UpdateCheck(checked_at=clock.time() + _A_DAY * 30, version=None, digest=None)),
            )
        service, releases, _, _ = _make(
            latest=LatestRelease(version="0.33.0", digest=None), clock=clock, uow_factory=uow_factory
        )

        assert (await service.get_update_notice())["latest_version"] == "0.33.0"
        assert releases.calls == 1


class TestDismissal:
    async def test_the_dismissed_version_is_no_longer_announced(self):
        settings = {DISMISSED_KEY: "0.33.0"}
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest="ab33cd"), settings=settings)

        notice = await service.get_update_notice()

        assert notice["available"] is False
        assert notice["latest_version"] == "0.33.0"

    async def test_the_next_version_is_announced_again(self):
        """Dismissal is per version — one Dismiss must not end the only channel there is."""
        settings = {DISMISSED_KEY: "0.33.0"}
        service, _, _, _ = _make(latest=LatestRelease(version="0.34.0", digest=None), settings=settings)

        assert (await service.get_update_notice())["available"] is True

    def test_dismissing_records_the_version_and_persists(self):
        service, _, settings, persister = _make()

        assert service.dismiss_update_notice("0.33.0") == {"success": True}
        assert settings[DISMISSED_KEY] == "0.33.0"
        assert persister.saves == 1

    def test_dismissing_twice_writes_the_same_answer(self):
        service, _, settings, persister = _make()

        service.dismiss_update_notice("0.33.0")
        assert service.dismiss_update_notice("0.33.0") == {"success": True}
        assert settings[DISMISSED_KEY] == "0.33.0"
        assert persister.saves == 2

    @pytest.mark.parametrize("unusable", [None, "", 3, ["0.33.0"], True])
    def test_a_version_that_is_not_one_is_refused(self, unusable):
        service, _, settings, persister = _make()

        assert service.dismiss_update_notice(unusable) == {
            "success": False,
            "reason": "invalid_value",
            "message": "Invalid version",
        }
        assert DISMISSED_KEY not in settings
        assert persister.saves == 0

    async def test_a_dismissal_stored_as_something_else_does_not_hide_the_card(self):
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest=None), settings={DISMISSED_KEY: 33})

        assert (await service.get_update_notice())["available"] is True


class TestTheSwitch:
    async def test_the_check_is_on_where_nothing_was_ever_set(self):
        """Absence is the default, so an install that never touched it still checks."""
        service, releases, settings, _ = _make(latest=LatestRelease(version="0.33.0", digest=None))

        notice = await service.get_update_notice()

        assert ENABLED_KEY not in settings
        assert notice["enabled"] is True
        assert releases.calls == 1

    async def test_switched_off_nothing_is_read_and_nothing_is_available(self):
        uow_factory = FakeUnitOfWorkFactory()
        service, releases, _, _ = _make(
            latest=LatestRelease(version="0.33.0", digest="ab33cd"),
            settings={ENABLED_KEY: False},
            uow_factory=uow_factory,
        )

        notice = await service.get_update_notice()

        assert releases.calls == 0
        assert notice["available"] is False
        assert notice["enabled"] is False
        assert notice["latest_version"] is None
        assert notice["digest"] is None
        assert _nothing_stored(uow_factory)

    async def test_switched_off_the_answer_still_carries_what_an_install_needs(self):
        service, _, _, _ = _make(settings={ENABLED_KEY: False})

        notice = await service.get_update_notice()

        assert notice["current_version"] == "0.32.0"
        assert notice["download_url"] == DOWNLOAD_URL
        assert notice["plugin_name"] == "Tender"

    def test_setting_the_switch_persists_it(self):
        service, _, settings, persister = _make()

        assert service.set_update_check_enabled(False) == {"success": True}
        assert settings[ENABLED_KEY] is False
        assert persister.saves == 1

    def test_switching_it_back_on_persists_too(self):
        service, _, settings, persister = _make(settings={ENABLED_KEY: False})

        assert service.set_update_check_enabled(True) == {"success": True}
        assert settings[ENABLED_KEY] is True
        assert persister.saves == 1

    @pytest.mark.parametrize("unusable", [None, "", "true", 1, 0, []])
    def test_a_value_that_is_not_a_boolean_is_refused(self, unusable):
        service, _, settings, persister = _make()

        assert service.set_update_check_enabled(unusable) == {
            "success": False,
            "reason": "invalid_value",
            "message": "Invalid value",
        }
        assert ENABLED_KEY not in settings
        assert persister.saves == 0


class TestTheAnswerShape:
    async def test_every_field_the_frontend_reads_is_present(self):
        service, _, _, _ = _make(latest=LatestRelease(version="0.33.0", digest="ab33cd"))

        notice = await service.get_update_notice()

        assert set(notice) == {
            "available",
            "latest_version",
            "current_version",
            "download_url",
            "plugin_name",
            "digest",
            "enabled",
        }
