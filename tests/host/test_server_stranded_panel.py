"""What the upgrade answers a panel another backend process loaded.

Such a panel carries that process's token, which this one refuses. A refused
handshake reaches a page exactly like no server at all, so the upgrade is
completed and closed at once with a code of this program's own instead — and
nothing is attached to it. Until the backend has looked at Steam's context it
does not know which code is true, and such an upgrade gets the plain 401. These
run against the real server on a real port, and read the close the way a browser
would: off the wire.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import struct
from typing import TYPE_CHECKING

import pytest

import host.server as server_module
from host.access import STEAM_UI_ORIGIN
from host.protocol import CLOSE_STRANDED_PANEL_RELOADS, CLOSE_STRANDED_PANEL_RESTART_STEAM, ReloadOutlook
from lib.websocket_frames import OPCODE_CLOSE, OPCODE_TEXT, build_frame, close_frame
from tests.host.conftest import SERVER_IDENTITY
from tests.host.ws_client import WsTestClient, http_get

if TYPE_CHECKING:
    from tests.host.conftest import RunningHost

LOGGER_NAME = "test_host"


async def knock(port: int, *, session: str = "panel-a", token: str = "an-earlier-backends-token") -> tuple[int, str]:
    """Upgrade with another process's token; answer the close's code and reason."""
    client = await WsTestClient.connect(port, token, session=session)
    try:
        opcode, payload = await client.recv_frame()
    finally:
        await client.close()
    assert opcode == OPCODE_CLOSE
    return struct.unpack("!H", payload[:2])[0], payload[2:].decode("utf-8")


def answering(outlook: ReloadOutlook):
    async def reload_outlook() -> ReloadOutlook:
        return outlook

    return reload_outlook


@pytest.fixture
def looked_host(running_host) -> RunningHost:
    """A host whose backend has read Steam's context and found no reload to come."""
    running_host.server.answer_stranded_panels_from(answering(ReloadOutlook.NO_RELOAD))
    return running_host


def refusals(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if "refused" in record.message]


class TestBeforeTheBackendHasLookedAtSteam:
    async def test_with_nothing_attached_the_upgrade_is_refused_with_401(self, running_host):
        with pytest.raises(AssertionError, match="refused with 401"):
            await WsTestClient.connect(running_host.port, "an-earlier-backends-token")

        assert not running_host.server.connected

    async def test_while_the_injector_has_not_read_the_context_it_is_refused_with_401(self, running_host):
        running_host.server.answer_stranded_panels_from(answering(ReloadOutlook.NOT_YET_LOOKED))

        with pytest.raises(AssertionError, match="refused with 401"):
            await WsTestClient.connect(running_host.port, "an-earlier-backends-token")

    async def test_each_such_refusal_is_logged_like_any_other(self, running_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            for _ in range(2):
                with pytest.raises(AssertionError, match="refused with 401"):
                    await WsTestClient.connect(running_host.port, "an-earlier-backends-token")

        lines = [record.message for record in refusals(caplog)]
        assert len(lines) == 2
        assert all("wrong token" in line and "panel another backend" not in line for line in lines)


class TestTheAnswer:
    async def test_no_reload_to_come_closes_at_once_with_restart_steam(self, looked_host):
        code, reason = await knock(looked_host.port)

        assert code == CLOSE_STRANDED_PANEL_RESTART_STEAM
        assert "restart Steam" in reason

    async def test_a_reload_to_come_is_said_with_its_own_code(self, running_host):
        running_host.server.answer_stranded_panels_from(answering(ReloadOutlook.RELOAD_TO_COME))

        code, reason = await knock(running_host.port)

        assert code == CLOSE_STRANDED_PANEL_RELOADS
        assert "once no game is running" in reason

    async def test_the_answer_is_asked_again_on_every_knock(self, running_host):
        answers = iter([ReloadOutlook.RELOAD_TO_COME, ReloadOutlook.NO_RELOAD])

        async def reload_outlook() -> ReloadOutlook:
            return next(answers)

        running_host.server.answer_stranded_panels_from(reload_outlook)

        assert (await knock(running_host.port))[0] == CLOSE_STRANDED_PANEL_RELOADS
        assert (await knock(running_host.port))[0] == CLOSE_STRANDED_PANEL_RESTART_STEAM

    async def test_the_handshake_names_this_program_like_every_refusal(self, looked_host):
        client = await WsTestClient.connect(looked_host.port, "an-earlier-backends-token")
        await client.close()

        assert client.handshake["server"] == SERVER_IDENTITY

    async def test_a_panel_that_sends_beside_the_upgrade_is_closed_without_a_reset(self, looked_host):
        """A panel flushes its queued calls once the upgrade completes; the socket must not be reset over them.

        The frame goes out with the request head and is larger than the
        server's read buffer, so part of it is still unread in the kernel when
        the server is done answering — and closing a socket with unread data
        makes the kernel answer with a reset instead of an orderly end. Linux
        still hands this client the bytes that came before the reset, so what
        is asserted is the orderly end: a reset that a browser's stack may let
        overtake the close frame.
        """
        reader, writer = await asyncio.open_connection("127.0.0.1", looked_host.port)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            head = (
                f"GET /ws?token=an-earlier-backends-token&session=panel-a HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{looked_host.port}\r\nOrigin: {STEAM_UI_ORIGIN}\r\n"
                f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode("latin-1")
            writer.write(head + build_frame(OPCODE_TEXT, b"x" * 1_000_000, mask=os.urandom(4)))
            await writer.drain()
            answered = await reader.readuntil(b"\r\n\r\n")
            closing = await reader.readexactly(4)
            rest = await asyncio.wait_for(reader.read(), 5)
        finally:
            writer.close()

        assert answered.startswith(b"HTTP/1.1 101")
        assert closing[0] & 0x0F == OPCODE_CLOSE
        assert struct.unpack("!H", closing[2:4])[0] == CLOSE_STRANDED_PANEL_RESTART_STEAM
        assert rest == b"stranded panel: restart Steam"

    async def test_the_socket_is_shut_once_the_panel_answers_the_close(self, looked_host, monkeypatch):
        monkeypatch.setattr(server_module, "STRANDED_DRAIN_SECONDS", 30.0)
        client = await WsTestClient.connect(looked_host.port, "an-earlier-backends-token")
        try:
            opcode, _ = await client.recv_frame()
            await client.send_raw(build_frame(OPCODE_CLOSE, close_frame(1000)[2:], mask=os.urandom(4)))
            assert await client.read_until_shut() == b""
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE

    async def test_a_panel_that_never_answers_the_close_is_shut_after_the_bound(self, looked_host, monkeypatch):
        monkeypatch.setattr(server_module, "STRANDED_DRAIN_SECONDS", 0.2)
        client = await WsTestClient.connect(looked_host.port, "an-earlier-backends-token")
        try:
            opcode, _ = await client.recv_frame()
            assert await client.read_until_shut() == b""
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE


class TestNothingIsAuthorised:
    async def test_no_connection_is_registered_and_no_call_is_carried(self, looked_host):
        client = await WsTestClient.connect(looked_host.port, "an-earlier-backends-token", session="panel-a")
        try:
            await client.send_json({"type": "call", "id": 1, "method": "echo", "args": ["hello"]})
            opcode, _ = await client.recv_frame()
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE
        assert not looked_host.server.connected
        assert not looked_host.events.connected
        assert looked_host.endpoints.calls == []

    async def test_a_connected_panel_of_this_backend_is_not_displaced(self, looked_host):
        ours = await WsTestClient.connect(looked_host.port, looked_host.token, session="ours")
        try:
            await knock(looked_host.port)

            assert looked_host.server.connected
            assert await ours.call(1, "echo", ["still here"]) == {
                "type": "reply",
                "id": 1,
                "result": {"echo": "still here"},
            }
        finally:
            await ours.close()


class TestEveryOtherRefusalIsAPlainStatus:
    async def test_an_upgrade_with_no_token_at_all_is_refused_with_401(self, running_host):
        """A panel never opens a connection without a token, so this is not one."""
        with pytest.raises(AssertionError, match="refused with 401"):
            await WsTestClient.connect(running_host.port, "")

    async def test_a_wrong_token_on_the_static_route_is_refused_with_401(self, running_host):
        status, _, body = await http_get(running_host.port, "/index.js", token="an-earlier-backends-token")

        assert status == 401
        assert body == b""

    async def test_a_wrong_token_on_ws_without_a_handshake_is_refused_with_401(self, running_host):
        status, _, _ = await http_get(running_host.port, "/ws", token="an-earlier-backends-token")

        assert status == 401

    async def test_a_foreign_origin_with_a_wrong_token_is_refused_with_403(self, running_host):
        with pytest.raises(AssertionError, match="refused with 403"):
            await WsTestClient.connect(running_host.port, "a-wrong-token", origin="https://evil.example.com")

    async def test_a_foreign_host_with_a_wrong_token_is_refused_with_421(self, running_host):
        with pytest.raises(AssertionError, match="refused with 421"):
            await WsTestClient.connect(running_host.port, "a-wrong-token", host="evil.example.com")


class TestTheLogCarriesOneLinePerPanel:
    async def test_many_knocks_of_one_panel_are_one_warning(self, looked_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            for _ in range(5):
                await knock(looked_host.port, session="panel-a")

        lines = refusals(caplog)
        assert len(lines) == 1
        assert lines[0].levelno == logging.WARNING
        assert "wrong token" in lines[0].message
        assert "'panel-a'" in lines[0].message
        assert "Steam has to be restarted" in lines[0].message
        assert "logged only if that answer changes" in lines[0].message

    async def test_another_panel_gets_a_line_of_its_own(self, looked_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(looked_host.port, session="panel-a")
            await knock(looked_host.port, session="panel-b")
            await knock(looked_host.port, session="panel-a")

        assert [("'panel-a'" in r.message, "'panel-b'" in r.message) for r in refusals(caplog)] == [
            (True, False),
            (False, True),
        ]

    async def test_a_changed_answer_gets_a_line_and_says_so(self, looked_host, caplog):
        outlook = ReloadOutlook.RELOAD_TO_COME

        async def reload_outlook() -> ReloadOutlook:
            return outlook

        looked_host.server.answer_stranded_panels_from(reload_outlook)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(looked_host.port)
            await knock(looked_host.port)
            outlook = ReloadOutlook.NO_RELOAD
            await knock(looked_host.port)
            await knock(looked_host.port)

        lines = [record.message for record in refusals(caplog)]
        assert len(lines) == 2
        assert "reloads Steam's interface once no game is running" in lines[0]
        assert "the answer changed" not in lines[0]
        assert "the answer changed: told it Steam has to be restarted" in lines[1]

    async def test_the_memory_is_bounded_and_a_forgotten_panel_costs_one_more_line(
        self, looked_host, caplog, monkeypatch
    ):
        monkeypatch.setattr(server_module, "STRANDED_SESSIONS_REMEMBERED", 2)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            for session in ("panel-a", "panel-b", "panel-c", "panel-a"):
                await knock(looked_host.port, session=session)

        assert len(refusals(caplog)) == 4

    async def test_a_long_session_is_cut_in_the_line_and_the_cut_is_marked(self, looked_host, caplog):
        session = "s" * 64 + "the-rest-is-cut"
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(looked_host.port, session=session)

        (line,) = [record.message for record in refusals(caplog)]
        assert f"(session {'s' * 64!r}… ({len(session)} characters))" in line
        assert "the-rest-is-cut" not in line

    async def test_a_session_of_the_cap_s_length_is_shown_whole(self, looked_host, caplog):
        session = "s" * 64
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(looked_host.port, session=session)

        (line,) = [record.message for record in refusals(caplog)]
        assert f"(session {session!r})" in line
        assert "characters" not in line

    async def test_no_line_carries_either_token(self, looked_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(looked_host.port, token="an-earlier-backends-token")

        assert refusals(caplog)
        for record in caplog.records:
            assert "an-earlier-backends-token" not in record.message
            assert looked_host.token not in record.message

    async def test_the_other_refusals_are_still_logged_every_time(self, looked_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            for _ in range(3):
                await http_get(looked_host.port, "/ws", token="x", origin="https://evil.example.com")
            for _ in range(2):
                await http_get(looked_host.port, "/ws", token=None)

        lines = [record.message for record in refusals(caplog)]
        assert sum("is not allowed" in line for line in lines) == 3
        assert sum("no token" in line for line in lines) == 2
