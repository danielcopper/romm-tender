"""Which of Tender's games Steam lists as running: the reading matched to ROMs by shortcut app id."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fakes.fake_unit_of_work import FakeUnitOfWorkFactory

from domain.rom import Rom
from services.running_games import RunningGames, RunningGamesConfig


@dataclass(frozen=True)
class _Listed:
    name: str
    status_read: bool = True
    app_id: int | None = None


class _Steam:
    """Answers one reading of the running apps, raises it, or never answers while ``hangs``."""

    def __init__(self, apps: tuple[_Listed, ...] | None = (), raises: Exception | None = None) -> None:
        self.apps = apps
        self.raises = raises
        self.hangs = False
        self.readings = 0

    async def running_apps(self) -> tuple[_Listed, ...] | None:
        self.readings += 1
        if self.raises is not None:
            raise self.raises
        if self.hangs:
            await asyncio.Event().wait()
        return self.apps

    async def reload_frees_at(self) -> float | None:
        return None


def _games(steam: _Steam, debug: list[str] | None = None) -> RunningGames:
    uow_factory = FakeUnitOfWorkFactory()
    with uow_factory.uow as uow:
        for rom_id, app_id in ((7, 3_000_000_007), (8, 3_000_000_008), (9, None)):
            uow.roms.save(
                Rom.synced(
                    rom_id=rom_id,
                    platform_slug="gba",
                    name=f"Game {rom_id}",
                    fs_name=f"game-{rom_id}",
                    shortcut_app_id=app_id,
                    synced_at="2026-01-01T00:00:00",
                )
            )
    log = debug if debug is not None else []

    def log_debug(msg: str) -> None:
        log.append(msg)

    return RunningGames(
        config=RunningGamesConfig(
            steam=steam, uow_factory=uow_factory, loop=asyncio.get_running_loop(), log_debug=log_debug
        )
    )


class TestWhichGamesRun:
    async def test_a_listed_app_is_the_rom_whose_shortcut_has_its_id(self):
        games = _games(_Steam((_Listed("Game 7", app_id=3_000_000_007),)))

        assert await games.running_rom_ids() == frozenset({7})

    async def test_an_app_that_counts_only_by_an_unread_status_runs_too(self):
        games = _games(_Steam((_Listed("Game 8", status_read=False, app_id=3_000_000_008),)))

        assert await games.running_rom_ids() == frozenset({8})

    async def test_an_app_that_is_no_shortcut_of_tenders_is_no_rom(self):
        games = _games(_Steam((_Listed("Celeste", app_id=504230), _Listed("Game 7", app_id=3_000_000_007))))

        assert await games.running_rom_ids() == frozenset({7})

    async def test_an_app_listed_without_an_id_matches_no_rom(self):
        games = _games(_Steam((_Listed("Game 9"),)))

        assert await games.running_rom_ids() == frozenset()

    async def test_every_call_is_a_reading_of_its_own(self):
        steam = _Steam((_Listed("Game 7", app_id=3_000_000_007),))
        games = _games(steam)
        await games.running_rom_ids()
        steam.apps = ()

        assert await games.running_rom_ids() == frozenset()
        assert steam.readings == 2


class TestWhereNoReadingCouldBeTaken:
    async def test_no_reading_is_nothing_running(self):
        assert await _games(_Steam(None)).running_rom_ids() == frozenset()

    async def test_a_reader_that_raised_is_nothing_running_and_is_logged(self):
        debug: list[str] = []
        games = _games(_Steam(raises=RuntimeError("debugger gone")), debug)

        assert await games.running_rom_ids() == frozenset()
        assert any("debugger gone" in line for line in debug)

    async def test_a_reading_that_does_not_answer_in_time_is_nothing_running(self, monkeypatch):
        monkeypatch.setattr("services.running_games.READING_TIMEOUT_SECONDS", 0.01)
        steam = _Steam((_Listed("Game 7", app_id=3_000_000_007),))
        steam.hangs = True
        debug: list[str] = []
        games = _games(steam, debug)

        assert await asyncio.wait_for(games.running_rom_ids(), 1) == frozenset()
        assert any("TimeoutError" in line for line in debug)

    async def test_a_failed_read_of_the_roms_is_nothing_running_and_is_logged(self):
        debug: list[str] = []
        games = _games(_Steam((_Listed("Game 7", app_id=3_000_000_007),)), debug)

        def broken_roms(_app_ids: set[int]) -> frozenset[int]:
            raise RuntimeError("database is locked")

        games._roms_of = broken_roms  # type: ignore[method-assign]

        assert await games.running_rom_ids() == frozenset()
        assert any("database is locked" in line for line in debug)
