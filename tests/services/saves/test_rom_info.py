"""Tests for RomInfoService — per-ROM save path resolution and local save discovery."""

from __future__ import annotations

from typing import cast

from fakes.fake_save_location_reader import FakeSaveLocationReader

from domain.save_answer import SaveAnswer
from tests.services.saves._helpers import (
    _create_save,
    _install_rom,
    _seed_install,
    _seed_rom,
    make_service,
)


def _fake(svc) -> FakeSaveLocationReader:
    return cast("FakeSaveLocationReader", svc._rom_info._save_locations)


def _asked(svc) -> list[tuple[str, str, str | None]]:
    """Every question the save-location seam was put, as the fake recorded them."""
    return cast("FakeSaveLocationReader", svc._rom_info._save_locations).calls


def _seed_amiga_inside_content(svc) -> None:
    """Make the fake answer for Amiga the way PUAE really does for an ``.adf``.

    The save is inside the disk image, so there is no separate file. Left at the
    fake's per-game default these tests would assert a state the real machine
    never gives this content, and the page's own docstrings would contradict
    their assertions.
    """
    cast("FakeSaveLocationReader", svc._rom_info._save_locations).answer_with(
        "amiga",
        SaveAnswer(
            state="inside_content",
            unestablished=None,
            emulator="PUAE",
            directory=None,
            backing_directory=None,
            granularity=None,
            needs=(),
            components=(),
            caveats=("save-inside-content",),
            content_installed=True,
        ),
    )


class TestFindSaveFiles:
    """Tests for find_save_files."""

    def test_finds_srm(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _create_save(tmp_path, system="gba", rom_name="pokemon")

        result = svc._rom_info.find_save_files(42)

        assert len(result) == 1
        assert result[0]["filename"] == "pokemon.srm"
        assert result[0]["path"].endswith("pokemon.srm")

    def test_finds_rtc_companion(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path, file_name="emerald.gba")
        _create_save(tmp_path, system="gba", rom_name="emerald", ext=".srm")
        _create_save(tmp_path, system="gba", rom_name="emerald", ext=".rtc", content=b"\x02" * 16)

        result = svc._rom_info.find_save_files(42)

        filenames = sorted(f["filename"] for f in result)
        assert filenames == ["emerald.rtc", "emerald.srm"]

    def test_multi_disc_uses_m3u_name(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _seed_install(
            svc,
            55,
            file_path=str(tmp_path / "retrodeck" / "roms" / "psx" / "FF7" / "Final Fantasy VII.m3u"),
            system="psx",
            platform_slug="psx",
            rom_dir=str(tmp_path / "retrodeck" / "roms" / "psx" / "FF7"),
        )
        # With sort_by_content=True, saves land in saves_base/{content_dir} where
        # content_dir = last folder component of the ROM's directory = "FF7"
        saves_dir = tmp_path / "saves" / "FF7"
        saves_dir.mkdir(parents=True, exist_ok=True)
        (saves_dir / "Final Fantasy VII.srm").write_bytes(b"\x00" * 1024)

        result = svc._rom_info.find_save_files(55)

        assert any(f["filename"] == "Final Fantasy VII.srm" for f in result)

    def test_no_save_file_returns_empty(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path, rom_id=10, system="n64", file_name="zelda.z64")
        (tmp_path / "saves" / "n64").mkdir(parents=True, exist_ok=True)

        result = svc._rom_info.find_save_files(10)

        assert result == []

    def test_saves_dir_not_exists_returns_empty(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        result = svc._rom_info.find_save_files(42)

        assert result == []

    def test_rom_not_installed_returns_empty(self, tmp_path):
        svc, _ = make_service(tmp_path)

        result = svc._rom_info.find_save_files(999)

        assert result == []

    def test_the_save_question_is_keyed_by_the_system_not_the_romm_slug(self, tmp_path):
        """The save answer is asked for the normalized system, never the raw RomM slug (#899).

        ADR-0010's regression: the save file set used to be looked up with the
        raw ``platform_slug`` (``sega-saturn``), which had no entry, so a Saturn
        backup-RAM ``.bkr`` was never probed for. The lookup is now a live
        question to the resolver, and the same leak is possible — asking it about
        ``sega-saturn`` would get an answer about a system no emulator declares.

        Pinned two ways, because either alone is weak: the seam records the
        system it was asked with, and the file the answer names is found. Passing
        the slug fails the first assertion outright, and the second with it,
        since the fake keys its Saturn answer by system.
        """
        svc, _ = make_service(tmp_path)
        _seed_install(
            svc,
            70,
            file_path=str(tmp_path / "retrodeck" / "roms" / "saturn" / "Panzer Dragoon.cue"),
            system="saturn",
            platform_slug="sega-saturn",
        )
        # sort_by_content=True (RetroDECK default, no sort settings seeded) →
        # saves land in saves_base/{content_dir}, content_dir = "saturn".
        saves_dir = tmp_path / "saves" / "saturn"
        saves_dir.mkdir(parents=True, exist_ok=True)
        (saves_dir / "Panzer Dragoon.bkr").write_bytes(b"\x00" * 256)

        result = svc._rom_info.find_save_files(70)

        assert [system for system, _content, _label in _asked(svc)] == ["saturn"]
        assert [f["filename"] for f in result] == ["Panzer Dragoon.bkr"]

    def test_an_uninstalled_rom_is_still_asked_about_the_path_it_would_occupy(self, tmp_path):
        """A library row carries ``fs_name``, so the extension the answer turns on is known.

        Omitting the content path would ask a different question and return an
        answer that looks like this ROM's. Nothing is probed for either way — an
        uninstalled ROM pairs its names with no directory.
        """
        svc, _ = make_service(tmp_path)
        _seed_amiga_inside_content(svc)
        # The slug DIFFERS from its system, so a site that passes the raw RomM
        # slug fails here rather than hiding behind an identity map — the same
        # ADR-0010 leak the installed site guards, on the other path.
        _seed_rom(svc, 80, platform_slug="commodore-amiga", fs_name="Turrican.adf")

        answer = svc._rom_info.save_answer(80)

        assert _asked(svc) == [("amiga", str(tmp_path / "retrodeck" / "roms" / "amiga" / "Turrican.adf"), None)]
        assert answer.state == "inside_content"
        # ...and the answer says its names are a prediction, so a page can word
        # them as "would use" rather than rendering save files for a game the
        # user has not installed.
        assert answer.content_installed is False
        assert svc._rom_info.synced_save_names(80) == ([], None)

    def test_a_rom_the_library_does_not_hold_asks_nothing_and_refuses(self, tmp_path):
        # No row, so no name and no extension: there is no question to put, and
        # a guess is exactly what the retired extension table was.
        svc, _ = make_service(tmp_path)

        answer = svc._rom_info.save_answer(999)

        assert _asked(svc) == []
        assert answer.state == "unestablished"
        assert answer.syncable is False

    def test_the_question_carries_the_roms_own_content_path(self, tmp_path):
        """The answer turns on the content file's extension, so the real path goes out.

        PUAE answers ``save-inside-content`` for an Amiga ``.adf`` and nothing at
        all for an ``.hdf``; a synthetic stem would ask about neither.
        """
        svc, _ = make_service(tmp_path)
        _seed_amiga_inside_content(svc)
        content = str(tmp_path / "retrodeck" / "roms" / "amiga" / "Turrican.adf")
        _seed_install(svc, 71, file_path=content, system="amiga", platform_slug="commodore-amiga")

        answer = svc._rom_info.save_answer(71)

        assert _asked(svc) == [("amiga", content, None)]
        assert answer.state == "inside_content"
        assert answer.content_installed is True


class TestGetRomSaveInfo:
    """Tests for get_rom_save_info."""

    def test_returns_info_for_installed_rom(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["system"] == "gba"
        assert result["rom_name"] == "pokemon"
        assert result["saves_dir"].endswith("saves/gba")

    def test_returns_none_for_missing_rom(self, tmp_path):
        svc, _ = make_service(tmp_path)

        result = svc._rom_info.get_rom_save_info(999)

        assert result is None

    def test_returns_none_for_empty_system(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _seed_install(svc, 42, file_path="/some/path.gba", system="", platform_slug="")

        result = svc._rom_info.get_rom_save_info(42)

        assert result is None

    def test_the_directory_is_the_resolvers_answer(self, tmp_path):
        # Nothing is joined onto it and nothing is computed beside it: a core
        # that keeps its saves in a subfolder of its own is answered so.
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        answer = SaveAnswer(
            state="per_game_files",
            unestablished=None,
            emulator="Opera",
            directory="/saves/3do/opera/per_game",
            backing_directory=None,
            granularity="per-game-file",
            needs=(),
            components=(),
            caveats=(),
            content_installed=True,
            root_kind="savefile_directory",
        )
        _fake(svc).answer_with("gba", answer)

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"] == "/saves/3do/opera/per_game"
        assert result["save_answer"].emulator == "Opera"

    def test_no_resolved_placement_is_no_directory_and_never_a_guess(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _fake(svc).refuse("gba")

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"] is None
        assert svc._rom_info.synced_save_names(42) == ([], None)

    def test_a_reading_the_caller_holds_is_used_rather_than_a_second(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        held = svc._rom_info.save_answer(42)
        asked_before = len(_asked(svc))

        result = svc._rom_info.get_rom_save_info(42, save_answer=held)

        assert result is not None
        assert result["save_answer"] is held
        assert len(_asked(svc)) == asked_before

    def test_a_save_beside_the_content_is_located_and_never_synced(self, tmp_path):
        svc, _ = make_service(tmp_path, save_locations=FakeSaveLocationReader(beside_content=True))
        _install_rom(svc, tmp_path)

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"] == str(tmp_path / "retrodeck" / "roms" / "gba")
        assert svc._rom_info.synced_save_names(42) == ([], None)

    def test_is_content_installed_asks_the_resolver_nothing(self, tmp_path):
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        assert svc._rom_info.is_content_installed(42) is True
        assert svc._rom_info.is_content_installed(999) is False
        assert _asked(svc) == []
