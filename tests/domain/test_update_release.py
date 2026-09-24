"""Tests for domain.update_release — the environment's answer, release naming, and the stored check."""

from __future__ import annotations

import json

import pytest

from domain.update_release import (
    DEFAULT_RELEASE_API,
    LatestRelease,
    ReleaseTarball,
    UpdateCheck,
    UpdateSource,
    decode_update_check,
    encode_update_check,
    resolve_update_source,
    sha256_hex,
    tarball_name,
    version_from_tag,
)

_CODE = "/home/deck/.local/lib/romm-tender"


class TestResolveUpdateSource:
    def test_defaults_to_github_s_latest_release_route(self):
        assert resolve_update_source({}, _CODE).release_api == DEFAULT_RELEASE_API

    def test_the_default_is_the_installer_s(self):
        """One override has to point both at the same server, so both start from one address."""
        assert DEFAULT_RELEASE_API == "https://api.github.com/repos/danielcopper/romm-tender/releases/latest"

    def test_reads_the_installer_s_test_seam(self):
        source = resolve_update_source({"TENDER_RELEASE_API": " http://127.0.0.1:8765/latest "}, _CODE)
        assert source.release_api == "http://127.0.0.1:8765/latest"

    def test_an_empty_override_counts_as_unset(self):
        assert resolve_update_source({"TENDER_RELEASE_API": "  "}, _CODE).release_api == DEFAULT_RELEASE_API

    def test_the_installed_service_is_the_installed_program(self):
        assert resolve_update_source({"TENDER_CODE_DIR": _CODE}, _CODE) == UpdateSource(
            release_api=DEFAULT_RELEASE_API, installed_program=True
        )

    def test_a_trailing_slash_or_a_doubled_one_is_the_same_directory(self):
        assert resolve_update_source({"TENDER_CODE_DIR": _CODE + "/"}, _CODE).installed_program is True
        assert resolve_update_source({"TENDER_CODE_DIR": "/home/deck//.local/lib/romm-tender"}, _CODE).installed_program

    def test_a_start_with_no_code_dir_is_a_checkout(self):
        assert resolve_update_source({}, "/home/deck/Repos/tender").installed_program is False

    def test_an_empty_code_dir_is_a_checkout(self):
        assert resolve_update_source({"TENDER_CODE_DIR": " "}, "/home/deck/Repos/tender").installed_program is False

    def test_a_code_dir_naming_somewhere_else_is_not_this_process(self):
        """A shell that exported the variable for an installer test must not make a checkout the install."""
        environ = {"TENDER_CODE_DIR": _CODE}
        assert resolve_update_source(environ, "/home/deck/Repos/tender").installed_program is False

    def test_a_relative_code_dir_errs_towards_offering_no_install(self):
        environ = {"TENDER_CODE_DIR": ".local/lib/romm-tender"}
        assert resolve_update_source(environ, _CODE).installed_program is False


class TestVersionFromTag:
    def test_strips_the_release_tag_prefix(self):
        assert version_from_tag("tender-v0.34.0") == "0.34.0"

    def test_tolerates_surrounding_whitespace(self):
        assert version_from_tag("  tender-v0.34.0\n") == "0.34.0"

    @pytest.mark.parametrize(
        "unusable", ["", "   ", "tender-v", "0.34.0", "v0.34.0", "other-v1.0.0", None, 3, ["tender-v0.34.0"]]
    )
    def test_names_no_tender_release(self, unusable):
        assert version_from_tag(unusable) is None


class TestTarballName:
    def test_is_the_packager_s_spelling(self):
        assert tarball_name("0.34.0") == "romm-tender-0.34.0.tar.gz"


class TestSha256Hex:
    def test_strips_the_algorithm_prefix(self):
        assert sha256_hex("sha256:ab34cd") == "ab34cd"

    @pytest.mark.parametrize("unusable", ["sha512:ab34cd", "ab34cd", "sha256:", "", None, 42, {"sha256": "ab"}])
    def test_is_not_a_digest_we_can_vouch_for(self, unusable):
        assert sha256_hex(unusable) is None


_RELEASE = LatestRelease(
    version="0.34.0",
    tarball=ReleaseTarball(url="https://x.test/tender-v0.34.0/romm-tender-0.34.0.tar.gz", digest="ab34cd"),
)


class TestStoredCheck:
    def test_round_trips_an_available_release(self):
        check = UpdateCheck(checked_at=1757500000.0, release=_RELEASE)
        assert decode_update_check(encode_update_check(check)) == check

    def test_round_trips_a_release_whose_digest_is_unknown(self):
        release = LatestRelease(version="0.34.0", tarball=ReleaseTarball(url="https://x.test/t.tar.gz", digest=None))
        check = UpdateCheck(checked_at=1757500000.0, release=release)
        assert decode_update_check(encode_update_check(check)) == check

    def test_round_trips_a_check_that_never_found_one(self):
        check = UpdateCheck(checked_at=1757500000.0, release=None)
        assert decode_update_check(encode_update_check(check)) == check

    def test_an_integer_timestamp_is_a_timestamp(self):
        decoded = decode_update_check(json.dumps({"checked_at": 1757500000}))
        assert decoded == UpdateCheck(checked_at=1757500000.0, release=None)

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "{not json",
            "[]",
            '"0.34.0"',
            "{}",
            json.dumps({"version": "0.34.0"}),
            json.dumps({"checked_at": "yesterday"}),
            json.dumps({"checked_at": True}),
        ],
    )
    def test_no_usable_stamp_reads_as_never_checked(self, raw):
        assert decode_update_check(raw) is None

    @pytest.mark.parametrize(
        "stored",
        [
            {"version": "0.34.0"},
            {"version": "0.34.0", "tarball_url": ""},
            {"version": "", "tarball_url": "https://x.test/t.tar.gz"},
            {"version": 34, "tarball_url": "https://x.test/t.tar.gz"},
            {"version": "0.34.0", "tarball_url": ["https://x.test/t.tar.gz"]},
        ],
    )
    def test_a_release_with_nothing_to_download_is_dropped_but_the_timestamp_stands(self, stored):
        decoded = decode_update_check(json.dumps({"checked_at": 1757500000.0, **stored}))
        assert decoded == UpdateCheck(checked_at=1757500000.0, release=None)

    @pytest.mark.parametrize("digest", ["", 7, None])
    def test_an_unusable_digest_reads_as_unknown(self, digest):
        raw = json.dumps({"checked_at": 1.0, "version": "0.34.0", "tarball_url": "https://x.test/t", "digest": digest})
        decoded = decode_update_check(raw)
        assert decoded is not None
        assert decoded.release == LatestRelease(
            version="0.34.0", tarball=ReleaseTarball(url="https://x.test/t", digest=None)
        )
