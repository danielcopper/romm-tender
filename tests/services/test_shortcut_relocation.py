"""Tests for the one-time move of every shortcut onto the launcher's home.

The service answers a question with three outcomes and stamps a completion that
nothing ever clears, so the cases that matter are the ones where it must NOT
stamp: an answer it could not establish has to leave the question open, or the
shortcuts it never reached stay on the old path for the life of the install.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from services.shortcut_relocation import (
    KV_RELOCATION_DONE,
    ShortcutRelocationService,
    ShortcutRelocationServiceConfig,
)

_HOME = "/home/deck/.local/share/romm-tender/bin/rom-launcher"
_HOME_DIR = "/home/deck/.local/share/romm-tender/bin"
_OLD = "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher"
_RENAMED = "/home/deck/homebrew/plugins/romm-tender/bin/rom-launcher"


class _FakeSteamConfig:
    """Only the one method this service asks of the Steam config store."""

    def __init__(self, exes: dict[int, str] | None) -> None:
        self._exes = exes
        self.reads = 0

    def read_shortcut_exes(self) -> dict[int, str] | None:
        self.reads += 1
        return self._exes


# ``None`` is a real answer here — the reading could not be done — so it cannot
# double as "the test did not care".
_UNSET: Any = object()


def _make(
    *,
    exes: dict[int, str] | None = _UNSET,
    at_home: bool = True,
    uow: FakeUnitOfWork | None = None,
) -> tuple[ShortcutRelocationService, _FakeSteamConfig, FakeUnitOfWorkFactory]:
    steam_config = _FakeSteamConfig({} if exes is _UNSET else exes)
    factory = FakeUnitOfWorkFactory(uow=uow)
    service = ShortcutRelocationService(
        config=ShortcutRelocationServiceConfig(
            launcher_exe=_HOME,
            launcher_at_home=at_home,
            steam_config=steam_config,  # type: ignore[arg-type]
            uow_factory=factory,
            loop=asyncio.get_event_loop(),
            logger=logging.getLogger("test_shortcut_relocation"),
        ),
    )
    return service, steam_config, factory


def _stamped() -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.kv_config.set(KV_RELOCATION_DONE, "1")
    return uow


class TestWhatIsLeftToDo:
    @pytest.mark.asyncio
    async def test_names_every_shortcut_still_pointing_into_a_plugin_folder(self):
        service, _, _ = _make(exes={10: _OLD, 20: _RENAMED, 30: _HOME, 40: "/usr/bin/other"})

        assert await service.get_shortcut_relocation() == {
            "status": "outstanding",
            "exe": _HOME,
            "start_dir": _HOME_DIR,
            "app_ids": [10, 20],
        }

    @pytest.mark.asyncio
    async def test_a_library_already_on_the_new_path_is_done(self):
        service, _, _ = _make(exes={10: _HOME, 20: _HOME})

        assert await service.get_shortcut_relocation() == {"status": "done"}

    @pytest.mark.asyncio
    async def test_a_machine_with_no_shortcuts_at_all_is_done(self):
        service, _, _ = _make(exes={})

        assert await service.get_shortcut_relocation() == {"status": "done"}


class TestTheCompletionStamp:
    @pytest.mark.asyncio
    async def test_finding_nothing_to_do_stamps_it(self):
        uow = FakeUnitOfWork()
        service, _, _ = _make(exes={10: _HOME}, uow=uow)

        await service.get_shortcut_relocation()

        assert uow.kv_config.get(KV_RELOCATION_DONE) == "1"

    @pytest.mark.asyncio
    async def test_a_stamped_install_reads_no_shortcut_file_at_all(self):
        """The plugin start already carries enough checks; this one must not be permanent."""
        service, steam_config, _ = _make(exes={10: _OLD}, uow=_stamped())

        assert await service.get_shortcut_relocation() == {"status": "done"}
        assert steam_config.reads == 0

    @pytest.mark.asyncio
    async def test_handing_out_a_plan_stamps_nothing(self):
        """Only the frontend can say the writes happened, so nothing is recorded yet."""
        uow = FakeUnitOfWork()
        service, _, _ = _make(exes={10: _OLD}, uow=uow)

        await service.get_shortcut_relocation()

        assert uow.kv_config.get(KV_RELOCATION_DONE) is None

    @pytest.mark.asyncio
    async def test_completing_stamps_it(self):
        uow = FakeUnitOfWork()
        service, _, _ = _make(exes={10: _OLD}, uow=uow)

        assert await service.complete_shortcut_relocation() == {"success": True}

        assert uow.kv_config.get(KV_RELOCATION_DONE) == "1"

    @pytest.mark.asyncio
    async def test_completing_twice_is_idempotent(self):
        uow = FakeUnitOfWork()
        service, _, _ = _make(uow=uow)

        await service.complete_shortcut_relocation()
        await service.complete_shortcut_relocation()

        assert uow.kv_config.get(KV_RELOCATION_DONE) == "1"


class TestWhenNothingMayBeRewritten:
    @pytest.mark.asyncio
    async def test_a_launcher_not_at_its_home_blocks_the_rewrite(self):
        """The data migration has not landed, or the install failed."""
        service, steam_config, _ = _make(exes={10: _OLD}, at_home=False)

        answer = await service.get_shortcut_relocation()

        assert answer["status"] == "blocked"
        assert answer["message"]
        assert steam_config.reads == 0

    @pytest.mark.asyncio
    async def test_an_unreadable_shortcut_file_blocks_the_rewrite(self):
        service, _, _ = _make(exes=None)

        answer = await service.get_shortcut_relocation()

        assert answer["status"] == "blocked"
        assert answer["message"]

    @pytest.mark.asyncio
    async def test_a_blocked_answer_stamps_nothing(self):
        """Stamping here would close the question on a reading that never happened."""
        uow = FakeUnitOfWork()
        service, _, _ = _make(exes=None, uow=uow)

        await service.get_shortcut_relocation()

        assert uow.kv_config.get(KV_RELOCATION_DONE) is None

    @pytest.mark.asyncio
    async def test_a_launcher_not_at_home_stamps_nothing_either(self):
        uow = FakeUnitOfWork()
        service, _, _ = _make(exes={10: _OLD}, at_home=False, uow=uow)

        await service.get_shortcut_relocation()

        assert uow.kv_config.get(KV_RELOCATION_DONE) is None
