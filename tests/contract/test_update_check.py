"""Contract tests for the update-check callables over the real wiring.

Driven frontend-shaped per ``frontend/src/api/backend.ts``: ``getUpdateNotice``
and ``checkForUpdateNow`` take nothing, ``dismissUpdateNotice`` a version string,
``setUpdateCheckEnabled`` a boolean. The real ``bootstrap()`` and SQLite are what
make it worth having: the answer really does outlive the call in the database,
and the two user-intent keys really do reach ``settings.json``.
"""

from __future__ import annotations

import json
import os
from typing import Any

from domain.update_release import LatestRelease, ReleaseTarball

_A_DAY = 24 * 60 * 60
_NOTICE_KEYS = {"available", "newer", "latest_version", "current_version", "enabled", "installed_program"}


def _release(version: str) -> LatestRelease:
    url = (
        f"https://github.com/danielcopper/romm-tender/releases/download/tender-v{version}/romm-tender-{version}.tar.gz"
    )
    return LatestRelease(version=version, tarball=ReleaseTarball(url=url, digest="ab99cd"))


def _settings_on_disk(harness) -> dict[str, Any]:
    with open(os.path.join(harness.settings_dir, "settings.json")) as f:
        return json.load(f)


async def test_a_newer_release_raises_the_card(harness):
    harness.releases.answer = _release("99.0.0")

    notice = await harness.plugin.get_update_notice()

    assert set(notice) == _NOTICE_KEYS
    assert notice["available"] is True
    assert notice["newer"] is True
    assert notice["latest_version"] == "99.0.0"
    assert notice["enabled"] is True
    assert notice["installed_program"] is False


async def test_a_release_without_its_tarball_raises_nothing(harness):
    harness.releases.answer = LatestRelease(version="99.0.0", tarball=None)

    notice = await harness.plugin.get_update_notice()

    assert notice["available"] is False
    assert notice["latest_version"] is None


async def test_a_check_that_reached_nothing_reports_no_update(harness):
    harness.releases.answer = None

    notice = await harness.plugin.get_update_notice()

    assert notice["available"] is False
    assert notice["latest_version"] is None


async def test_the_answer_outlives_the_call_and_is_not_read_again(harness):
    harness.releases.answer = _release("99.0.0")
    await harness.plugin.get_update_notice()

    harness.clock.advance(60)
    harness.releases.answer = None
    notice = await harness.plugin.get_update_notice()

    assert harness.releases.calls == 1
    assert notice["available"] is True


async def test_a_day_later_the_release_is_read_again(harness):
    harness.releases.answer = _release("99.0.0")
    await harness.plugin.get_update_notice()

    harness.clock.advance(_A_DAY)
    await harness.plugin.get_update_notice()

    assert harness.releases.calls == 2


async def test_dismissing_a_version_takes_that_card_down_only(harness):
    harness.releases.answer = _release("99.0.0")
    await harness.plugin.get_update_notice()

    assert harness.plugin.dismiss_update_notice("99.0.0") == {"success": True}
    assert (await harness.plugin.get_update_notice())["available"] is False
    assert _settings_on_disk(harness)["update_notice_dismissed_version"] == "99.0.0"

    harness.clock.advance(_A_DAY)
    harness.releases.answer = _release("99.1.0")
    assert (await harness.plugin.get_update_notice())["available"] is True


async def test_switching_the_check_off_stops_the_read(harness):
    assert harness.plugin.set_update_check_enabled(False) == {"success": True}
    harness.releases.answer = _release("99.0.0")

    notice = await harness.plugin.get_update_notice()

    assert harness.releases.calls == 0
    assert notice["enabled"] is False
    assert notice["available"] is False
    assert _settings_on_disk(harness)["update_check_enabled"] is False


async def test_a_non_boolean_switch_takes_the_canonical_failure_shape(harness):
    result = harness.plugin.set_update_check_enabled("yes")

    assert result == {"success": False, "reason": "invalid_value", "message": "Invalid value"}


async def test_a_version_that_is_not_one_takes_the_canonical_failure_shape(harness):
    result = harness.plugin.dismiss_update_notice(None)

    assert result == {"success": False, "reason": "invalid_value", "message": "Invalid version"}


async def test_check_for_update_now_reads_again_and_brings_a_dismissed_card_back(harness):
    harness.releases.answer = _release("99.0.0")
    await harness.plugin.get_update_notice()
    harness.plugin.dismiss_update_notice("99.0.0")
    harness.clock.advance(60)

    notice = await harness.plugin.check_for_update_now()

    assert harness.releases.calls == 2
    assert set(notice) == _NOTICE_KEYS | {"reached"}
    assert notice["reached"] is True
    assert notice["available"] is True
    assert "update_notice_dismissed_version" not in _settings_on_disk(harness)


async def test_a_check_that_reached_nothing_is_told_apart_from_nothing_being_out(harness):
    harness.releases.answer = None
    unreachable = await harness.plugin.check_for_update_now()

    harness.releases.answer = _release(unreachable["current_version"])
    nothing_newer = await harness.plugin.check_for_update_now()

    assert (unreachable["reached"], unreachable["available"]) == (False, False)
    assert (nothing_newer["reached"], nothing_newer["available"]) == (True, False)


async def test_the_switch_still_holds_against_an_asked_for_check(harness):
    harness.plugin.set_update_check_enabled(False)
    harness.releases.answer = _release("99.0.0")

    notice = await harness.plugin.check_for_update_now()

    assert harness.releases.calls == 0
    assert (notice["enabled"], notice["reached"], notice["available"]) == (False, False, False)
