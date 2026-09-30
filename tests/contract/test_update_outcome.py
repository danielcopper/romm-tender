"""Contract tests for the update-outcome endpoints over the real wiring.

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``getUpdateOutcome``, ``acknowledgeUpdateToast`` and
``dismissUpdateAnnouncement`` take nothing, ``dismissUpdateFailure`` the
record's ``rolled_back_at`` string. The real ``bootstrap()`` is what makes it
worth having: the record is read by the real adapter from the state directory
the run was told about, the last-run version really is stored in SQLite, and the
dismissal really reaches ``settings.json``.

The harness runs none of the start-up repairs, so a test that is about a start
calls the step they run, ``note_start``, itself.
"""

from __future__ import annotations

import json
import os
from typing import Any

from domain.identity import VERSION

_OUTCOME_KEYS = {"announce_version", "announce_direction", "toast_owed", "failure", "failure_dismissed"}
_STAMP = "2026-09-25T10:15:00Z"


def _record(
    harness, *, attempted: str = "99.0.0", restored: str = VERSION, at: str = _STAMP, kind: str | None = None
) -> None:
    """Leave the installer's record where the installer leaves it, spelled as it spells it."""
    state = harness.tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    tail = f', "kind": "{kind}"' if kind is not None else ""
    (state / "update-failure.json").write_text(
        f'{{"attempted_version": "{attempted}", "restored_version": "{restored}", "rolled_back_at": "{at}"{tail}}}\n',
        encoding="utf-8",
    )


def _last_run(harness, version: str | None = None) -> str | None:
    with harness.uow_factory() as uow:
        if version is not None:
            uow.kv_config.set("last_run_version", version)
        return uow.kv_config.get("last_run_version")


def _settings_on_disk(harness) -> dict[str, Any]:
    with open(os.path.join(harness.settings_dir, "settings.json")) as f:
        return json.load(f)


async def test_a_start_after_an_update_owes_one_announcement(harness):
    _last_run(harness, "0.0.1")
    harness.app.services.update_outcome_service.note_start()

    outcome = await harness.endpoints.get_update_outcome()

    assert set(outcome) == _OUTCOME_KEYS
    assert outcome == {
        "announce_version": VERSION,
        "announce_direction": "updated",
        "toast_owed": True,
        "failure": None,
        "failure_dismissed": False,
    }
    assert _last_run(harness) == VERSION


async def test_a_start_after_a_return_to_an_earlier_release_owes_one_announcement_of_it(harness):
    _last_run(harness, "99.0.0")
    harness.app.services.update_outcome_service.note_start()

    outcome = await harness.endpoints.get_update_outcome()

    assert outcome == {
        "announce_version": VERSION,
        "announce_direction": "back",
        "toast_owed": True,
        "failure": None,
        "failure_dismissed": False,
    }
    assert _last_run(harness) == VERSION


async def test_a_raised_toast_is_not_owed_again_and_the_card_stays_until_dismissed(harness):
    _last_run(harness, "0.0.1")
    harness.app.services.update_outcome_service.note_start()

    assert harness.endpoints.acknowledge_update_toast() == {"success": True}

    outcome = await harness.endpoints.get_update_outcome()
    assert (outcome["announce_version"], outcome["announce_direction"], outcome["toast_owed"]) == (
        VERSION,
        "updated",
        False,
    )

    assert harness.endpoints.dismiss_update_announcement() == {"success": True}

    outcome = await harness.endpoints.get_update_outcome()
    assert (outcome["announce_version"], outcome["announce_direction"], outcome["toast_owed"]) == (None, None, False)


async def test_a_card_dismissed_before_its_toast_owes_no_toast(harness):
    _last_run(harness, "0.0.1")
    harness.app.services.update_outcome_service.note_start()

    assert harness.endpoints.dismiss_update_announcement() == {"success": True}

    outcome = await harness.endpoints.get_update_outcome()
    assert (outcome["announce_version"], outcome["toast_owed"]) == (None, False)


async def test_the_first_start_records_its_version_and_owes_nothing(harness):
    harness.app.services.update_outcome_service.note_start()

    assert (await harness.endpoints.get_update_outcome())["announce_version"] is None
    assert _last_run(harness) == VERSION


async def test_a_start_after_a_rollback_announces_nothing_and_reports_the_record(harness):
    """The installer restored the database, so the stored version is the one running again."""
    _last_run(harness, VERSION)
    _record(harness)
    harness.app.services.update_outcome_service.note_start()

    outcome = await harness.endpoints.get_update_outcome()

    assert outcome == {
        "announce_version": None,
        "announce_direction": None,
        "toast_owed": False,
        "failure": {
            "attempted_version": "99.0.0",
            "restored_version": VERSION,
            "rolled_back_at": _STAMP,
            "kind": "rollback",
        },
        "failure_dismissed": False,
    }


async def test_a_refusal_by_the_installer_s_check_is_reported_with_its_kind(harness):
    _last_run(harness, VERSION)
    _record(harness, kind="check")
    harness.app.services.update_outcome_service.note_start()

    outcome = await harness.endpoints.get_update_outcome()

    assert outcome["failure"] == {
        "attempted_version": "99.0.0",
        "restored_version": VERSION,
        "rolled_back_at": _STAMP,
        "kind": "check",
    }


async def test_a_record_of_a_kind_this_version_does_not_know_is_reported_as_unknown(harness):
    """A later installer's kind: the update still did not go through, for a cause this panel cannot word."""
    _last_run(harness, VERSION)
    _record(harness, kind="a-later-kind")
    harness.app.services.update_outcome_service.note_start()

    outcome = await harness.endpoints.get_update_outcome()

    assert outcome["failure"] == {
        "attempted_version": "99.0.0",
        "restored_version": VERSION,
        "rolled_back_at": _STAMP,
        "kind": "unknown",
    }


async def test_a_record_left_behind_by_an_update_that_went_through_is_no_record(harness):
    """The update to the running version answered and the record was not removed: it no longer stands."""
    _last_run(harness, "0.0.1")
    _record(harness, attempted=VERSION, restored="0.0.1")
    harness.app.services.update_outcome_service.note_start()

    outcome = await harness.endpoints.get_update_outcome()

    assert outcome == {
        "announce_version": VERSION,
        "announce_direction": "updated",
        "toast_owed": True,
        "failure": None,
        "failure_dismissed": False,
    }


async def test_the_record_goes_when_the_installer_removes_it(harness):
    _record(harness)
    assert (await harness.endpoints.get_update_outcome())["failure"] is not None

    (harness.tmp_path / "state" / "update-failure.json").unlink()

    assert (await harness.endpoints.get_update_outcome())["failure"] is None


async def test_a_malformed_record_is_no_record(harness):
    state = harness.tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "update-failure.json").write_text('{"attempted_version": "99.0.0"}', encoding="utf-8")

    assert (await harness.endpoints.get_update_outcome())["failure"] is None


async def test_dismissing_the_card_holds_for_that_record_only(harness):
    _record(harness)

    assert harness.endpoints.dismiss_update_failure(_STAMP) == {"success": True}
    assert (await harness.endpoints.get_update_outcome())["failure_dismissed"] is True
    assert _settings_on_disk(harness)["update_failure_dismissed_at"] == _STAMP

    _record(harness, attempted="99.0.1", at="2026-09-26T08:00:00Z")
    assert (await harness.endpoints.get_update_outcome())["failure_dismissed"] is False


async def test_a_stamp_that_is_not_one_takes_the_canonical_failure_shape(harness):
    result = harness.endpoints.dismiss_update_failure(None)

    assert result == {"success": False, "reason": "invalid_value", "message": "Invalid record"}
    assert "update_failure_dismissed_at" not in _settings_on_disk(harness)
