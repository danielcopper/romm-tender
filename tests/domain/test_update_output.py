"""Tests for domain.update_output — reading the journal's entries, picking a failure's run, and what of it is shown.

Every journal line here is synthetic, written in the shape ``journalctl --user
--output=json`` prints.
"""

from __future__ import annotations

import json

import pytest

from domain.update_output import (
    MAX_LINE_CHARS,
    MAX_LINES,
    JournalEntry,
    OutputSection,
    decode_journal_entry,
    first_run_from,
    hide_token,
    journal_runs,
    output_section,
    run_around,
    runs_other_than,
    utc_stamp_seconds,
)

# The start-up line every start logs to its journal, in its real shape: the
# log format, the host's message and the address with a token_urlsafe(32).
_ADDRESS_LINE = (
    "[2026-09-30 21:10:01,032][INFO]: host: load the panel from "
    "http://127.0.0.1:27737/index.js?token=Zq3_x-9Ab0cDeFgHiJkLmNoPqRsTuVwXyZ012345678"  # gitleaks:allow
)


def _entry(at: float, invocation: str | None, message: str = "line") -> JournalEntry:
    return JournalEntry(at=at, invocation=invocation, message=message)


class TestDecodeJournalEntry:
    def test_reads_what_the_unit_s_process_printed(self):
        raw = json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1790794693534793",
                "_SYSTEMD_INVOCATION_ID": "aa11",
                "MESSAGE": "[ok] Checking     python 3.13",
                "__CURSOR": "s=0;i=1",
            }
        )

        assert decode_journal_entry(raw) == JournalEntry(
            at=1790794693.534793, invocation="aa11", message="[ok] Checking     python 3.13"
        )

    def test_reads_what_the_user_manager_said_about_the_unit_under_its_own_key(self):
        raw = json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1790794693000000",
                "USER_INVOCATION_ID": "bb22",
                "MESSAGE": "romm-tender-update.service: Failed with result 'exit-code'.",
            }
        )

        entry = decode_journal_entry(raw)

        assert entry is not None
        assert entry.invocation == "bb22"

    def test_a_line_of_no_run_has_no_invocation(self):
        raw = json.dumps({"__REALTIME_TIMESTAMP": "1000000", "MESSAGE": "Failed to open /run/user/1000/..."})

        assert decode_journal_entry(raw) == JournalEntry(
            at=1.0, invocation=None, message="Failed to open /run/user/1000/..."
        )

    def test_a_message_that_is_not_utf8_arrives_as_bytes_and_is_decoded_with_the_bad_ones_replaced(self):
        raw = json.dumps({"__REALTIME_TIMESTAMP": "1000000", "MESSAGE": [104, 105, 255]})

        entry = decode_journal_entry(raw)

        assert entry is not None
        assert entry.message == "hi�"

    @pytest.mark.parametrize("message", [None, 7, [300], ["a"]])
    def test_a_message_of_no_readable_shape_is_empty(self, message):
        entry = decode_journal_entry(json.dumps({"__REALTIME_TIMESTAMP": "1000000", "MESSAGE": message}))

        assert entry is not None
        assert entry.message == ""

    @pytest.mark.parametrize(
        "raw", ["", "{not json", "[]", "null", json.dumps({"MESSAGE": "x"}), json.dumps({"__REALTIME_TIMESTAMP": "x"})]
    )
    def test_a_line_with_no_timestamp_is_none(self, raw):
        assert decode_journal_entry(raw) is None


class TestUtcStampSeconds:
    def test_reads_the_installer_s_stamp_as_utc(self):
        assert utc_stamp_seconds("1970-01-01T00:01:40Z") == 100.0

    @pytest.mark.parametrize("stamp", ["", "2026-09-30 20:58:13", "2026-09-30T20:58:13+02:00", "yesterday"])
    def test_anything_else_is_none(self, stamp):
        assert utc_stamp_seconds(stamp) is None


class TestJournalRuns:
    def test_groups_the_lines_by_run_in_the_order_each_run_first_appears_and_drops_lines_of_none(self):
        a1, b1, stray, a2 = _entry(1, "a"), _entry(2, "b"), _entry(3, None), _entry(4, "a")

        assert journal_runs([a1, b1, stray, a2]) == [(a1, a2), (b1,)]


class TestRunAround:
    def test_picks_the_run_going_on_at_the_record_s_stamp_and_never_merely_the_latest(self):
        older = [_entry(100, "old"), _entry(160, "old")]
        failed = [_entry(1000, "mine"), _entry(1060, "mine"), _entry(1065, "mine")]
        later = [_entry(5000, "later"), _entry(5010, "later")]

        assert run_around([*older, *failed, *later], 1060) == tuple(failed)

    def test_the_stamp_is_cut_to_the_second_so_a_run_that_began_within_that_second_is_the_one(self):
        run = [_entry(1060.4, "mine"), _entry(1061, "mine")]

        assert run_around(run, 1060) == tuple(run)

    def test_a_run_that_ended_before_the_stamp_is_not_it(self):
        assert run_around([_entry(100, "old"), _entry(159.9, "old")], 160) is None

    def test_a_run_that_began_after_the_stamp_s_second_is_not_it(self):
        assert run_around([_entry(161, "next"), _entry(170, "next")], 160) is None


class TestFirstRunFrom:
    def test_picks_the_first_run_that_began_at_or_after_the_start(self):
        going = [_entry(90, "going"), _entry(101, "going")]
        mine = [_entry(100, "mine"), _entry(120, "mine")]
        next_one = [_entry(200, "next")]

        assert first_run_from([going[0], mine[0], going[1], mine[1], *next_one], 100) == tuple(mine)

    def test_nothing_that_began_since_is_none(self):
        assert first_run_from([_entry(90, "old"), _entry(101, "old")], 100) is None


class TestRunsOtherThan:
    def test_leaves_out_the_named_run_and_the_lines_of_none(self):
        kept = [_entry(2, "new"), _entry(4, "new-again")]

        assert runs_other_than([_entry(1, "old"), kept[0], _entry(3, None), kept[1]], "old") == tuple(kept)

    def test_with_no_run_named_it_keeps_every_run_s_lines(self):
        kept = [_entry(1, "a"), _entry(2, "b")]

        assert runs_other_than(kept, None) == tuple(kept)


class TestHideToken:
    def test_the_start_up_address_line_keeps_everything_but_the_token(self):
        assert hide_token(_ADDRESS_LINE) == (
            "[2026-09-30 21:10:01,032][INFO]: host: load the panel from http://127.0.0.1:27737/index.js?token=[hidden]"
        )

    def test_a_token_between_other_parameters_is_hidden_and_they_stay(self):
        assert hide_token("GET /index.js?a=1&token=abc-_9&b=2 200") == "GET /index.js?a=1&token=[hidden]&b=2 200"

    def test_every_token_on_a_line_is_hidden(self):
        assert hide_token("x?token=one y?token=two") == "x?token=[hidden] y?token=[hidden]"

    def test_a_word_ending_in_token_is_not_a_parameter(self):
        assert hide_token("csrftoken=abc") == "csrftoken=abc"

    def test_the_parameter_is_the_one_the_host_reads(self):
        from host.access import TOKEN_PARAM

        assert hide_token(f"?{TOKEN_PARAM}=secret") == f"?{TOKEN_PARAM}=[hidden]"


class TestOutputSection:
    def test_shows_every_line_of_a_short_run_with_the_token_hidden(self):
        entries = [_entry(1, "a", "[..] Installing   trying 1.0.52"), _entry(2, "a", _ADDRESS_LINE)]

        section = output_section(entries)

        assert section.lines == ("[..] Installing   trying 1.0.52", hide_token(_ADDRESS_LINE))
        assert section.earlier == 0
        assert "Zq3_x" not in "".join(section.lines)

    def test_a_message_of_several_lines_is_shown_as_several(self):
        section = output_section([_entry(1, "a", 'Traceback (most recent call last):\n  File "main.py"')])

        assert section.lines == ("Traceback (most recent call last):", '  File "main.py"')

    def test_keeps_the_last_lines_of_a_long_run_and_counts_the_rest(self):
        entries = [_entry(i, "a", f"line {i}") for i in range(MAX_LINES + 25)]

        section = output_section(entries)

        assert len(section.lines) == MAX_LINES
        assert section.lines[0] == "line 25"
        assert section.lines[-1] == f"line {MAX_LINES + 24}"
        assert section.earlier == 25

    def test_cuts_a_long_line_and_says_it_was_cut(self):
        section = output_section([_entry(1, "a", "x" * (MAX_LINE_CHARS + 1))])

        assert section.lines == ("x" * MAX_LINE_CHARS + "…",)

    def test_a_line_exactly_at_the_limit_is_whole(self):
        assert output_section([_entry(1, "a", "x" * MAX_LINE_CHARS)]).lines == ("x" * MAX_LINE_CHARS,)

    def test_an_empty_message_is_an_empty_line(self):
        assert output_section([_entry(1, "a", "")]).lines == ("",)

    def test_is_answered_as_lines_and_the_count_left_out(self):
        assert OutputSection(lines=("a",), earlier=3).to_wire() == {"lines": ["a"], "earlier": 3}
