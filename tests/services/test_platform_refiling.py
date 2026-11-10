"""Tests for services/platform_refiling.py — older download records and picks moved to their platform's fs_slug."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.bios_file import BiosFile
from services.platform_refiling import PlatformRefiling, PlatformRefilingConfig

# RomM keeps PlayStation in ``ps``; two platforms share the slug ``gamecube``;
# ``snes`` is a slug and a folder at once, beside ``snes-hacks`` of the same slug.
_PLATFORMS: list[dict[str, Any]] = [
    {
        "id": 1,
        "slug": "psx",
        "fs_slug": "ps",
        "firmware": [{"id": 10, "file_name": "scph5501.bin", "platform_id": 1}],
    },
    {"id": 2, "slug": "gamecube", "fs_slug": "gc", "firmware": []},
    {"id": 3, "slug": "gamecube", "fs_slug": "ngc", "firmware": []},
    {"id": 4, "slug": "snes", "fs_slug": "snes", "firmware": []},
    {"id": 5, "slug": "snes", "fs_slug": "snes-hacks", "firmware": []},
]


def _record(
    folder: str,
    *,
    path: str = "/bios/scph5501.bin",
    firmware_id: int | None = 10,
    file_name: str = "scph5501.bin",
) -> BiosFile:
    return BiosFile.mark_downloaded(
        platform_folder=folder,
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
        picks: dict[str, Any] | None = None,
        persister: FakeSettingsPersister | None = None,
    ) -> None:
        self.uow = FakeUnitOfWork()
        for record in records:
            self.uow.bios_files.save(record)
        self.settings: dict[str, Any] = {"platform_cores": dict(picks or {})}
        self.persister = persister if persister is not None else FakeSettingsPersister()
        self.refiling = PlatformRefiling(
            config=PlatformRefilingConfig(
                uow_factory=FakeUnitOfWorkFactory(self.uow),
                settings=self.settings,
                settings_persister=self.persister,
                logger=logging.getLogger("test"),
            )
        )

    def refile(self) -> None:
        self.refiling.do_refile(_PLATFORMS)

    def records(self) -> set[tuple[str, str, str]]:
        return {(r.platform_folder, r.file_name, r.file_path) for r in self.uow.bios_files.iter_all()}


class TestDownloadRecords:
    def test_a_record_under_a_slug_moves_to_the_one_folder_with_that_slug(self):
        harness = _Harness(_record("psx", firmware_id=None))

        harness.refile()

        assert harness.records() == {("ps", "scph5501.bin", "/bios/scph5501.bin")}

    def test_a_record_under_a_server_folder_moves_by_the_file_its_id_names(self):
        harness = _Harness(_record("bios_from_user"))

        harness.refile()

        assert harness.records() == {("ps", "scph5501.bin", "/bios/scph5501.bin")}

    def test_a_record_moves_everything_it_holds_with_it(self):
        harness = _Harness(_record("psx"))

        harness.refile()

        moved = harness.uow.bios_files.get("ps", "scph5501.bin")
        assert moved is not None
        assert (moved.downloaded_at, moved.firmware_id) == ("2026-01-01T00:00:00+00:00", 10)

    def test_a_record_under_a_slug_two_folders_share_stays(self):
        harness = _Harness(_record("gamecube", path="/bios/IPL.bin", firmware_id=None, file_name="IPL.bin"))

        harness.refile()

        assert harness.records() == {("gamecube", "IPL.bin", "/bios/IPL.bin")}

    def test_a_record_under_a_listed_folder_stays_though_its_id_names_a_file_elsewhere(self):
        harness = _Harness(_record("gc"))

        harness.refile()

        assert harness.records() == {("gc", "scph5501.bin", "/bios/scph5501.bin")}

    def test_running_again_moves_nothing(self):
        harness = _Harness(_record("psx"))
        harness.refile()
        after_first = harness.records()

        harness.refile()

        assert harness.records() == after_first == {("ps", "scph5501.bin", "/bios/scph5501.bin")}

    def test_a_record_whose_place_is_taken_by_another_file_stays(self, caplog):
        harness = _Harness(_record("psx", path="/bios/old/scph5501.bin"), _record("ps", path="/bios/scph5501.bin"))

        with caplog.at_level(logging.WARNING):
            harness.refile()

        assert harness.records() == {
            ("psx", "scph5501.bin", "/bios/old/scph5501.bin"),
            ("ps", "scph5501.bin", "/bios/scph5501.bin"),
        }
        assert "ps already records another file of that name" in caplog.text

    def test_a_duplicate_naming_the_same_file_is_dropped(self):
        harness = _Harness(_record("psx"), _record("ps"))

        harness.refile()

        assert harness.records() == {("ps", "scph5501.bin", "/bios/scph5501.bin")}


class TestPerPlatformPicks:
    def test_a_pick_under_a_slug_goes_to_every_folder_of_that_slug_and_is_saved(self):
        harness = _Harness(picks={"gamecube": {"label": "Dolphin"}})

        harness.refile()

        assert harness.settings["platform_cores"] == {"gc": {"label": "Dolphin"}, "ngc": {"label": "Dolphin"}}
        assert harness.persister.save_count == 1

    def test_each_folder_gets_a_copy_of_its_own(self):
        harness = _Harness(picks={"gamecube": {"label": "Dolphin"}})

        harness.refile()

        picks = harness.settings["platform_cores"]
        assert picks["gc"] is not picks["ngc"]

    def test_a_folder_with_a_pick_of_its_own_keeps_it(self):
        harness = _Harness(picks={"gamecube": {"label": "Dolphin"}, "ngc": {"label": "Other"}})

        harness.refile()

        assert harness.settings["platform_cores"] == {"gc": {"label": "Dolphin"}, "ngc": {"label": "Other"}}

    def test_a_pick_under_a_listed_folder_stays_and_its_sibling_starts_at_the_default(self):
        """D18: ``snes`` is a listed folder, so its pick is its own; ``snes-hacks`` has none."""
        harness = _Harness(picks={"snes": {"label": "Snes9x"}})

        harness.refile()

        assert harness.settings["platform_cores"] == {"snes": {"label": "Snes9x"}}
        assert harness.persister.save_count == 0

    def test_the_picks_move_in_the_live_dict_itself(self):
        harness = _Harness(picks={"psx": {"label": "SwanStation"}})
        live = harness.settings["platform_cores"]

        harness.refile()

        assert harness.settings["platform_cores"] is live
        assert live == {"ps": {"label": "SwanStation"}}

    def test_a_pick_stored_meanwhile_is_kept(self):
        harness = _Harness(picks={"psx": {"label": "SwanStation"}})
        live = harness.settings["platform_cores"]

        class _StoresOneMeanwhile(FakeSettingsPersister):
            def save_settings(self) -> None:
                live["gc"] = {"label": "Dolphin"}
                super().save_settings()

        harness.refiling._settings_persister = _StoresOneMeanwhile()
        harness.refile()

        assert live == {"ps": {"label": "SwanStation"}, "gc": {"label": "Dolphin"}}

    def test_a_save_that_fails_gives_every_move_back_and_raises(self):
        picks = {"psx": {"label": "SwanStation"}, "gamecube": {"label": "Dolphin"}, "snes": {"label": "Snes9x"}}
        harness = _Harness(picks=picks, persister=_FailingPersister())

        with pytest.raises(OSError, match="disk full"):
            harness.refile()

        assert harness.settings["platform_cores"] == picks

    def test_running_again_saves_nothing(self):
        harness = _Harness(picks={"psx": {"label": "SwanStation"}})
        harness.refile()

        harness.refile()

        assert harness.settings["platform_cores"] == {"ps": {"label": "SwanStation"}}
        assert harness.persister.save_count == 1

    def test_settings_without_picks_are_left_alone(self):
        harness = _Harness()
        del harness.settings["platform_cores"]

        harness.refile()

        assert "platform_cores" not in harness.settings
        assert harness.persister.save_count == 0
