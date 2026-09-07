"""Tests for RomInfoService — per-ROM save path resolution and local save discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fakes.fake_active_core_resolver import FakeActiveCoreResolver

from domain.save_answer import SaveAnswer

if TYPE_CHECKING:
    from fakes.fake_save_location_reader import FakeSaveLocationReader

from tests.services.saves._helpers import (
    _create_save,
    _install_rom,
    _seed_install,
    _seed_rom,
    _set_sort_settings,
    _set_sort_settings_previous,
    make_service,
)


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

    # ------------------------------------------------------------------
    # Regression tests for issue #238 — Rule 1: when a save-sort migration
    # is pending, prefer save_sort_settings_previous so sync reads the
    # layout RetroArch actually wrote to during the session that just
    # ended.
    # ------------------------------------------------------------------

    def test_get_rom_save_info_prefers_previous_sort_settings_when_migration_pending(self, tmp_path):
        """Pending migration: previous (OLD) sort settings override current (NEW) (#238)."""
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("mgba_libretro", "mGBA")),
            get_core_name=lambda core_so: "mGBA",
        )
        _install_rom(svc, tmp_path)
        # NEW layout (what settings currently say):
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})
        # OLD layout (what the session actually wrote to):
        _set_sort_settings_previous(svc, {"sort_by_content": True, "sort_by_core": False})

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        # OLD layout: no /mGBA subdir.
        assert result["saves_dir"].endswith("saves/gba")
        assert "/mGBA" not in result["saves_dir"]

    def test_get_rom_save_info_uses_current_sort_settings_when_no_pending_migration(self, tmp_path):
        """No pending migration: use current sort settings (#238)."""
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("mgba_libretro", "mGBA")),
            get_core_name=lambda core_so: "mGBA",
        )
        _install_rom(svc, tmp_path)
        # Only save_sort_settings is present — no pending migration marker at all.
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})
        assert svc._rom_info.pending_sort_settings() is None

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        # CURRENT layout: /mGBA subdir is appended because sort_by_core=True.
        assert result["saves_dir"].endswith("saves/gba/mGBA")

    def test_pending_sort_settings_rejects_empty_dict_half_state(self, tmp_path):
        """Empty-dict ``save_sort_settings_previous`` must NOT count as pending (#238 review).

        Freezes the contract: ``get_rom_save_info`` and ``is_save_sort_changed``
        must agree on what counts as pending. Before ``pending_sort_settings``
        was introduced, a literal empty dict at ``save_sort_settings_previous``
        would put the service in a half-state — ``get_rom_save_info`` would
        fall back to current settings (``{} or current``), but
        ``is_save_sort_changed`` would treat the same ``{}`` as pending
        (``is not None``). This test locks in the agreement.
        """
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("mgba_libretro", "mGBA")),
            get_core_name=lambda core_so: "mGBA",
        )
        _install_rom(svc, tmp_path)
        # Half-state input: empty previous, populated current (NEW).
        _set_sort_settings_previous(svc, {})
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})

        # Both call sites must agree there is NO pending migration.
        assert svc._rom_info.is_save_sort_changed() is False
        assert svc._rom_info.pending_sort_settings() is None

        result = svc._rom_info.get_rom_save_info(42)
        assert result is not None
        # Reads CURRENT settings (NEW layout), not the empty previous —
        # mGBA subdir is appended because sort_by_core=True.
        assert result["saves_dir"].endswith("saves/gba/mGBA")

    # ------------------------------------------------------------------
    # Regression tests for issue #232 — RomInfoService must resolve the
    # RetroArch ``corename`` via the .info parser when sort_by_core is
    # active, and must fall back with a warning when it cannot.
    # ------------------------------------------------------------------

    def test_default_sort_only_by_content_no_core_subdir(self, tmp_path):
        """sort_by_core=False (RetroDECK default) → no core subdir."""
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("mgba_libretro", "mGBA")),
            get_core_name=lambda core_so: "mGBA",
        )
        _install_rom(svc, tmp_path)
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": False})

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"].endswith("saves/gba")
        assert "/mGBA" not in result["saves_dir"]

    def test_sort_by_core_appends_retroarch_corename(self, tmp_path):
        """sort_by_core=True with resolvable corename → saves_dir ends in /{system}/{corename}."""
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("mgba_libretro", "mGBA")),
            get_core_name=lambda core_so: "mGBA",
        )
        _install_rom(svc, tmp_path)
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"].endswith("saves/gba/mGBA")

    def test_save_dir_differs_by_per_game_override(self, tmp_path):
        """RESULT-FLIP: two gba ROMs, one pinned to gpSP + one default, get different save dirs.

        With sort_by_core active, the per-core save subdir is named after the
        resolved core's ``.info`` corename. The override ROM resolves to gpSP →
        ``/gpSP`` subdir; the NULL ROM resolves to the default mGBA → ``/mGBA``.
        The save dir flips on the per-game override alone — proving the per-core
        layout keys off the same core the ROM launches with, not a platform default.
        """
        active_core = FakeActiveCoreResolver(
            default=("mgba_libretro", "mGBA"),
            per_rom={42: ("gpsp_libretro", "gpSP")},
        )
        svc, _ = make_service(
            tmp_path,
            active_core=active_core,
            # The .info corename mirrors the ES-DE label here for test simplicity.
            get_core_name=lambda core_so: "gpSP" if core_so == "gpsp_libretro" else "mGBA",
        )
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pinned.gba")
        _install_rom(svc, tmp_path, rom_id=43, system="gba", file_name="default.gba")
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})

        pinned = svc._rom_info.get_rom_save_info(42)
        plain = svc._rom_info.get_rom_save_info(43)

        assert pinned is not None
        assert plain is not None
        assert pinned["saves_dir"].endswith("saves/gba/gpSP")
        assert plain["saves_dir"].endswith("saves/gba/mGBA")
        # Each ROM's save dir was resolved by its own rom_id.
        assert active_core.calls == [42, 43]

    def test_sort_by_core_uses_corename_not_es_de_label(self, tmp_path):
        """The RetroArch .info corename (``Snes9x``) must be used, not the ES-DE label (``Snes9x - Current``)."""
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("snes9x_libretro", "Snes9x - Current")),
            get_core_name=lambda core_so: "Snes9x",
        )
        _install_rom(svc, tmp_path, system="snes", file_name="mario.sfc")
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})

        result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"].endswith("saves/snes/Snes9x")
        assert "Snes9x - Current" not in result["saves_dir"]

    def test_sort_by_core_falls_back_when_corename_none(self, tmp_path, caplog):
        """sort_by_core=True but corename unresolvable → warn + fall back to parent dir.

        The warning must include ``core_so=mgba_libretro`` so a user can identify
        which ``.info`` file the parser failed on.
        """
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("mgba_libretro", "mGBA")),
            get_core_name=lambda core_so: None,  # .info unreadable / field missing
        )
        _install_rom(svc, tmp_path)
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})

        with caplog.at_level("WARNING"):
            result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"].endswith("saves/gba")
        assert "/mGBA" not in result["saves_dir"]
        warnings = [rec.message for rec in caplog.records if "unable to resolve RetroArch corename" in rec.message]
        assert warnings, "expected fallback warning"
        assert "core_so=mgba_libretro" in warnings[0]

    def test_sort_by_core_falls_back_when_get_core_name_returns_none(self, tmp_path, caplog):
        """``get_core_name`` returns ``None`` (.info unreadable) → warns and falls back.

        the resolver succeeded so ``core_so`` is identified in the
        diagnostic log to help the user locate the unreadable .info file.
        """
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("mgba_libretro", "mGBA")),
            get_core_name=lambda core_so: None,
        )
        _install_rom(svc, tmp_path)
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})

        with caplog.at_level("WARNING"):
            result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"].endswith("saves/gba")
        warnings = [rec.message for rec in caplog.records if "unable to resolve RetroArch corename" in rec.message]
        assert warnings, "expected fallback warning"
        assert "core_so=mgba_libretro" in warnings[0]

    def test_sort_by_core_falls_back_when_active_core_unresolved(self, tmp_path, caplog):
        """sort_by_core=True but the resolver returns (None, None) → warn + fall back.

        When ES-DE cannot determine the active core, ``core_so`` is ``None`` and
        the log records ``core_so=unresolved``.
        """
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=(None, None)),
            get_core_name=lambda core_so: "mGBA",
        )
        _install_rom(svc, tmp_path)
        _set_sort_settings(svc, {"sort_by_content": True, "sort_by_core": True})

        with caplog.at_level("WARNING"):
            result = svc._rom_info.get_rom_save_info(42)

        assert result is not None
        assert result["saves_dir"].endswith("saves/gba")
        warnings = [rec.message for rec in caplog.records if "unable to resolve RetroArch corename" in rec.message]
        assert warnings, "expected fallback warning"
        assert "core_so=unresolved" in warnings[0]

    def test_resolve_retroarch_corename_happy_path(self, tmp_path):
        """Direct test of the helper: both seams resolve → (corename, core_so) tuple returned."""
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("snes9x_libretro", "Snes9x - Current")),
            get_core_name=lambda core_so: "Snes9x",
        )
        assert svc._rom_info.resolve_retroarch_corename(42) == ("Snes9x", "snes9x_libretro")

    def test_resolve_retroarch_corename_returns_none_tuple_when_core_so_empty(self, tmp_path):
        """The resolver returns (None, None) → helper returns (None, None)."""
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=(None, None)),
            get_core_name=lambda core_so: "Snes9x",
        )
        assert svc._rom_info.resolve_retroarch_corename(42) == (None, None)

    def test_resolve_retroarch_corename_preserves_core_so_when_corename_empty(self, tmp_path):
        """Empty corename with resolved core_so → (None, core_so).

        The core_so is preserved in the second element so the caller can log
        which ``.info`` file failed diagnostically. The first element is None
        because the empty-string corename is treated as "no usable value".
        """
        svc, _ = make_service(
            tmp_path,
            active_core=FakeActiveCoreResolver(default=("snes9x_libretro", "Snes9x")),
            get_core_name=lambda core_so: "",
        )
        assert svc._rom_info.resolve_retroarch_corename(42) == (None, "snes9x_libretro")
