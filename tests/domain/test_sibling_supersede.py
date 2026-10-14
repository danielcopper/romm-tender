"""Tests for domain/sibling_supersede.py — which installed siblings an install displaces, and how."""

from __future__ import annotations

from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.sibling_supersede import SiblingSupersede, supersede_siblings

_APP_ID = 700
_TARGET = "/roms/gba/game.gba"


def _rom(rom_id: int, app_id: int | None) -> Rom:
    return Rom.synced(
        rom_id=rom_id,
        platform_slug="gba",
        name=f"rom-{rom_id}",
        fs_name=f"rom-{rom_id}",
        shortcut_app_id=app_id,
        synced_at="2026-01-01T00:00:00",
    )


def _install(rom_id: int, file_path: str, rom_dir: str | None = None) -> RomInstall:
    return RomInstall.mark_installed(
        rom_id=rom_id,
        file_path=file_path,
        rom_dir=rom_dir,
        platform_slug="gba",
        system="gba",
        installed_at="2026-01-01T00:00:00",
    )


class TestADownloadDisplaces:
    def test_an_unbound_installed_sibling_is_removed(self):
        siblings = [(_rom(2, None), _install(2, "/roms/gba/old.gba"))]

        assert supersede_siblings(_APP_ID, siblings, None) == SiblingSupersede(removed=(2,), handed_over=())

    def test_a_sibling_bound_to_the_same_shortcut_is_removed(self):
        siblings = [(_rom(2, _APP_ID), _install(2, "/roms/gba/old.gba"))]

        assert supersede_siblings(_APP_ID, siblings, None).removed == (2,)

    def test_a_sibling_with_no_install_is_not_displaced(self):
        assert supersede_siblings(_APP_ID, [(_rom(2, None), None)], None) == SiblingSupersede((), ())

    def test_a_grandfathered_sibling_with_its_own_shortcut_is_not_displaced(self):
        siblings = [(_rom(2, 800), _install(2, "/roms/gba/old.gba"))]

        assert supersede_siblings(_APP_ID, siblings, None) == SiblingSupersede((), ())

    def test_an_unbound_version_still_displaces_an_unbound_sibling(self):
        siblings = [(_rom(2, None), _install(2, "/roms/gba/old.gba"))]

        assert supersede_siblings(None, siblings, None).removed == (2,)

    def test_a_download_hands_nothing_over_even_where_a_sibling_sits_at_its_path(self):
        siblings = [(_rom(2, None), _install(2, _TARGET))]

        assert supersede_siblings(_APP_ID, siblings, None) == SiblingSupersede(removed=(2,), handed_over=())


class TestAnAdoptionDisplaces:
    def test_a_sibling_recorded_at_the_adopted_file_hands_its_record_over(self):
        siblings = [(_rom(2, None), _install(2, _TARGET))]

        assert supersede_siblings(_APP_ID, siblings, _TARGET) == SiblingSupersede(removed=(), handed_over=(2,))

    def test_a_sibling_whose_folder_is_the_adopted_folder_hands_its_record_over(self):
        folder = "/roms/psx/Game (USA)"
        siblings = [(_rom(2, None), _install(2, f"{folder}/Game (USA).m3u", rom_dir=folder))]

        assert supersede_siblings(_APP_ID, siblings, folder).handed_over == (2,)

    def test_a_sibling_at_another_path_is_still_removed(self):
        siblings = [(_rom(2, None), _install(2, "/roms/gba/old.gba"))]

        assert supersede_siblings(_APP_ID, siblings, _TARGET) == SiblingSupersede(removed=(2,), handed_over=())

    def test_a_grandfathered_sibling_at_the_adopted_path_is_neither_removed_nor_handed_over(self):
        siblings = [(_rom(2, 800), _install(2, _TARGET))]

        assert supersede_siblings(_APP_ID, siblings, _TARGET) == SiblingSupersede((), ())

    def test_each_sibling_is_judged_by_its_own_path(self):
        siblings = [
            (_rom(2, None), _install(2, "/roms/gba/old.gba")),
            (_rom(3, None), _install(3, _TARGET)),
            (_rom(4, None), None),
        ]

        assert supersede_siblings(_APP_ID, siblings, _TARGET) == SiblingSupersede(removed=(2,), handed_over=(3,))
