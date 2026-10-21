"""Tests for domain.header_value — which credential no HTTP header can carry."""

from __future__ import annotations

import pytest

from domain.header_value import no_header_can_carry


class TestNoHeaderCanCarry:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("fake-key\r\nX-Injected: 1", id="cr-lf"),
            pytest.param("fake-key\n", id="line-feed"),
            pytest.param("fake\rkey", id="carriage-return"),
            pytest.param("fake-key-\u2019", id="past-latin-1"),
        ],
    )
    def test_a_line_break_or_a_character_past_latin_1_cannot_be_carried(self, value):
        assert no_header_can_carry(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("fake-key", id="plain"),
            pytest.param("", id="empty"),
            pytest.param("fake key\t", id="space-and-tab"),
            pytest.param("fake-key-\xff", id="last-latin-1"),
        ],
    )
    def test_anything_else_can_be(self, value):
        assert no_header_can_carry(value) is False
