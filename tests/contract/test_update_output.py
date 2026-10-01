"""Contract tests for ``get_update_output`` over the real wiring.

Driven frontend-shaped per ``frontend/src/api/backend.ts``: ``getUpdateOutput``
takes the record's ``rolled_back_at`` string, or ``null`` for this process's
latest attempt. The installer's record is read by the real adapter from the
state directory the run was told about; the journal is the harness's
:class:`FakeJournal`, holding synthetic lines, so no test reads this machine's.
"""

from __future__ import annotations

from domain.identity import VERSION
from domain.update_install import INSTALLER_UNIT
from domain.update_output import SERVICE_UNIT, JournalEntry, utc_stamp_seconds

_STAMP = "2026-09-25T10:15:00Z"
_AT = utc_stamp_seconds(_STAMP) or 0.0


def _record(harness, kind: str) -> None:
    state = harness.tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "update-failure.json").write_text(
        f'{{"attempted_version": "99.0.0", "restored_version": "{VERSION}", "rolled_back_at": "{_STAMP}", '
        f'"kind": "{kind}"}}\n',
        encoding="utf-8",
    )


async def test_after_a_rollback_both_runs_are_answered_with_the_token_hidden(harness):
    _record(harness, "rollback")
    harness.journal.entries = [
        (INSTALLER_UNIT, JournalEntry(_AT - 70, "inst", "[ok] Checking     python 3.13")),
        (INSTALLER_UNIT, JournalEntry(_AT - 60, "inst", "[..] Service      waiting for 99.0.0 to answer")),
        (
            SERVICE_UNIT,
            JournalEntry(_AT - 50, "new", "host: load the panel from http://127.0.0.1:1/index.js?token=s3cr3t"),
        ),
        (INSTALLER_UNIT, JournalEntry(_AT + 2, "inst", "[!!] Service      update to 99.0.0 failed")),
    ]

    answer = await harness.endpoints.get_update_output(_STAMP)

    assert answer["success"] is True
    assert answer["installer"]["lines"] == [
        "[ok] Checking     python 3.13",
        "[!!] Service      update to 99.0.0 failed",
    ]
    assert answer["new_version"] == {
        "lines": ["host: load the panel from http://127.0.0.1:1/index.js?token=[hidden]"],
        "earlier": 0,
    }


async def test_a_refusal_run_by_hand_is_answered_as_output_in_a_terminal(harness):
    _record(harness, "check")
    harness.journal.entries = [("plasma", JournalEntry(_AT - 7200, "x", "the journal reaches back"))]

    answer = await harness.endpoints.get_update_output(_STAMP)

    assert (answer["success"], answer["missing"], answer["installer"]) == (True, "terminal", None)


async def test_no_failure_standing_is_answered_in_the_canonical_shape(harness):
    answer = await harness.endpoints.get_update_output(None)

    assert answer == {"success": False, "reason": "not_found", "message": "No failed update to show the output of"}
