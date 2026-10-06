"""Tests for services/emulator_sources.py — the settings list, the switch and the order.

The service runs over the real sources holder with a detection the test names,
so "kept" means what the user sees: the next reading arranges the sources the
way the stored settings say.
"""

from __future__ import annotations

from typing import Any

import pytest
from _vendor.atlas import CAVEAT_EMULATOR_CATALOGUE_SEALED, HEALTH_ISSUE_ROOT_MISSING
from _vendor.atlas.installations import Health, SystemsAnswer
from _vendor.atlas.placement import Caveat
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.running_loop import running_loop

from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.refusal import DomainRefused
from lib.errors import Refused
from services.emulator_sources import EmulatorSourcesService, EmulatorSourcesServiceConfig


class _Installation:
    def __init__(self, kind: str, *, issues: tuple[Caveat, ...] = (), sealed: bool = False) -> None:
        self.kind = kind
        self._issues = issues
        self._sealed = sealed

    def health(self) -> Health:
        return Health(self._issues)

    def root(self) -> str:
        return f"/roots/{self.kind}"

    def systems(self) -> SystemsAnswer:
        caveats = (Caveat(CAVEAT_EMULATOR_CATALOGUE_SEALED, "prose"),) if self._sealed else ()
        return SystemsAnswer(systems=("gba",), caveats=caveats)


class _Rig:
    def __init__(self, *installations: _Installation) -> None:
        self.detected = list(installations)
        self.settings: dict[str, Any] = {"emulator_source_order": [], "emulator_sources_off": []}
        self.persister = FakeSettingsPersister()
        self.sources = EmulatorSourcesAdapter(
            user_home="/home/deck",
            settings=self.settings,
            log_debug=lambda message: None,
            detect_installations=lambda home, machine: list(self.detected),
            machine=object(),
        )
        self.service = EmulatorSourcesService(
            config=EmulatorSourcesServiceConfig(
                sources=self.sources,
                settings=self.settings,
                settings_persister=self.persister,
                loop=running_loop(),
                log_debug=lambda message: None,
            )
        )

    def kinds(self, listing: dict[str, Any]) -> list[str]:
        return [source["kind"] for source in listing["sources"]]


@pytest.fixture
def both() -> _Rig:
    return _Rig(
        _Installation("retrodeck", issues=(Caveat(HEALTH_ISSUE_ROOT_MISSING, "prose", {"path": "/sd/retrodeck"}),)),
        _Installation("emudeck", sealed=True),
    )


class TestTheListing:
    @pytest.mark.asyncio
    async def test_retrodeck_and_emudeck_are_listed_with_their_health_and_switch(self, both):
        listing = await both.service.get_emulator_sources()

        assert listing == {
            "sources": [
                {
                    "kind": "retrodeck",
                    "enabled": True,
                    "starts_games": True,
                    "root": "/roots/retrodeck",
                    "findings": [{"code": HEALTH_ISSUE_ROOT_MISSING, "data": {"path": "/sd/retrodeck"}}],
                    "catalogue": "read",
                },
                {
                    "kind": "emudeck",
                    "enabled": True,
                    "starts_games": False,
                    "root": "/roots/emudeck",
                    "findings": [],
                    "catalogue": "sealed",
                },
            ],
            "answering": "retrodeck",
        }

    @pytest.mark.asyncio
    async def test_nothing_detected_lists_nothing(self):
        assert await _Rig().service.get_emulator_sources() == {"sources": [], "answering": None}

    @pytest.mark.asyncio
    async def test_with_retrodeck_switched_off_emudeck_answers(self, both):
        listing = await both.service.set_emulator_source_enabled("retrodeck", False)

        assert listing["answering"] == "emudeck"


class TestTheSwitch:
    @pytest.mark.asyncio
    async def test_switching_a_source_off_stores_it_and_answers_the_new_listing(self, both):
        listing = await both.service.set_emulator_source_enabled("emudeck", False)

        assert both.settings["emulator_sources_off"] == ["emudeck"]
        assert both.persister.save_count == 1
        assert [source["enabled"] for source in listing["sources"]] == [True, False]

    @pytest.mark.asyncio
    async def test_switching_it_on_again_takes_it_off_the_list(self, both):
        await both.service.set_emulator_source_enabled("emudeck", False)
        listing = await both.service.set_emulator_source_enabled("emudeck", True)

        assert both.settings["emulator_sources_off"] == []
        assert [source["enabled"] for source in listing["sources"]] == [True, True]

    @pytest.mark.asyncio
    async def test_a_source_that_is_not_detected_has_no_switch(self, both):
        with pytest.raises(DomainRefused) as refusal:
            await both.service.set_emulator_source_enabled("bare_retroarch_native", False)

        assert refusal.value.reason == "unknown_source"
        assert both.persister.save_count == 0
        assert both.settings["emulator_sources_off"] == []


class TestTheOrder:
    @pytest.mark.asyncio
    async def test_a_move_is_stored_and_kept_for_the_next_reading(self, both):
        listing = await both.service.move_emulator_source("emudeck", "up")

        assert both.settings["emulator_source_order"] == ["emudeck", "retrodeck"]
        assert both.persister.save_count == 1
        assert both.kinds(listing) == ["emudeck", "retrodeck"]
        assert both.kinds(await both.service.get_emulator_sources()) == ["emudeck", "retrodeck"]
        assert both.sources.read().answering_kind == "retrodeck"

    @pytest.mark.asyncio
    async def test_moving_down_moves_it_back(self, both):
        await both.service.move_emulator_source("emudeck", "up")
        listing = await both.service.move_emulator_source("emudeck", "down")

        assert both.kinds(listing) == ["retrodeck", "emudeck"]

    @pytest.mark.asyncio
    async def test_a_move_past_the_end_is_refused_and_stores_nothing(self, both):
        with pytest.raises(DomainRefused) as refusal:
            await both.service.move_emulator_source("retrodeck", "up")

        assert refusal.value.reason == "cannot_move"
        assert both.persister.save_count == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("direction", ["left", "", "UP"])
    async def test_a_direction_that_is_not_up_or_down_is_refused(self, both, direction):
        with pytest.raises(Refused) as refusal:
            await both.service.move_emulator_source("emudeck", direction)

        assert refusal.value.reason == "invalid_direction"
        assert both.persister.save_count == 0
