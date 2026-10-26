"""Unit tests for the ``Rom`` aggregate."""

from __future__ import annotations

import pytest

from domain.rom import Rom


class TestSynced:
    def test_all_required_plus_igdb_sets_fields(self):
        rom = Rom.synced(
            rom_id=42,
            platform_slug="snes",
            name="Super Metroid",
            fs_name="Super Metroid.sfc",
            shortcut_app_id=123456789,
            synced_at="2026-05-28T10:00:00",
            igdb_id=1234,
        )
        assert rom.rom_id == 42
        assert rom.platform_slug == "snes"
        assert rom.name == "Super Metroid"
        assert rom.fs_name == "Super Metroid.sfc"
        assert rom.shortcut_app_id == 123456789
        assert rom.last_synced_at == "2026-05-28T10:00:00"
        assert rom.igdb_id == 1234

    def test_without_igdb_leaves_optional_ids_none(self):
        rom = Rom.synced(
            rom_id=7,
            platform_slug="gba",
            name="Metroid Fusion",
            fs_name="Metroid Fusion.gba",
            shortcut_app_id=987654321,
            synced_at="2026-05-28T11:00:00",
        )
        assert rom.igdb_id is None
        assert rom.cover_path is None
        assert rom.sgdb_id is None
        assert rom.ra_id is None

    def test_carries_fs_size_bytes(self):
        rom = Rom.synced(
            rom_id=42,
            platform_slug="snes",
            name="Super Metroid",
            fs_name="Super Metroid.sfc",
            shortcut_app_id=123456789,
            synced_at="2026-05-28T10:00:00",
            fs_size_bytes=3_145_728,
        )
        assert rom.fs_size_bytes == 3_145_728

    def test_fs_size_bytes_defaults_to_none_when_omitted(self):
        rom = Rom.synced(
            rom_id=7,
            platform_slug="gba",
            name="Metroid Fusion",
            fs_name="Metroid Fusion.gba",
            shortcut_app_id=987654321,
            synced_at="2026-05-28T11:00:00",
        )
        assert rom.fs_size_bytes is None

    def test_non_positive_rom_id_raises(self):
        with pytest.raises(ValueError, match="rom_id must be positive"):
            Rom.synced(
                rom_id=0,
                platform_slug="snes",
                name="x",
                fs_name="x.sfc",
                shortcut_app_id=1,
                synced_at="2026-05-28T10:00:00",
            )

    def test_empty_platform_slug_raises(self):
        with pytest.raises(ValueError, match="platform_slug is required"):
            Rom.synced(
                rom_id=1,
                platform_slug="",
                name="x",
                fs_name="x.sfc",
                shortcut_app_id=1,
                synced_at="2026-05-28T10:00:00",
            )


def _make_rom() -> Rom:
    return Rom.synced(
        rom_id=1,
        platform_slug="snes",
        name="x",
        fs_name="x.sfc",
        shortcut_app_id=1,
        synced_at="2026-05-28T10:00:00",
    )


class TestUpdateCoverPath:
    def test_sets_cover_path(self):
        rom = _make_rom()
        rom.update_cover_path("/covers/1.png")
        assert rom.cover_path == "/covers/1.png"


class TestAdoptCoverSource:
    def test_sets_cover_source_verbatim(self):
        # The RomM ?ts= cache-buster carries an unencoded space — stored as-is,
        # never normalised, so the opaque equality compare stays exact (#1386).
        rom = _make_rom()
        rom.adopt_cover_source("/assets/romm/resources/roms/1/cover/big.png?ts=2025-07-28 00:05:03")
        assert rom.cover_source == "/assets/romm/resources/roms/1/cover/big.png?ts=2025-07-28 00:05:03"

    def test_defaults_to_none(self):
        assert _make_rom().cover_source is None

    def test_empty_source_raises(self):
        rom = _make_rom()
        with pytest.raises(ValueError, match="cover_source"):
            rom.adopt_cover_source("")
        assert rom.cover_source is None

    def test_whitespace_only_source_raises(self):
        rom = _make_rom()
        with pytest.raises(ValueError, match="cover_source"):
            rom.adopt_cover_source("   ")
        assert rom.cover_source is None


class TestUnbindShortcut:
    def test_clears_app_id_and_keeps_row(self):
        rom = Rom.synced(
            rom_id=1,
            platform_slug="snes",
            name="Super Metroid",
            fs_name="Super Metroid.sfc",
            shortcut_app_id=123456789,
            synced_at="2026-05-28T10:00:00",
        )
        rom.update_cover_path("/covers/1.png")
        rom.assign_sgdb_id(7)

        rom.unbind_shortcut()

        assert rom.shortcut_app_id is None
        assert rom.rom_id == 1
        assert rom.platform_slug == "snes"
        assert rom.name == "Super Metroid"
        assert rom.cover_path == "/covers/1.png"
        assert rom.sgdb_id == 7


class TestBindShortcut:
    def test_sets_app_id_from_unbound(self):
        rom = Rom.synced(
            rom_id=2,
            platform_slug="snes",
            name="Super Metroid (Japan)",
            fs_name="Super Metroid (Japan).sfc",
            shortcut_app_id=None,
            synced_at="2026-05-28T10:00:00",
        )
        rom.bind_shortcut(123456789)
        assert rom.shortcut_app_id == 123456789

    def test_moves_binding_from_previous_app_id(self):
        rom = Rom.synced(
            rom_id=2,
            platform_slug="snes",
            name="Super Metroid (Japan)",
            fs_name="Super Metroid (Japan).sfc",
            shortcut_app_id=111,
            synced_at="2026-05-28T10:00:00",
        )
        rom.bind_shortcut(222)
        assert rom.shortcut_app_id == 222

    def test_zero_app_id_raises(self):
        rom = Rom.synced(
            rom_id=2,
            platform_slug="snes",
            name="x",
            fs_name="x.sfc",
            shortcut_app_id=None,
            synced_at="2026-05-28T10:00:00",
        )
        with pytest.raises(ValueError, match="positive"):
            rom.bind_shortcut(0)

    def test_negative_app_id_raises(self):
        rom = Rom.synced(
            rom_id=2,
            platform_slug="snes",
            name="x",
            fs_name="x.sfc",
            shortcut_app_id=None,
            synced_at="2026-05-28T10:00:00",
        )
        with pytest.raises(ValueError, match="positive"):
            rom.bind_shortcut(-5)


class TestAssignSgdbId:
    def test_sets_sgdb_id(self):
        rom = _make_rom()
        rom.assign_sgdb_id(7)
        assert rom.sgdb_id == 7


class TestChooseSgdbGame:
    def test_records_the_choice_beside_romms_id(self):
        rom = _make_rom()
        rom.assign_sgdb_id(7)

        rom.choose_sgdb_game(9)

        assert (rom.sgdb_id, rom.chosen_sgdb_id) == (7, 9)


class TestSupersedeChosenSgdbGame:
    def test_drops_the_choice_and_keeps_romms_id(self):
        rom = _make_rom()
        rom.choose_sgdb_game(9)
        rom.assign_sgdb_id(7)

        rom.supersede_chosen_sgdb_game()

        assert (rom.sgdb_id, rom.chosen_sgdb_id) == (7, None)


class TestRecordFsSizeBytes:
    def test_records_the_size(self):
        rom = _make_rom()

        rom.record_fs_size_bytes(3_145_728)

        assert rom.fs_size_bytes == 3_145_728

    @pytest.mark.parametrize("size", [0, -1])
    def test_a_size_of_zero_or_less_is_refused(self, size):
        rom = Rom.synced(
            rom_id=1,
            platform_slug="n64",
            name="Game",
            fs_name="game.z64",
            shortcut_app_id=None,
            synced_at="2026-01-01T00:00:00+00:00",
            fs_size_bytes=999_000,
        )

        with pytest.raises(ValueError, match="fs_size_bytes must be positive"):
            rom.record_fs_size_bytes(size)
        assert rom.fs_size_bytes == 999_000


class TestArtworkCheck:
    def test_a_check_records_when_it_started(self):
        rom = _make_rom()

        rom.begin_artwork_check(1700000000.5)

        assert rom.artwork_checked_at == 1700000000.5

    def test_a_withdrawn_check_puts_the_earlier_time_back(self):
        rom = _make_rom()
        rom.begin_artwork_check(100.0)
        rom.begin_artwork_check(200.0)

        rom.withdraw_artwork_check(100.0)

        assert rom.artwork_checked_at == 100.0

    def test_withdrawing_the_first_check_leaves_it_never_checked(self):
        rom = _make_rom()
        rom.begin_artwork_check(200.0)

        rom.withdraw_artwork_check(None)

        assert rom.artwork_checked_at is None


class TestAssignRaId:
    def test_sets_ra_id(self):
        rom = _make_rom()
        rom.assign_ra_id(9)
        assert rom.ra_id == 9


class TestPinEmulatorOverride:
    def test_sets_label(self):
        rom = _make_rom()
        rom.pin_emulator_override("PCSX ReARMed")
        assert rom.emulator_override == "PCSX ReARMed"

    def test_strips_surrounding_whitespace(self):
        rom = _make_rom()
        rom.pin_emulator_override("  PCSX ReARMed  ")
        assert rom.emulator_override == "PCSX ReARMed"

    def test_empty_label_raises(self):
        rom = _make_rom()
        with pytest.raises(ValueError, match="emulator_override label must not be empty"):
            rom.pin_emulator_override("")
        assert rom.emulator_override is None

    def test_whitespace_only_label_raises(self):
        rom = _make_rom()
        with pytest.raises(ValueError, match="emulator_override label must not be empty"):
            rom.pin_emulator_override("   ")
        assert rom.emulator_override is None


class TestClearEmulatorOverride:
    def test_clear_after_pin_sets_none(self):
        rom = _make_rom()
        rom.pin_emulator_override("PCSX ReARMed")
        rom.clear_emulator_override()
        assert rom.emulator_override is None

    def test_clear_when_already_none_stays_none(self):
        rom = _make_rom()
        assert rom.emulator_override is None
        rom.clear_emulator_override()
        assert rom.emulator_override is None


class TestPinSelectedDisc:
    def test_sets_filename(self):
        rom = _make_rom()
        rom.pin_selected_disc("FF7 (Disc 2).cue")
        assert rom.selected_disc == "FF7 (Disc 2).cue"

    def test_strips_surrounding_whitespace(self):
        rom = _make_rom()
        rom.pin_selected_disc("  FF7 (Disc 2).cue  ")
        assert rom.selected_disc == "FF7 (Disc 2).cue"

    def test_empty_filename_raises(self):
        rom = _make_rom()
        with pytest.raises(ValueError, match="selected_disc filename must not be empty"):
            rom.pin_selected_disc("")
        assert rom.selected_disc is None

    def test_whitespace_only_filename_raises(self):
        rom = _make_rom()
        with pytest.raises(ValueError, match="selected_disc filename must not be empty"):
            rom.pin_selected_disc("   ")
        assert rom.selected_disc is None

    def test_defaults_to_none(self):
        rom = _make_rom()
        assert rom.selected_disc is None


class TestClearSelectedDisc:
    def test_clear_after_pin_sets_none(self):
        rom = _make_rom()
        rom.pin_selected_disc("FF7 (Disc 2).cue")
        rom.clear_selected_disc()
        assert rom.selected_disc is None

    def test_clear_when_already_none_stays_none(self):
        rom = _make_rom()
        assert rom.selected_disc is None
        rom.clear_selected_disc()
        assert rom.selected_disc is None


class TestRecordDelivery:
    def test_a_synced_rom_names_no_route(self):
        assert _make_rom().delivered_through is None

    @pytest.mark.parametrize("route", ["platform", "collection"])
    def test_the_last_route_recorded_wins(self, route):
        rom = _make_rom()
        rom.record_delivery("collection" if route == "platform" else "platform")
        rom.record_delivery(route)
        assert rom.delivered_through == route


class TestGoneFromRomm:
    def test_a_synced_rom_carries_no_mark(self):
        assert _make_rom().gone_from_romm is None

    @pytest.mark.parametrize("evidence", ["not_found", "delisted"])
    def test_a_mark_records_its_evidence_and_a_clear_drops_it(self, evidence):
        rom = _make_rom()
        rom.mark_gone_from_romm(evidence)
        assert rom.gone_from_romm == evidence
        rom.clear_gone_from_romm()
        assert rom.gone_from_romm is None
