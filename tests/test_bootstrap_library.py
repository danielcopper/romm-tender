"""The library service's wiring: every part built once, in ``bootstrap/library.py``, and handed to its holders."""

from __future__ import annotations

import logging

import pytest
from _factories import _make_conflict_rules
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_emulator_sources import FakeEmulatorSources
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_renderer_gc import FakeRendererGc
from fakes.fake_renderer_rss import FakeRendererRss
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_running_rom_ids import FakeRunningRomIds
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_shortcut_artwork_job import FakeShortcutArtworkJob
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.library_peers import FakeArtworkManager
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock, FakeSleeper, FakeUuidGen

from adapters.steam_config import SteamConfigAdapter
from bootstrap import LibraryParts, LibraryWiring, wire_library

LOGGER = logging.getLogger("test_bootstrap_library")


@pytest.fixture
def parts(home) -> LibraryParts:
    return wire_library(
        LibraryWiring(
            romm_api=FakeRommApi(),
            steam_config=SteamConfigAdapter(user_home=str(home), logger=LOGGER),
            settings={},
            loop=running_loop(),
            logger=LOGGER,
            launcher_exe=f"{home}/.local/bin/tender-rom-launcher",
            emit=FakeEventSink().emit,
            clock=FakeClock(),
            uuid_gen=FakeUuidGen(),
            sleeper=FakeSleeper(),
            settings_persister=FakeSettingsPersister(),
            log_debug=lambda msg: None,
            artwork=FakeArtworkManager(),
            uow_factory=FakeUnitOfWorkFactory(FakeUnitOfWork()),
            active_core=FakeActiveCoreResolver(default=(None, None)),
            disc_resolver=FakeDiscResolver(),
            emulator_sources=FakeEmulatorSources(),
            renderer_rss=FakeRendererRss(),
            renderer_gc=FakeRendererGc(),
            conflict_rules=_make_conflict_rules(),
            artwork_job=FakeShortcutArtworkJob(),
            running_rom_ids=FakeRunningRomIds(),
        )
    )


# (holder, the attribute it keeps a collaborator in, the part that must be there)
_HELD = [
    ("service", "_box", "sync_state_box"),
    ("service", "_fetcher", "fetcher"),
    ("service", "_progress", "progress"),
    ("service", "_session_budget", "session_budget"),
    ("service", "_reporter", "reporter"),
    ("service", "_preview", "preview"),
    ("service", "_orchestrator", "orchestrator"),
    ("fetcher", "_local_library_reader", "local_library_reader"),
    ("chunk_dispatcher", "_reporter", "reporter"),
    ("chunk_dispatcher", "_session_budget", "session_budget"),
    ("unit_applier", "_fetcher", "fetcher"),
    ("unit_applier", "_collection_fetcher", "collection_fetcher"),
    ("unit_applier", "_shortcut_launch_resolver", "shortcut_launch_resolver"),
    ("unit_applier", "_local_library_reader", "local_library_reader"),
    ("unit_applier", "_cover_preparer", "cover_preparer"),
    ("unit_applier", "_chunk_dispatcher", "chunk_dispatcher"),
    ("preview", "_fetcher", "fetcher"),
    ("preview", "_collection_fetcher", "collection_fetcher"),
    ("preview", "_shortcut_launch_resolver", "shortcut_launch_resolver"),
    ("preview", "_local_library_reader", "local_library_reader"),
    ("preview", "_session_budget", "session_budget"),
    ("preview", "_cover_preparer", "cover_preparer"),
    ("preview", "_progress", "progress"),
    ("orchestrator", "_fetcher", "fetcher"),
    ("orchestrator", "_unit_applier", "unit_applier"),
    ("orchestrator", "_reporter", "reporter"),
    ("orchestrator", "_shortcut_launch_resolver", "shortcut_launch_resolver"),
    ("orchestrator", "_local_library_reader", "local_library_reader"),
    ("orchestrator", "_session_budget", "session_budget"),
    ("orchestrator", "_sync_run_recorder", "sync_run_recorder"),
    ("orchestrator", "_progress", "progress"),
]

# Every part that keeps the state box, which each keeps in ``_sync_state``.
_BOX_HOLDERS = [
    "progress",
    "fetcher",
    "collection_fetcher",
    "session_budget",
    "reporter",
    "chunk_dispatcher",
    "cover_preparer",
    "unit_applier",
    "preview",
    "orchestrator",
]


class TestEveryPartIsBuiltOnce:
    @pytest.mark.parametrize(("holder", "attribute", "part"), _HELD)
    def test_a_holder_keeps_the_part_wire_library_built(self, parts, holder, attribute, part):
        assert getattr(getattr(parts, holder), attribute) is getattr(parts, part)

    @pytest.mark.parametrize("holder", _BOX_HOLDERS)
    def test_every_part_shares_the_one_state_box(self, parts, holder):
        assert getattr(parts, holder)._sync_state is parts.sync_state_box
