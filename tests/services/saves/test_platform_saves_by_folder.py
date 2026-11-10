"""A platform's save files are its own games' saves, found by the folder RomM keeps it in (#2452 D9, D11, D17)."""

from __future__ import annotations

import pytest

from domain.rom import Rom
from domain.rom_install import RomInstall
from tests.services.saves._helpers import _create_save, _uow, make_service


def _install(svc, tmp_path, rom_id: int, folder: str | None, file_name: str) -> None:
    """An installed ``snes`` game on the platform RomM keeps in *folder*; ``None`` is one no sync recorded it for."""
    with _uow(svc) as uow:
        uow.roms.save(
            Rom.synced(
                rom_id=rom_id,
                platform_slug="snes",
                platform_fs_slug=folder,
                name=f"rom-{rom_id}",
                fs_name=file_name,
                shortcut_app_id=rom_id,
                synced_at="2026-01-01T00:00:00",
            )
        )
    with _uow(svc) as uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=rom_id,
                file_path=str(tmp_path / "retrodeck" / "roms" / "snes" / file_name),
                rom_dir=None,
                platform_slug="snes",
                system="snes",
                installed_at="2026-01-01T00:00:00",
            )
        )


@pytest.fixture
def two_platforms_of_one_slug(tmp_path):
    svc, _ = make_service(tmp_path)
    _install(svc, tmp_path, 1, "snes", "mario.sfc")
    _install(svc, tmp_path, 2, "snes-hacks", "hack.sfc")
    _install(svc, tmp_path, 3, None, "lost.sfc")
    saves = {name: _create_save(tmp_path, system="snes", rom_name=name) for name in ("mario", "hack", "lost")}
    return svc, saves


class TestAPlatformsSavesAreItsOwnGames:
    @pytest.mark.asyncio
    async def test_each_platform_of_a_slug_counts_its_own_games_saves(self, two_platforms_of_one_slug):
        svc, _ = two_platforms_of_one_slug

        assert (await svc.count_platform_saves("snes"))["count"] == 1
        assert (await svc.count_platform_saves("snes-hacks"))["count"] == 1

    @pytest.mark.asyncio
    async def test_deleting_one_platforms_saves_leaves_the_other_and_a_game_without_a_folder(
        self, two_platforms_of_one_slug
    ):
        svc, saves = two_platforms_of_one_slug

        result = await svc.delete_platform_saves("snes-hacks")

        assert result["deleted_count"] == 1
        assert not saves["hack"].exists()
        assert saves["mario"].exists()
        assert saves["lost"].exists()
