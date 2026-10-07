"""What the upgrade answers a panel another backend process loaded.

Such a panel carries that process's token, which this one refuses. A refused
handshake reaches a page exactly like no server at all, so the upgrade is
completed and closed at once with a code of this program's own instead — and
nothing is attached to it. These run against the real server on a real port, and
read the close the way a browser would: off the wire.
"""

from __future__ import annotations

import logging
import struct

import pytest

import host.server as server_module
from host.protocol import CLOSE_STRANDED_PANEL_RELOADS, CLOSE_STRANDED_PANEL_RESTART_STEAM
from lib.websocket_frames import OPCODE_CLOSE
from tests.host.conftest import SERVER_IDENTITY
from tests.host.ws_client import WsTestClient, http_get

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


def answering(reloads: bool):
    async def reload_to_come() -> bool:
        return reloads

    return reload_to_come


def refusals(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if "refused" in record.message]


class TestTheAnswer:
    async def test_the_upgrade_completes_and_closes_at_once_with_restart_steam_by_default(self, running_host):
        """No recovery attached: nothing here will reload Steam's interface."""
        code, reason = await knock(running_host.port)

        assert code == CLOSE_STRANDED_PANEL_RESTART_STEAM
        assert "restart Steam" in reason

    async def test_a_reload_to_come_is_said_with_its_own_code(self, running_host):
        running_host.server.answer_stranded_panels_from(answering(True))

        code, reason = await knock(running_host.port)

        assert code == CLOSE_STRANDED_PANEL_RELOADS
        assert "once no game is running" in reason

    async def test_the_answer_is_asked_again_on_every_knock(self, running_host):
        answers = iter([True, False])

        async def reload_to_come() -> bool:
            return next(answers)

        running_host.server.answer_stranded_panels_from(reload_to_come)

        assert (await knock(running_host.port))[0] == CLOSE_STRANDED_PANEL_RELOADS
        assert (await knock(running_host.port))[0] == CLOSE_STRANDED_PANEL_RESTART_STEAM

    async def test_the_handshake_names_this_program_like_every_refusal(self, running_host):
        client = await WsTestClient.connect(running_host.port, "an-earlier-backends-token")
        await client.close()

        assert client.handshake["server"] == SERVER_IDENTITY


class TestNothingIsAuthorised:
    async def test_no_connection_is_registered_and_no_call_is_carried(self, running_host):
        client = await WsTestClient.connect(running_host.port, "an-earlier-backends-token", session="panel-a")
        try:
            await client.send_json({"type": "call", "id": 1, "method": "echo", "args": ["hello"]})
            opcode, _ = await client.recv_frame()
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE
        assert not running_host.server.connected
        assert not running_host.events.connected
        assert running_host.endpoints.calls == []

    async def test_a_connected_panel_of_this_backend_is_not_displaced(self, running_host):
        ours = await WsTestClient.connect(running_host.port, running_host.token, session="ours")
        try:
            await knock(running_host.port)

            assert running_host.server.connected
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
    async def test_many_knocks_of_one_panel_are_one_warning(self, running_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            for _ in range(5):
                await knock(running_host.port, session="panel-a")

        lines = refusals(caplog)
        assert len(lines) == 1
        assert lines[0].levelno == logging.WARNING
        assert "wrong token" in lines[0].message
        assert "'panel-a'" in lines[0].message
        assert "Steam has to be restarted" in lines[0].message
        assert "logged only if that answer changes" in lines[0].message

    async def test_another_panel_gets_a_line_of_its_own(self, running_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(running_host.port, session="panel-a")
            await knock(running_host.port, session="panel-b")
            await knock(running_host.port, session="panel-a")

        assert [("'panel-a'" in r.message, "'panel-b'" in r.message) for r in refusals(caplog)] == [
            (True, False),
            (False, True),
        ]

    async def test_a_changed_answer_gets_a_line_and_says_so(self, running_host, caplog):
        reloads = True

        async def reload_to_come() -> bool:
            return reloads

        running_host.server.answer_stranded_panels_from(reload_to_come)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(running_host.port)
            await knock(running_host.port)
            reloads = False
            await knock(running_host.port)
            await knock(running_host.port)

        lines = [record.message for record in refusals(caplog)]
        assert len(lines) == 2
        assert "reloads Steam's interface once no game is running" in lines[0]
        assert "the answer changed" not in lines[0]
        assert "the answer changed: told it Steam has to be restarted" in lines[1]

    async def test_the_memory_is_bounded_and_a_forgotten_panel_costs_one_more_line(
        self, running_host, caplog, monkeypatch
    ):
        monkeypatch.setattr(server_module, "STRANDED_SESSIONS_REMEMBERED", 2)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            for session in ("panel-a", "panel-b", "panel-c", "panel-a"):
                await knock(running_host.port, session=session)

        assert len(refusals(caplog)) == 4

    async def test_no_line_carries_either_token(self, running_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await knock(running_host.port, token="an-earlier-backends-token")

        assert refusals(caplog)
        for record in caplog.records:
            assert "an-earlier-backends-token" not in record.message
            assert running_host.token not in record.message

    async def test_the_other_refusals_are_still_logged_every_time(self, running_host, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            for _ in range(3):
                await http_get(running_host.port, "/ws", token="x", origin="https://evil.example.com")
            for _ in range(2):
                await http_get(running_host.port, "/ws", token=None)

        lines = [record.message for record in refusals(caplog)]
        assert sum("is not allowed" in line for line in lines) == 3
        assert sum("no token" in line for line in lines) == 2
