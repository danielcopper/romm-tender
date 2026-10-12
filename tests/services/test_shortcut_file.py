"""Tests for the reading of a shortcut's launch options off Steam's shortcut file."""

from __future__ import annotations

import asyncio
import threading

import pytest

from lib.errors import Refused
from services.shortcut_file import ShortcutFileService, ShortcutFileServiceConfig


class _FakeSteamConfig:
    """Only the one method this service asks of the Steam config store."""

    def __init__(self, launch_options: dict[int, str] | None) -> None:
        self._launch_options = launch_options
        self.read_on: list[int] = []

    def read_shortcut_launch_options(self) -> dict[int, str] | None:
        self.read_on.append(threading.get_ident())
        return self._launch_options


def _make(launch_options: dict[int, str] | None) -> tuple[ShortcutFileService, _FakeSteamConfig]:
    steam_config = _FakeSteamConfig(launch_options)
    service = ShortcutFileService(
        config=ShortcutFileServiceConfig(
            steam_config=steam_config,  # type: ignore[arg-type]
            loop=asyncio.get_running_loop(),
        ),
    )
    return service, steam_config


class TestGetShortcutLaunchOptions:
    @pytest.mark.asyncio
    async def test_answers_only_the_app_ids_asked_about(self):
        service, _ = _make({1: "one", 2: "two", 3: ""})

        answer = await service.get_shortcut_launch_options([1, 3])

        assert answer == {
            "success": True,
            "launch_options": [{"app_id": 1, "launch_options": "one"}, {"app_id": 3, "launch_options": ""}],
        }

    @pytest.mark.asyncio
    async def test_an_app_id_the_file_does_not_hold_is_absent_from_the_answer(self):
        service, _ = _make({1: "one"})

        assert await service.get_shortcut_launch_options([2]) == {"success": True, "launch_options": []}

    @pytest.mark.asyncio
    async def test_an_app_id_sent_as_a_string_is_matched_and_a_non_number_dropped(self):
        service, _ = _make({1: "one"})

        answer = await service.get_shortcut_launch_options(["1", "not a number", None])  # type: ignore[list-item]

        assert answer == {"success": True, "launch_options": [{"app_id": 1, "launch_options": "one"}]}

    @pytest.mark.asyncio
    async def test_a_file_that_could_not_be_read_is_refused(self):
        service, _ = _make(None)

        with pytest.raises(Refused) as refused:
            await service.get_shortcut_launch_options([1])

        assert refused.value.reason == "shortcut_file_unreadable"
        assert refused.value.message
        assert refused.value.details == {}

    @pytest.mark.asyncio
    async def test_a_machine_with_no_shortcut_file_answers_an_empty_reading(self):
        """No file is a reading of no shortcuts, not a file that could not be read."""
        service, _ = _make({})

        assert await service.get_shortcut_launch_options([1]) == {"success": True, "launch_options": []}

    @pytest.mark.asyncio
    async def test_the_file_is_read_off_the_event_loop(self):
        """A parse of the whole file on the loop would stall every other call while it ran."""
        service, steam_config = _make({1: "one"})

        await service.get_shortcut_launch_options([1])

        assert len(steam_config.read_on) == 1
        assert steam_config.read_on[0] != threading.get_ident()

    @pytest.mark.asyncio
    async def test_each_call_reads_the_file_afresh(self):
        """Nothing is kept between calls: a later write is only seen by a later reading."""
        service, steam_config = _make({1: "one"})

        await service.get_shortcut_launch_options([1])
        await service.get_shortcut_launch_options([1])

        assert len(steam_config.read_on) == 2
