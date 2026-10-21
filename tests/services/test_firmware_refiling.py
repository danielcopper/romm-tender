"""Tests for services/firmware/refiling.py — older download records and overrides moved to the platform RomM states."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.bios_file import BiosFile
from domain.firmware_platforms import library_firmware
from services.firmware.refiling import FirmwareRefiling, FirmwareRefilingConfig

# RomM's PlayStation platform, whose server folder is named ``ps``.
_PLATFORMS: list[dict[str, Any]] = [
    {
        "id": 1,
        "slug": "psx",
        "fs_slug": "ps",
        "firmware": [{"id": 10, "file_name": "scph5501.bin", "platform_id": 1}],
    },
    {"id": 2, "slug": "gba", "fs_slug": "gba", "firmware": []},
    {
        "id": 3,
        "slug": "neogeo",
        "fs_slug": "neogeo",
        "firmware": [{"id": 30, "file_name": "neogeo.zip", "platform_id": 3}],
    },
    {"id": 4, "slug": "arcade", "fs_slug": "arcade", "firmware": []},
    {"id": 5, "slug": "segacd", "fs_slug": "scd", "firmware": []},
]


def _record(
    platform_slug: str,
    *,
    path: str = "/bios/scph5501.bin",
    firmware_id: int | None = 10,
    file_name: str = "scph5501.bin",
) -> BiosFile:
    return BiosFile.mark_downloaded(
        platform_slug=platform_slug,
        file_name=file_name,
        file_path=path,
        downloaded_at="2026-01-01T00:00:00+00:00",
        firmware_id=firmware_id,
    )


class _FailingPersister(FakeSettingsPersister):
    def save_settings(self) -> None:
        raise OSError("disk full")


class _Harness:
    def __init__(
        self,
        *records: BiosFile,
        overrides: dict[str, str] | None = None,
        persister: FakeSettingsPersister | None = None,
    ) -> None:
        self.uow = FakeUnitOfWork()
        for record in records:
            self.uow.bios_files.save(record)
        self.settings: dict[str, Any] = {"platform_cores": dict(overrides or {})}
        self.persister = persister if persister is not None else FakeSettingsPersister()
        self.refiling = FirmwareRefiling(
            config=FirmwareRefilingConfig(
                uow_factory=FakeUnitOfWorkFactory(self.uow),
                settings=self.settings,
                settings_persister=self.persister,
                logger=logging.getLogger("test"),
            )
        )

    def refile(self) -> None:
        self.refiling.do_refile(_PLATFORMS, library_firmware(_PLATFORMS))

    def records(self) -> set[tuple[str, str, str]]:
        return {(r.platform_slug, r.file_name, r.file_path) for r in self.uow.bios_files.iter_all()}


class TestDownloadRecords:
    def test_a_record_under_the_server_folder_name_moves_to_its_platform(self):
        harness = _Harness(_record("ps"))

        harness.refile()

        assert harness.records() == {("psx", "scph5501.bin", "/bios/scph5501.bin")}

    def test_a_record_under_a_shared_folder_moves_by_the_file_its_id_names(self):
        """RomM's ``bios_from_user/{platform}`` structure put every record under one folder name."""
        harness = _Harness(_record("bios_from_user"))

        harness.refile()

        assert harness.records() == {("psx", "scph5501.bin", "/bios/scph5501.bin")}

    def test_a_record_moves_everything_it_holds_with_it(self):
        harness = _Harness(_record("ps"))

        harness.refile()

        moved = harness.uow.bios_files.get("psx", "scph5501.bin")
        assert moved is not None
        assert (moved.downloaded_at, moved.firmware_id) == ("2026-01-01T00:00:00+00:00", 10)

    def test_running_again_moves_nothing(self):
        harness = _Harness(_record("ps"))
        harness.refile()
        after_first = harness.records()

        harness.refile()

        assert harness.records() == after_first == {("psx", "scph5501.bin", "/bios/scph5501.bin")}

    def test_a_record_whose_place_is_taken_by_another_file_stays(self, caplog):
        harness = _Harness(_record("ps", path="/bios/old/scph5501.bin"), _record("psx", path="/bios/scph5501.bin"))

        with caplog.at_level(logging.WARNING):
            harness.refile()

        assert harness.records() == {
            ("ps", "scph5501.bin", "/bios/old/scph5501.bin"),
            ("psx", "scph5501.bin", "/bios/scph5501.bin"),
        }
        assert "psx already records another file of that name" in caplog.text

    def test_a_duplicate_naming_the_same_file_is_dropped(self):
        harness = _Harness(_record("ps"), _record("psx"))

        harness.refile()

        assert harness.records() == {("psx", "scph5501.bin", "/bios/scph5501.bin")}

    def test_a_record_under_a_listed_platform_stays_though_its_id_names_a_same_named_file_elsewhere(self):
        """``arcade`` is a platform of its own; the file id 30 now names is filed on ``neogeo``."""
        harness = _Harness(_record("arcade", path="/bios/neogeo.zip", firmware_id=30, file_name="neogeo.zip"))

        harness.refile()

        assert harness.records() == {("arcade", "neogeo.zip", "/bios/neogeo.zip")}

    def test_a_record_already_under_its_platform_is_left_alone(self):
        harness = _Harness(_record("psx"))

        harness.refile()

        assert harness.records() == {("psx", "scph5501.bin", "/bios/scph5501.bin")}


class TestPerPlatformEmulatorOverrides:
    def test_an_override_under_the_server_folder_name_moves_to_its_platform_and_is_saved(self):
        harness = _Harness(overrides={"ps": "SwanStation", "gba": "mGBA"})

        harness.refile()

        assert harness.settings["platform_cores"] == {"psx": "SwanStation", "gba": "mGBA"}
        assert harness.persister.save_count == 1

    def test_the_overrides_move_in_the_live_dict_itself(self):
        """A reader holding the dict — the per-platform override's own writer — sees the move, and keeps its writes."""
        harness = _Harness(overrides={"ps": "SwanStation"})
        live = harness.settings["platform_cores"]

        harness.refile()

        assert harness.settings["platform_cores"] is live
        assert live == {"psx": "SwanStation"}

    def test_an_override_stored_for_another_platform_meanwhile_is_kept(self):
        harness = _Harness(overrides={"ps": "SwanStation"})
        live = harness.settings["platform_cores"]

        class _StoresOneMeanwhile(FakeSettingsPersister):
            def save_settings(self) -> None:
                live["gba"] = "mGBA"
                super().save_settings()

        harness.refiling._settings_persister = _StoresOneMeanwhile()
        harness.refile()

        assert live == {"psx": "SwanStation", "gba": "mGBA"}

    def test_a_save_that_fails_gives_every_move_back_and_raises(self):
        overrides = {"ps": "SwanStation", "scd": "Genesis Plus GX", "gba": "mGBA"}
        harness = _Harness(overrides=overrides, persister=_FailingPersister())

        with pytest.raises(OSError, match="disk full"):
            harness.refile()

        assert harness.settings["platform_cores"] == overrides

    def test_running_again_saves_nothing(self):
        harness = _Harness(overrides={"ps": "SwanStation"})
        harness.refile()

        harness.refile()

        assert harness.settings["platform_cores"] == {"psx": "SwanStation"}
        assert harness.persister.save_count == 1

    def test_an_override_stays_where_its_platform_has_its_own(self):
        harness = _Harness(overrides={"ps": "SwanStation", "psx": "Beetle PSX"})

        harness.refile()

        assert harness.settings["platform_cores"] == {"ps": "SwanStation", "psx": "Beetle PSX"}
        assert harness.persister.save_count == 0

    def test_settings_without_overrides_are_left_alone(self):
        harness = _Harness()
        del harness.settings["platform_cores"]

        harness.refile()

        assert "platform_cores" not in harness.settings
        assert harness.persister.save_count == 0
