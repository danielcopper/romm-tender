"""Tests for domain.custom_headers — what a configured proxy header may be."""

from __future__ import annotations

import pytest

from domain.custom_headers import (
    RESERVED_NAMES,
    CustomHeader,
    HeaderProblem,
    HeaderRefusal,
    resolve_custom_headers,
    stored_custom_headers,
)


def _set(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value_action": "set", "value": value}


def _keep(name: str) -> dict[str, str]:
    return {"name": name, "value_action": "keep"}


class TestValidPairs:
    def test_it_resolves_a_pair_in_the_order_it_was_given(self):
        resolved = resolve_custom_headers(
            [_set("P-Access-Token", "tok"), _set("P-Access-Token-Id", "id")],
            stored=(),
        )
        assert resolved == (
            CustomHeader(name="P-Access-Token", value="tok"),
            CustomHeader(name="P-Access-Token-Id", value="id"),
        )

    def test_it_accepts_every_rfc_7230_token_character_in_a_name(self):
        name = "!#$%&'*+-.^_`|~0aZ"
        resolved = resolve_custom_headers([_set(name, "v")], stored=())
        assert resolved == (CustomHeader(name=name, value="v"),)

    def test_it_normalises_surrounding_whitespace_in_a_name(self):
        resolved = resolve_custom_headers([_set("  X-Token  ", "v")], stored=())
        assert resolved == (CustomHeader(name="X-Token", value="v"),)

    def test_an_empty_list_resolves_to_no_headers(self):
        assert resolve_custom_headers([], stored=()) == ()


class TestRefusedValues:
    def test_a_crlf_in_a_value_is_refused(self):
        """Header injection: an unchecked value would append a second header to every request."""
        refused = resolve_custom_headers([_set("X-Token", "abc\r\nX-Injected: yes")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.UNSAFE_VALUE, "X-Token")

    @pytest.mark.parametrize("value", ["a\rb", "a\nb", "a\x00b", "a\x1fb", "a\x7fb", "\r\nabc", "\tabc"])
    def test_a_control_character_anywhere_is_refused(self, value):
        refused = resolve_custom_headers([_set("X-Token", value)], stored=())
        assert refused == HeaderRefusal(HeaderProblem.UNSAFE_VALUE, "X-Token")

    def test_an_empty_value_is_refused(self):
        refused = resolve_custom_headers([_set("X-Token", "")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.EMPTY_VALUE, "X-Token")

    @pytest.mark.parametrize("value", [" abc", "abc ", "  abc  "])
    def test_surrounding_whitespace_is_refused_rather_than_trimmed(self, value):
        refused = resolve_custom_headers([_set("X-Token", value)], stored=())
        assert refused == HeaderRefusal(HeaderProblem.PADDED_VALUE, "X-Token")

    def test_a_value_no_http_header_can_carry_is_refused(self):
        """``http.client`` encodes a header value as latin-1 — anything outside it would raise per request."""
        refused = resolve_custom_headers([_set("X-Token", "abc\N{CJK UNIFIED IDEOGRAPH-65E5}def")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.UNENCODABLE_VALUE, "X-Token")

    def test_a_non_string_value_is_a_malformed_entry(self):
        refused = resolve_custom_headers([{"name": "X-Token", "value_action": "set", "value": 7}], stored=())
        assert refused == HeaderRefusal(HeaderProblem.MALFORMED_ENTRY, "X-Token")


class TestRefusedNames:
    @pytest.mark.parametrize("name", ["X Token", "X-Token:", "", "  ", "Ünicode", "a,b"])
    def test_a_name_that_is_not_a_token_is_refused(self, name):
        refused = resolve_custom_headers([_set(name, "v")], stored=())
        assert isinstance(refused, HeaderRefusal)
        assert refused.problem is HeaderProblem.INVALID_NAME

    @pytest.mark.parametrize("reserved", sorted(RESERVED_NAMES - {"authorization"}))
    @pytest.mark.parametrize("spelling", [str.lower, str.upper, str.title])
    def test_every_reserved_name_is_refused_case_insensitively(self, reserved, spelling):
        name = spelling(reserved)
        refused = resolve_custom_headers([_set(name, "v")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.RESERVED_NAME, name)

    @pytest.mark.parametrize("spelling", ["authorization", "AUTHORIZATION", "Authorization"])
    def test_authorization_is_refused_with_a_reason_of_its_own(self, spelling):
        """It is the header a proxy's docs suggest first and the one the RomM bearer occupies."""
        refused = resolve_custom_headers([_set(spelling, "Basic abc")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.AUTHORIZATION_RESERVED, spelling)

    @pytest.mark.parametrize("second", ["X-Token", "x-token", "X-TOKEN"])
    def test_a_duplicate_name_is_refused_case_insensitively(self, second):
        refused = resolve_custom_headers([_set("X-Token", "a"), _set(second, "b")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.DUPLICATE_NAME, second)


class TestWireShape:
    def test_a_non_list_is_refused(self):
        assert resolve_custom_headers({"name": "X", "value": "v"}, stored=()) == HeaderRefusal(HeaderProblem.NOT_A_LIST)

    def test_a_non_dict_entry_is_refused(self):
        assert resolve_custom_headers(["X-Token: v"], stored=()) == HeaderRefusal(HeaderProblem.MALFORMED_ENTRY)

    def test_a_missing_name_is_refused(self):
        assert resolve_custom_headers([{"value_action": "set", "value": "v"}], stored=()) == HeaderRefusal(
            HeaderProblem.MALFORMED_ENTRY
        )

    @pytest.mark.parametrize("action", ["replace", "", None, 1])
    def test_an_unknown_value_action_is_refused(self, action):
        refused = resolve_custom_headers([{"name": "X-Token", "value_action": action, "value": "v"}], stored=())
        assert refused == HeaderRefusal(HeaderProblem.UNKNOWN_VALUE_ACTION, "X-Token")

    def test_keep_reuses_the_stored_value(self):
        stored = (CustomHeader(name="X-Token", value="secret"),)
        assert resolve_custom_headers([_keep("X-Token")], stored=stored) == stored

    def test_keep_matches_the_stored_name_case_insensitively(self):
        stored = (CustomHeader(name="X-Token", value="secret"),)
        resolved = resolve_custom_headers([_keep("x-TOKEN")], stored=stored)
        assert resolved == (CustomHeader(name="x-TOKEN", value="secret"),)

    def test_keep_for_a_name_with_no_stored_value_is_refused(self):
        """Never a silent empty header: the row would otherwise send ``X-Token:``."""
        refused = resolve_custom_headers([_keep("X-Token")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.NO_STORED_VALUE, "X-Token")

    def test_keep_carrying_a_value_is_refused(self):
        refused = resolve_custom_headers(
            [{"name": "X-Token", "value_action": "keep", "value": "new"}],
            stored=(CustomHeader(name="X-Token", value="old"),),
        )
        assert refused == HeaderRefusal(HeaderProblem.UNEXPECTED_VALUE, "X-Token")

    def test_one_bad_row_refuses_the_whole_list(self):
        refused = resolve_custom_headers([_set("X-Good", "v"), _set("Host", "evil")], stored=())
        assert refused == HeaderRefusal(HeaderProblem.RESERVED_NAME, "Host")


class TestValueSecrecy:
    def test_a_header_repr_never_carries_its_value(self):
        assert "secret" not in repr(CustomHeader(name="X-Token", value="secret"))

    def test_a_repr_of_the_whole_resolution_never_carries_a_value(self):
        resolved = resolve_custom_headers([_set("X-Token", "secret")], stored=())
        assert "secret" not in repr(resolved)


class TestStoredReading:
    def test_it_reads_well_formed_pairs_back_in_order(self):
        stored = stored_custom_headers([{"name": "A", "value": "1"}, {"name": "B", "value": "2"}])
        assert stored == (CustomHeader(name="A", value="1"), CustomHeader(name="B", value="2"))

    @pytest.mark.parametrize(
        "entry",
        [
            {"name": "Authorization", "value": "Basic abc"},
            {"name": "Host", "value": "evil.example"},
            {"name": "X Token", "value": "v"},
            {"name": "X-Token", "value": "a\r\nX-Injected: yes"},
            {"name": "X-Token", "value": ""},
            {"name": "X-Token"},
            {"value": "v"},
            "X-Token: v",
        ],
    )
    def test_an_entry_the_validation_would_refuse_is_never_sent(self, entry):
        """A hand-edited settings.json must not put a header on the wire the plugin cannot vouch for."""
        assert stored_custom_headers([entry]) == ()

    def test_a_later_duplicate_is_dropped(self):
        stored = stored_custom_headers([{"name": "X-Token", "value": "first"}, {"name": "x-token", "value": "second"}])
        assert stored == (CustomHeader(name="X-Token", value="first"),)

    @pytest.mark.parametrize("stored", [None, {}, "headers", 3])
    def test_a_non_list_reads_as_no_headers(self, stored):
        assert stored_custom_headers(stored) == ()
