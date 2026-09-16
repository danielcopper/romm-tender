"""The three checks — that they hold, and that they hold in that order.

The order is tested by constructing requests that fail more than one check at
once and pinning which refusal comes back. A test that only fails one check at a
time would pass under any order at all.
"""

from __future__ import annotations

import pytest

from host.access import STEAM_UI_ORIGIN, AccessPolicy, check_access, new_token
from lib.http_messages import parse_request_head
from tests.host.conftest import SERVER_IDENTITY
from tests.host.ws_client import WsTestClient, http_get

PORT = 27737
TOKEN = "the-admission-token"
POLICY = AccessPolicy(port=PORT, token=TOKEN)


def head(*, host: str = f"127.0.0.1:{PORT}", origin: str | None = STEAM_UI_ORIGIN, token: str | None = TOKEN):
    """Build a request head with the three inputs the checks read."""
    target = "/index.js" if token is None else f"/index.js?token={token}"
    lines = [f"GET {target} HTTP/1.1", f"Host: {host}"]
    if origin is not None:
        lines.append(f"Origin: {origin}")
    return parse_request_head("\r\n".join(lines).encode("latin-1"))


class TestTheHostCheck:
    @pytest.mark.parametrize("spelling", [f"127.0.0.1:{PORT}", f"localhost:{PORT}"])
    def test_both_spellings_of_this_server_are_accepted(self, spelling):
        assert check_access(head(host=spelling), POLICY).allowed

    @pytest.mark.parametrize(
        "host",
        ["evil.example.com", f"evil.example.com:{PORT}", "127.0.0.1:1", f"127.0.0.2:{PORT}", "", "[::1]:27737"],
    )
    def test_any_other_authority_is_refused(self, host):
        """DNS rebinding makes the request same-origin; the Host field is what gives it away."""
        verdict = check_access(head(host=host), POLICY)

        assert verdict.refused
        assert verdict.status == 421

    def test_the_port_checked_is_the_one_actually_bound(self):
        """A policy built from the port we WANTED would refuse everything after a fallback."""
        fallen_back = AccessPolicy(port=27750, token=TOKEN)

        assert check_access(head(host="127.0.0.1:27750"), fallen_back).allowed
        assert check_access(head(host=f"127.0.0.1:{PORT}"), fallen_back).refused


class TestTheOriginCheck:
    @pytest.mark.parametrize(
        "origin",
        [STEAM_UI_ORIGIN, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"],
    )
    def test_the_allowed_origins_pass(self, origin):
        assert check_access(head(origin=origin), POLICY).allowed

    @pytest.mark.parametrize(
        "origin",
        [
            "https://evil.example.com",
            "http://127.0.0.1:1",
            "https://127.0.0.1:27737",
            "http://[::1]:27737",
            "null",
        ],
    )
    def test_a_foreign_origin_is_refused(self, origin):
        verdict = check_access(head(origin=origin), POLICY)

        assert verdict.refused
        assert verdict.status == 403

    def test_the_refused_origin_is_named_in_the_log_line(self):
        """So the first run answers what Steam's UI actually sends."""
        foreign = "https://evil.example.com"

        verdict = check_access(head(origin=foreign), POLICY)

        assert repr(foreign) in verdict.log_line

    def test_an_absent_origin_passes(self):
        """A browser never omits it; whoever did is not one, and still needs the token."""
        assert check_access(head(origin=None), POLICY).allowed

    def test_an_absent_origin_is_echoed_nowhere(self):
        assert check_access(head(origin=None), POLICY).echo_origin == ""

    def test_an_allowed_origin_is_the_one_echoed(self):
        assert check_access(head(origin=STEAM_UI_ORIGIN), POLICY).echo_origin == STEAM_UI_ORIGIN


class TestTheTokenCheck:
    def test_the_right_token_passes(self):
        assert check_access(head(token=TOKEN), POLICY).allowed

    @pytest.mark.parametrize("offered", [None, "", "wrong", TOKEN[:-1], TOKEN + "x", TOKEN.upper()])
    def test_anything_else_is_refused(self, offered):
        verdict = check_access(head(token=offered), POLICY)

        assert verdict.refused
        assert verdict.status == 401

    def test_a_missing_token_and_a_wrong_one_read_differently_in_the_log(self):
        assert "no token" in check_access(head(token=None), POLICY).log_line
        assert "wrong token" in check_access(head(token="wrong"), POLICY).log_line


class TestTheOrder:
    def test_a_bad_host_is_reported_as_a_bad_host_even_with_no_token(self):
        """Otherwise the log says 'wrong token' about a rebinding attempt."""
        verdict = check_access(head(host="evil.example.com", token=None), POLICY)

        assert verdict.status == 421

    def test_a_foreign_origin_outranks_a_bad_token(self):
        verdict = check_access(head(origin="https://evil.example.com", token="wrong"), POLICY)

        assert verdict.status == 403

    def test_a_bad_host_outranks_a_foreign_origin(self):
        verdict = check_access(head(host="evil.example.com", origin="https://evil.example.com"), POLICY)

        assert verdict.status == 421


class TestTheToken:
    def test_two_tokens_differ(self):
        first, second = new_token(), new_token()

        assert first != second

    def test_a_token_is_long_enough_not_to_be_guessed(self):
        assert len(new_token()) >= 32


class TestTheChecksOnTheWire:
    """Both entry points — the file route and the upgrade — run the same three."""

    @pytest.mark.parametrize("path", ["/index.js", "/ws"])
    async def test_no_token_is_refused_with_401(self, running_host, path):
        status, headers, _ = await http_get(running_host.port, path, token=None)

        assert status == 401
        assert headers["server"] == SERVER_IDENTITY

    @pytest.mark.parametrize("path", ["/index.js", "/ws"])
    async def test_a_wrong_token_is_refused(self, running_host, path):
        status, _, _ = await http_get(running_host.port, path, token="not-the-token")

        assert status == 401

    @pytest.mark.parametrize("path", ["/index.js", "/ws"])
    async def test_a_foreign_host_is_refused(self, running_host, path):
        status, _, _ = await http_get(running_host.port, path, token=running_host.token, host="evil.example.com")

        assert status == 421

    @pytest.mark.parametrize("path", ["/index.js", "/ws"])
    async def test_a_foreign_origin_is_refused(self, running_host, path):
        status, _, _ = await http_get(
            running_host.port, path, token=running_host.token, origin="https://evil.example.com"
        )

        assert status == 403

    async def test_a_refused_request_gets_no_bundle(self, running_host):
        _, _, body = await http_get(running_host.port, "/index.js", token=None)

        assert body == b""

    async def test_an_upgrade_without_a_token_never_becomes_a_connection(self, running_host):
        with pytest.raises(AssertionError, match="refused with 401"):
            await WsTestClient.connect(running_host.port, "not-the-token")

        assert not running_host.server.connected

    async def test_the_token_is_not_served_by_any_route(self, running_host):
        """There is no reader for it: the bundle gets it from the address it loaded from."""
        for path in ("/token", "/index.js", "/ws", "/.token", "/config.json"):
            _, _, body = await http_get(running_host.port, path, token=running_host.token)
            assert running_host.token.encode() not in body
