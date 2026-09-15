"""The real plugin, reached the way the panel will reach it: over a socket.

Two halves are already covered separately and neither one covers this. The host
tier drives a real socket onto a stand-in plugin, so it proves the protocol and
nothing about the callables; the rest of this tier drives the real callables by
calling them, so it proves the answers and nothing about the wire. What is only
here is the seam between them — a real callable resolved by name out of the
loaded `Plugin`, its answer encoded by `encode_reply`, and the size cap judged
against a payload a real callable actually produced.

Every case builds the real `Plugin` through the real `bootstrap()` (the shared
harness) and puts a `HostServer` in front of it, then speaks to that server the
way `frontend/src/api/backend.ts` will: positional, JSON-shaped arguments in a
`call` message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from domain.rom import Rom
from domain.rom_metadata import RomMetadata
from host.dispatch import CallDispatcher
from host.events import EventSink
from host.protocol import REASON_BACKEND_EXCEPTION, REASON_METHOD_UNKNOWN, TYPE_ERROR, TYPE_REPLY
from host.server import HostServer
from tests.host.conftest import free_port
from tests.host.ws_client import WsTestClient, http_get

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.contract._harness import ContractHarness

LOGGER = logging.getLogger("contract_host")


def _seed_metadata(harness: ContractHarness, rom_id: int) -> None:
    """Seed a `Rom` anchor and a cached metadata row, so a page has something in it."""
    with harness.uow_factory() as uow:
        uow.roms.save(
            Rom.synced(
                rom_id=rom_id,
                platform_slug="gba",
                name=f"rom-{rom_id}",
                fs_name=f"rom-{rom_id}",
                shortcut_app_id=rom_id,
                synced_at="2026-01-01T00:00:00",
            )
        )
        uow.rom_metadata.save(
            rom_id,
            RomMetadata(
                summary=f"Game {rom_id}",
                genres=("RPG",),
                companies=(),
                first_release_date=None,
                average_rating=None,
                game_modes=(),
                player_count="1",
                cached_at=100.0,
            ),
        )


class ServedPlugin:
    """A started `HostServer` in front of the harness's real `Plugin`."""

    def __init__(self, server: HostServer, harness: ContractHarness) -> None:
        self.server = server
        self.harness = harness

    async def call(self, method: str, args: list[Any] | None = None) -> dict[str, Any]:
        """Put one call on a fresh connection and read the message that answers it."""
        client = await WsTestClient.connect(self.server.port, self.server.token)
        try:
            return await client.call(1, method, args or [])
        finally:
            await client.close()


@pytest.fixture
async def served(harness, tmp_path) -> AsyncIterator[ServedPlugin]:
    """Serve the real plugin on a free loopback port for the length of one test."""
    static_root = tmp_path / "dist"
    static_root.mkdir(exist_ok=True)
    server = HostServer(
        dispatcher=CallDispatcher(harness.plugin, LOGGER),
        events=EventSink(LOGGER),
        static_root=str(static_root),
        logger=LOGGER,
        server_identity="romm-tender/0.0.0-test",
        preferred_port=free_port(),
    )
    await server.start()
    try:
        yield ServedPlugin(server, harness)
    finally:
        await server.stop()


class TestARealCallableOverTheRealConnection:
    async def test_it_answers_with_the_callables_own_payload(self, served):
        """`get_settings` is a plain read, so the answer is the settings dict itself."""
        answer = await served.call("get_settings")

        assert answer["type"] == TYPE_REPLY
        assert answer["id"] == 1
        assert isinstance(answer["result"], dict)
        assert "romm_url" in answer["result"]

    async def test_an_argument_taking_callable_receives_them_in_order(self, served):
        """`args` are positional, and the order is what this asserts.

        Two rows seeded, then `(offset=0, limit=1)`. Swapped, that is
        `(offset=1, limit=0)` — an empty page — so the two readings give
        different answers rather than the same one by symmetry.
        """
        _seed_metadata(served.harness, 1)
        _seed_metadata(served.harness, 2)

        answer = await served.call("get_metadata_cache_page", [0, 1])

        assert answer["result"]["total"] == 2
        assert list(answer["result"]["items"].keys()) == ["1"]

    async def test_a_callables_own_failure_shape_travels_inside_result(self, served):
        """A refused callable is a SUCCESSFUL transport — the distinction this protocol exists to keep.

        `test_connection` with no server configured answers the repo's
        `{success, reason, message}` shape. It must arrive as a `reply`, not as
        an `error`: reading one as the other shows a user a sentence about their
        server where a programming error would stand.
        """
        answer = await served.call("test_connection")

        assert answer["type"] == TYPE_REPLY
        assert answer["result"]["success"] is False
        assert "reason" in answer["result"]
        assert "message" in answer["result"]

    async def test_a_name_the_plugin_does_not_have_is_a_transport_error(self, served):
        answer = await served.call("no_such_callable")

        assert answer["type"] == TYPE_ERROR
        assert answer["reason"] == REASON_METHOD_UNKNOWN

    async def test_a_private_method_of_the_real_plugin_is_unreachable(self, served):
        """`_main` exists on the loaded class and is not callable surface."""
        answer = await served.call("_main")

        assert answer["reason"] == REASON_METHOD_UNKNOWN

    async def test_wrong_arity_against_a_real_callable_is_reported_not_swallowed(self, served):
        answer = await served.call("get_metadata_cache_page", [])

        assert answer["type"] == TYPE_ERROR
        assert answer["reason"] == REASON_BACKEND_EXCEPTION


class TestTheSizeCapAgainstARealAnswer:
    async def test_a_real_answer_under_the_cap_arrives_whole(self, served):
        """Nothing is truncated on the way out — the cap only refuses."""
        answer = await served.call("get_sync_stats")

        assert answer["type"] == TYPE_REPLY
        assert isinstance(answer["result"], dict)

    async def test_an_answer_over_the_cap_is_refused_for_that_call_alone(self, harness, tmp_path):
        """The cap is judged on a payload a real callable produced, not a synthetic one."""
        static_root = tmp_path / "dist"
        static_root.mkdir(exist_ok=True)
        server = HostServer(
            dispatcher=CallDispatcher(harness.plugin, LOGGER, payload_limit=8),
            events=EventSink(LOGGER),
            static_root=str(static_root),
            logger=LOGGER,
            server_identity="romm-tender/0.0.0-test",
            preferred_port=free_port(),
        )
        await server.start()
        client = await WsTestClient.connect(server.port, server.token)
        try:
            refused = await client.call(1, "get_settings")
            after = await client.call(2, "get_settings")
        finally:
            await client.close()
            await server.stop()

        assert refused["reason"] == "payload_too_large"
        # The connection survived it, which is the whole reason this cap is not
        # the frame cap.
        assert after["reason"] == "payload_too_large"


class TestAdmissionInFrontOfTheRealPlugin:
    async def test_no_token_reaches_no_callable(self, served):
        status, _, _ = await http_get(served.server.port, "/ws", token=None)

        assert status == 401

    async def test_a_foreign_origin_reaches_no_callable(self, served):
        status, _, _ = await http_get(
            served.server.port,
            "/ws",
            token=served.server.token,
            origin="https://evil.example.com",
        )

        assert status == 403
