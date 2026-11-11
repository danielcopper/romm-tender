"""Tests for services/platform_refiling.py — older download records and picks moved to their platform's fs_slug."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.bios_file import BiosFile
from services.platform_picks import PlatformPicks, PlatformPicksConfig
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
    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error if error is not None else OSError("disk full")

    def save_settings(self) -> None:
        raise self.error


class _Keeper:
    """Keeps each listing it is handed; raises *error* instead where one is set."""

    def __init__(self) -> None:
        self.listings: list[list[Any]] = []
        self.error: Exception | None = None
        self.on_keep: Any = None

    def keep_listing(self, platforms: Any) -> None:
        if self.on_keep is not None:
            self.on_keep()
        if self.error is not None:
            raise self.error
        self.listings.append(list(platforms))


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
        self.picks_lock = threading.Lock()
        self.keeper = _Keeper()
        self.debug: list[str] = []
        self.refiling = PlatformRefiling(
            config=PlatformRefilingConfig(
                uow_factory=FakeUnitOfWorkFactory(self.uow),
                platform_picks=PlatformPicks(
                    config=PlatformPicksConfig(
                        settings=self.settings, settings_persister=self.persister, lock=self.picks_lock
                    )
                ),
                refile_lock=threading.Lock(),
                listing_keeper=lambda: self.keeper,
                logger=logging.getLogger("test"),
                log_debug=lambda msg: self.debug.append(msg),
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

    def test_a_record_under_a_slug_two_folders_share_moves_by_the_file_its_id_names(self):
        harness = _Harness(_record("gamecube", path="/bios/IPL.bin", firmware_id=20, file_name="IPL.bin"))
        listing = [
            {**platform, "firmware": [{"id": 20, "file_name": "IPL.bin", "platform_id": 3}]}
            if platform["fs_slug"] == "ngc"
            else platform
            for platform in _PLATFORMS
        ]

        harness.refiling.do_refile(listing)

        assert harness.records() == {("ngc", "IPL.bin", "/bios/IPL.bin")}

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

    def test_the_picks_are_replaced_whole_and_the_dict_in_place_is_left_as_it_was(self):
        harness = _Harness(picks={"psx": {"label": "SwanStation"}})
        before = harness.settings["platform_cores"]

        harness.refile()

        assert harness.settings["platform_cores"] == {"ps": {"label": "SwanStation"}}
        assert before == {"psx": {"label": "SwanStation"}}

    def test_a_save_of_the_whole_settings_iterating_them_meanwhile_does_not_fail(self):
        """A save on another thread may be walking the settings while a refile replaces the picks."""
        harness = _Harness(picks={"psx": {"label": "SwanStation"}, "gamecube": {"label": "Dolphin"}})
        walking_settings = iter(harness.settings)
        walking_picks = iter(harness.settings["platform_cores"])
        next(walking_settings)
        next(walking_picks)

        harness.refile()

        assert list(walking_settings) == []
        assert list(walking_picks) == ["gamecube"]

    def test_a_save_that_fails_gives_every_move_back(self):
        picks = {"psx": {"label": "SwanStation"}, "gamecube": {"label": "Dolphin"}, "snes": {"label": "Snes9x"}}
        harness = _Harness(picks=picks, persister=_FailingPersister())

        harness.refile()

        assert harness.settings["platform_cores"] == picks

    def test_any_failure_gives_every_move_back(self):
        picks = {"psx": {"label": "SwanStation"}}
        harness = _Harness(picks=picks, persister=_FailingPersister(RuntimeError("not serialisable")))

        harness.refile()

        assert harness.settings["platform_cores"] == picks

    def test_a_pick_waits_for_a_writer_that_holds_the_lock(self):
        harness = _Harness(picks={"psx": {"label": "SwanStation"}})

        with harness.picks_lock:
            worker = threading.Thread(target=harness.refile)
            worker.start()
            worker.join(timeout=0.2)
            assert worker.is_alive()
            assert harness.settings["platform_cores"] == {"psx": {"label": "SwanStation"}}
        worker.join(timeout=5)

        assert harness.settings["platform_cores"] == {"ps": {"label": "SwanStation"}}

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


class TestFailures:
    """``do_refile`` never raises: each half fails on its own, logged here, and the other still runs."""

    def test_a_records_half_that_fails_leaves_the_picks_half_running(self, caplog, monkeypatch):
        harness = _Harness(_record("psx"), picks={"psx": {"label": "SwanStation"}})

        def _fail() -> list[BiosFile]:
            raise OSError("db locked")

        monkeypatch.setattr(harness.uow.bios_files, "iter_all", _fail)
        with caplog.at_level(logging.WARNING):
            harness.refile()

        assert harness.settings["platform_cores"] == {"ps": {"label": "SwanStation"}}
        [failure] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert (
            failure.getMessage() == "Failed to move older BIOS download records to their platforms: OSError: db locked"
        )
        assert failure.exc_info
        assert failure.exc_info[0] is OSError

    def test_a_picks_half_that_fails_leaves_the_records_moved(self, caplog):
        harness = _Harness(_record("psx"), picks={"psx": {"label": "SwanStation"}}, persister=_FailingPersister())

        with caplog.at_level(logging.WARNING):
            harness.refile()

        assert harness.records() == {("ps", "scph5501.bin", "/bios/scph5501.bin")}
        [failure] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert failure.getMessage() == "Failed to move older per-platform picks to their platforms: OSError: disk full"
        assert failure.exc_info
        assert failure.exc_info[0] is OSError

    def test_a_records_half_that_fails_midway_moves_none(self, monkeypatch):
        harness = _Harness(_record("psx"), _record("bios_from_user", path="/bios/other.bin", file_name="other.bin"))
        before = harness.records()
        save = harness.uow.bios_files.save
        saves: list[BiosFile] = []

        def _second_save_fails(record: BiosFile) -> None:
            saves.append(record)
            if len(saves) == 2:
                raise OSError("disk full")
            save(record)

        monkeypatch.setattr(harness.uow.bios_files, "save", _second_save_fails)
        harness.refiling.do_refile([*_PLATFORMS, _other_platform()])

        assert len(saves) == 2
        assert harness.records() == before

    def test_a_listing_it_cannot_read_moves_nothing_and_says_so(self, caplog):
        harness = _Harness(_record("psx"))

        with caplog.at_level(logging.WARNING):
            harness.refiling.do_refile([None])  # type: ignore[list-item]

        assert harness.records() == {("psx", "scph5501.bin", "/bios/scph5501.bin")}
        assert harness.refiling.listed_folders() is None
        assert "Failed to read where older entries belong: AttributeError" in caplog.text

    def test_the_same_failure_again_is_logged_without_its_traceback(self, caplog):
        harness = _Harness(_record("psx"), persister=_FailingPersister())
        harness.settings["platform_cores"] = {"psx": {"label": "SwanStation"}}

        with caplog.at_level(logging.WARNING):
            harness.refile()
            harness.refile()

        failures = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert [bool(r.exc_info) for r in failures] == [True, False]

    def test_a_failure_with_the_same_words_raised_elsewhere_brings_its_traceback(self, caplog):
        harness = _Harness(_record("psx"), persister=_FailingPersister())
        harness.settings["platform_cores"] = {"psx": {"label": "SwanStation"}}

        def _fails_here() -> None:
            raise OSError("disk full")

        with caplog.at_level(logging.WARNING):
            harness.refile()
            harness.persister.save_settings = _fails_here  # type: ignore[method-assign]
            harness.refile()

        failures = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert [r.getMessage() for r in failures] == [
            "Failed to move older per-platform picks to their platforms: OSError: disk full"
        ] * 2
        assert [bool(r.exc_info) for r in failures] == [True, True]

    def test_a_keeper_that_fails_leaves_the_moves_running(self, caplog):
        harness = _Harness(_record("psx"), picks={"psx": {"label": "SwanStation"}})
        harness.keeper.error = OSError("db locked")

        with caplog.at_level(logging.WARNING):
            harness.refile()

        assert harness.records() == {("ps", "scph5501.bin", "/bios/scph5501.bin")}
        assert harness.settings["platform_cores"] == {"ps": {"label": "SwanStation"}}
        assert "Failed to keep the listed platforms: OSError: db locked" in caplog.text


class TestListedPlatformsAreKept:
    def test_every_listing_handed_here_is_kept(self):
        harness = _Harness()

        harness.refile()
        harness.refile()

        assert harness.keeper.listings == [_PLATFORMS, _PLATFORMS]

    def test_the_listing_is_kept_before_anything_moves(self):
        harness = _Harness(_record("psx"), picks={"psx": {"label": "SwanStation"}})
        seen: list[tuple[set[tuple[str, str, str]], dict[str, Any]]] = []
        harness.keeper.on_keep = lambda: seen.append((harness.records(), dict(harness.settings["platform_cores"])))

        harness.refile()

        assert seen == [({("psx", "scph5501.bin", "/bios/scph5501.bin")}, {"psx": {"label": "SwanStation"}})]


class TestWhatItSays:
    def test_moved_records_are_counted(self, caplog):
        harness = _Harness(_record("psx"))

        with caplog.at_level(logging.INFO):
            harness.refile()

        assert "Moved 1 older BIOS download records to their platforms" in caplog.text

    def test_a_dropped_duplicate_is_no_move(self, caplog):
        harness = _Harness(_record("psx"), _record("ps"))

        with caplog.at_level(logging.INFO):
            harness.refile()

        assert harness.records() == {("ps", "scph5501.bin", "/bios/scph5501.bin")}
        assert "Moved" not in caplog.text
        assert "Dropped 1 older BIOS download records their platform already records the same file under" in (
            caplog.text
        )

    def test_moved_picks_name_where_each_went(self, caplog):
        harness = _Harness(picks={"gamecube": {"label": "Dolphin"}, "psx": {"label": "SwanStation"}})

        with caplog.at_level(logging.INFO):
            harness.refile()

        assert "Moved older per-platform picks to their platforms: gamecube → gc, ngc; psx → ps" in caplog.text

    def test_a_dropped_pick_is_said_apart(self, caplog):
        harness = _Harness(picks={"gamecube": {}, "gc": {}, "ngc": {}})

        with caplog.at_level(logging.INFO):
            harness.refile()

        assert "Dropped older per-platform picks every platform of their slug has its own of: gamecube" in caplog.text
        assert "Moved older per-platform picks" not in caplog.text

    def test_a_record_kept_for_a_taken_place_is_warned_once_then_said_to_the_debug_log(self, caplog):
        harness = _Harness(_record("psx", path="/bios/old/scph5501.bin"), _record("ps", path="/bios/scph5501.bin"))

        with caplog.at_level(logging.DEBUG):
            harness.refile()
            harness.refile()

        warned = [r for r in caplog.records if "already records another file of that name" in r.getMessage()]
        assert [r.levelno for r in warned] == [logging.WARNING]
        assert [m for m in harness.debug if "already records another file of that name" in m] == [
            "Kept the BIOS record scph5501.bin under psx: ps already records another file of that name"
        ]


class TestOneRefileAtATime:
    def test_two_readers_refiling_at_once_take_turns(self, monkeypatch):
        harness = _Harness(_record("psx"), picks={"psx": {"label": "SwanStation"}})
        inside = 0
        most = 0
        halves: list[str] = []

        def _counting(name, step):
            def run(listed):
                nonlocal inside, most
                inside += 1
                most = max(most, inside)
                halves.append(name)
                time.sleep(0.05)
                try:
                    return step(listed)
                finally:
                    inside -= 1

            return run

        monkeypatch.setattr(harness.refiling, "_refile_records", _counting("records", harness.refiling._refile_records))
        monkeypatch.setattr(harness.refiling, "_refile_picks", _counting("picks", harness.refiling._refile_picks))
        workers = [threading.Thread(target=harness.refile) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        assert not any(worker.is_alive() for worker in workers)
        assert halves == ["records", "picks", "records", "picks"]
        assert most == 1


class TestListedFolders:
    def test_none_before_any_listing(self):
        assert _Harness().refiling.listed_folders() is None

    def test_every_listed_folder_after_one_with_a_platform_without_a_slug(self):
        harness = _Harness()

        harness.refiling.do_refile([*_PLATFORMS, {"id": 9, "slug": None, "fs_slug": "arcade", "firmware": []}])

        assert harness.refiling.listed_folders() == frozenset({"ps", "gc", "ngc", "snes", "snes-hacks", "arcade"})


def _other_platform() -> dict[str, Any]:
    return {
        "id": 6,
        "slug": "other",
        "fs_slug": "other",
        "firmware": [{"id": 10, "file_name": "other.bin", "platform_id": 6}],
    }
