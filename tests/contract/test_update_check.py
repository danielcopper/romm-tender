"""Contract tests for the update-check callables over the real wiring.

Drives the real ``main.py`` callables through the real ``bootstrap()`` + SQLite,
pinning the payload the frontend consumes and that the answer really does
survive in the database — the plugin is not in Decky's catalogue, so this is the
only route by which a user ever hears that a release exists.
"""

from __future__ import annotations

from domain.update_release import LatestRelease

_A_DAY = 24 * 60 * 60


async def test_get_update_notice_reports_a_newer_release(harness):
    harness.releases.answer = LatestRelease(version="99.0.0", digest="ab33cd")

    notice = await harness.plugin.get_update_notice()

    assert notice["available"] is True
    assert notice["latest_version"] == "99.0.0"
    assert notice["digest"] == "ab33cd"
    assert notice["enabled"] is True
    assert notice["download_url"].endswith("/releases/latest/download/Tender.zip")
    assert set(notice) == {
        "available",
        "latest_version",
        "current_version",
        "download_url",
        "plugin_name",
        "digest",
        "enabled",
    }


async def test_a_check_that_reached_nothing_reports_no_update(harness):
    """No network, no error — the panel is told there is nothing to show."""
    harness.releases.answer = None

    notice = await harness.plugin.get_update_notice()

    assert notice["available"] is False
    assert notice["latest_version"] is None


async def test_the_answer_outlives_the_call_and_is_not_read_again(harness):
    harness.releases.answer = LatestRelease(version="99.0.0", digest=None)
    await harness.plugin.get_update_notice()

    harness.clock.advance(60)
    harness.releases.answer = None
    notice = await harness.plugin.get_update_notice()

    assert harness.releases.calls == 1
    assert notice["available"] is True
    assert notice["latest_version"] == "99.0.0"


async def test_a_day_later_the_release_is_read_again(harness):
    harness.releases.answer = LatestRelease(version="99.0.0", digest=None)
    await harness.plugin.get_update_notice()

    harness.clock.advance(_A_DAY)
    await harness.plugin.get_update_notice()

    assert harness.releases.calls == 2


async def test_dismissing_a_version_takes_that_card_down_only(harness):
    harness.releases.answer = LatestRelease(version="99.0.0", digest=None)
    await harness.plugin.get_update_notice()

    assert await harness.plugin.dismiss_update_notice("99.0.0") == {"success": True}
    assert (await harness.plugin.get_update_notice())["available"] is False

    harness.clock.advance(_A_DAY)
    harness.releases.answer = LatestRelease(version="99.1.0", digest=None)
    assert (await harness.plugin.get_update_notice())["available"] is True


async def test_switching_the_check_off_stops_the_read(harness):
    assert await harness.plugin.set_update_check_enabled(False) == {"success": True}
    harness.releases.answer = LatestRelease(version="99.0.0", digest=None)

    notice = await harness.plugin.get_update_notice()

    assert harness.releases.calls == 0
    assert notice["enabled"] is False
    assert notice["available"] is False
    assert notice["latest_version"] is None


async def test_the_switch_survives_in_settings(harness):
    await harness.plugin.set_update_check_enabled(False)
    assert harness.plugin.settings["update_check_enabled"] is False

    await harness.plugin.set_update_check_enabled(True)
    assert harness.plugin.settings["update_check_enabled"] is True


async def test_a_non_boolean_switch_takes_the_canonical_failure_shape(harness):
    result = await harness.plugin.set_update_check_enabled("yes")

    assert result == {"success": False, "reason": "invalid_value", "message": "Invalid value"}


async def test_a_version_that_is_not_one_takes_the_canonical_failure_shape(harness):
    result = await harness.plugin.dismiss_update_notice(None)

    assert result == {"success": False, "reason": "invalid_value", "message": "Invalid version"}
