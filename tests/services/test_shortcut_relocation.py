"""Tests for the one-time move of every shortcut onto the launcher's home.

The service answers a question with three outcomes and stamps a completion that
nothing ever clears, so the cases that matter are the ones where it must NOT
stamp: an answer it could not establish has to leave the question open, or the
shortcuts it never reached stay on the old path for the life of the install.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
from typing import Any

import pytest
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from services.shortcut_relocation import (
    KV_RELOCATION_DONE,
    ShortcutRelocationService,
    ShortcutRelocationServiceConfig,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

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
        """The one writer: a reading of the file that finds nothing of ours outside the home."""
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
        """The writes have not happened yet, and a later reading is what will say they did."""
        uow = FakeUnitOfWork()
        service, _, _ = _make(exes={10: _OLD}, uow=uow)

        await service.get_shortcut_relocation()

        assert uow.kv_config.get(KV_RELOCATION_DONE) is None


class TestTheStampHasOneWriter:
    """The reading of ``shortcuts.vdf`` is the single stamp authority, structurally.

    The design rests on this: the completion may be recorded only by a call that
    has just read the file and found nothing of ours outside the launcher's home.
    No report can record it — not the frontend's, which is why the callable that
    once let it was removed — because Steam writes its in-memory shortcuts to
    that file when it chooses, so a report describes writes the file cannot yet
    show. A second writer breaks no behavioural test below: it would stamp
    early, the panel would offer the pre-rename install for removal on the
    strength of it, and every later start would read nothing.

    The scan pins the whole chain from the key to the reading — one write of the
    ``kv_config`` key, one reference to the method holding it, one reference to
    the method holding THAT — so a new writer has to break one of the three
    counts wherever it is spliced in.

    **What the scan SEES are attribute references by name, in this module's own
    AST.** That covers a bound method handed to something else, which a call
    count would not: ``run_in_executor(None, self._stamp_done_io)`` is how this
    module reaches its own ``_io`` bodies. What it does not see is an alias
    (``stamp = self._stamp_done`` under another name), a ``getattr``, or any
    module but this one — :data:`KV_RELOCATION_DONE` is importable, and a write
    under it from elsewhere would pass every assertion here.
    """

    _SOURCE = _REPO_ROOT / "py_modules" / "services" / "shortcut_relocation.py"

    @classmethod
    def _tree(cls) -> ast.Module:
        return ast.parse(cls._SOURCE.read_text(encoding="utf-8"))

    @staticmethod
    def _holder(tree: ast.Module, node: ast.AST) -> str:
        """The innermost ``def`` whose body holds *node*, by name."""
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        current: ast.AST | None = node
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current.name
            current = parents.get(current)
        return "<module>"

    @classmethod
    def _refs(cls, tree: ast.Module, attr: str) -> list[str]:
        """Every ``<...>.attr`` reference, reported by the function holding it."""
        return [
            cls._holder(tree, node) for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == attr
        ]

    def test_the_key_is_written_in_one_place(self):
        tree = self._tree()
        writes = [
            self._holder(tree, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "set"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "kv_config"
        ]

        assert writes == ["_stamp_done_io"]

    def test_that_place_is_reached_from_one_place(self):
        assert self._refs(self._tree(), "_stamp_done_io") == ["_stamp_done"]

    def test_and_that_one_is_reached_only_from_the_reading(self):
        assert self._refs(self._tree(), "_stamp_done") == ["get_shortcut_relocation"]

    def test_no_completion_method_survives_on_the_service(self):
        """The frontend's route to the stamp, removed rather than tuned."""
        assert not [name for name in dir(ShortcutRelocationService) if "complete" in name]


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
