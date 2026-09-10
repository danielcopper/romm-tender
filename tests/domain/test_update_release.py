"""Tests for the pure release-naming and last-check helpers."""

from __future__ import annotations

import json

import pytest

from domain.update_release import (
    DOWNLOAD_URL,
    UpdateCheck,
    decode_update_check,
    encode_update_check,
    sha256_hex,
    version_from_tag,
)


class TestVersionFromTag:
    def test_strips_the_release_tag_prefix(self):
        assert version_from_tag("tender-v0.33.0") == "0.33.0"

    def test_accepts_a_bare_version(self):
        """A tag shape that loses the prefix must not silence every comparison."""
        assert version_from_tag("0.33.0") == "0.33.0"

    def test_tolerates_surrounding_whitespace(self):
        assert version_from_tag("  tender-v0.33.0\n") == "0.33.0"

    @pytest.mark.parametrize("unusable", ["", "   ", "tender-v", None, 3, 1.2, ["0.33.0"], {"v": "0.33.0"}])
    def test_names_no_version(self, unusable):
        assert version_from_tag(unusable) is None


class TestSha256Hex:
    def test_strips_the_algorithm_prefix(self):
        assert sha256_hex("sha256:ab33cd") == "ab33cd"

    def test_another_algorithm_is_not_a_sha256(self):
        """Passing an sha512 on as sha256 would fail Decky's check with nothing saying why."""
        assert sha256_hex("sha512:ab33cd") is None

    @pytest.mark.parametrize("unusable", ["ab33cd", "sha256:", "", None, 42, {"sha256": "ab33cd"}])
    def test_is_not_a_digest_we_can_vouch_for(self, unusable):
        assert sha256_hex(unusable) is None


class TestUpdateCheckStamp:
    def test_round_trips_through_the_stored_text(self):
        check = UpdateCheck(checked_at=1757500000.0, version="0.33.0", digest="ab33cd")
        assert decode_update_check(encode_update_check(check)) == check

    def test_round_trips_a_check_that_never_reached_an_answer(self):
        check = UpdateCheck(checked_at=1757500000.0, version=None, digest=None)
        assert decode_update_check(encode_update_check(check)) == check

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "{not json",
            "[]",
            '"0.33.0"',
            "{}",
            json.dumps({"version": "0.33.0"}),
            json.dumps({"checked_at": "yesterday", "version": "0.33.0"}),
            json.dumps({"checked_at": True, "version": "0.33.0"}),
        ],
    )
    def test_no_usable_stamp_reads_as_never_checked(self, raw):
        assert decode_update_check(raw) is None

    def test_an_integer_timestamp_is_a_timestamp(self):
        decoded = decode_update_check(json.dumps({"checked_at": 1757500000, "version": "0.33.0", "digest": None}))
        assert decoded == UpdateCheck(checked_at=1757500000.0, version="0.33.0", digest=None)

    @pytest.mark.parametrize("unusable", [None, "", 7, ["0.33.0"]])
    def test_an_unusable_version_is_dropped_but_the_timestamp_stands(self, unusable):
        """When we last asked is still true even where what we saw is not readable."""
        decoded = decode_update_check(json.dumps({"checked_at": 1757500000.0, "version": unusable}))
        assert decoded == UpdateCheck(checked_at=1757500000.0, version=None, digest=None)


class TestDownloadUrl:
    def test_names_the_fixed_latest_asset(self):
        """The address is a constant, not the browser_download_url of a parsed answer."""
        assert DOWNLOAD_URL == "https://github.com/danielcopper/romm-tender/releases/latest/download/Tender.zip"
