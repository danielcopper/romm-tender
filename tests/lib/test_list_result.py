"""Tests for ``ErrorCode``, the coarse failure categories."""

from __future__ import annotations

from lib.list_result import ErrorCode


class TestErrorCode:
    """ErrorCode is a str-enum with the documented members."""

    def test_is_str_subclass(self):
        # str-enum members compare equal to their string value, which lets
        # callers serialize/transport the code without an explicit .value.
        assert isinstance(ErrorCode.SERVER_UNREACHABLE, str)
        assert ErrorCode.SERVER_UNREACHABLE == "server_unreachable"

    def test_required_members_present(self):
        assert ErrorCode.SERVER_UNREACHABLE.value == "server_unreachable"
        assert ErrorCode.AUTH_FAILED.value == "auth_failed"
        assert ErrorCode.UNKNOWN.value == "unknown"
