"""Tests for UpdateCheckService — whether a newer release is out, and whether to say so."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
from fakes.fake_latest_release import FakeLatestRelease
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock

from domain.update_release import LatestRelease, ReleaseTarball, UpdateCheck, encode_update_check
from services.update_check import (
    DISMISSED_KEY,
    ENABLED_KEY,
    LAST_CHECK_KEY,
    UpdateCheckService,
    UpdateCheckServiceConfig,
)

_A_DAY = 24 * 60 * 60
_NOTICE_KEYS = {"available", "newer", "latest_version", "current_version", "enabled", "installed_program"}


_HEX = "ab34" * 16


def _release(version: str, digest: str = _HEX) -> LatestRelease:
    url = f"https://x.test/releases/download/tender-v{version}/romm-tender-{version}.tar.gz"
    return LatestRelease(version=version, tarball=ReleaseTarball(url=url, digest=digest))


def _untarred(version: str) -> LatestRelease:
    """A release published minutes ago, whose tarball the assets job has not attached yet."""
    return LatestRelease(version=version, tarball=None)


def _make(
    *,
    latest: LatestRelease | None = None,
    raises: Exception | None = None,
    settings: dict[str, Any] | None = None,
    current_version: str = "0.33.0",
    installed_program: bool = True,
    clock: FakeClock | None = None,
    uow_factory: FakeUnitOfWorkFactory | None = None,
    log: list[str] | None = None,
):
    releases = FakeLatestRelease(answer=latest, raises=raises)
    used_settings = settings if settings is not None else {}
    persister = FakeSettingsPersister()
    sink = log if log is not None else []
    service = UpdateCheckService(
        config=UpdateCheckServiceConfig(
            latest_release=releases,
            current_version=current_version,
            installed_program=installed_program,
            clock=clock if clock is not None else FakeClock(),
            uow_factory=uow_factory if uow_factory is not None else FakeUnitOfWorkFactory(),
            settings=used_settings,
            settings_persister=persister,
            loop=running_loop(),
            log_debug=lambda msg: sink.append(msg),
        ),
    )
    return service, releases, used_settings, persister


def _stored(uow_factory: FakeUnitOfWorkFactory) -> dict[str, Any]:
    with uow_factory() as uow:
        raw = uow.kv_config.get(LAST_CHECK_KEY)
    assert raw is not None
    return json.loads(raw)


def _nothing_stored(uow_factory: FakeUnitOfWorkFactory) -> bool:
    with uow_factory() as uow:
        return uow.kv_config.get(LAST_CHECK_KEY) is None


class TestWhetherAnUpdateIsAvailable:
    async def test_a_newer_release_is_available(self):
        notice = await _make(latest=_release("0.34.0"))[0].get_update_notice()

        assert notice == {
            "available": True,
            "newer": True,
            "latest_version": "0.34.0",
            "current_version": "0.33.0",
            "enabled": True,
            "installed_program": True,
        }

    async def test_the_running_release_is_not_an_update(self):
        notice = await _make(latest=_release("0.33.0"))[0].get_update_notice()

        assert notice["available"] is False
        assert notice["newer"] is False
        assert notice["latest_version"] == "0.33.0"

    async def test_an_older_release_is_not_an_update(self):
        """A development build ahead of the last release must not be told to downgrade."""
        service, _, _, _ = _make(latest=_release("0.32.0"))

        assert (await service.get_update_notice())["available"] is False

    async def test_the_answer_says_whether_this_is_the_installed_program(self):
        service, _, _, _ = _make(latest=_release("0.34.0"), installed_program=False)

        notice = await service.get_update_notice()

        assert notice["installed_program"] is False
        assert notice["available"] is True, "a checkout still sees the card"

    async def test_the_address_and_digest_are_kept_for_the_install_but_never_put_on_the_wire(self):
        uow_factory = FakeUnitOfWorkFactory()
        service, _, _, _ = _make(latest=_release("0.34.0"), uow_factory=uow_factory)

        notice = await service.get_update_notice()

        assert set(notice) == _NOTICE_KEYS
        stored = _stored(uow_factory)
        assert stored["tarball_url"] == "https://x.test/releases/download/tender-v0.34.0/romm-tender-0.34.0.tar.gz"
        assert stored["digest"] == _HEX


class TestAReleaseWithoutItsTarball:
    async def test_is_not_available(self):
        uow_factory = FakeUnitOfWorkFactory()
        service, _, _, _ = _make(latest=_untarred("0.34.0"), uow_factory=uow_factory)

        notice = await service.get_update_notice()

        assert notice["available"] is False
        assert notice["latest_version"] is None
        assert _stored(uow_factory)["version"] is None

    async def test_leaves_the_last_available_release_standing(self):
        """0.34.0 is still a real, installable release while 0.35.0 waits for its tarball."""
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        service, releases, _, _ = _make(latest=_release("0.34.0"), clock=clock, uow_factory=uow_factory)
        await service.get_update_notice()

        clock.advance(_A_DAY)
        releases.answer = _untarred("0.35.0")
        notice = await service.get_update_notice()

        assert notice["latest_version"] == "0.34.0"
        assert notice["available"] is True
        assert _stored(uow_factory)["tarball_url"].endswith("romm-tender-0.34.0.tar.gz")

    async def test_is_announced_once_the_tarball_is_attached(self):
        service, releases, _, _ = _make(latest=_untarred("0.34.0"))
        assert (await service.check_for_update_now())["available"] is False

        releases.answer = _release("0.34.0")

        assert (await service.check_for_update_now())["available"] is True

    async def test_was_still_reached(self):
        """GitHub answered; there is simply nothing to install yet."""
        notice = await _make(latest=_untarred("0.34.0"))[0].check_for_update_now()

        assert notice["reached"] is True
        assert notice["available"] is False


class TestTheDigest:
    async def test_a_stored_release_with_a_valid_digest_is_available(self):
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        with uow_factory() as uow:
            uow.kv_config.set(
                LAST_CHECK_KEY,
                json.dumps(
                    {"checked_at": clock.time(), "version": "0.34.0", "tarball_url": "https://x.test/t", "digest": _HEX}
                ),
            )
        service, releases, _, _ = _make(latest=None, clock=clock, uow_factory=uow_factory)

        notice = await service.get_update_notice()

        assert releases.calls == 0
        assert notice["available"] is True

    async def test_a_stored_release_without_a_valid_digest_raises_no_card(self):
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        with uow_factory() as uow:
            uow.kv_config.set(
                LAST_CHECK_KEY,
                json.dumps(
                    {"checked_at": clock.time(), "version": "0.34.0", "tarball_url": "https://x.test/t", "digest": None}
                ),
            )
        service, releases, _, _ = _make(latest=None, clock=clock, uow_factory=uow_factory)

        notice = await service.get_update_notice()

        assert releases.calls == 0
        assert notice["available"] is False
        assert notice["latest_version"] is None


class TestEveryFailureIsSilent:
    async def test_a_check_that_reached_nothing_says_nothing(self):
        service, releases, _, _ = _make(latest=None)

        notice = await service.get_update_notice()

        assert releases.calls == 1
        assert notice["available"] is False
        assert notice["latest_version"] is None

    async def test_a_raising_seam_is_still_silent_and_reaches_the_debug_log(self):
        log: list[str] = []
        service, _, _, _ = _make(raises=TimeoutError("timed out"), log=log)

        notice = await service.get_update_notice()

        assert notice["available"] is False
        assert any("timed out" in line for line in log)

    async def test_a_failed_check_keeps_the_answer_it_already_had_whole(self):
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        service, releases, _, _ = _make(latest=_release("0.34.0"), clock=clock, uow_factory=uow_factory)
        await service.get_update_notice()

        clock.advance(_A_DAY + 1)
        releases.answer = None
        notice = await service.get_update_notice()

        assert notice["available"] is True
        assert notice["latest_version"] == "0.34.0"
        stored = _stored(uow_factory)
        assert stored["digest"] == _HEX
        assert stored["tarball_url"].endswith("romm-tender-0.34.0.tar.gz")

    async def test_a_failed_check_still_holds_the_next_one_off(self):
        """An offline machine must not pay a request timeout at every panel load."""
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
        service, releases, _, _ = _make(latest=_release("0.34.0"), uow_factory=uow_factory)

        assert (await service.get_update_notice())["latest_version"] == "0.34.0"
        assert releases.calls == 1


class TestTheDailyThrottle:
    async def test_a_second_ask_inside_the_day_reads_no_release(self):
        clock = FakeClock()
        service, releases, _, _ = _make(latest=_release("0.34.0"), clock=clock)

        first = await service.get_update_notice()
        clock.advance(_A_DAY - 1)
        second = await service.get_update_notice()

        assert releases.calls == 1
        assert second == first

    async def test_a_restart_inside_the_day_still_shows_the_card(self):
        uow_factory = FakeUnitOfWorkFactory()
        clock = FakeClock()
        first, _, _, _ = _make(latest=_release("0.34.0"), clock=clock, uow_factory=uow_factory)
        await first.get_update_notice()

        clock.advance(60)
        restarted, restarted_releases, _, _ = _make(latest=None, clock=clock, uow_factory=uow_factory)
        notice = await restarted.get_update_notice()

        assert restarted_releases.calls == 0
        assert notice["available"] is True

    async def test_a_day_later_the_release_is_read_again(self):
        clock = FakeClock()
        service, releases, _, _ = _make(latest=_release("0.34.0"), clock=clock)

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
                LAST_CHECK_KEY, encode_update_check(UpdateCheck(checked_at=clock.time() + _A_DAY * 30, release=None))
            )
        service, releases, _, _ = _make(latest=_release("0.34.0"), clock=clock, uow_factory=uow_factory)

        assert (await service.get_update_notice())["latest_version"] == "0.34.0"
        assert releases.calls == 1


class TestDismissal:
    async def test_the_dismissed_version_raises_no_card_but_is_still_named(self):
        service, _, _, _ = _make(latest=_release("0.34.0"), settings={DISMISSED_KEY: "0.34.0"})

        notice = await service.get_update_notice()

        assert notice["available"] is False
        assert notice["newer"] is True, "the Settings section still states what is out"
        assert notice["latest_version"] == "0.34.0"

    async def test_the_next_version_is_announced_again(self):
        service, _, _, _ = _make(latest=_release("0.35.0"), settings={DISMISSED_KEY: "0.34.0"})

        assert (await service.get_update_notice())["available"] is True

    def test_dismissing_records_the_version_and_persists(self):
        service, _, settings, persister = _make()

        assert service.dismiss_update_notice("0.34.0") == {"success": True}
        assert settings[DISMISSED_KEY] == "0.34.0"
        assert persister.save_count == 1

    @pytest.mark.parametrize("unusable", [None, "", 3, ["0.34.0"], True])
    def test_a_version_that_is_not_one_is_refused(self, unusable):
        service, _, settings, persister = _make()

        assert service.dismiss_update_notice(unusable) == {
            "success": False,
            "reason": "invalid_value",
            "message": "Invalid version",
        }
        assert DISMISSED_KEY not in settings
        assert persister.save_count == 0

    async def test_a_dismissal_stored_as_something_else_does_not_hide_the_card(self):
        service, _, _, _ = _make(latest=_release("0.34.0"), settings={DISMISSED_KEY: 34})

        assert (await service.get_update_notice())["available"] is True


class TestTheSwitch:
    async def test_the_check_is_on_where_nothing_was_ever_set(self):
        service, releases, settings, _ = _make(latest=_release("0.34.0"))

        notice = await service.get_update_notice()

        assert ENABLED_KEY not in settings
        assert notice["enabled"] is True
        assert releases.calls == 1

    async def test_switched_off_nothing_is_read_and_nothing_is_available(self):
        uow_factory = FakeUnitOfWorkFactory()
        service, releases, _, _ = _make(
            latest=_release("0.34.0"), settings={ENABLED_KEY: False}, uow_factory=uow_factory
        )

        notice = await service.get_update_notice()

        assert releases.calls == 0
        assert notice == {
            "available": False,
            "newer": False,
            "latest_version": None,
            "current_version": "0.33.0",
            "enabled": False,
            "installed_program": True,
        }
        assert _nothing_stored(uow_factory)

    def test_setting_the_switch_persists_it(self):
        service, _, settings, persister = _make()

        assert service.set_update_check_enabled(False) == {"success": True}
        assert settings[ENABLED_KEY] is False
        assert persister.save_count == 1

    def test_switching_it_back_on_persists_too(self):
        service, _, settings, persister = _make(settings={ENABLED_KEY: False})

        assert service.set_update_check_enabled(True) == {"success": True}
        assert settings[ENABLED_KEY] is True
        assert persister.save_count == 1

    @pytest.mark.parametrize("unusable", [None, "", "true", 1, 0, []])
    def test_a_value_that_is_not_a_boolean_is_refused(self, unusable):
        service, _, settings, persister = _make()

        assert service.set_update_check_enabled(unusable) == {
            "success": False,
            "reason": "invalid_value",
            "message": "Invalid value",
        }
        assert ENABLED_KEY not in settings
        assert persister.save_count == 0


class TestCheckingNow:
    async def test_the_release_is_read_again_inside_the_day(self):
        clock = FakeClock()
        service, releases, _, _ = _make(latest=_release("0.34.0"), clock=clock)
        await service.get_update_notice()
        clock.advance(60)

        notice = await service.check_for_update_now()

        assert releases.calls == 2
        assert notice["available"] is True
        assert notice["reached"] is True

    async def test_the_forced_read_re_arms_the_throttle(self):
        clock = FakeClock()
        service, releases, _, _ = _make(latest=_release("0.34.0"), clock=clock)

        await service.check_for_update_now()
        clock.advance(60)
        await service.get_update_notice()

        assert releases.calls == 1

    async def test_a_dismissed_release_is_announced_again_and_stays_so(self):
        clock = FakeClock()
        service, _, settings, persister = _make(
            latest=_release("0.34.0"), settings={DISMISSED_KEY: "0.34.0"}, clock=clock
        )
        assert (await service.get_update_notice())["available"] is False

        assert (await service.check_for_update_now())["available"] is True
        assert DISMISSED_KEY not in settings
        assert persister.save_count == 1

        clock.advance(_A_DAY)
        assert (await service.get_update_notice())["available"] is True

    async def test_nothing_is_persisted_where_nothing_was_dismissed(self):
        service, _, _, persister = _make(latest=_release("0.34.0"))

        await service.check_for_update_now()

        assert persister.save_count == 0

    async def test_a_read_that_reached_nothing_says_so(self):
        notice = await _make(latest=None)[0].check_for_update_now()

        assert notice["reached"] is False
        assert notice["available"] is False

    async def test_a_raising_seam_also_reaches_nothing(self):
        notice = await _make(raises=RuntimeError("github said no"))[0].check_for_update_now()

        assert notice["reached"] is False

    async def test_a_failed_read_keeps_the_answer_it_already_had(self):
        service, releases, _, _ = _make(latest=_release("0.34.0"))
        await service.get_update_notice()

        releases.answer = None
        notice = await service.check_for_update_now()

        assert notice["reached"] is False
        assert notice["latest_version"] == "0.34.0"
        assert notice["available"] is True

    async def test_a_release_that_answered_is_reached_even_when_it_is_not_newer(self):
        notice = await _make(latest=_release("0.33.0"))[0].check_for_update_now()

        assert notice["reached"] is True
        assert notice["available"] is False

    async def test_with_the_switch_off_nothing_is_read_and_nothing_is_forgotten(self):
        uow_factory = FakeUnitOfWorkFactory()
        service, releases, settings, persister = _make(
            latest=_release("0.34.0"),
            settings={ENABLED_KEY: False, DISMISSED_KEY: "0.34.0"},
            uow_factory=uow_factory,
        )

        notice = await service.check_for_update_now()

        assert releases.calls == 0
        assert notice["enabled"] is False
        assert notice["reached"] is False
        assert notice["available"] is False
        assert settings[DISMISSED_KEY] == "0.34.0"
        assert persister.save_count == 0
        assert _nothing_stored(uow_factory)

    async def test_the_answer_is_the_notice_plus_whether_the_read_answered(self):
        notice = await _make(latest=_release("0.34.0"))[0].check_for_update_now()

        assert set(notice) == _NOTICE_KEYS | {"reached"}


class _GatedRelease:
    """A release seam whose first answer is held until the test lets it go.

    Answers run on executor threads, so the gate is a thread event: the first
    call waits on it, every later call answers at once.
    """

    def __init__(self, first: LatestRelease | None, later: LatestRelease | None) -> None:
        self.first = first
        self.later = later
        self.calls = 0
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def __call__(self) -> LatestRelease | None:
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            self.release_first.wait(timeout=5)
            return self.first
        return self.later


class TestOverlappingChecks:
    """The panel-load read and a Check now can overlap; one runs at a time."""

    def _make_gated(self, seam: _GatedRelease, uow_factory: FakeUnitOfWorkFactory) -> UpdateCheckService:
        return self._make_gated_with_settings(seam, uow_factory, {})[0]

    def _make_gated_with_settings(
        self, seam: _GatedRelease, uow_factory: FakeUnitOfWorkFactory, settings: dict[str, Any]
    ) -> tuple[UpdateCheckService, dict[str, Any]]:
        service = UpdateCheckService(
            config=UpdateCheckServiceConfig(
                latest_release=seam,
                current_version="0.33.0",
                installed_program=True,
                clock=FakeClock(),
                uow_factory=uow_factory,
                settings=settings,
                settings_persister=FakeSettingsPersister(),
                loop=running_loop(),
                log_debug=lambda msg: None,
            ),
        )
        return service, settings

    async def test_a_slow_failed_read_does_not_stamp_over_a_fresh_answer(self):
        """Without the lock the panel-load read, finishing last, recorded its stale previous answer."""
        uow_factory = FakeUnitOfWorkFactory()
        seam = _GatedRelease(first=None, later=_release("0.35.0"))
        service = self._make_gated(seam, uow_factory)

        panel_load = asyncio.ensure_future(service.get_update_notice())
        await asyncio.to_thread(seam.first_started.wait, 5)
        check_now = asyncio.ensure_future(service.check_for_update_now())
        # Room for an unserialised Check now to finish first, which is the
        # ordering that lost the fresh answer; with the lock it waits instead.
        await asyncio.sleep(0.2)
        seam.release_first.set()
        await asyncio.gather(panel_load, check_now)

        assert _stored(uow_factory)["version"] == "0.35.0"
        assert (await service.get_update_notice())["latest_version"] == "0.35.0"

    async def test_two_throttled_reads_at_once_ask_github_once(self):
        """The second re-reads the stamp the first just wrote, and finds it not due."""
        uow_factory = FakeUnitOfWorkFactory()
        seam = _GatedRelease(first=_release("0.34.0"), later=_release("0.34.0"))
        service = self._make_gated(seam, uow_factory)

        first = asyncio.ensure_future(service.get_update_notice())
        await asyncio.to_thread(seam.first_started.wait, 5)
        second = asyncio.ensure_future(service.get_update_notice())
        await asyncio.sleep(0)
        seam.release_first.set()
        answers = await asyncio.gather(first, second)

        assert seam.calls == 1
        assert [a["latest_version"] for a in answers] == ["0.34.0", "0.34.0"]

    async def test_a_check_now_queued_behind_a_read_asks_nothing_once_the_switch_went_off(self):
        uow_factory = FakeUnitOfWorkFactory()
        seam = _GatedRelease(first=_release("0.34.0"), later=_release("0.35.0"))
        service, settings = self._make_gated_with_settings(seam, uow_factory, {DISMISSED_KEY: "0.34.0"})

        panel_load = asyncio.ensure_future(service.get_update_notice())
        await asyncio.to_thread(seam.first_started.wait, 5)
        check_now = asyncio.ensure_future(service.check_for_update_now())
        await asyncio.sleep(0)
        assert service.set_update_check_enabled(False) == {"success": True}
        seam.release_first.set()
        _, answer = await asyncio.gather(panel_load, check_now)

        assert seam.calls == 1
        assert answer["enabled"] is False
        assert answer["reached"] is False
        assert answer["latest_version"] is None
        assert settings[DISMISSED_KEY] == "0.34.0", "nothing is forgotten either"

    async def test_a_throttled_read_queued_behind_a_check_asks_nothing_once_the_switch_went_off(self):
        uow_factory = FakeUnitOfWorkFactory()
        seam = _GatedRelease(first=_release("0.34.0"), later=_release("0.35.0"))
        service, _ = self._make_gated_with_settings(seam, uow_factory, {})

        check_now = asyncio.ensure_future(service.check_for_update_now())
        await asyncio.to_thread(seam.first_started.wait, 5)
        panel_load = asyncio.ensure_future(service.get_update_notice())
        await asyncio.sleep(0)
        service.set_update_check_enabled(False)
        seam.release_first.set()
        _, answer = await asyncio.gather(check_now, panel_load)

        assert seam.calls == 1
        assert answer["enabled"] is False

    async def test_a_dismiss_pressed_while_a_check_now_waited_is_not_erased(self):
        """Check now forgets the dismissal standing at its press, not a newer one."""
        uow_factory = FakeUnitOfWorkFactory()
        seam = _GatedRelease(first=_release("0.34.0"), later=_release("0.34.0"))
        service, settings = self._make_gated_with_settings(seam, uow_factory, {})

        panel_load = asyncio.ensure_future(service.get_update_notice())
        await asyncio.to_thread(seam.first_started.wait, 5)
        check_now = asyncio.ensure_future(service.check_for_update_now())
        await asyncio.sleep(0)
        assert service.dismiss_update_notice("0.34.0") == {"success": True}
        seam.release_first.set()
        _, answer = await asyncio.gather(panel_load, check_now)

        assert settings[DISMISSED_KEY] == "0.34.0"
        assert answer["available"] is False
        assert answer["latest_version"] == "0.34.0"

    async def test_a_newer_dismiss_pressed_while_a_check_now_waited_replaces_the_one_it_was_undoing(self):
        """Check now forgets the value standing at its press — never a different one that took its place."""
        uow_factory = FakeUnitOfWorkFactory()
        seam = _GatedRelease(first=_release("0.35.0"), later=_release("0.35.0"))
        service, settings = self._make_gated_with_settings(seam, uow_factory, {DISMISSED_KEY: "0.34.0"})

        panel_load = asyncio.ensure_future(service.get_update_notice())
        await asyncio.to_thread(seam.first_started.wait, 5)
        check_now = asyncio.ensure_future(service.check_for_update_now())
        await asyncio.sleep(0)
        assert service.dismiss_update_notice("0.35.0") == {"success": True}
        seam.release_first.set()
        _, answer = await asyncio.gather(panel_load, check_now)

        assert settings[DISMISSED_KEY] == "0.35.0"
        assert answer["available"] is False
