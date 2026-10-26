import asyncio
import concurrent.futures
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from unittest.mock import AsyncMock, MagicMock

import pytest
from _factories import (
    _make_conflict_rules,
    _make_prune_conflicts,
    _record_operations_at_lease,
    _refused_by_conflict_rule,
)
from _spawns import record_spawns
from fakes.fake_core_info_provider import FakeCoreInfoProvider, FakeSandboxLauncher
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_platform_systems import RETRODECK_SOURCE, FakePlatformSystems
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_save_location_reader import FakeSaveLocationReader
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeClock

from adapters.adoption_move import AdoptionMoveAdapter
from adapters.download_file import DownloadFileAdapter
from adapters.rom_files import RomFileAdapter
from domain.platform_system import NO_SYSTEM, SWITCHED_OFF, PlatformSystem
from domain.refusal import DomainRefused
from domain.retrodeck_folders import GAME_DOWNLOAD, FolderRefused, finding_refusal, switched_off
from domain.rom import Rom
from domain.rom_adoption import TargetOccupied
from domain.rom_file import NoFileOnRomm
from domain.rom_files import TMP_EXT, ZIP_TMP_EXT
from domain.rom_install import RomInstall
from domain.version_metadata import VersionMetadata
from lib.errors import NotInstalled, Refused, RommNotFoundError
from lib.prune_conflicts import PruneConflicts
from services.active_core_resolver import ActiveCoreResolver, ActiveCoreResolverConfig
from services.downloads import (
    DownloadQueue,
    DownloadQueueConfig,
    DownloadService,
    DownloadServiceConfig,
    DownloadsStateBox,
    DownloadTransfer,
    DownloadTransferConfig,
    DownloadUnpacker,
    DownloadUnpackerConfig,
)
from services.downloads._names import safe_extract_dir_name
from services.downloads._state import DownloadControl
from services.rom_adoption import RomAdoptionService, RomAdoptionServiceConfig
from services.rom_adoption.renamer import AdoptionIncomplete
from services.rom_install_recorder import RomInstallRecorder, RomInstallRecorderConfig
from services.rom_removal import RomRemovalService, RomRemovalServiceConfig, UninstallIncomplete


def _seed_rom(uow: FakeUnitOfWork, rom_id: int, *, platform_slug: str = "n64") -> None:
    """Seed a synced ``Rom`` so a ``RomInstall`` save passes the FK check at commit."""
    uow.roms.save(
        Rom.synced(
            rom_id=rom_id,
            platform_slug=platform_slug,
            name=f"Game {rom_id}",
            fs_name=f"game_{rom_id}.z64",
            shortcut_app_id=1000 + rom_id,
            synced_at="2026-01-01T00:00:00+00:00",
        )
    )


def _seed_install(
    uow: FakeUnitOfWork,
    rom_id: int,
    *,
    file_path: str,
    rom_dir: str | None = None,
    system: str = "n64",
) -> None:
    """Seed the FK-parent ``Rom`` THEN its ``RomInstall`` record, in one commit."""
    with uow:
        _seed_rom(uow, rom_id, platform_slug=system)
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=rom_id,
                file_path=file_path,
                rom_dir=rom_dir,
                platform_slug=system,
                system=system,
                installed_at="2026-01-01T00:00:00+00:00",
            )
        )


def _seed_group_member(
    uow: FakeUnitOfWork,
    rom_id: int,
    *,
    group_key: str | None,
    app_id: int | None,
    installed: bool,
    system: str = "n64",
) -> None:
    """Seed one ``Rom`` in a sibling group (controllable app_id + group key), optionally installed.

    Unlike ``_seed_rom`` this sets ``sibling_group_key`` and takes an explicit
    ``shortcut_app_id`` so a #1298 supersede test can build a real multi-version
    group (bound rep / unbound install / grandfathered separate shortcut).
    """
    with uow:
        uow.roms.save(
            Rom.synced(
                rom_id=rom_id,
                platform_slug=system,
                name=f"Game {rom_id}",
                fs_name=f"game_{rom_id}.z64",
                shortcut_app_id=app_id,
                synced_at="2026-01-01T00:00:00+00:00",
                version=VersionMetadata(sibling_group_key=group_key),
            )
        )
        if installed:
            uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=rom_id,
                    file_path=f"/roms/{system}/game_{rom_id}.z64",
                    rom_dir=None,
                    platform_slug=system,
                    system=system,
                    installed_at="2026-01-01T00:00:00+00:00",
                )
            )


def _hops_answering(answer: Any) -> AsyncMock:
    """An executor stand-in whose every hop answers *answer*, but for two questions about this machine.

    Those run inline: the platform's system, and whether a whole download
    already sits on disk — neither is something a stubbed ROM read can answer.
    """

    async def hop(_executor: Any, fn: Any, *args: Any) -> Any:
        inline = getattr(fn, "__name__", "") in {"_download_platform_io", "kept_download_complete"}
        return fn(*args) if inline else answer

    return AsyncMock(side_effect=hop)


def _admitted(downloads, rom_id: int) -> DownloadControl:
    """Register the download whose entry a test seeded for *rom_id*; answer the control it runs with.

    Only a start admits a download in production; a test that runs the
    transfer directly admits it here — without the claim a start takes — so
    the release at the transfer's end gives back what an admission
    registered.
    """
    control = DownloadControl()
    downloads.state.admit(
        rom_id,
        task=MagicMock(),
        reserved=downloads.state.reserved_bytes.get(rom_id, 0),
        control=control,
    )
    return control


@dataclass
class DownloadsHarness:
    """The download service and its parts, the two services it works with, and the seams a test drives.

    ``state``, ``queue``, ``transfer`` and ``unpacker`` are the parts the
    service was handed, built over one state box as the composition root
    builds them. ``uow`` is the one :class:`FakeUnitOfWork` every service's
    factory wraps; install records flow through it, and tests inspect
    ``uow.rom_installs`` after the service has run. ``loop``,
    ``download_file_store`` and ``retrodeck_folders`` are the ones the download
    parts were built with. ``core_info`` is the core-info fake the launch
    command is baked through, so a test can seed ``available_cores``.
    ``m3u_supported`` answers the ``m3u_support`` seam of the unpacker and the
    adoption service; a test that simulates a non-m3u platform (Switch/Xbox
    360) flips it to False.
    """

    service: DownloadService
    state: DownloadsStateBox
    queue: DownloadQueue
    transfer: DownloadTransfer
    unpacker: DownloadUnpacker
    removal: RomRemovalService
    adoption: RomAdoptionService
    uow: FakeUnitOfWork
    romm_api: MagicMock
    core_info: FakeCoreInfoProvider
    prune_conflicts: PruneConflicts
    loop: asyncio.AbstractEventLoop
    download_file_store: Any
    retrodeck_folders: FakeRetroDeckFolders
    m3u_supported: bool = True


class MakeDownloads(Protocol):
    def __call__(
        self,
        *,
        loop: Any = None,
        retrodeck_folders: Any = None,
        adoption_folders: Any = None,
        removal_folders: Any = None,
        download_file_store: Any = None,
        emit: Any = None,
        clock: Any = None,
        platform_systems: Any = None,
        target_gate: Any = None,
        rom_remover: Any = None,
        migration_pending: bool = False,
    ) -> DownloadsHarness: ...


@pytest.fixture
async def make_downloads(emit, logger) -> MakeDownloads:
    """Build the download service the way ``bootstrap/`` does, over the fakes a test hands it.

    Each argument replaces one seam of the download service's parts — the
    adoption and removal services keep their own unless their folders are
    handed too. The loop the parts get is the test's running loop unless one is
    handed: the progress callback reaches ``call_soon_threadsafe`` from an
    executor thread, where only the loop object itself answers.
    """
    running = asyncio.get_running_loop()
    fixture_emit = emit

    def build(
        *,
        loop=None,
        retrodeck_folders=None,
        adoption_folders=None,
        removal_folders=None,
        download_file_store=None,
        emit=None,
        clock=None,
        platform_systems=None,
        target_gate=None,
        rom_remover=None,
        migration_pending=False,
    ) -> DownloadsHarness:
        romm_api = MagicMock()
        shared_platform_systems = FakePlatformSystems()
        prune_conflicts = _make_prune_conflicts()
        uow = FakeUnitOfWork()
        # The real ActiveCoreResolver folds the DB override over this fake's
        # es_systems default — the same seam the transfer re-bakes through on
        # download-complete, so a test can assert the per-game override re-bakes
        # the ``-e`` form.
        core_info = FakeCoreInfoProvider()
        active_core = ActiveCoreResolver(
            config=ActiveCoreResolverConfig(
                uow_factory=FakeUnitOfWorkFactory(uow),
                core_info=core_info,
                sandbox_launcher=FakeSandboxLauncher(),
                platform_core_reader=FakePlatformCoreReader(),
                platform_systems=shared_platform_systems,
                logger=logger,
            ),
        )

        shared_folders = FakeRetroDeckFolders(
            roms=os.path.join(os.path.expanduser("~"), "retrodeck", "roms"),
            bios=os.path.join(os.path.expanduser("~"), "retrodeck", "bios"),
        )
        shared_store = DownloadFileAdapter()
        install_recorder = RomInstallRecorder(
            config=RomInstallRecorderConfig(
                logger=logger,
                clock=FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC)),
                uow_factory=FakeUnitOfWorkFactory(uow),
                # Empty ("ES-DE could not answer"), which the launch-target check
                # reads as launchable, so it accepts every install.
                system_extensions=lambda system_name, reading=None: frozenset(),
                active_core=active_core,
                disc_resolver=FakeDiscResolver(),
            ),
        )
        adoption = RomAdoptionService(
            config=RomAdoptionServiceConfig(
                romm_api=romm_api,
                download_file_store=shared_store,
                platform_systems=shared_platform_systems,
                retrodeck_folders=shared_folders if adoption_folders is None else adoption_folders,
                install_recorder=install_recorder,
                adoption_move=AdoptionMoveAdapter(),
                back_up_save=lambda saves_dir, filename, *, keep: False,
                m3u_support=lambda system_name: harness.m3u_supported,
                system_extensions=lambda system_name, reading=None: frozenset(),
                # ``None`` is "es_systems.xml could not answer", which the search
                # reads as permission to proceed — the behaviour these tests predate.
                system_known=lambda system_name: None,
                save_locations=FakeSaveLocationReader(saves_root=shared_folders.saves),
                active_core=active_core,
                # Late-bound like production: the download service is constructed below.
                sibling_supersede=lambda: harness.service.supersede_sibling_installs,
                uow_factory=FakeUnitOfWorkFactory(uow),
                loop=running,
                logger=logger,
                log_debug=lambda msg: None,
                emit=fixture_emit,
                clock=FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC)),
                conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
            ),
        )

        download_loop = running if loop is None else loop
        download_emit = fixture_emit if emit is None else emit
        download_store = shared_store if download_file_store is None else download_file_store
        download_folders = shared_folders if retrodeck_folders is None else retrodeck_folders
        conflict_rules = _make_conflict_rules(prune_conflicts=prune_conflicts, migration_pending=migration_pending)
        state = DownloadsStateBox()
        queue = DownloadQueue(
            config=DownloadQueueConfig(
                loop=download_loop,
                emit=download_emit,
                clock=FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC)) if clock is None else clock,
                logger=logger,
            ),
        )
        unpacker = DownloadUnpacker(
            config=DownloadUnpackerConfig(
                download_file_store=download_store,
                retrodeck_folders=download_folders,
                install_recorder=install_recorder,
                m3u_support=lambda system_name: harness.m3u_supported,
                logger=logger,
            ),
        )
        transfer = DownloadTransfer(
            config=DownloadTransferConfig(
                state=state,
                queue=queue,
                unpacker=unpacker,
                romm_api=romm_api,
                install_recorder=install_recorder,
                conflict_rules=conflict_rules,
                loop=download_loop,
                emit=download_emit,
                logger=logger,
            ),
        )
        service = DownloadService(
            config=DownloadServiceConfig(
                state=state,
                queue=queue,
                transfer=transfer,
                unpacker=unpacker,
                romm_api=romm_api,
                download_file_store=download_store,
                platform_systems=shared_platform_systems if platform_systems is None else platform_systems,
                loop=download_loop,
                logger=logger,
                retrodeck_folders=download_folders,
                target_gate=adoption.check_download_target if target_gate is None else target_gate,
                uow_factory=FakeUnitOfWorkFactory(uow),
                # Late-bound remover for the #1298 sibling supersede — resolved at call
                # time, by which point the removal service is constructed below.
                rom_remover=(lambda: harness.removal.remove_rom_unchecked) if rom_remover is None else rom_remover,
                conflict_rules=conflict_rules,
            ),
        )
        removal = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=running,
                clock=FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC)),
                emit=fixture_emit,
                rom_file_store=RomFileAdapter(),
                retrodeck_folders=FakeRetroDeckFolders(roms=os.path.join(os.path.expanduser("~"), "retrodeck", "roms"))
                if removal_folders is None
                else removal_folders,
                download_queue_cleanup=service,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
            ),
        )
        harness = DownloadsHarness(
            service=service,
            state=state,
            queue=queue,
            transfer=transfer,
            unpacker=unpacker,
            removal=removal,
            adoption=adoption,
            uow=uow,
            romm_api=romm_api,
            core_info=core_info,
            prune_conflicts=prune_conflicts,
            loop=download_loop,
            download_file_store=download_store,
            retrodeck_folders=download_folders,
        )
        return harness

    return build


@pytest.fixture
def downloads(make_downloads) -> DownloadsHarness:
    return make_downloads()


class TestStartDownload:
    @pytest.mark.asyncio
    async def test_starts_download_task(self, make_downloads, tmp_path):

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)
        _spawn_calls = []

        def _close_coro_task(coro, _name):
            coro.close()
            _spawn_calls.append(coro)
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task
        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024

        result = await downloads.service.start_download(42)

        assert result["success"] is True
        assert 42 in downloads.queue.entries
        # The initial status is "queued" (#1053) — the task flips it to
        # "downloading" only once it acquires the concurrency semaphore.
        assert downloads.queue.entries[42]["status"] == "queued"
        assert len(_spawn_calls) == 1
        # The download's required bytes are reserved so a sibling's pre-flight
        # accounts for the outstanding claim.
        assert 42 in downloads.state.reserved_bytes

    @pytest.mark.asyncio
    async def test_rejects_already_downloading(self, downloads):
        downloads.state.in_progress.add(42)
        coro = downloads.service.start_download(42)
        with pytest.raises(Refused) as refused:
            await coro
        assert (refused.value.reason, refused.value.message) == ("already_downloading", "Already downloading")

    @pytest.mark.asyncio
    async def test_a_romm_error_fetching_the_rom_passes_on_and_frees_the_rom(self, make_downloads):
        from unittest.mock import AsyncMock

        downloads = make_downloads(loop=MagicMock())
        downloads.loop.run_in_executor = AsyncMock(side_effect=RommNotFoundError("HTTP Error 404: Not Found"))

        coro = downloads.service.start_download(9999)
        with pytest.raises(RommNotFoundError):
            await coro
        assert 9999 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_a_rom_romm_holds_no_file_for_starts_nothing(self, make_downloads):
        # The stored column says the ROM has a file; RomM's live answer decides.
        downloads = make_downloads(loop=MagicMock())
        _seed_rom(downloads.uow, 42)
        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "has_file_on_disk": False,
        }
        downloads.loop.run_in_executor = _hops_answering(rom_detail)
        downloads.service._tasks.spawn = MagicMock()

        with pytest.raises(NoFileOnRomm) as refused:
            await downloads.service.start_download(42)

        assert refused.value.reason == "no_file_on_romm"
        assert refused.value.message.startswith("No file on RomM")
        assert 42 not in downloads.queue.entries
        assert 42 not in downloads.state.in_progress
        downloads.service._tasks.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_checks_disk_space(self, make_downloads, tmp_path):

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "fs_size_bytes": 500 * 1024 * 1024,  # 500MB
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        downloads.download_file_store.disk_free = lambda _path: 50 * 1024 * 1024
        coro = downloads.service.start_download(42)
        with pytest.raises(Refused) as refused:
            await coro

        assert refused.value.reason == "insufficient_space"
        assert "disk space" in refused.value.message.lower()


class TestCancelDownload:
    @pytest.mark.asyncio
    async def test_cancels_active_download(self, downloads):
        # Create a real future that raises CancelledError when awaited
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.cancel()

        downloads.state.tasks[42] = fut
        downloads.queue.enqueue(42, {"status": "downloading"})

        result = downloads.service.cancel_download(42)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_is_refused(self, downloads):
        with pytest.raises(Refused) as refused:
            downloads.service.cancel_download(999)
        assert refused.value.reason == "no_active_download"

    @pytest.mark.asyncio
    async def test_cancels_paused_download_deletes_tmp_evicts_and_emits(self, downloads, tmp_path, emit):
        """A paused download has no live task: cancel deletes the partial .tmp,
        emits the terminal cancelled frame, and evicts the entry (#149
        downloads-round finding A/B). Previously this was a silent no-op."""

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")
        tmp_file = target_path + ".tmp"
        with open(tmp_file, "wb") as f:
            f.write(b"\x00" * 256)  # the partial the pause kept for resume

        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "rom_name": "Zelda",
                "platform_name": "Nintendo 64",
                "file_name": "zelda.z64",
                "status": "paused",
                "progress": 0.3,
                "bytes_downloaded": 300,
                "total_bytes": 1000,
                "resumable": True,
                "_target_path": target_path,
            },
        )

        result = downloads.service.cancel_download(42)
        await asyncio.sleep(0)  # let the scheduled cancelled-frame emit run + drain

        assert result == {"success": True, "message": "Download cancelled"}
        assert 42 not in downloads.queue.entries  # evicted, no residue
        assert not os.path.exists(tmp_file)  # partial deleted (no resume)
        cancelled = [
            c for c in emit.call_args_list if c[0][0] == "download_progress" and c[0][1].get("status") == "cancelled"
        ]
        assert len(cancelled) == 1
        assert cancelled[0][0][1]["rom_id"] == 42

    @pytest.mark.asyncio
    async def test_cancels_paused_download_without_recorded_target_still_evicts(self, downloads, emit):
        """A paused entry lacking ``_target_path`` (defensive) still cancels: no
        tmp removal attempted, entry evicted, success returned. The startup sweep
        reaps any orphaned .tmp."""

        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "rom_name": "Zelda",
                "platform_name": "Nintendo 64",
                "file_name": "zelda.z64",
                "status": "paused",
            },
        )

        result = downloads.service.cancel_download(42)
        await asyncio.sleep(0)

        assert result == {"success": True, "message": "Download cancelled"}
        assert 42 not in downloads.queue.entries

    @pytest.mark.asyncio
    async def test_cancel_no_task_and_not_paused_is_refused(self, downloads):
        """No live task AND the entry isn't paused → ``no_active_download``, and
        the non-paused entry is left untouched (not evicted)."""
        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading"})
        with pytest.raises(Refused) as refused:
            downloads.service.cancel_download(42)
        assert (refused.value.reason, refused.value.message) == (
            "no_active_download",
            "No active download for this ROM",
        )
        assert 42 in downloads.queue.entries


class TestGetDownloadQueue:
    @pytest.mark.asyncio
    async def test_returns_empty_queue(self, downloads):
        result = downloads.service.get_download_queue()
        assert result["downloads"] == []

    @pytest.mark.asyncio
    async def test_returns_active_downloads(self, downloads):
        downloads.queue.enqueue(
            1,
            {
                "rom_id": 1,
                "rom_name": "Game A",
                "status": "downloading",
                "progress": 0.5,
            },
        )
        result = downloads.service.get_download_queue()
        assert len(result["downloads"]) == 1
        assert result["downloads"][0]["status"] == "downloading"
        assert result["downloads"][0]["progress"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_returns_completed_downloads(self, downloads):
        downloads.queue.enqueue(
            1,
            {
                "rom_id": 1,
                "rom_name": "Game A",
                "status": "downloading",
                "progress": 0.5,
            },
        )
        downloads.queue.enqueue(
            2,
            {
                "rom_id": 2,
                "rom_name": "Game B",
                "status": "completed",
                "progress": 1.0,
            },
        )
        result = downloads.service.get_download_queue()
        assert len(result["downloads"]) == 2
        statuses = {d["status"] for d in result["downloads"]}
        assert statuses == {"downloading", "completed"}

    @pytest.mark.asyncio
    async def test_strips_internal_underscore_keys_from_wire_shape(self, downloads):
        """Internal ``_``-prefixed keys (e.g. ``_target_path`` on a paused entry,
        kept only for the paused-cancel cleanup) never cross the wire."""
        downloads.queue.enqueue(
            1,
            {
                "rom_id": 1,
                "rom_name": "Game A",
                "status": "paused",
                "_target_path": "/games/n64/game.z64",
            },
        )
        result = downloads.service.get_download_queue()
        assert len(result["downloads"]) == 1
        assert "_target_path" not in result["downloads"][0]
        assert result["downloads"][0] == {"rom_id": 1, "rom_name": "Game A", "status": "paused"}


class TestClearCompletedDownloads:
    @pytest.mark.asyncio
    async def test_evicts_terminal_keeps_active_and_returns_count(self, downloads):
        # One entry per status: the three terminal ones evict, the four
        # non-terminal ones (active/queued/paused/extracting) stay.
        queue = downloads.queue.entries
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "completed"})
        downloads.queue.enqueue(2, {"rom_id": 2, "status": "failed", "error": "boom"})
        downloads.queue.enqueue(3, {"rom_id": 3, "status": "cancelled"})
        downloads.queue.enqueue(4, {"rom_id": 4, "status": "downloading"})
        downloads.queue.enqueue(5, {"rom_id": 5, "status": "queued"})
        downloads.queue.enqueue(6, {"rom_id": 6, "status": "paused"})
        downloads.queue.enqueue(7, {"rom_id": 7, "status": "extracting"})

        result = downloads.service.clear_completed_downloads()

        assert result == {"success": True, "cleared": 3}
        assert set(queue.keys()) == {4, 5, 6, 7}
        assert {item["status"] for item in queue.values()} == {"downloading", "queued", "paused", "extracting"}

    @pytest.mark.asyncio
    async def test_idempotent_on_empty_queue(self, downloads):
        result = downloads.service.clear_completed_downloads()
        assert result == {"success": True, "cleared": 0}
        assert downloads.queue.entries == {}

    @pytest.mark.asyncio
    async def test_no_terminal_entries_clears_nothing(self, downloads):
        queue = downloads.queue.entries
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "downloading"})
        downloads.queue.enqueue(2, {"rom_id": 2, "status": "paused"})

        result = downloads.service.clear_completed_downloads()

        assert result == {"success": True, "cleared": 0}
        assert set(queue.keys()) == {1, 2}


class TestGetInstalledRom:
    @pytest.mark.asyncio
    async def test_returns_installed_rom(self, downloads):
        _seed_rom(downloads.uow, 42)
        downloads.uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=42,
                file_path="/roms/n64/zelda.z64",
                rom_dir=None,
                platform_slug="n64",
                system="n64",
                installed_at="2026-01-01T00:00:00+00:00",
            )
        )
        result = downloads.service.get_installed_rom(42)
        assert result is not None
        assert result["rom_id"] == 42
        assert result["system"] == "n64"
        # file_name is derived from the launch file_path.
        assert result["file_name"] == "zelda.z64"
        assert result["file_path"] == "/roms/n64/zelda.z64"
        assert result["platform_slug"] == "n64"

    @pytest.mark.asyncio
    async def test_returns_none_not_installed(self, downloads):
        result = downloads.service.get_installed_rom(999)
        assert result is None


class TestRomInstallForeignKey:
    """A RomInstall whose rom_id has no synced Rom is rejected at commit.

    Mirrors the schema's ``rom_installs.rom_id REFERENCES roms(rom_id)`` under
    ``PRAGMA foreign_keys=ON`` — the FakeUnitOfWork enforces it on commit so the
    install slice can't silently persist an orphan.
    """

    def test_orphan_install_save_raises_integrity_error_at_commit(self, downloads):
        uow = downloads.uow
        with pytest.raises(sqlite3.IntegrityError, match="rom_installs"), uow:
            uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=42,  # no matching roms row seeded
                    file_path="/roms/n64/zelda.z64",
                    rom_dir=None,
                    platform_slug="n64",
                    system="n64",
                    installed_at="2026-01-01T00:00:00+00:00",
                )
            )
        assert uow.committed is False


_SINGLE_DETAIL: dict[str, Any] = {
    "id": 1,
    "name": "Game 1",
    "fs_name": "game1.z64",
    "fs_size_bytes": 1024,
    "platform_slug": "n64",
    "platform_name": "Nintendo 64",
}
_MULTI_DETAIL: dict[str, Any] = {
    "id": 1,
    "name": "Game 1",
    "fs_name": "Game 1.zip",
    "fs_name_no_ext": "Game 1",
    "fs_size_bytes": 2048,
    "platform_slug": "psx",
    "platform_name": "PlayStation",
    "has_multiple_files": True,
    "files": [{"file_name": "a.bin"}, {"file_name": "b.bin"}],
}


class TestOccupiedTargetPreFlight:
    """A download refuses rather than writing over content already in place (#260).

    The pre-flight sits with ``insufficient_space``: it runs before any directory
    is created and before the transfer task exists, so a refusal leaves nothing
    behind — including the in-progress claim, which a stuck download would
    otherwise hold until a backend restart.
    """

    @pytest.fixture
    def downloads(self, make_downloads):
        return make_downloads(loop=MagicMock())

    def _stage(self, downloads, detail, *, occupied_path, is_dir=False, size=4096):
        """Point the detail fetch at *detail* and stage one occupied path."""

        downloads.loop.run_in_executor = _hops_answering(detail)
        # Close the coroutine the real spawn would have owned; leaving it
        # unawaited makes pytest raise a RuntimeWarning at collection time.
        downloads.service._tasks.spawn = MagicMock(side_effect=lambda coro, _name: (coro.close(), MagicMock())[1])
        store = downloads.download_file_store
        store.describe_path = lambda path: (
            {
                "path": path,
                "kind": "dir" if is_dir else "file",
                "size_bytes": size,
                "modified_at": 1_700_000_000.0,
            }
            if path == occupied_path
            else None
        )
        return store

    def _roms_path(self, downloads, system):
        return os.path.join(downloads.retrodeck_folders.roms, system)

    @pytest.mark.asyncio
    async def test_single_file_refuses_with_the_comparison(self, downloads):
        target = os.path.join(self._roms_path(downloads, "n64"), "game1.z64")
        self._stage(downloads, _SINGLE_DETAIL, occupied_path=target)

        starting = downloads.service.start_download(1)

        with pytest.raises(TargetOccupied) as refused:
            await starting

        assert refused.value.reason == "target_occupied"
        assert refused.value.details["existing"]["path"] == target
        assert refused.value.details["incoming"] == {"name": "game1.z64", "size_bytes": 1024}
        assert refused.value.details["sizes_match"] is False

    @pytest.mark.asyncio
    async def test_a_refusal_starts_nothing_and_releases_the_claim(self, downloads):
        target = os.path.join(self._roms_path(downloads, "n64"), "game1.z64")
        store = self._stage(downloads, _SINGLE_DETAIL, occupied_path=target)
        made_dirs = []
        store.make_dirs = made_dirs.append
        starting = downloads.service.start_download(1)

        with pytest.raises(TargetOccupied):
            await starting

        assert made_dirs == []
        assert downloads.queue.entries == {}
        assert downloads.service.active_download_rom_ids() == set()
        downloads.service._tasks.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_file_checks_the_extract_directory_not_the_archive(self, downloads):
        extract_dir = os.path.join(self._roms_path(downloads, "psx"), "Game 1")
        self._stage(downloads, _MULTI_DETAIL, occupied_path=extract_dir, is_dir=True, size=2048)

        starting = downloads.service.start_download(1)

        with pytest.raises(TargetOccupied) as refused:
            await starting

        assert refused.value.reason == "target_occupied"
        assert refused.value.details["existing"]["path"] == extract_dir
        assert refused.value.details["existing"]["kind"] == "dir"
        assert refused.value.details["sizes_match"] is True

    @pytest.mark.asyncio
    async def test_a_free_target_proceeds(self, downloads):
        self._stage(downloads, _SINGLE_DETAIL, occupied_path="/nowhere")
        downloads.download_file_store.disk_free = lambda _path: 900 * 1024 * 1024

        result = await downloads.service.start_download(1)

        assert result == {"success": True, "message": "Download started"}

    @pytest.mark.asyncio
    async def test_replace_clears_an_occupied_directory_and_proceeds(self, downloads):
        extract_dir = os.path.join(self._roms_path(downloads, "psx"), "Game 1")
        store = self._stage(downloads, _MULTI_DETAIL, occupied_path=extract_dir, is_dir=True)
        store.disk_free = lambda _path: 900 * 1024 * 1024
        removed = []
        store.remove_tree = removed.append

        result = await downloads.service.start_download(1, True)

        assert result == {"success": True, "message": "Download started"}
        assert removed == [extract_dir]

    @pytest.mark.asyncio
    async def test_replace_leaves_a_single_file_to_the_atomic_rename(self, downloads):
        target = os.path.join(self._roms_path(downloads, "n64"), "game1.z64")
        store = self._stage(downloads, _SINGLE_DETAIL, occupied_path=target)
        store.disk_free = lambda _path: 900 * 1024 * 1024
        removed = []
        store.remove_file = removed.append
        store.remove_tree = removed.append

        result = await downloads.service.start_download(1, True)

        assert result == {"success": True, "message": "Download started"}
        assert removed == []

    @pytest.mark.asyncio
    async def test_a_failed_replace_aborts_the_download(self, downloads):
        extract_dir = os.path.join(self._roms_path(downloads, "psx"), "Game 1")
        store = self._stage(downloads, _MULTI_DETAIL, occupied_path=extract_dir, is_dir=True)

        def _boom(_path):
            raise OSError("read-only filesystem")

        store.remove_tree = _boom

        starting = downloads.service.start_download(1, True)

        with pytest.raises(Refused) as refused:
            await starting

        assert refused.value.reason == "replace_failed"
        assert downloads.service.active_download_rom_ids() == set()

    @pytest.mark.asyncio
    async def test_a_partial_answer_is_the_answer_and_releases_the_claim(self, make_downloads):
        # Download Instead's removal failed after the candidate's saves were
        # carried: the gate answers what it already did, and nothing downloads.
        stopped = AdoptionIncomplete(
            reason="replace_failed",
            message="Could not remove the existing files — download aborted",
            renamed=["Game.srm"],
            still_under_old_name=[],
            set_aside=[],
        )

        async def gate(rom_detail, checked_path, *, replace, resume=False, **answer):
            return stopped

        downloads = make_downloads(target_gate=gate, loop=MagicMock())
        started = _stage_download_prologue(downloads)

        result = await downloads.service.start_download(1, True)

        assert result is stopped
        assert started == []
        assert downloads.service.active_download_rom_ids() == set()

    @pytest.mark.asyncio
    async def test_a_refusal_leaves_an_installed_sibling_untouched(self, make_downloads, tmp_path):
        # The #1298 supersede deletes ANOTHER version's files, so it must not run
        # until the gate has passed. Otherwise the user who opens the dialog and
        # presses Cancel is left with one version uninstalled and nothing in its
        # place. Real files, real remover, real install rows.

        roms = tmp_path / "retrodeck" / "roms"
        paths = FakeRetroDeckFolders(roms=str(roms), bios=str(tmp_path / "retrodeck" / "bios"))
        downloads = make_downloads(
            retrodeck_folders=paths, adoption_folders=paths, removal_folders=paths, loop=MagicMock()
        )

        (roms / "n64").mkdir(parents=True)
        sibling_file = roms / "n64" / "game_2.z64"
        sibling_file.write_bytes(b"the other version")
        # What the user put where rom 1 would download (``_SINGLE``'s fs_name).
        (roms / "n64" / "game1.z64").write_bytes(b"user's own copy")

        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=False)
        with downloads.uow:
            downloads.uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=2,
                    file_path=str(sibling_file),
                    rom_dir=None,
                    platform_slug="n64",
                    system="n64",
                    installed_at="2026-01-01T00:00:00+00:00",
                )
            )

        downloads.loop.run_in_executor = _hops_answering(_SINGLE_DETAIL)
        downloads.service._tasks.spawn = MagicMock(side_effect=lambda coro, _name: (coro.close(), MagicMock())[1])

        starting = downloads.service.start_download(1)

        with pytest.raises(TargetOccupied) as refused:
            await starting

        assert refused.value.reason == "target_occupied"
        assert sibling_file.read_bytes() == b"the other version"
        assert downloads.uow.rom_installs.get(2) is not None


class TestResumingAReplaceDownload:
    """A paused replace-download can still be resumed (#260).

    A single-file replace deliberately deletes nothing at admission — the final
    ``os.replace`` is what swaps the bytes in — so on resume the gate meets the
    very file the download is replacing. Without the user's stored answer it
    refuses, every time, and Cancel (which discards the transferred bytes) is the
    only way out. A multi-file replace is NOT symmetric: its directory was
    removed when the answer was given, so a directory found there on resume is
    content the user has never seen and must be asked about, not deleted.
    """

    @pytest.fixture
    def downloads(self, make_downloads):
        return make_downloads(loop=MagicMock())

    def _stage(self, downloads, detail, *, occupied_path):
        """Point the detail fetch at *detail* and hold one path permanently occupied."""

        downloads.loop.run_in_executor = _hops_answering(detail)
        downloads.service._tasks.spawn = MagicMock(side_effect=lambda coro, _name: (coro.close(), MagicMock())[1])
        store = downloads.download_file_store
        store.describe_path = lambda path: (
            {
                "path": path,
                "kind": "dir" if detail is _MULTI_DETAIL else "file",
                "size_bytes": 8,
                "modified_at": 0.0,
            }
            if path == occupied_path
            else None
        )
        store.disk_free = lambda _path: 900 * 1024 * 1024
        store.remove_tree = lambda _path: None
        return store

    def _roms(self, downloads, system):
        return os.path.join(downloads.retrodeck_folders.roms, system)

    @pytest.mark.asyncio
    async def test_a_paused_single_file_replace_resumes(self, downloads):
        target = os.path.join(self._roms(downloads, "n64"), "game1.z64")
        self._stage(downloads, _SINGLE_DETAIL, occupied_path=target)

        assert (await downloads.service.start_download(1, True))["success"] is True
        # The file the user chose to replace is still there — os.replace swaps it
        # at the end — so a resume that forgot the answer would be refused by it.
        downloads.queue.entries[1]["status"] = "paused"

        result = await downloads.service.resume_download(1)

        assert result == {"success": True, "message": "Download started"}

    @pytest.mark.asyncio
    async def test_a_resume_without_a_replace_answer_is_still_refused(self, downloads):
        # The stored answer is scoped to the download that was admitted — it is
        # not a blanket exemption for the path.
        target = os.path.join(self._roms(downloads, "n64"), "game1.z64")
        self._stage(downloads, _SINGLE_DETAIL, occupied_path=target)
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "paused"})

        resuming = downloads.service.resume_download(1)

        with pytest.raises(TargetOccupied) as refused:
            await resuming

        assert refused.value.reason == "target_occupied"

    @pytest.mark.asyncio
    async def test_a_multi_file_replace_answer_is_spent_and_not_carried(self, downloads):
        # Its directory was removed at admission. A directory found there on
        # resume is new content the user has never been shown, so the resume is
        # refused rather than deleting it — this PR's own failure mode arriving
        # through the back door.
        extract_dir = os.path.join(self._roms(downloads, "psx"), "Game 1")
        store = self._stage(downloads, _MULTI_DETAIL, occupied_path=extract_dir)
        assert (await downloads.service.start_download(1, True))["success"] is True
        assert downloads.queue.entries[1]["_replace_existing"] is False
        downloads.queue.entries[1]["status"] = "paused"
        removed: list[str] = []
        store.remove_tree = removed.append

        resuming = downloads.service.resume_download(1)

        with pytest.raises(TargetOccupied) as refused:
            await resuming

        assert refused.value.reason == "target_occupied"
        assert removed == []

    @pytest.mark.asyncio
    async def test_the_stored_answer_never_crosses_the_wire(self, downloads):
        target = os.path.join(self._roms(downloads, "n64"), "game1.z64")
        self._stage(downloads, _SINGLE_DETAIL, occupied_path=target)
        await downloads.service.start_download(1, True)

        (item,) = downloads.service.get_download_queue()["downloads"]

        assert "_replace_existing" not in item


class TestRemoveRom:
    @pytest.mark.asyncio
    async def test_deletes_file_and_clears_state(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        rom_file = tmp_path / "retrodeck" / "roms" / "n64" / "zelda.z64"
        rom_file.parent.mkdir(parents=True)
        rom_file.write_text("fake rom data")

        _seed_install(
            downloads.uow,
            42,
            file_path=str(rom_file),
            rom_dir=None,
        )
        downloads.queue.enqueue(42, {"status": "completed"})

        result = await downloads.removal.remove_rom(42)
        assert result["success"] is True
        assert not rom_file.exists()
        assert downloads.uow.rom_installs.get(42) is None
        assert 42 not in downloads.queue.entries

    @pytest.mark.asyncio
    async def test_refuses_not_installed(self, downloads):
        coro = downloads.removal.remove_rom(999)
        with pytest.raises(NotInstalled) as refused:
            await coro
        assert refused.value.message == "ROM not installed"


class TestUninstallAllRoms:
    @pytest.mark.asyncio
    async def test_removes_all_installed(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        file_a = roms_dir / "game_a.z64"
        file_b = roms_dir / "game_b.z64"
        file_a.write_text("data a")
        file_b.write_text("data b")

        _seed_install(downloads.uow, 1, file_path=str(file_a), rom_dir=None)
        _seed_install(downloads.uow, 2, file_path=str(file_b), rom_dir=None)

        result = await downloads.removal.uninstall_all_roms()
        assert result["success"] is True
        assert result["removed_count"] == 2
        assert not file_a.exists()
        assert not file_b.exists()

    @pytest.mark.asyncio
    async def test_clears_state(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        _seed_install(
            downloads.uow,
            1,
            file_path=str(tmp_path / "retrodeck" / "roms" / "n64" / "nonexistent.z64"),
            rom_dir=None,
        )

        await downloads.removal.uninstall_all_roms()
        assert list(downloads.uow.rom_installs.iter_all()) == []

    @pytest.mark.asyncio
    async def test_handles_missing_files(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_base = tmp_path / "retrodeck" / "roms"
        _seed_install(
            downloads.uow,
            1,
            file_path=str(roms_base / "n64" / "missing.z64"),
            rom_dir=None,
        )
        _seed_install(
            downloads.uow,
            2,
            file_path=str(roms_base / "snes" / "also_missing.z64"),
            rom_dir=None,
            system="snes",
        )

        result = await downloads.removal.uninstall_all_roms()
        assert result["success"] is True
        assert list(downloads.uow.rom_installs.iter_all()) == []


class TestDetectLaunchFile:
    def test_prefers_m3u(self, downloads, tmp_path):
        (tmp_path / "game.m3u").write_text("disc1.cue")
        (tmp_path / "disc1.cue").write_text("cue data")
        (tmp_path / "disc1.bin").write_bytes(b"\x00" * 1000)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".m3u")

    def test_falls_back_to_cue(self, downloads, tmp_path):
        (tmp_path / "disc1.cue").write_text("cue data")
        (tmp_path / "disc1.bin").write_bytes(b"\x00" * 1000)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".cue")

    def test_falls_back_to_largest(self, downloads, tmp_path):
        (tmp_path / "small.bin").write_bytes(b"\x00" * 100)
        (tmp_path / "large.bin").write_bytes(b"\x00" * 10000)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith("large.bin")

    def test_wiiu_rpx_in_code_subdir(self, downloads, tmp_path):
        code_dir = tmp_path / "code"
        code_dir.mkdir()
        (code_dir / "game.rpx").write_bytes(b"\x00" * 500)
        (tmp_path / "meta" / "meta.xml").parent.mkdir()
        (tmp_path / "meta" / "meta.xml").write_text("<xml/>")

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".rpx")

    def test_wiiu_disc_image(self, downloads, tmp_path):
        (tmp_path / "game.wux").write_bytes(b"\x00" * 1000)
        (tmp_path / "readme.txt").write_text("info")

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".wux")

    def test_wiiu_wud_format(self, downloads, tmp_path):
        (tmp_path / "game.wud").write_bytes(b"\x00" * 1000)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".wud")

    def test_wiiu_wua_format(self, downloads, tmp_path):
        (tmp_path / "game.wua").write_bytes(b"\x00" * 1000)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".wua")

    def test_ps3_eboot_bin(self, downloads, tmp_path):
        usrdir = tmp_path / "PS3_GAME" / "USRDIR"
        usrdir.mkdir(parents=True)
        (usrdir / "EBOOT.BIN").write_bytes(b"\x00" * 500)
        (tmp_path / "PS3_GAME" / "PARAM.SFO").write_bytes(b"\x00" * 100)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith("EBOOT.BIN")

    def test_3ds_prefers_3ds_over_cia(self, downloads, tmp_path):
        (tmp_path / "game.3ds").write_bytes(b"\x00" * 500)
        (tmp_path / "game.cia").write_bytes(b"\x00" * 500)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".3ds")

    def test_3ds_falls_back_to_cia(self, downloads, tmp_path):
        (tmp_path / "game.cia").write_bytes(b"\x00" * 500)
        (tmp_path / "game.cxi").write_bytes(b"\x00" * 500)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".cia")

    def test_m3u_still_preferred_over_platform_specific(self, downloads, tmp_path):
        """M3U takes priority even when platform-specific files exist."""
        (tmp_path / "game.m3u").write_text("disc1.cue")
        code_dir = tmp_path / "code"
        code_dir.mkdir()
        (code_dir / "game.rpx").write_bytes(b"\x00" * 500)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), True)
        assert result.endswith(".m3u")

    def test_bundled_m3u_ignored_when_platform_unsupported(self, downloads, tmp_path):
        """#1111: a RomM-bundled .m3u must NOT be chosen on a non-m3u platform.

        With ``m3u_supported=False`` the .m3u is skipped and selection falls
        through to the real game file (here the .nsp, picked as largest).
        """
        (tmp_path / "Zelda.m3u").write_text("Zelda.nsp")
        (tmp_path / "Zelda.nsp").write_bytes(b"\x00" * 5000)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), False)
        assert result.endswith(".nsp")

    def test_bundled_m3u_ignored_unsupported_falls_back_to_cue(self, downloads, tmp_path):
        """When unsupported, a bundled .m3u is ignored and a .cue is preferred next."""
        (tmp_path / "game.m3u").write_text("disc1.cue")
        (tmp_path / "disc1.cue").write_text("cue data")
        (tmp_path / "disc1.bin").write_bytes(b"\x00" * 1000)

        result = downloads.unpacker.detect_launch_file(str(tmp_path), False)
        assert result.endswith(".cue")


class TestDiskSpaceMultiFile:
    @pytest.mark.asyncio
    async def test_multi_file_rom_requires_double_space(self, make_downloads, tmp_path):

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        file_size = 500 * 1024 * 1024  # 500MB
        rom_detail = {
            "id": 42,
            "name": "WiiU Game",
            "fs_name": "game.zip",
            "fs_size_bytes": file_size,
            "platform_slug": "wiiu",
            "platform_name": "Wii U",
            "has_multiple_files": True,
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        # 700MB free: enough for single-file (600MB) but not multi-file (1100MB)
        downloads.download_file_store.disk_free = lambda _path: 700 * 1024 * 1024
        coro = downloads.service.start_download(42)
        with pytest.raises(Refused) as refused:
            await coro

        assert refused.value.reason == "insufficient_space"

    @pytest.mark.asyncio
    async def test_single_file_rom_uses_normal_space_check(self, make_downloads, tmp_path):

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        file_size = 500 * 1024 * 1024  # 500MB
        rom_detail = {
            "id": 43,
            "name": "N64 Game",
            "fs_name": "game.z64",
            "fs_size_bytes": file_size,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            # Consume the spawned transfer coroutine so it isn't left
            # un-awaited (RuntimeWarning); this unit only exercises the disk-space
            # gate, not the download itself.
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task

        # 700MB free: enough for single-file (600MB)
        downloads.download_file_store.disk_free = lambda _path: 700 * 1024 * 1024
        result = await downloads.service.start_download(43)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_nested_multi_file_rom_requires_double_space(self, make_downloads, tmp_path):
        """#855: nested-multi (has_multiple_files=False, len(files) > 1) reserves 2x."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        file_size = 500 * 1024 * 1024  # 500MB
        rom_detail = {
            "id": 44,
            "name": "Switch Game",
            "fs_name": "game.nsp",
            "fs_size_bytes": file_size,
            "platform_slug": "switch",
            "platform_name": "Nintendo Switch",
            "has_multiple_files": False,
            "has_nested_single_file": True,
            "files": [
                {"file_name": "game.nsp"},
                {"file_name": "update/patch.nsp"},
            ],
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        # 700MB free: enough for single-file (600MB) but not multi-file (1100MB).
        # If the gate only read has_multiple_files (False), this would pass —
        # the 2x reservation is what makes it fail.
        downloads.download_file_store.disk_free = lambda _path: 700 * 1024 * 1024
        coro = downloads.service.start_download(44)
        with pytest.raises(Refused) as refused:
            await coro

        assert refused.value.reason == "insufficient_space"


class TestMultiFileRomDeletion:
    @pytest.mark.asyncio
    async def test_remove_rom_deletes_rom_dir(self, make_downloads, tmp_path):
        """Multi-file ROM with rom_dir should delete the entire directory."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        rom_dir = tmp_path / "retrodeck" / "roms" / "psx" / "FF7"
        rom_dir.mkdir(parents=True)
        (rom_dir / "FF7.m3u").write_text("disc1.cue")
        (rom_dir / "disc1.cue").write_text("cue")
        (rom_dir / "disc1.bin").write_bytes(b"\x00" * 100)

        _seed_install(
            downloads.uow,
            42,
            file_path=str(rom_dir / "FF7.m3u"),
            rom_dir=str(rom_dir),
            system="psx",
        )

        result = await downloads.removal.remove_rom(42)
        assert result["success"] is True
        assert not rom_dir.exists()
        # Parent system dir should still exist
        assert (tmp_path / "retrodeck" / "roms" / "psx").exists()

    @pytest.mark.asyncio
    async def test_uninstall_all_deletes_rom_dirs(self, make_downloads, tmp_path):
        """uninstall_all_roms should delete multi-file ROM directories."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        rom_dir = tmp_path / "retrodeck" / "roms" / "psx" / "FF7"
        rom_dir.mkdir(parents=True)
        (rom_dir / "disc1.bin").write_bytes(b"\x00" * 100)

        _seed_install(
            downloads.uow,
            1,
            file_path=str(rom_dir / "FF7.m3u"),
            rom_dir=str(rom_dir),
            system="psx",
        )

        result = await downloads.removal.uninstall_all_roms()
        assert result["success"] is True
        assert result["removed_count"] == 1
        assert not rom_dir.exists()


class TestMaybeGenerateM3u:
    def test_generates_m3u_for_multiple_cue_files(self, downloads, tmp_path):
        """When multiple .cue files exist and no .m3u, auto-generate one."""
        (tmp_path / "Game - Disc 1.cue").write_text("cue disc 1")
        (tmp_path / "Game - Disc 1.bin").write_bytes(b"\x00" * 1000)
        (tmp_path / "Game - Disc 2.cue").write_text("cue disc 2")
        (tmp_path / "Game - Disc 2.bin").write_bytes(b"\x00" * 1000)

        rom_detail = {"fs_name_no_ext": "Final Fantasy VII", "name": "Final Fantasy VII"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        m3u_path = tmp_path / "Final Fantasy VII.m3u"
        assert m3u_path.exists()
        content = m3u_path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert lines[0] == "Game - Disc 1.cue"
        assert lines[1] == "Game - Disc 2.cue"

    def test_generates_m3u_for_multiple_chd_files(self, downloads, tmp_path):
        """CHD multi-disc should also get an M3U."""
        (tmp_path / "Game (Disc 1).chd").write_bytes(b"\x00" * 100)
        (tmp_path / "Game (Disc 2).chd").write_bytes(b"\x00" * 100)

        rom_detail = {"fs_name_no_ext": "Game", "name": "Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        m3u_path = tmp_path / "Game.m3u"
        assert m3u_path.exists()
        lines = m3u_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_skips_if_m3u_exists(self, downloads, tmp_path):
        """Should not overwrite an existing M3U."""
        (tmp_path / "existing.m3u").write_text("original content")
        (tmp_path / "disc1.cue").write_text("cue 1")
        (tmp_path / "disc2.cue").write_text("cue 2")

        rom_detail = {"fs_name_no_ext": "Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        # Only the original M3U should exist, unchanged
        assert (tmp_path / "existing.m3u").read_text() == "original content"
        assert not (tmp_path / "Game.m3u").exists()

    def test_single_cue_generates_game_named_m3u(self, downloads, tmp_path):
        """Single-disc bin/cue generates a game-named M3U so the dir collapses in ES-DE."""
        (tmp_path / "disc1.cue").write_text("FILE disc1.bin BINARY")
        (tmp_path / "disc1.bin").write_bytes(b"\x00" * 1000)

        rom_detail = {"fs_name_no_ext": "Metal Gear Solid"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        m3u = tmp_path / "Metal Gear Solid.m3u"
        assert m3u.exists()
        assert m3u.read_text().strip() == "disc1.cue"

    def test_skips_single_chd(self, downloads, tmp_path):
        """Single-disc chd is out of scope (cue-only) — no M3U generated."""
        (tmp_path / "game.chd").write_bytes(b"\x00" * 1000)

        rom_detail = {"fs_name_no_ext": "Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        assert not (tmp_path / "Game.m3u").exists()

    def test_skips_single_iso(self, downloads, tmp_path):
        """Single-disc iso is out of scope (cue-only) — no M3U generated."""
        (tmp_path / "game.iso").write_bytes(b"\x00" * 1000)

        rom_detail = {"fs_name_no_ext": "Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        assert not (tmp_path / "Game.m3u").exists()

    def test_uses_name_fallback(self, downloads, tmp_path):
        """Falls back to rom name when fs_name_no_ext is missing."""
        (tmp_path / "d1.chd").write_bytes(b"\x00" * 100)
        (tmp_path / "d2.chd").write_bytes(b"\x00" * 100)

        rom_detail = {"name": "My Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        assert (tmp_path / "My Game.m3u").exists()

    def test_skips_generation_when_platform_unsupported(self, downloads, tmp_path):
        """#1111: no M3U is generated when the platform does not support .m3u,
        even for a multi-disc layout that would otherwise warrant one."""
        (tmp_path / "Game - Disc 1.cue").write_text("cue 1")
        (tmp_path / "Game - Disc 2.cue").write_text("cue 2")

        rom_detail = {"fs_name_no_ext": "Game", "name": "Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, False)

        assert not (tmp_path / "Game.m3u").exists()

    def test_folder_boot_layout_suppresses_m3u(self, downloads, tmp_path):
        """#1212: a folder-boot dump (PS3) never gets a playlist, even on an
        .m3u-supported platform with disc-suffixed payload files that would
        otherwise warrant one — it launches the game directory directly."""
        eboot = tmp_path / "PS3_GAME" / "USRDIR" / "EBOOT.BIN"
        eboot.parent.mkdir(parents=True)
        eboot.write_bytes(b"\x00" * 16)
        # Two disc-suffixed payload files that WOULD trigger needs_m3u without the gate.
        (tmp_path / "PS3_GAME" / "USRDIR" / "part1.iso").write_bytes(b"\x00" * 100)
        (tmp_path / "PS3_GAME" / "USRDIR" / "part2.iso").write_bytes(b"\x00" * 100)

        rom_detail = {"fs_name_no_ext": "MyGame", "name": "MyGame"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        assert not (tmp_path / "MyGame.m3u").exists()
        assert list(tmp_path.rglob("*.m3u")) == []

    def test_genuine_multi_disc_still_generates_when_not_folder_boot(self, downloads, tmp_path):
        """The folder-boot gate does not suppress a genuine multi-disc set (no marker)."""
        (tmp_path / "Game (Disc 1).chd").write_bytes(b"\x00" * 100)
        (tmp_path / "Game (Disc 2).chd").write_bytes(b"\x00" * 100)

        rom_detail = {"fs_name_no_ext": "Game", "name": "Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        assert (tmp_path / "Game.m3u").exists()


class TestMaybeHealPs3Sfb:
    """``DownloadUnpacker.heal_ps3_sfb`` restores a folder-boot dump's mis-suffixed disc SFB (#1212)."""

    def _seed_ps3_layout(self, tmp_path):
        """Lay down a PS3 folder-boot layout; return the game root (holds PS3_GAME)."""
        eboot = tmp_path / "PS3_GAME" / "USRDIR" / "EBOOT.BIN"
        eboot.parent.mkdir(parents=True)
        eboot.write_bytes(b"\x00" * 16)
        return tmp_path

    def test_heals_txt_suffixed_sfb(self, downloads, tmp_path):
        root = self._seed_ps3_layout(tmp_path)
        (root / "PS3_DISC.SFB.txt").write_bytes(b"SFB-BYTES")

        downloads.unpacker.heal_ps3_sfb(str(root))

        sfb = root / "PS3_DISC.SFB"
        assert sfb.exists()
        assert sfb.read_bytes() == b"SFB-BYTES"
        # The original .txt is preserved (copy, not move).
        assert (root / "PS3_DISC.SFB.txt").exists()

    def test_heals_at_one_level_deeper_root(self, downloads, tmp_path):
        # An extract that nests PS3_GAME one level below the extract dir: the SFB
        # heals at the inner game root, not the extract dir.
        inner = tmp_path / "MyGame"
        eboot = inner / "PS3_GAME" / "USRDIR" / "EBOOT.BIN"
        eboot.parent.mkdir(parents=True)
        eboot.write_bytes(b"\x00" * 16)
        (inner / "PS3_DISC.SFB.txt").write_bytes(b"SFB")

        downloads.unpacker.heal_ps3_sfb(str(tmp_path))

        assert (inner / "PS3_DISC.SFB").exists()

    def test_existing_sfb_is_not_overwritten(self, downloads, tmp_path):
        root = self._seed_ps3_layout(tmp_path)
        (root / "PS3_DISC.SFB").write_bytes(b"REAL")
        (root / "PS3_DISC.SFB.txt").write_bytes(b"WRONG")

        downloads.unpacker.heal_ps3_sfb(str(root))

        assert (root / "PS3_DISC.SFB").read_bytes() == b"REAL"

    def test_no_sfb_txt_does_nothing(self, downloads, tmp_path):
        root = self._seed_ps3_layout(tmp_path)

        downloads.unpacker.heal_ps3_sfb(str(root))

        assert not (root / "PS3_DISC.SFB").exists()

    def test_non_folder_boot_layout_does_nothing(self, downloads, tmp_path):
        # A stray PS3_DISC.SFB.txt without the folder-boot marker is left untouched.
        (tmp_path / "PS3_DISC.SFB.txt").write_bytes(b"SFB")
        (tmp_path / "game.iso").write_bytes(b"\x00" * 100)

        downloads.unpacker.heal_ps3_sfb(str(tmp_path))

        assert not (tmp_path / "PS3_DISC.SFB").exists()


class TestDoDownloadSingleFile:
    """Tests for DownloadTransfer.run happy path — single file."""

    @pytest.mark.asyncio
    async def test_single_file_happy_path(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        _seed_rom(downloads.uow, 42)
        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        # File ends up at target_path (not .tmp)
        assert os.path.exists(target_path)
        assert not os.path.exists(target_path + ".tmp")
        # RomInstall record persisted via the Unit of Work.
        installed = downloads.uow.rom_installs.get(42)
        assert installed is not None
        assert installed.rom_id == 42
        assert installed.file_path == target_path
        # Single-file ROM owns no dedicated folder.
        assert installed.rom_dir is None
        assert installed.system == "n64"
        assert installed.platform_slug == "n64"
        assert installed.installed_at
        # download_complete event emitted
        emit_calls = [c for c in emit.call_args_list if c[0][0] == "download_complete"]
        assert len(emit_calls) == 1
        payload = emit_calls[0][0][1]
        assert payload["rom_id"] == 42
        assert payload["file_path"] == target_path
        # app_id carries the ROM's bound shortcut_app_id (seeded as 1000 + rom_id)
        # so the frontend confirm-sets launch options without a full-library scan.
        assert payload["app_id"] == 1042
        # launch_options carries the full RetroDECK launch command for the resolved path.
        assert payload["launch_options"] == f"flatpak run --nosocket=wayland net.retrodeck.retrodeck '{target_path}'"
        # download_queue status is completed
        assert downloads.queue.entries[42]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_download_complete_app_id_null_when_unbound(self, make_downloads, tmp_path, emit):
        """A ROM downloaded before it's synced (no Steam shortcut) emits ``app_id: None``.

        The ROM row exists (FK parent for the install) but its
        ``shortcut_app_id`` is ``None`` — so ``download_complete`` carries
        ``app_id == None`` and the frontend handler no-ops gracefully.
        """
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "metroid.z64")

        rom_detail = {
            "id": 7,
            "name": "Metroid",
            "fs_name": "metroid.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        # Unbound ROM row (FK parent for the install) — no Steam shortcut yet.
        with downloads.uow:
            downloads.uow.roms.save(
                Rom(
                    rom_id=7,
                    platform_slug="n64",
                    name="Metroid",
                    fs_name="metroid.z64",
                    shortcut_app_id=None,
                    last_synced_at="2025-01-01T00:00:00",
                )
            )
        downloads.queue.enqueue(7, {"rom_id": 7, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(7, rom_detail, target_path, "n64", "metroid.z64", _admitted(downloads, 7))

        emit_calls = [c for c in emit.call_args_list if c[0][0] == "download_complete"]
        assert len(emit_calls) == 1
        assert emit_calls[0][0][1]["app_id"] is None

    @pytest.mark.asyncio
    async def test_download_complete_records_applied_launch_options_for_bound_rom(self, make_downloads, tmp_path, emit):
        """A bound ROM's freshly baked launch command (the value the frontend
        confirm-sets onto its shortcut) is recorded as the applied state, so the
        next sync skips the now-correct shortcut instead of re-touching it (#1383)."""
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")
        rom_detail = {"id": 42, "name": "Zelda", "fs_name": "zelda.z64", "platform_slug": "n64", "platform_name": "N64"}

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        _seed_rom(downloads.uow, 42)  # bound (app_id 1042)
        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        payload = next(c[0][1] for c in emit.call_args_list if c[0][0] == "download_complete")
        with downloads.uow as uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.applied_launch_options == payload["launch_options"]
        assert rom.applied_launch_options == f"flatpak run --nosocket=wayland net.retrodeck.retrodeck '{target_path}'"

    @pytest.mark.asyncio
    async def test_download_complete_does_not_record_applied_for_unbound_rom(self, make_downloads, tmp_path, emit):
        """A ROM downloaded before it is synced (no shortcut) records nothing — there
        is no shortcut to reflect; the next sync creates it and records the value."""
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "metroid.z64")
        rom_detail = {
            "id": 7,
            "name": "Metroid",
            "fs_name": "metroid.z64",
            "platform_slug": "n64",
            "platform_name": "N64",
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        with downloads.uow:
            downloads.uow.roms.save(
                Rom(
                    rom_id=7,
                    platform_slug="n64",
                    name="Metroid",
                    fs_name="metroid.z64",
                    shortcut_app_id=None,
                    last_synced_at="2025-01-01T00:00:00",
                )
            )
        downloads.queue.enqueue(7, {"rom_id": 7, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(7, rom_detail, target_path, "n64", "metroid.z64", _admitted(downloads, 7))

        with downloads.uow as uow:
            rom = uow.roms.get(7)
        assert rom is not None
        assert rom.applied_launch_options is None


class TestASystemFolderLinkedToAnotherDrive:
    """A system folder linked out of RetroDECK's ROM folder is the bound its games land in and are removed from."""

    @staticmethod
    def _link(make_downloads, tmp_path, system: str) -> tuple[DownloadsHarness, Path]:
        roms = tmp_path / "retrodeck" / "roms"
        drive = tmp_path / "sdcard" / system
        drive.mkdir(parents=True)
        roms.mkdir(parents=True)
        (roms / system).symlink_to(drive)
        # The resolver answers the system's folder resolved, as the adapter does.
        folders = FakeRetroDeckFolders(roms=str(roms), system_dirs={system: os.path.realpath(drive)})
        downloads = make_downloads(retrodeck_folders=folders, removal_folders=folders)
        return downloads, Path(os.path.realpath(drive))

    @pytest.mark.asyncio
    async def test_a_single_file_download_lands_there_and_its_uninstall_removes_it_there(
        self, make_downloads, tmp_path
    ):
        from unittest.mock import patch

        downloads, drive = self._link(make_downloads, tmp_path, "n64")
        target_path = str(drive / "zelda.z64")
        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        _seed_rom(downloads.uow, 42)
        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})
        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        assert os.path.exists(target_path)
        result = await downloads.removal.remove_rom(42)

        assert result["success"] is True
        assert not os.path.exists(target_path)
        assert drive.is_dir()

    @pytest.mark.asyncio
    async def test_a_multi_file_download_extracts_there_and_its_uninstall_removes_it_there(
        self, make_downloads, tmp_path
    ):
        import zipfile as zf
        from unittest.mock import patch

        downloads, drive = self._link(make_downloads, tmp_path, "psx")
        target_path = str(drive / "FF7.zip")
        archive = tmp_path / "source.zip"
        with zf.ZipFile(str(archive), "w") as z:
            z.writestr("disc1.cue", "FILE disc1.bin BINARY")
            z.writestr("disc1.bin", b"\x00" * 100)
            z.writestr("disc2.cue", "FILE disc2.bin BINARY")
            z.writestr("disc2.bin", b"\x00" * 100)
        zip_bytes = archive.read_bytes()
        rom_detail = {
            "id": 55,
            "name": "Final Fantasy VII",
            "fs_name": "FF7.zip",
            "fs_name_no_ext": "FF7",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 55, platform_slug="psx")
        downloads.queue.enqueue(55, {"rom_id": 55, "status": "downloading", "progress": 0})
        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(55, rom_detail, target_path, "psx", "FF7.zip", _admitted(downloads, 55))

        installed = downloads.uow.rom_installs.get(55)
        assert installed is not None
        assert installed.rom_dir == str(drive / "FF7.m3u")
        assert (drive / "FF7.m3u" / "disc1.cue").exists()
        result = await downloads.removal.remove_rom(55)

        assert result["success"] is True
        assert not (drive / "FF7.m3u").exists()
        assert drive.is_dir()


class TestDoDownloadOverrideRebake:
    """``download_complete`` re-bakes a per-game ``emulator_override`` into launch_options.

    This is the load-bearing site (B2): the override lives on ``roms`` precisely so
    it survives uninstall → reinstall, and reinstall flows through ``DownloadTransfer.run``.
    """

    @staticmethod
    def _harness(make_downloads, tmp_path):
        return make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

    async def _run_single_download(self, downloads, tmp_path, emit, *, rom_id, override):
        """Download one single-file ROM (bound) with ``override`` pre-pinned; return payload."""
        from unittest.mock import patch

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "game.chd")
        rom_detail = {
            "id": rom_id,
            "name": "PSX Game",
            "fs_name": "game.chd",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        _seed_rom(downloads.uow, rom_id, platform_slug="psx")
        if override is not None:
            with downloads.uow:
                downloads.uow.roms.set_emulator_override(rom_id, override)
        downloads.queue.enqueue(rom_id, {"rom_id": rom_id, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(
                rom_id, rom_detail, target_path, "psx", "game.chd", _admitted(downloads, rom_id)
            )

        emit_calls = [c for c in emit.call_args_list if c[0][0] == "download_complete"]
        assert len(emit_calls) == 1
        return emit_calls[0][0][1], target_path

    @pytest.mark.asyncio
    async def test_reinstall_with_override_rebakes_e_form(self, make_downloads, tmp_path, emit):
        """An override-set ROM's reinstall emits ``-e`` baked launch_options (B2)."""
        downloads = self._harness(make_downloads, tmp_path)
        downloads.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]
        payload, target_path = await self._run_single_download(
            downloads, tmp_path, emit, rom_id=42, override="PCSX ReARMed"
        )
        assert payload["app_id"] == 1042
        assert payload["launch_options"] == (
            "flatpak run --nosocket=wayland net.retrodeck.retrodeck "
            '-e "%EMULATOR_RETROARCH% -L /var/config/retroarch/cores/pcsx_rearmed_libretro.so %ROM%" '
            f"'{target_path}'"
        )

    @pytest.mark.asyncio
    async def test_reinstall_without_override_is_plain(self, make_downloads, tmp_path, emit):
        """A NULL-override ROM's reinstall emits the plain launch — no ``-e`` (B2)."""
        downloads = self._harness(make_downloads, tmp_path)
        downloads.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]
        payload, target_path = await self._run_single_download(downloads, tmp_path, emit, rom_id=43, override=None)
        assert payload["launch_options"] == f"flatpak run --nosocket=wayland net.retrodeck.retrodeck '{target_path}'"
        assert "-e" not in payload["launch_options"]

    @pytest.mark.asyncio
    async def test_reinstall_with_stale_override_rebakes_plain_and_warns(self, make_downloads, tmp_path, caplog, emit):
        """A stale override LABEL reinstall emits the PLAIN launch + WARNs (B4)."""
        downloads = self._harness(make_downloads, tmp_path)

        # available_cores does not carry the pinned label → resolution returns None.
        downloads.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]
        with caplog.at_level(logging.WARNING):
            payload, target_path = await self._run_single_download(
                downloads, tmp_path, emit, rom_id=44, override="Removed Core"
            )
        assert payload["launch_options"] == f"flatpak run --nosocket=wayland net.retrodeck.retrodeck '{target_path}'"
        assert "-e" not in payload["launch_options"]
        assert "Removed Core" in caplog.text
        assert "no longer resolves" in caplog.text


class TestDoDownloadMultiFile:
    """Tests for DownloadTransfer.run happy path — multi-file (ZIP)."""

    @pytest.mark.asyncio
    async def test_multi_file_happy_path(self, make_downloads, tmp_path, emit):
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "FF7.zip")

        # Create a real ZIP file that our fake download will write
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("disc1.cue", "FILE disc1.bin BINARY")
            z.writestr("disc1.bin", b"\x00" * 100)
            z.writestr("disc2.cue", "FILE disc2.bin BINARY")
            z.writestr("disc2.bin", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 55,
            "name": "Final Fantasy VII",
            "fs_name": "FF7.zip",
            "fs_name_no_ext": "FF7",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 55, platform_slug="psx")
        downloads.queue.enqueue(55, {"rom_id": 55, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(55, rom_detail, target_path, "psx", "FF7.zip", _admitted(downloads, 55))

        # ES-DE collapse: the extract dir is renamed after the auto-generated
        # M3U (incl. extension) so ES-DE shows one game entry, not a folder.
        extract_dir = roms_dir / "FF7.m3u"
        assert extract_dir.is_dir()
        assert (extract_dir / "disc1.cue").exists()
        assert (extract_dir / "disc2.cue").exists()
        # The pre-rename staging dir must not linger.
        assert not (roms_dir / "FF7").exists()
        # .zip.tmp is cleaned up
        assert not os.path.exists(target_path + ".zip.tmp")
        # RomInstall record has rom_dir pointing at the renamed dir.
        installed = downloads.uow.rom_installs.get(55)
        assert installed is not None
        assert installed.rom_dir == str(extract_dir)
        # Launch file detection: M3U generated from 2 cue files, so prefer M3U > CUE
        # (M3U auto-generated by _maybe_generate_m3u). The launch file lives
        # inside the renamed dir.
        assert installed.file_path == str(extract_dir / "FF7.m3u")
        # download_complete carries the launch command for the detected launch file.
        emit_calls = [c for c in emit.call_args_list if c[0][0] == "download_complete"]
        assert len(emit_calls) == 1
        payload = emit_calls[0][0][1]
        assert payload["file_path"] == installed.file_path
        assert (
            payload["launch_options"]
            == f"flatpak run --nosocket=wayland net.retrodeck.retrodeck '{installed.file_path}'"
        )
        # Status is completed
        assert downloads.queue.entries[55]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_nested_multi_file_takes_extract_path(self, make_downloads, tmp_path, emit):
        """#855: one top-level file but len(files) > 1 → RomM zips → EXTRACT path.

        Switch base/update/DLC: ``has_multiple_files=False`` (single top-level
        file) yet ``len(files) > 1``. RomM streams a mod_zip ZIP, so Tender
        must extract it into a per-game folder instead of writing the ZIP bytes
        verbatim into one .nsp.
        """
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "switch"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Zelda.nsp")

        # Real ZIP mirroring RomM's mod_zip output: base at root + nested update/DLC
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("Zelda.nsp", b"\x00" * 100)
            z.writestr("update/Zelda_update.nsp", b"\x00" * 100)
            z.writestr("dlc/Zelda_dlc.nsp", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 99,
            "name": "Zelda",
            "fs_name": "Zelda.nsp",
            "fs_name_no_ext": "Zelda",
            "platform_slug": "switch",
            "platform_name": "Nintendo Switch",
            "has_multiple_files": False,
            "has_nested_single_file": True,
            "files": [
                {"file_name": "Zelda.nsp"},
                {"file_name": "update/Zelda_update.nsp"},
                {"file_name": "dlc/Zelda_dlc.nsp"},
            ],
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 99, platform_slug="switch")
        downloads.queue.enqueue(99, {"rom_id": 99, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(99, rom_detail, target_path, "switch", "Zelda.nsp", _admitted(downloads, 99))

        # ZIP is extracted into a per-game folder (not written verbatim into one
        # .nsp). ES-DE collapse renames the dir after the launch file (the root
        # Zelda.nsp), so the folder is "Zelda.nsp/", not the staging "Zelda/".
        extract_dir = roms_dir / "Zelda.nsp"
        assert extract_dir.is_dir()
        assert (extract_dir / "Zelda.nsp").exists()
        assert (extract_dir / "update" / "Zelda_update.nsp").exists()
        assert (extract_dir / "dlc" / "Zelda_dlc.nsp").exists()
        # The pre-rename staging dir must not linger.
        assert not (roms_dir / "Zelda").exists()
        # The verbatim single-file artifact must NOT exist. The renamed extract
        # dir lands at roms_dir/Zelda.nsp (named after the launch file), so that
        # path exists but as a directory — never a flat verbatim .nsp file.
        assert not os.path.isfile(target_path)
        assert os.path.isdir(target_path)
        # .zip.tmp is cleaned up
        assert not os.path.exists(target_path + ".zip.tmp")
        # RomInstall record registers rom_dir (renamed extract path), and the
        # launch file_path points inside it — not a flat file written verbatim.
        installed = downloads.uow.rom_installs.get(99)
        assert installed is not None
        assert installed.rom_dir == str(extract_dir)
        assert installed.file_path == str(extract_dir / "Zelda.nsp")
        assert downloads.queue.entries[99]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_nested_multi_file_cleanup_removes_extract_dir(self, make_downloads, tmp_path, emit):
        """#855: a nested-multi download failure must remove the extract dir.

        The partial-download cleanup keys on the same multi-file gate, so a
        ZIP-extraction failure for a nested-multi ROM must tear down the
        per-game folder (the 2x-reservation multi-file branch), not leave it.
        """
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "switch"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Zelda.nsp")

        # Pre-seed an extract dir as if extraction had partially happened.
        extract_dir = roms_dir / "Zelda"
        extract_dir.mkdir()
        (extract_dir / "Zelda.nsp").write_bytes(b"\x00" * 100)

        rom_detail = {
            "id": 99,
            "name": "Zelda",
            "fs_name": "Zelda.nsp",
            "fs_name_no_ext": "Zelda",
            "platform_slug": "switch",
            "platform_name": "Nintendo Switch",
            "has_multiple_files": False,
            "has_nested_single_file": True,
            "files": [
                {"file_name": "Zelda.nsp"},
                {"file_name": "update/Zelda_update.nsp"},
            ],
        }

        def fake_download(_rom_id, _filename, _dest, _progress_callback=None, *, resume=False, on_meta=None):
            raise OSError("network died mid-download")

        downloads.queue.enqueue(99, {"rom_id": 99, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(99, rom_detail, target_path, "switch", "Zelda.nsp", _admitted(downloads, 99))

        # Failure path keyed on the multi-file gate → extract dir torn down.
        assert not extract_dir.exists()
        assert downloads.queue.entries[99]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_nested_multi_file_names_dir_from_identity_not_files_zero(self, make_downloads, tmp_path, emit):
        """#1292: a folder game served as a nested-single ZIP (PS3 MGS4) must name
        its extract dir after the ROM identity, never after ``files[0]``.

        For ``has_nested_single_file`` ROMs ``resolve_local_file_name`` returns
        ``files[0].file_name`` — an arbitrary inner asset ("AttackoftheDwarfGekko.dbm",
        a music file). Deriving the extract dir from it named the whole install
        after that asset and corrupted the folder-boot launch target. The dir must
        come from ``fs_name_no_ext`` ("Metal Gear Solid 4") instead.
        """
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "ps3"
        roms_dir.mkdir(parents=True)
        # target_path is derived from files[0] (the nested-single filename) — for a
        # multi-file ROM it only ever names the transient .zip.tmp, never the dir.
        target_path = str(roms_dir / "AttackoftheDwarfGekko.dbm")

        # ZIP mirroring RomM's mod_zip output: PS3_GAME at the root (no wrapper
        # folder) plus the arbitrary asset RomM lists first.
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("AttackoftheDwarfGekko.dbm", b"\x00" * 32)
            z.writestr("PS3_GAME/USRDIR/EBOOT.BIN", b"\x00" * 64)
            z.writestr("PS3_GAME/PARAM.SFO", b"\x00" * 16)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 4778,
            "name": "Metal Gear Solid 4",
            "fs_name": "Metal Gear Solid 4",
            "fs_name_no_ext": "Metal Gear Solid 4",
            "platform_slug": "ps3",
            "platform_name": "PlayStation 3",
            "has_multiple_files": False,
            "has_nested_single_file": True,
            "files": [
                {"file_name": "AttackoftheDwarfGekko.dbm"},
                {"file_name": "PS3_GAME/USRDIR/EBOOT.BIN"},
                {"file_name": "PS3_GAME/PARAM.SFO"},
            ],
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 4778, platform_slug="ps3")
        downloads.queue.enqueue(4778, {"rom_id": 4778, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(
                4778, rom_detail, target_path, "ps3", "AttackoftheDwarfGekko.dbm", _admitted(downloads, 4778)
            )

        # The extract dir carries the ROM identity, NOT the files[0] asset name.
        extract_dir = roms_dir / "Metal Gear Solid 4"
        assert extract_dir.is_dir()
        assert (extract_dir / "PS3_GAME" / "USRDIR" / "EBOOT.BIN").exists()
        # The files[0]-derived dir must NEVER exist.
        assert not (roms_dir / "AttackoftheDwarfGekko").exists()
        # EBOOT.BIN is nested, so no ES-DE collapse rename — the dir keeps the
        # identity name and the launch file points inside it.
        installed = downloads.uow.rom_installs.get(4778)
        assert installed is not None
        assert installed.rom_dir == str(extract_dir)
        assert installed.file_path == str(extract_dir / "PS3_GAME" / "USRDIR" / "EBOOT.BIN")
        assert downloads.queue.entries[4778]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_nested_multi_file_cleanup_targets_identity_dir_not_files_zero(self, make_downloads, tmp_path, emit):
        """#1292: a failed folder-game download tears down the identity-named
        extract dir, not a ``files[0]``-derived stale name.

        The extract dir is created and cleaned up from the SAME
        ROM-identity base name, so a failure never orphans the real dir while
        removing a phantom one.
        """
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "ps3"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "AttackoftheDwarfGekko.dbm")

        # Pre-seed the identity-named extract dir as if extraction had partially run.
        extract_dir = roms_dir / "Metal Gear Solid 4"
        extract_dir.mkdir()
        (extract_dir / "PS3_GAME").mkdir()

        rom_detail = {
            "id": 4778,
            "name": "Metal Gear Solid 4",
            "fs_name": "Metal Gear Solid 4",
            "fs_name_no_ext": "Metal Gear Solid 4",
            "platform_slug": "ps3",
            "platform_name": "PlayStation 3",
            "has_multiple_files": False,
            "has_nested_single_file": True,
            "files": [
                {"file_name": "AttackoftheDwarfGekko.dbm"},
                {"file_name": "PS3_GAME/USRDIR/EBOOT.BIN"},
            ],
        }

        def fake_download(_rom_id, _filename, _dest, _progress_callback=None, *, resume=False, on_meta=None):
            raise OSError("network died mid-download")

        downloads.queue.enqueue(4778, {"rom_id": 4778, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(
                4778, rom_detail, target_path, "ps3", "AttackoftheDwarfGekko.dbm", _admitted(downloads, 4778)
            )

        # The identity-named dir is torn down; the files[0]-derived name was never
        # the target, so nothing orphans.
        assert not extract_dir.exists()
        assert downloads.queue.entries[4778]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_multi_file_emits_extracting_progress(self, make_downloads, tmp_path, emit, monkeypatch):
        """A multi-file download emits ``download_progress`` ``status:"extracting"``
        frames after the byte transfer, then ``download_complete`` after.

        Drives the real adapter's streaming extraction through the fake store so
        the passed extract callback fires per member; the final tick (extracted
        == total) always bypasses the throttle, so at least one extracting frame
        lands regardless of the fake clock. Every executor hop settles behind the
        ticks it queued, the order 3.12 keeps: from 3.13 a hop that finished
        before it was chained settles at once, and its ticks are then dropped as
        the comment at ``DownloadQueue._live_entry`` says.
        """
        from unittest.mock import patch

        loop = asyncio.get_running_loop()
        run_in_executor = loop.run_in_executor

        def settle_behind_its_ticks(executor, fn, *args):
            hop = run_in_executor(executor, fn, *args)
            behind = loop.create_future()

            def copy(done):
                if done.cancelled():
                    behind.cancel()
                elif done.exception() is not None:
                    behind.set_exception(done.exception())
                else:
                    behind.set_result(done.result())

            hop.add_done_callback(lambda done: loop.call_soon(copy, done))
            return behind

        monkeypatch.setattr(loop, "run_in_executor", settle_behind_its_ticks)

        from fakes.fake_download_file_store import FakeDownloadFileStore

        fake = FakeDownloadFileStore()
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            download_file_store=fake,
        )

        roms_base = str(tmp_path / "retrodeck" / "roms")
        target_path = os.path.join(roms_base, "psx", "FF7.zip")
        tmp_zip = target_path + ".zip.tmp"

        fake.make_dirs(roms_base)
        # The ZIP the fake "extracts": two members so total > any single member.
        fake.set_zip_members(tmp_zip, {"disc1.bin": b"\x00" * 600, "disc2.bin": b"\x00" * 400})

        rom_detail = {
            "id": 55,
            "name": "Final Fantasy VII",
            "fs_name": "FF7.zip",
            "fs_name_no_ext": "FF7",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            # Populate the fake store's virtual filesystem with the tmp zip so
            # the subsequent extract_zip finds it and drives the callback.
            fake.files[dest] = b"ZIPDATA"

        _seed_rom(downloads.uow, 55, platform_slug="psx")
        downloads.queue.enqueue(
            55,
            {
                "rom_id": 55,
                "rom_name": "Final Fantasy VII",
                "platform_name": "PlayStation",
                "file_name": "FF7.zip",
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
                "resumable": False,
            },
        )

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(55, rom_detail, target_path, "psx", "FF7.zip", _admitted(downloads, 55))

        # Drain the spawned emits marshaled onto the loop.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        progress_calls = [c for c in emit.call_args_list if c[0][0] == "download_progress"]
        extracting = [c[0][1] for c in progress_calls if c[0][1].get("status") == "extracting"]
        assert extracting, "expected at least one extracting download_progress frame"
        frame = extracting[-1]
        total = 1000
        assert frame["status"] == "extracting"
        assert frame["resumable"] is False
        assert frame["bytes_downloaded"] == total
        assert frame["total_bytes"] == total
        assert frame["progress"] == 1.0
        assert frame["rom_id"] == 55
        assert frame["file_name"] == "FF7.zip"

        # The queue entry reflects the extracting phase's last tick.
        # download_complete still fires after extraction.
        complete = [c for c in emit.call_args_list if c[0][0] == "download_complete"]
        assert len(complete) == 1
        assert downloads.queue.entries[55]["status"] == "completed"


class TestADownloadUnderWay:
    """A download that started lands where it started; only a finding of RetroDECK's stops its extraction."""

    @staticmethod
    async def _run(make_downloads, tmp_path, folders: FakeRetroDeckFolders) -> str:
        from unittest.mock import patch

        from fakes.fake_download_file_store import FakeDownloadFileStore

        roms_base = str(tmp_path / "retrodeck" / "roms")
        target_path = os.path.join(roms_base, "psx", "FF7.zip")
        fake = FakeDownloadFileStore()
        fake.make_dirs(roms_base)
        fake.set_zip_members(target_path + ".zip.tmp", {"disc1.bin": b"\x00" * 6, "disc2.bin": b"\x00" * 4})
        downloads = make_downloads(retrodeck_folders=folders, download_file_store=fake)
        rom_detail = {
            "id": 55,
            "name": "Final Fantasy VII",
            "fs_name": "FF7.zip",
            "fs_name_no_ext": "FF7",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            fake.files[dest] = b"ZIPDATA"

        _seed_rom(downloads.uow, 55, platform_slug="psx")
        downloads.queue.enqueue(
            55,
            {
                "rom_id": 55,
                "rom_name": "Final Fantasy VII",
                "platform_name": "PlayStation",
                "file_name": "FF7.zip",
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
                "resumable": False,
            },
        )
        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(55, rom_detail, target_path, "psx", "FF7.zip", _admitted(downloads, 55))
        await asyncio.sleep(0)
        return downloads.queue.entries[55]["status"]

    @pytest.mark.asyncio
    async def test_retrodeck_switched_off_after_the_start_still_lets_it_finish(self, make_downloads, tmp_path):
        folders = FakeRetroDeckFolders(
            roms=str(tmp_path / "retrodeck" / "roms"), download_refusal=switched_off(GAME_DOWNLOAD)
        )

        assert await self._run(make_downloads, tmp_path, folders) == "completed"

    @pytest.mark.asyncio
    async def test_a_finding_that_makes_its_folders_defaults_stops_the_extraction(self, make_downloads, tmp_path):
        folders = FakeRetroDeckFolders(
            roms=str(tmp_path / "retrodeck" / "roms"), refusal=finding_refusal("marker-invalid", {})
        )

        assert await self._run(make_downloads, tmp_path, folders) == "failed"


class TestDoDownloadBundledM3uPlatformGate:
    """#1111: a RomM-bundled .m3u must not drive launch/collapse on non-m3u systems.

    RomM zips a platform-blind ``.m3u`` into every multi-file game. On a system
    whose emulator can't read a playlist (Switch, Xbox 360), ES-DE does not list
    ``.m3u`` as a supported extension, so the ``<Game>.m3u/`` folder never
    collapses and the launch points at an unusable file. The fix gates both the
    launch-file pick and the collapse-dir name on ``m3u_support(system)``.
    """

    @pytest.mark.asyncio
    async def test_bundled_m3u_ignored_on_non_m3u_platform(self, make_downloads, tmp_path, emit):
        """Switch ZIP with a bundled .m3u + real .nsp, m3u unsupported → launch
        is the .nsp and the collapse dir is ``<Game>.nsp/``, NOT ``<Game>.m3u/``."""
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )
        # Switch does not list .m3u in ES-DE's es_systems.xml.
        downloads.m3u_supported = False

        roms_dir = tmp_path / "retrodeck" / "roms" / "switch"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Zelda.nsp")

        # RomM ships a bundled platform-blind .m3u alongside the real .nsp.
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("Zelda.m3u", "Zelda.nsp\n")
            z.writestr("Zelda.nsp", b"\x00" * 5000)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 111,
            "name": "Zelda",
            "fs_name": "Zelda.nsp",
            "fs_name_no_ext": "Zelda",
            "platform_slug": "switch",
            "platform_name": "Nintendo Switch",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 111, platform_slug="switch")
        downloads.queue.enqueue(111, {"rom_id": 111, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(111, rom_detail, target_path, "switch", "Zelda.nsp", _admitted(downloads, 111))

        # Collapse dir is named after the real launch file, not the bundled m3u.
        nsp_dir = roms_dir / "Zelda.nsp"
        assert nsp_dir.is_dir()
        assert not (roms_dir / "Zelda.m3u").exists()
        # The bundled .m3u is left inert on disk (no destructive op), but is NOT
        # the launch file.
        installed = downloads.uow.rom_installs.get(111)
        assert installed is not None
        assert installed.rom_dir == str(nsp_dir)
        assert installed.file_path == str(nsp_dir / "Zelda.nsp")
        assert (nsp_dir / "Zelda.m3u").exists()
        assert downloads.queue.entries[111]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_disc_platform_still_generates_and_uses_m3u(self, make_downloads, tmp_path, emit):
        """Mirror positive: a disc system (m3u supported) still generates/keeps
        the m3u and names the collapse dir ``<Game>.m3u/``."""
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )
        # psx lists .m3u in ES-DE's es_systems.xml.
        downloads.m3u_supported = True

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "FF7.zip")

        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("disc1.cue", "FILE disc1.bin BINARY")
            z.writestr("disc1.bin", b"\x00" * 100)
            z.writestr("disc2.cue", "FILE disc2.bin BINARY")
            z.writestr("disc2.bin", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 112,
            "name": "Final Fantasy VII",
            "fs_name": "FF7.zip",
            "fs_name_no_ext": "FF7",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 112, platform_slug="psx")
        downloads.queue.enqueue(112, {"rom_id": 112, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(112, rom_detail, target_path, "psx", "FF7.zip", _admitted(downloads, 112))

        m3u_dir = roms_dir / "FF7.m3u"
        assert m3u_dir.is_dir()
        installed = downloads.uow.rom_installs.get(112)
        assert installed is not None
        assert installed.rom_dir == str(m3u_dir)
        assert installed.file_path == str(m3u_dir / "FF7.m3u")
        assert downloads.queue.entries[112]["status"] == "completed"


class TestEsDeCollapseRename:
    """Tests for the ES-DE directory-collapse rename on new multi-file downloads (#943)."""

    def _wire_paths(self, make_downloads, tmp_path):
        return make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

    async def _run_multi(self, make_downloads, tmp_path, *, rom_id, platform, archive_name, zip_members, rom_detail):
        import zipfile as zf
        from unittest.mock import patch

        downloads = self._wire_paths(make_downloads, tmp_path)
        roms_dir = tmp_path / "retrodeck" / "roms" / platform
        roms_dir.mkdir(parents=True, exist_ok=True)
        target_path = str(roms_dir / archive_name)

        zip_content_path = tmp_path / f"source_{rom_id}.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            for name, data in zip_members.items():
                z.writestr(name, data)
        zip_bytes = zip_content_path.read_bytes()

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, rom_id, platform_slug=platform)
        downloads.queue.enqueue(rom_id, {"rom_id": rom_id, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(
                rom_id, rom_detail, target_path, platform, archive_name, _admitted(downloads, rom_id)
            )
        return downloads, roms_dir

    @pytest.mark.asyncio
    async def test_explicit_m3u_dir_named_after_launch_file(self, make_downloads, tmp_path):
        """A multi-file ZIP shipping its own .m3u → dir renamed to '<name>.m3u/'."""
        downloads, roms_dir = await self._run_multi(
            make_downloads,
            tmp_path,
            rom_id=70,
            platform="psx",
            archive_name="FF7.zip",
            zip_members={
                "FF7.m3u": "disc1.cue\ndisc2.cue\n",
                "disc1.cue": "FILE disc1.bin BINARY",
                "disc1.bin": b"\x00" * 100,
                "disc2.cue": "FILE disc2.bin BINARY",
                "disc2.bin": b"\x00" * 100,
            },
            rom_detail={
                "id": 70,
                "name": "Final Fantasy VII",
                "fs_name": "FF7.zip",
                "fs_name_no_ext": "FF7",
                "platform_slug": "psx",
                "platform_name": "PlayStation",
                "has_multiple_files": True,
            },
        )
        extract_dir = roms_dir / "FF7.m3u"
        assert extract_dir.is_dir()
        assert not (roms_dir / "FF7").exists()
        installed = downloads.uow.rom_installs.get(70)
        assert installed is not None
        assert os.path.basename(installed.rom_dir) == "FF7.m3u"
        assert installed.file_path == str(extract_dir / "FF7.m3u")
        assert installed.file_path.endswith(".m3u")

    @pytest.mark.asyncio
    async def test_auto_generated_m3u_names_dir(self, make_downloads, tmp_path):
        """No .m3u in the ZIP but multiple disc files → dir named after the generated <name>.m3u."""
        downloads, roms_dir = await self._run_multi(
            make_downloads,
            tmp_path,
            rom_id=71,
            platform="psx",
            archive_name="Chrono Cross.zip",
            zip_members={
                "disc1.cue": "FILE disc1.bin BINARY",
                "disc1.bin": b"\x00" * 100,
                "disc2.cue": "FILE disc2.bin BINARY",
                "disc2.bin": b"\x00" * 100,
            },
            rom_detail={
                "id": 71,
                "name": "Chrono Cross",
                "fs_name": "Chrono Cross.zip",
                "fs_name_no_ext": "Chrono Cross",
                "platform_slug": "psx",
                "platform_name": "PlayStation",
                "has_multiple_files": True,
            },
        )
        extract_dir = roms_dir / "Chrono Cross.m3u"
        assert extract_dir.is_dir()
        assert not (roms_dir / "Chrono Cross").exists()
        installed = downloads.uow.rom_installs.get(71)
        assert installed is not None
        assert os.path.basename(installed.rom_dir) == "Chrono Cross.m3u"
        assert installed.file_path == str(extract_dir / "Chrono Cross.m3u")

    @pytest.mark.asyncio
    async def test_single_cue_dir_named_after_generated_m3u(self, make_downloads, tmp_path):
        """A single .cue auto-generates a game-named M3U → dir renamed to '<name>.m3u/'.

        The generically-named ``disc1.cue`` would make a ``disc1.cue/`` folder,
        so single-disc bin/cue gets a ``<fs_name_no_ext>.m3u`` playlist (the
        launch file, since M3U outranks CUE). ES-DE then collapses the dir to a
        single game-named entry.
        """
        downloads, roms_dir = await self._run_multi(
            make_downloads,
            tmp_path,
            rom_id=72,
            platform="psx",
            archive_name="Metal Gear Solid.zip",
            zip_members={
                "disc1.cue": "FILE disc1.bin BINARY",
                "disc1.bin": b"\x00" * 200,
            },
            rom_detail={
                "id": 72,
                "name": "Metal Gear Solid",
                "fs_name": "Metal Gear Solid.zip",
                "fs_name_no_ext": "Metal Gear Solid",
                "platform_slug": "psx",
                "platform_name": "PlayStation",
                "has_multiple_files": True,
            },
        )
        extract_dir = roms_dir / "Metal Gear Solid.m3u"
        assert extract_dir.is_dir()
        assert not (roms_dir / "Metal Gear Solid").exists()
        # The generated playlist references the original cue.
        assert (extract_dir / "Metal Gear Solid.m3u").read_text().strip() == "disc1.cue"
        installed = downloads.uow.rom_installs.get(72)
        assert installed is not None
        assert os.path.basename(installed.rom_dir) == "Metal Gear Solid.m3u"
        assert installed.file_path == str(extract_dir / "Metal Gear Solid.m3u")
        assert installed.file_path.endswith(".m3u")

    @pytest.mark.asyncio
    async def test_nested_launch_file_keeps_staging_dir_name(self, make_downloads, tmp_path):
        """Launch file nested in a subdir (WiiU loadiine) → no rename, staging name kept."""
        downloads, roms_dir = await self._run_multi(
            make_downloads,
            tmp_path,
            rom_id=75,
            platform="wiiu",
            archive_name="Loadiine Game.zip",
            zip_members={
                "code/Game.rpx": b"\x00" * 200,
                "content/data.bin": b"\x00" * 50,
                "meta/meta.xml": b"<xml/>",
            },
            rom_detail={
                "id": 75,
                "name": "Loadiine Game",
                "fs_name": "Loadiine Game.zip",
                "fs_name_no_ext": "Loadiine Game",
                "platform_slug": "wiiu",
                "platform_name": "Wii U",
                "has_multiple_files": True,
            },
        )
        # The launch file (.rpx) is nested under code/, so ES-DE would not
        # collapse the dir anyway — the staging name is kept unchanged.
        extract_dir = roms_dir / "Loadiine Game"
        assert extract_dir.is_dir()
        installed = downloads.uow.rom_installs.get(75)
        assert installed is not None
        assert installed.rom_dir == str(extract_dir)
        assert installed.file_path == str(extract_dir / "code" / "Game.rpx")

    @pytest.mark.asyncio
    async def test_single_file_rom_no_rename(self, make_downloads, tmp_path):
        """Single-file ROM: no extract dir, no rename — rom_dir stays None."""
        from unittest.mock import patch

        downloads = self._wire_paths(make_downloads, tmp_path)
        roms_dir = tmp_path / "retrodeck" / "roms" / "gba"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Game.gba")

        rom_detail = {
            "id": 73,
            "name": "Game",
            "fs_name": "Game.gba",
            "platform_slug": "gba",
            "platform_name": "Game Boy Advance",
            "has_multiple_files": False,
            "files": [{"file_name": "Game.gba"}],
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 100)

        _seed_rom(downloads.uow, 73, platform_slug="gba")
        downloads.queue.enqueue(73, {"rom_id": 73, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(73, rom_detail, target_path, "gba", "Game.gba", _admitted(downloads, 73))

        installed = downloads.uow.rom_installs.get(73)
        assert installed is not None
        assert installed.rom_dir is None
        assert installed.file_path == target_path
        # No extra directory was created for a flat single-file ROM.
        assert os.path.isfile(target_path)

    @pytest.mark.asyncio
    async def test_collision_skips_rename_and_keeps_staging_dir(self, make_downloads, tmp_path, caplog):
        """Pre-existing rename target → rename skipped, no clobber, install under the staging name."""
        import zipfile as zf
        from unittest.mock import patch

        downloads = self._wire_paths(make_downloads, tmp_path)
        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "FF8.zip")

        # Pre-existing dir at the rename target with a sentinel file that must survive.
        collision_dir = roms_dir / "FF8.m3u"
        collision_dir.mkdir()
        (collision_dir / "PREEXISTING.txt").write_text("do not clobber")

        zip_content_path = tmp_path / "source_collision.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("disc1.cue", "FILE disc1.bin BINARY")
            z.writestr("disc1.bin", b"\x00" * 100)
            z.writestr("disc2.cue", "FILE disc2.bin BINARY")
            z.writestr("disc2.bin", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 74,
            "name": "Final Fantasy VIII",
            "fs_name": "FF8.zip",
            "fs_name_no_ext": "FF8",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 74, platform_slug="psx")
        downloads.queue.enqueue(74, {"rom_id": 74, "status": "downloading", "progress": 0})

        with (
            caplog.at_level(logging.WARNING),
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download),
        ):
            await downloads.transfer.run(74, rom_detail, target_path, "psx", "FF8.zip", _admitted(downloads, 74))

        # The pre-existing dir is untouched — never clobbered or merged.
        assert (collision_dir / "PREEXISTING.txt").read_text() == "do not clobber"
        # The newly-extracted ROM kept the staging name (FF8/), not the target.
        staging_dir = roms_dir / "FF8"
        assert staging_dir.is_dir()
        assert (staging_dir / "FF8.m3u").exists()
        # Install still recorded, under the staging dir.
        installed = downloads.uow.rom_installs.get(74)
        assert installed is not None
        assert installed.rom_dir == str(staging_dir)
        assert installed.file_path == str(staging_dir / "FF8.m3u")
        assert downloads.queue.entries[74]["status"] == "completed"
        # Observable side effect of the collision guard: a warning was logged.
        assert any(
            "ES-DE collapse rename skipped" in rec.message and "already exists" in rec.message for rec in caplog.records
        )


class TestDoDownloadNestedSingleFile:
    """Tests for has_nested_single_file: fs_name is the parent folder, not the file (#226)."""

    @pytest.mark.asyncio
    async def test_simple_single_file_unchanged(self, make_downloads, tmp_path):
        """Regression: simple-single-file still uses fs_name as the local filename."""
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "gba"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Game.gba")

        rom_detail = {
            "id": 1,
            "name": "Game",
            "fs_name": "Game.gba",
            "platform_slug": "gba",
            "platform_name": "Game Boy Advance",
            "has_simple_single_file": True,
            "has_nested_single_file": False,
            "has_multiple_files": False,
            "files": [{"file_name": "Game.gba"}],
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 64)

        _seed_rom(downloads.uow, 1, platform_slug="gba")
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(1, rom_detail, target_path, "gba", "Game.gba", _admitted(downloads, 1))

        assert os.path.exists(target_path)
        installed = downloads.uow.rom_installs.get(1)
        assert installed is not None
        assert os.path.basename(installed.file_path) == "Game.gba"
        assert installed.file_path == target_path

    @pytest.mark.asyncio
    async def test_nested_single_file_uses_files_entry(self, make_downloads, tmp_path):
        """Happy path: has_nested_single_file derives the local filename from files[0].file_name."""
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "dc"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "My Game.chd")

        rom_detail = {
            "id": 7,
            "name": "My Game",
            "fs_name": "My Game",
            "platform_slug": "dc",
            "platform_name": "Dreamcast",
            "has_nested_single_file": True,
            "has_multiple_files": False,
            "files": [{"file_name": "My Game.chd"}],
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 128)

        _seed_rom(downloads.uow, 7, platform_slug="dc")
        downloads.queue.enqueue(7, {"rom_id": 7, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(7, rom_detail, target_path, "dc", "My Game.chd", _admitted(downloads, 7))

        assert os.path.exists(target_path)
        installed = downloads.uow.rom_installs.get(7)
        assert installed is not None
        assert os.path.basename(installed.file_path) == "My Game.chd"
        assert installed.file_path == target_path
        # Must NOT keep the parent-folder name from fs_name as a real on-disk file
        assert not os.path.exists(str(roms_dir / "My Game"))
        # #855 regression: a genuine nested-single ROM (len(files) == 1) must
        # stay on the single-file path — it flattens to a flat file and never
        # registers an extract directory.
        assert installed.rom_dir is None

    @pytest.mark.asyncio
    async def test_nested_single_file_start_download_uses_files_entry(self, make_downloads, tmp_path):
        """start_download: nested-single-file enters the queue with the resolved filename."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 7,
            "name": "Resident Evil",
            "fs_name": "Resident Evil",
            "fs_size_bytes": 1024,
            "platform_slug": "dc",
            "platform_name": "Dreamcast",
            "has_nested_single_file": True,
            "has_multiple_files": False,
            "files": [{"file_name": "Resident Evil.chd"}],
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task

        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024
        result = await downloads.service.start_download(7)

        assert result["success"] is True
        assert downloads.queue.entries[7]["file_name"] == "Resident Evil.chd"

    @pytest.mark.asyncio
    async def test_nested_single_file_empty_files_falls_back(self, make_downloads, tmp_path, caplog, logger):
        """Defensive: empty files list falls back to fs_name and logs a warning."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 8,
            "name": "My Game",
            "fs_name": "My Game",
            "fs_size_bytes": 1024,
            "platform_slug": "dc",
            "platform_name": "Dreamcast",
            "has_nested_single_file": True,
            "has_multiple_files": False,
            "files": [],
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task
        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024

        with caplog.at_level(logging.WARNING, logger=logger.name):
            result = await downloads.service.start_download(8)

        assert result["success"] is True
        assert downloads.queue.entries[8]["file_name"] == "My Game"
        assert any("has_nested_single_file" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_nested_single_file_missing_files_key_falls_back(self, make_downloads, tmp_path, caplog, logger):
        """Defensive: missing files key falls back to fs_name and logs a warning."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 9,
            "name": "My Game",
            "fs_name": "My Game",
            "fs_size_bytes": 1024,
            "platform_slug": "dc",
            "platform_name": "Dreamcast",
            "has_nested_single_file": True,
            "has_multiple_files": False,
            # no "files" key at all
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task
        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024

        with caplog.at_level(logging.WARNING, logger=logger.name):
            result = await downloads.service.start_download(9)

        assert result["success"] is True
        assert downloads.queue.entries[9]["file_name"] == "My Game"
        assert any("has_nested_single_file" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_nested_single_file_traversal_sanitized(self, make_downloads, tmp_path):
        """Defensive: path traversal in files[0].file_name is sanitized via os.path.basename."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 13,
            "name": "Evil Nested",
            "fs_name": "Evil",
            "fs_size_bytes": 1024,
            "platform_slug": "dc",
            "platform_name": "Dreamcast",
            "has_nested_single_file": True,
            "has_multiple_files": False,
            "files": [{"file_name": "../evil.chd"}],
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task

        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024
        result = await downloads.service.start_download(13)

        assert result["success"] is True
        queue_entry = downloads.queue.entries[13]
        assert queue_entry["file_name"] == "evil.chd"
        assert ".." not in queue_entry["file_name"]


class TestPathTraversalDeleteRomFiles:
    """Tests for path traversal safety in _delete_rom_files."""

    @pytest.mark.asyncio
    async def test_rejects_rom_dir_outside_roms_base(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        # Create a file outside roms dir that should NOT be deleted
        evil_dir = tmp_path / "evil"
        evil_dir.mkdir()
        evil_file = evil_dir / "important.txt"
        evil_file.write_text("do not delete")

        _seed_install(
            downloads.uow,
            99,
            file_path=str(evil_file),
            rom_dir=str(evil_dir),
        )

        coro = downloads.removal.remove_rom(99)
        with pytest.raises(Refused) as refused:
            await coro
        # The evil dir/file should NOT be deleted
        assert evil_dir.exists()
        assert evil_file.exists()
        assert refused.value.reason == "uninstall_failed"
        # Unsafe paths are failures: retain the record so the user can repair it.
        assert downloads.uow.rom_installs.get(99) is not None

    @pytest.mark.asyncio
    async def test_rejects_file_path_outside_roms_base(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        evil_file = tmp_path / "etc" / "passwd"
        evil_file.parent.mkdir(parents=True)
        evil_file.write_text("root:x:0:0")

        _seed_install(
            downloads.uow,
            99,
            file_path=str(evil_file),
            rom_dir=None,
        )

        coro = downloads.removal.remove_rom(99)
        with pytest.raises(Refused) as refused:
            await coro
        assert evil_file.exists()
        assert refused.value.reason == "uninstall_failed"
        assert downloads.uow.rom_installs.get(99) is not None


class TestPathTraversalFsName:
    """Tests for path traversal safety in download — fs_name sanitization."""

    @pytest.mark.asyncio
    async def test_fs_name_traversal_sanitized(self, make_downloads, tmp_path):

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 77,
            "name": "Evil ROM",
            "fs_name": "../../../etc/passwd",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task

        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024
        result = await downloads.service.start_download(77)

        assert result["success"] is True
        # The target path should use sanitized basename only
        queue_entry = downloads.queue.entries[77]
        assert queue_entry["file_name"] == "passwd"
        # The coroutine was created — just verify the queue entry is safe
        assert ".." not in queue_entry["file_name"]

    @pytest.mark.asyncio
    async def test_fs_name_degenerate_falls_back_to_synthetic(self, make_downloads, tmp_path):
        """A degenerate fs_name ("..") basenames to ".." — the file_name guard
        must fall back to the synthetic rom_<id>, so target_path can never
        resolve to the platform dir's parent (the roms root)."""

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 77,
            "name": "Evil ROM",
            "fs_name": "..",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task
        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024

        result = await downloads.service.start_download(77)

        assert result["success"] is True
        queue_entry = downloads.queue.entries[77]
        assert queue_entry["file_name"] == "rom_77"


class TestPathTraversalPlatformSlug:
    """#967: an unmapped server platform slug must not escape the ROM folder."""

    @pytest.mark.asyncio
    async def test_traversal_slug_rejected_before_make_dirs(self, make_downloads, tmp_path):
        roms_root = tmp_path / "retrodeck" / "roms"
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(roms_root),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        rom_detail = {
            "id": 77,
            "name": "Evil ROM",
            "fs_name": "game.z64",
            "fs_size_bytes": 1024,
            # The fake answers a platform as its own system, so the slug reaches
            # the folder question as a system name.
            "platform_slug": "../../etc",
            "platform_name": "Nintendo 64",
        }

        # Track make_dirs to prove the slug is rejected BEFORE any directory work.
        made_dirs: list[str] = []
        downloads.download_file_store.make_dirs = lambda p: made_dirs.append(p)

        from unittest.mock import patch

        coro = downloads.service.start_download(77)
        with (
            patch.object(downloads.romm_api, "get_rom", return_value=rom_detail),
            pytest.raises(FolderRefused) as refused,
        ):
            await coro

        # A slug that is no ES-DE system has no folder the resolver names.
        assert refused.value.reason == "no_rom_folder"
        # Rejected before any make_dirs.
        assert made_dirs == []
        # No directory created outside the roms root.
        escape_dir = tmp_path / "etc"
        assert not escape_dir.exists()
        # The rom is no longer marked in-progress (cleaned up on rejection).
        assert 77 not in downloads.state.in_progress


class TestAPlatformWithNoSystemDownloadsNothing:
    """A platform RetroDECK has no switched-on system for is refused before anything is fetched."""

    @pytest.mark.parametrize(
        ("state", "system", "reason"),
        [(NO_SYSTEM, None, "no_platform_system"), (SWITCHED_OFF, "xbox360", "platform_system_off")],
    )
    @pytest.mark.asyncio
    async def test_it_is_refused_naming_why(self, make_downloads, tmp_path, state, system, reason):
        platform = PlatformSystem(state, "xbox-360", "Xbox 360", system=system, source=RETRODECK_SOURCE)
        downloads = make_downloads(platform_systems=FakePlatformSystems(answers={"xbox-360": platform}))
        made_dirs: list[str] = []
        downloads.download_file_store.make_dirs = lambda p: made_dirs.append(p)
        rom_detail = {
            "id": 78,
            "name": "Halo",
            "fs_name": "halo.iso",
            "fs_size_bytes": 1024,
            "platform_slug": "xbox-360",
        }

        from unittest.mock import patch

        with (
            patch.object(downloads.romm_api, "get_rom", return_value=rom_detail),
            pytest.raises(FolderRefused) as refused,
        ):
            await downloads.service.start_download(78)

        assert refused.value.reason == reason
        assert refused.value.details == {"source": "retrodeck", "platform": "Xbox 360", "system": system}
        assert made_dirs == []
        assert 78 not in downloads.queue.entries
        assert 78 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_the_question_is_asked_off_the_loop(self, make_downloads):
        # It may read RomM's listing, which would hold every endpoint for as
        # long as the request takes.
        threads: list[threading.Thread] = []
        platform_systems = FakePlatformSystems()
        original = platform_systems.platform_system

        def recording(platform_slug, *, source=None, reading=None, ask_romm=True):
            threads.append(threading.current_thread())
            return original(platform_slug, source=source, reading=reading, ask_romm=ask_romm)

        platform_systems.platform_system = recording
        downloads = make_downloads(platform_systems=platform_systems)
        rom_detail = {"id": 80, "name": "Mario", "fs_name": "mario.sfc", "fs_size_bytes": 1024, "platform_slug": "snes"}

        from unittest.mock import patch

        with patch.object(downloads.romm_api, "get_rom", return_value=rom_detail):
            await downloads.service.start_download(80)

        assert threads
        assert threading.main_thread() not in threads

    @pytest.mark.asyncio
    async def test_the_system_is_asked_of_retrodeck_which_a_download_lands_in(self, make_downloads):
        platform_systems = FakePlatformSystems()
        downloads = make_downloads(platform_systems=platform_systems)
        rom_detail = {"id": 79, "name": "Mario", "fs_name": "mario.sfc", "fs_size_bytes": 1024, "platform_slug": "snes"}

        from unittest.mock import patch

        with patch.object(downloads.romm_api, "get_rom", return_value=rom_detail):
            await downloads.service.start_download(79)

        assert platform_systems.calls[0] == ("snes", "retrodeck")


class TestCleanupPartialDownload:
    """Tests for DownloadUnpacker.remove_partial_download — all paths."""

    def test_cleans_tmp_file_single(self, downloads, tmp_path):
        target = str(tmp_path / "game.z64")
        tmp_file = tmp_path / "game.z64.tmp"
        tmp_file.write_text("partial")

        # Single-file: no extract dir, so extract_dir_name is unused ("").
        downloads.unpacker.remove_partial_download(target, False, "")
        assert not tmp_file.exists()

    def test_cleans_zip_tmp_multi(self, downloads, tmp_path):
        target = str(tmp_path / "game.zip")
        zip_tmp = tmp_path / "game.zip.zip.tmp"
        zip_tmp.write_text("partial zip")

        downloads.unpacker.remove_partial_download(target, True, "game")
        assert not zip_tmp.exists()

    def test_cleans_extract_dir(self, downloads, tmp_path):
        target = str(tmp_path / "game.zip")
        extract_dir = tmp_path / "game"
        extract_dir.mkdir()
        (extract_dir / "disc1.bin").write_bytes(b"\x00" * 100)

        downloads.unpacker.remove_partial_download(target, True, "game")
        assert not extract_dir.exists()

    def test_cleanup_errors_are_caught(self, downloads, tmp_path):
        """Cleanup should not raise even if files don't exist."""
        target = str(tmp_path / "nonexistent.z64")
        # Should not raise
        downloads.unpacker.remove_partial_download(target, False, "")
        downloads.unpacker.remove_partial_download(target, True, "nonexistent")

    def test_cleans_renamed_extract_dir_via_final_path(self, downloads, tmp_path):
        """A failure after the ES-DE collapse rename must clean the renamed dir, not just the staging name."""
        target = str(tmp_path / "game.zip")
        # The staging dir (the ROM-identity extract_dir_name) no longer exists —
        # it was renamed. Only the renamed dir, derived from final_path, is on disk.
        renamed_dir = tmp_path / "game.m3u"
        renamed_dir.mkdir()
        (renamed_dir / "game.m3u").write_text("disc1.cue\n")
        final_path = str(renamed_dir / "game.m3u")

        downloads.unpacker.remove_partial_download(target, True, "game", final_path)
        assert not renamed_dir.exists()


class TestResolveSafeExtractDirName:
    """#1292: the extract-dir base name is ROM-identity-derived and traversal-safe."""

    def test_returns_fs_name_no_ext(self, logger):
        name = safe_extract_dir_name({"fs_name_no_ext": "Metal Gear Solid 4"}, logger)
        assert name == "Metal Gear Solid 4"

    def test_sanitizes_relative_traversal(self, caplog, logger):
        with caplog.at_level(logging.WARNING, logger=logger.name):
            name = safe_extract_dir_name({"fs_name_no_ext": "../../etc/pwned"}, logger)
        assert name == "pwned"
        assert any("Sanitized extract dir name" in rec.message for rec in caplog.records)

    def test_sanitizes_absolute_path(self, caplog, logger):
        with caplog.at_level(logging.WARNING, logger=logger.name):
            name = safe_extract_dir_name({"fs_name": "/etc/passwd"}, logger)
        assert name == "passwd"
        assert any("Sanitized extract dir name" in rec.message for rec in caplog.records)

    @pytest.mark.parametrize("degenerate", ["..", ".", "foo/", "   "])
    def test_degenerate_component_falls_back_to_synthetic(self, caplog, degenerate, logger):
        """A server-supplied name that basenames to ``..``/``.``/empty/whitespace
        must NOT resolve to the roms root or platform dir — it falls back to the
        synthetic rom_<id> identity + one warning (the HIGH-severity guard)."""

        with caplog.at_level(logging.WARNING, logger=logger.name):
            name = safe_extract_dir_name({"id": 4778, "fs_name_no_ext": degenerate}, logger)
        assert name == "rom_4778"
        assert any("Sanitized extract dir name" in rec.message for rec in caplog.records)

    def test_empty_fs_name_no_ext_yields_synthetic_without_warning(self, caplog, logger):
        """An empty ``fs_name_no_ext`` is upstream-handled by
        ``resolve_extract_dir_name`` (it falls through to the synthetic name), so
        the guard sees an already-clean component — safe result, no coercion warning."""

        with caplog.at_level(logging.WARNING, logger=logger.name):
            name = safe_extract_dir_name({"id": 4778, "fs_name_no_ext": ""}, logger)
        assert name == "rom_4778"
        assert not any("Sanitized extract dir name" in rec.message for rec in caplog.records)


class TestDoDownloadCancelled:
    """Tests for DownloadTransfer.run — cancelled mid-download."""

    @pytest.mark.asyncio
    async def test_cancelled_sets_status_and_cleans_up(self, make_downloads, tmp_path):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download_cancel(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            raise asyncio.CancelledError()

        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with (
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download_cancel),
            pytest.raises(asyncio.CancelledError),
        ):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        # A cancel is an explicit discard: the entry is evicted, not left as a
        # lingering "cancelled" row (#149 downloads-round).
        assert 42 not in downloads.queue.entries
        assert not os.path.exists(target_path)
        assert downloads.uow.rom_installs.get(42) is None


class TestDoDownloadZipFailure:
    """Tests for DownloadTransfer.run — ZIP extraction failure."""

    @pytest.mark.asyncio
    async def test_zip_failure_sets_failed_and_cleans_up(self, make_downloads, tmp_path):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "game.zip")

        rom_detail = {
            "id": 66,
            "name": "Bad ZIP Game",
            "fs_name": "game.zip",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            # Write invalid data (not a real zip)
            with open(dest, "wb") as f:
                f.write(b"not a zip file")

        downloads.queue.enqueue(66, {"rom_id": 66, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(66, rom_detail, target_path, "psx", "game.zip", _admitted(downloads, 66))

        assert downloads.queue.entries[66]["status"] == "failed"
        # .zip.tmp should be cleaned up
        assert not os.path.exists(target_path + ".zip.tmp")


class TestDoDownloadPostDecodeTraversal:
    """#968: a ZIP member that passes the ZIP-slip check but decodes to a traversal."""

    @pytest.mark.asyncio
    async def test_decoded_traversal_aborts_cleans_up_and_emits(self, make_downloads, tmp_path, emit):
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Evil.zip")

        # A ZIP whose member name is a single literal basename with NO real
        # separator (the %2e/%2f are literal chars), so it passes the pre-decode
        # ZIP-slip realpath check and extracts inside the dir — plus a legit
        # member so we can prove the already-extracted file is cleaned up.
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("legit.bin", b"\x00" * 50)
            z.writestr("%2e%2e%2fevil.sh", b"payload")
        zip_bytes = zip_content_path.read_bytes()

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        rom_detail = {
            "id": 88,
            "name": "Evil Multi",
            "fs_name": "Evil.zip",
            "fs_name_no_ext": "Evil",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        _seed_rom(downloads.uow, 88, platform_slug="psx")
        downloads.queue.enqueue(88, {"rom_id": 88, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(88, rom_detail, target_path, "psx", "Evil.zip", _admitted(downloads, 88))

        # The traversal target must NOT exist anywhere outside the extraction dir.
        assert not (roms_dir / "evil.sh").exists()
        assert not (tmp_path / "retrodeck" / "roms" / "evil.sh").exists()
        assert not (tmp_path / "evil.sh").exists()
        # Already-extracted members are cleaned up — no half-installed ROM dir.
        assert not (roms_dir / "Evil").exists()
        assert not (roms_dir / "Evil.zip").is_dir()
        # .zip.tmp cleaned up.
        assert not os.path.exists(target_path + ".zip.tmp")
        # Nothing persisted.
        assert downloads.uow.rom_installs.get(88) is None
        # Queue marked failed.
        assert downloads.queue.entries[88]["status"] == "failed"
        # download_failed fired (UI doesn't hang); offending name surfaced.
        failed = [c for c in emit.call_args_list if c[0][0] == "download_failed"]
        assert len(failed) == 1
        assert failed[0][0][1]["rom_id"] == 88
        assert "evil.sh" in failed[0][0][1]["error_message"]
        # No download_complete.
        assert not [c for c in emit.call_args_list if c[0][0] == "download_complete"]

    @pytest.mark.asyncio
    async def test_legit_multi_file_subdir_still_extracts(self, make_downloads, tmp_path, emit):
        """The #968 fix must not break a legitimate nested-subdir multi-file ROM."""
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "switch"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Game.nsp")

        # Real nested layout with URL-encoded basenames inside a real subdir.
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("Game%20Base.nsp", b"\x00" * 100)
            z.writestr("update/Game%20Update.nsp", b"\x00" * 50)
        zip_bytes = zip_content_path.read_bytes()

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        rom_detail = {
            "id": 91,
            "name": "Game",
            "fs_name": "Game.nsp",
            "fs_name_no_ext": "Game",
            "platform_slug": "switch",
            "platform_name": "Nintendo Switch",
            "has_multiple_files": True,
        }

        _seed_rom(downloads.uow, 91, platform_slug="switch")
        downloads.queue.enqueue(91, {"rom_id": 91, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(91, rom_detail, target_path, "switch", "Game.nsp", _admitted(downloads, 91))

        # The encoded names decoded correctly (per-basename) and the install
        # succeeded — the legit nested ROM is intact.
        assert downloads.queue.entries[91]["status"] == "completed"
        install = downloads.uow.rom_installs.get(91)
        assert install is not None
        rom_dir = install.rom_dir
        assert rom_dir is not None
        assert os.path.exists(os.path.join(rom_dir, "Game Base.nsp"))
        assert os.path.exists(os.path.join(rom_dir, "update", "Game Update.nsp"))
        assert [c for c in emit.call_args_list if c[0][0] == "download_complete"]


class TestDoDownloadFailureEmit:
    """Tests for DownloadTransfer.run — ``download_failed`` event emission."""

    @pytest.mark.asyncio
    async def test_failure_emits_download_failed(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, _dest, _progress_callback=None, *, resume=False, on_meta=None):
            raise OSError("simulated network drop")

        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        # download_failed event emitted with the expected payload shape
        emit_calls = [c for c in emit.call_args_list if c[0][0] == "download_failed"]
        assert len(emit_calls) == 1
        payload = emit_calls[0][0][1]
        assert payload["rom_id"] == 42
        assert payload["rom_name"] == "Zelda"
        assert payload["platform_name"] == "Nintendo 64"
        assert payload["error_message"] == "simulated network drop"
        # No download_complete in the failure path
        assert not [c for c in emit.call_args_list if c[0][0] == "download_complete"]
        # Queue status reflects the failure
        assert downloads.queue.entries[42]["status"] == "failed"
        assert downloads.queue.entries[42]["error"] == "simulated network drop"


class TestDoDownloadInvariantFailure:
    """Tests for DownloadTransfer.run — RomInstall invariant rejects the ROM data.

    A non-positive ``rom_id`` fails ``RomInstall.mark_installed``. The worker
    catches the ValueError, removes the just-installed artifact, persists no
    record, and the download is reported as failed — no exception escapes.
    """

    @pytest.mark.asyncio
    async def test_single_file_invariant_failure_cleans_up_and_persists_nothing(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 0,
            "name": "Bad ROM",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 64)

        # rom_id=0 violates RomInstall's invariant (rom_id must be positive).
        downloads.queue.enqueue(0, {"rom_id": 0, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(0, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 0))

        # Download reported as failed via the canonical failure path.
        assert downloads.queue.entries[0]["status"] == "failed"
        assert "Invalid install metadata" in downloads.queue.entries[0]["error"]
        # The just-renamed file was cleaned up — nothing left dangling.
        assert not os.path.exists(target_path)
        # No RomInstall record persisted.
        assert downloads.uow.rom_installs.get(0) is None
        assert list(downloads.uow.rom_installs.iter_all()) == []
        # download_failed emitted, no download_complete.
        assert [c for c in emit.call_args_list if c[0][0] == "download_failed"]
        assert not [c for c in emit.call_args_list if c[0][0] == "download_complete"]

    @pytest.mark.asyncio
    async def test_multi_file_invariant_failure_removes_extract_dir(self, make_downloads, tmp_path, emit):
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "FF7.zip")

        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("disc1.cue", "FILE disc1.bin BINARY")
            z.writestr("disc1.bin", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 0,
            "name": "Bad Multi ROM",
            "fs_name": "FF7.zip",
            "fs_name_no_ext": "FF7",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        downloads.queue.enqueue(0, {"rom_id": 0, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(0, rom_detail, target_path, "psx", "FF7.zip", _admitted(downloads, 0))

        assert downloads.queue.entries[0]["status"] == "failed"
        assert "Invalid install metadata" in downloads.queue.entries[0]["error"]
        # Single-disc cue auto-generates FF7.m3u, so the launch file is the M3U
        # and the dir was renamed to FF7.m3u/ before the invariant failure; the
        # cleanup must tear down that *renamed* dir. Asserting FF7/ alone would
        # be vacuous — the rename already removed it.
        assert not (roms_dir / "FF7.m3u").exists()
        assert not (roms_dir / "FF7").exists()
        # No RomInstall record persisted.
        assert downloads.uow.rom_installs.get(0) is None
        assert list(downloads.uow.rom_installs.iter_all()) == []


class TestStartDownloadReDownload:
    """Test start_download allows re-download after completion."""

    @pytest.mark.asyncio
    async def test_re_download_after_completed(self, make_downloads, tmp_path):

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task

        # Set status to completed (previous download)
        downloads.queue.enqueue(42, {"status": "completed"})

        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024
        result = await downloads.service.start_download(42)

        assert result["success"] is True
        # Re-download re-enters the queue as "queued" (#1053).
        assert downloads.queue.entries[42]["status"] == "queued"


class TestMaybeGenerateM3uMixedFormats:
    """Test M3U generation with mixed disc formats."""

    def test_mixed_cue_and_chd(self, downloads, tmp_path):
        (tmp_path / "disc1.cue").write_text("cue 1")
        (tmp_path / "disc2.chd").write_bytes(b"\x00" * 100)

        rom_detail = {"fs_name_no_ext": "Mixed Game", "name": "Mixed Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        m3u_path = tmp_path / "Mixed Game.m3u"
        assert m3u_path.exists()
        content = m3u_path.read_text().strip()
        lines = content.split("\n")
        assert len(lines) == 2
        # Should include both formats
        exts = {os.path.splitext(line)[1] for line in lines}
        assert ".cue" in exts
        assert ".chd" in exts


class TestMaybeGenerateM3uSpecialCharacters:
    """Test M3U preserves special characters in filenames."""

    def test_special_characters_preserved(self, downloads, tmp_path):
        names = [
            "Game (Disc 1) [Japan].cue",
            "Game (Disc 2) [Japan].cue",
        ]
        for name in names:
            (tmp_path / name).write_text("cue data")

        rom_detail = {"fs_name_no_ext": "Game", "name": "Game"}
        downloads.unpacker.generate_m3u(str(tmp_path), rom_detail, True)

        m3u_path = tmp_path / "Game.m3u"
        assert m3u_path.exists()
        content = m3u_path.read_text().strip()
        lines = content.split("\n")
        assert len(lines) == 2
        # Verify special chars preserved exactly
        for name in names:
            assert name in lines


class TestUninstallAllRomsMixedResults:
    """Test uninstall_all_roms with mixed success/failure."""

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        # Create a real file that can be deleted
        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        good_file = roms_dir / "game_a.z64"
        good_file.write_text("data")

        # Create another file but make deletion fail by using a non-safe path.
        bad_file = tmp_path / "outside" / "game_b.z64"
        bad_file.parent.mkdir(parents=True)
        bad_file.write_text("data")

        _seed_install(downloads.uow, 1, file_path=str(good_file), rom_dir=None)
        _seed_install(downloads.uow, 2, file_path=str(bad_file), rom_dir=None, system="snes")

        result = await downloads.removal.uninstall_all_roms()
        assert isinstance(result, UninstallIncomplete)
        # good_file should be deleted
        assert not good_file.exists()
        # bad_file should still exist (outside roms dir)
        assert bad_file.exists()
        assert result.removed_count == 1
        assert len(result.errors) == 1
        assert result.errors[0]["rom_id"] == "2"
        assert downloads.uow.rom_installs.get(1) is None
        assert downloads.uow.rom_installs.get(2) is not None


class TestRemoveRomFileAlreadyGone:
    """Test remove_rom when file is already deleted."""

    @pytest.mark.asyncio
    async def test_file_already_gone_cleans_state(self, make_downloads, tmp_path):
        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        # Install record exists but the file is gone on disk.
        _seed_install(
            downloads.uow,
            42,
            file_path=str(tmp_path / "retrodeck" / "roms" / "n64" / "gone.z64"),
            rom_dir=None,
        )

        result = await downloads.removal.remove_rom(42)
        assert result["success"] is True
        assert downloads.uow.rom_installs.get(42) is None


class TestUrlEncodedFilenameRename:
    """Tests for URL-encoded filename fix after ZIP extraction."""

    @pytest.mark.asyncio
    async def test_renames_url_encoded_files_after_extract(self, make_downloads, tmp_path, emit):
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Vagrant Story (USA).zip")

        # Create a ZIP with URL-encoded filenames (as RomM generates)
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("Vagrant%20Story%20%28USA%29.m3u", "Vagrant%20Story%20%28USA%29%20%28Disc%201%29.chd\n")
            z.writestr("Vagrant%20Story%20%28USA%29%20%28Disc%201%29.chd", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 99,
            "name": "Vagrant Story (USA)",
            "fs_name": "Vagrant Story (USA).zip",
            "fs_name_no_ext": "Vagrant Story (USA)",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 99, platform_slug="psx")
        downloads.queue.enqueue(99, {"rom_id": 99, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(
                99, rom_detail, target_path, "psx", "Vagrant Story (USA).zip", _admitted(downloads, 99)
            )

        # ES-DE collapse renames the dir after the decoded .m3u launch file.
        extract_dir = roms_dir / "Vagrant Story (USA).m3u"
        # URL-encoded filenames should be decoded
        assert (extract_dir / "Vagrant Story (USA).m3u").exists()
        assert (extract_dir / "Vagrant Story (USA) (Disc 1).chd").exists()
        # The percent-encoded versions should NOT exist
        assert not (extract_dir / "Vagrant%20Story%20%28USA%29.m3u").exists()
        assert not (extract_dir / "Vagrant%20Story%20%28USA%29%20%28Disc%201%29.chd").exists()

    @pytest.mark.asyncio
    async def test_leaves_normal_filenames_alone(self, make_downloads, tmp_path, emit):
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "FF7.zip")

        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("disc1.cue", "FILE disc1.bin BINARY")
            z.writestr("disc1.bin", b"\x00" * 100)
            z.writestr("disc2.cue", "FILE disc2.bin BINARY")
            z.writestr("disc2.bin", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()

        rom_detail = {
            "id": 55,
            "name": "Final Fantasy VII",
            "fs_name": "FF7.zip",
            "fs_name_no_ext": "FF7",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)

        _seed_rom(downloads.uow, 55, platform_slug="psx")
        downloads.queue.enqueue(55, {"rom_id": 55, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(55, rom_detail, target_path, "psx", "FF7.zip", _admitted(downloads, 55))

        # ES-DE collapse renames the dir after the auto-generated FF7.m3u.
        extract_dir = roms_dir / "FF7.m3u"
        # Normal filenames should be unchanged
        assert (extract_dir / "disc1.cue").exists()
        assert (extract_dir / "disc1.bin").exists()
        assert (extract_dir / "disc2.cue").exists()
        assert (extract_dir / "disc2.bin").exists()


class TestPruneDownloadQueue:
    def test_keeps_active_downloads(self, downloads):
        """Active (downloading) items are never pruned."""
        for i in range(60):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "downloading"})
        downloads.queue.prune()
        assert len(downloads.queue.entries) == 60

    def test_removes_oldest_terminal_when_over_limit(self, downloads):
        """When there are more than 50 terminal items, remove the oldest."""
        # Insert 60 completed items (rom_id 0..59)
        for i in range(60):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "completed"})
        downloads.queue.prune()
        # Should keep the 50 most recent (10..59)
        assert len(downloads.queue.entries) == 50
        for i in range(10):
            assert i not in downloads.queue.entries
        for i in range(10, 60):
            assert i in downloads.queue.entries

    def test_does_nothing_when_under_limit(self, downloads):
        """No pruning if terminal count is at or below the limit."""
        for i in range(30):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "completed"})
        downloads.queue.prune()
        assert len(downloads.queue.entries) == 30

    def test_does_nothing_at_exactly_limit(self, downloads):
        """No pruning when terminal count is exactly 50."""
        for i in range(50):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "failed"})
        downloads.queue.prune()
        assert len(downloads.queue.entries) == 50

    def test_mixed_active_and_terminal(self, downloads):
        """Active items are kept; only terminal items count toward the limit."""
        # 5 active + 55 completed = 55 terminal -> prune 5 oldest terminal
        for i in range(5):
            downloads.queue.enqueue(1000 + i, {"rom_id": 1000 + i, "status": "downloading"})
        for i in range(55):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "completed"})
        downloads.queue.prune()
        # 5 active + 50 terminal = 55 total
        assert len(downloads.queue.entries) == 55
        # All active still present
        for i in range(5):
            assert 1000 + i in downloads.queue.entries
        # Oldest 5 terminal removed (0..4)
        for i in range(5):
            assert i not in downloads.queue.entries
        # Remaining terminal still present (5..54)
        for i in range(5, 55):
            assert i in downloads.queue.entries

    def test_handles_all_terminal_statuses(self, downloads):
        """Completed, failed, and cancelled items are all treated as terminal."""
        for i in range(20):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "completed"})
        for i in range(20, 40):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "failed"})
        for i in range(40, 60):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "cancelled"})
        downloads.queue.prune()
        assert len(downloads.queue.entries) == 50
        # Oldest 10 (all completed, 0..9) should be removed
        for i in range(10):
            assert i not in downloads.queue.entries


class TestStartDownloadSpawnFailure:
    """Tests for start_download when spawn raises."""

    @pytest.mark.asyncio
    async def test_spawn_failure_propagates_and_releases_the_flag(self, make_downloads, tmp_path):

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            ),
            removal_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
            ),
            loop=MagicMock(),
        )

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _raise_after_closing(coro, _name):
            # Close the spawned transfer coroutine before raising so it
            # isn't left un-awaited (RuntimeWarning); the raise still drives the
            # spawn-failure branch under test.
            coro.close()
            raise RuntimeError("loop closed")

        downloads.service._tasks.spawn = _raise_after_closing

        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024
        coro = downloads.service.start_download(42)
        with pytest.raises(RuntimeError, match="loop closed"):
            await coro

        # Should not remain in download_in_progress
        assert 42 not in downloads.state.in_progress


class TestShutdown:
    """Tests for DownloadService.shutdown — cancel active tasks + clear tracking."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_active_tasks_and_clears(self, downloads):
        task_a = downloads.service._tasks.spawn(asyncio.Event().wait(), "download of rom 1")
        task_b = downloads.service._tasks.spawn(asyncio.Event().wait(), "download of rom 2")
        downloads.state.tasks[1] = task_a
        downloads.state.tasks[2] = task_b

        await downloads.service.shutdown()

        assert task_a.cancelled()
        assert task_b.cancelled()
        assert downloads.state.tasks == {}

    @pytest.mark.asyncio
    async def test_shutdown_no_tasks_is_noop(self, downloads):
        # No tasks registered — must not raise.
        await downloads.service.shutdown()
        assert downloads.state.tasks == {}


class TestTheProgressFramesAreHeld:
    """Every ``download_progress`` frame starts through ``BackgroundTasks``."""

    @staticmethod
    def _live_entry(downloads) -> None:
        downloads.queue.enqueue(
            7,
            {
                "rom_id": 7,
                "rom_name": "Mario",
                "platform_name": "N64",
                "file_name": "mario.z64",
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )

    @pytest.mark.asyncio
    async def test_a_progress_tick_starts_its_frame_through_the_helper(self, downloads, monkeypatch, emit):
        spawned = record_spawns(monkeypatch)
        self._live_entry(downloads)

        downloads.queue._apply_progress("downloading", 7, "Mario", "N64", "mario.z64", 0.5, 512, 1024)
        await asyncio.sleep(0)

        assert spawned == ["download_progress"]
        assert [c[0][0] for c in emit.call_args_list] == ["download_progress"]

    @pytest.mark.asyncio
    async def test_the_resumability_verdict_starts_its_frame_through_the_helper(self, downloads, monkeypatch, emit):
        spawned = record_spawns(monkeypatch)
        self._live_entry(downloads)

        downloads.queue.make_on_meta(7, "Mario", "N64", "mario.z64")(True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert spawned == ["download_progress"]
        assert [c[0][1]["resumable"] for c in emit.call_args_list] == [True]

    @pytest.mark.asyncio
    async def test_cancelling_a_paused_download_starts_its_frame_through_the_helper(self, downloads, monkeypatch):
        spawned = record_spawns(monkeypatch)
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "rom_name": "Zelda",
                "platform_name": "Nintendo 64",
                "file_name": "zelda.z64",
                "status": "paused",
            },
        )

        downloads.service.cancel_download(42)
        await asyncio.sleep(0)

        assert spawned == ["download_progress"]


class TestShutdownWaitsForTheDownloads:
    @pytest.mark.asyncio
    async def test_a_download_still_running_has_ended_when_shutdown_returns(self, downloads):
        ended: list[str] = []

        async def downloading() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                ended.append("cleaned up")
                raise

        downloads.state.tasks[1] = downloads.service._tasks.spawn(downloading(), "download of rom 1")
        await asyncio.sleep(0)

        await asyncio.wait_for(downloads.service.shutdown(), 5)

        assert ended == ["cleaned up"]
        assert downloads.state.tasks == {}

    @pytest.mark.asyncio
    async def test_a_frame_still_being_sent_has_ended_when_shutdown_returns(self, make_downloads):
        ended: list[str] = []

        async def sending(_event, _payload) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                ended.append("frame cancelled")
                raise

        downloads = make_downloads(emit=sending)
        downloads.queue.spawn_frame({"rom_id": 1})
        await asyncio.sleep(0)

        await asyncio.wait_for(downloads.service.shutdown(), 5)

        assert ended == ["frame cancelled"]


class TestMakeProgressCallback:
    """Tests for DownloadQueue.make_progress_callback — throttling, logging, emission."""

    def test_progress_callback_updates_queue_and_dispatches_emit(self, make_downloads):
        # Pre-populate the queue entry the callback updates in place.
        fake_clock = FakeClock()

        def _record_emit(event, payload):
            emit_calls.append((event, payload))

            # Return a coroutine because the real emit is awaitable —
            # the closure passes it to spawn.
            async def _noop():
                return None

            return _noop()

        downloads = make_downloads(clock=fake_clock, loop=MagicMock(), emit=_record_emit)
        downloads.queue.enqueue(
            7,
            {
                "rom_id": 7,
                "rom_name": "Mario",
                "platform_name": "N64",
                "file_name": "mario.z64",
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )

        # Replace the event loop with a MagicMock so call_soon_threadsafe
        # is observable without actually scheduling on the real loop.
        # Run spawn eagerly inside call_soon_threadsafe so the
        # coroutine returned by emit() gets consumed (otherwise it
        # leaks as un-awaited).
        scheduled_calls: list[int] = []

        def _eager_call_soon_threadsafe(fn, *args, **kwargs):
            scheduled_calls.append(1)
            return fn(*args, **kwargs)

        downloads.loop.call_soon_threadsafe = _eager_call_soon_threadsafe
        downloads.queue._tasks.spawn = lambda coro, _name: coro.close() or MagicMock()
        emit_calls = []

        cb = downloads.queue.make_progress_callback(7, "Mario", "N64", "mario.z64")
        # Advance the clock so both branches fire (log-throttle >= 30s
        # AND emit-throttle >= 0.5s — both gated on now - last_<x>).
        fake_clock.advance(60)

        cb(512, 1024)

        # Queue entry must have been updated in place.
        entry = downloads.queue.entries[7]
        assert entry["progress"] == 0.5
        assert entry["bytes_downloaded"] == 512
        assert entry["total_bytes"] == 1024

        # call_soon_threadsafe must have been invoked once to schedule
        # the emit coroutine.
        assert len(scheduled_calls) == 1
        # Emit was called with the right event name + payload shape.
        assert len(emit_calls) == 1
        event, payload = emit_calls[0]
        assert event == "download_progress"
        assert payload["rom_id"] == 7
        assert payload["progress"] == 0.5
        assert payload["bytes_downloaded"] == 512
        assert payload["total_bytes"] == 1024

    def test_progress_callback_throttles_intermediate_emits(self, make_downloads):
        fake_clock = FakeClock()

        def _record_emit(_event, _payload):
            async def _noop():
                return None

            return _noop()

        downloads = make_downloads(clock=fake_clock, loop=MagicMock(), emit=_record_emit)
        downloads.queue.enqueue(
            8,
            {
                "rom_id": 8,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )
        # The queue mutation happens inside the function marshaled onto the
        # loop thread via call_soon_threadsafe, so run it eagerly to
        # observe the in-place update; spawn just consumes the coroutine.
        downloads.loop.call_soon_threadsafe = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        downloads.queue._tasks.spawn = lambda coro, _name: coro.close() or MagicMock()
        call_soon_count = [0]
        _orig_css = downloads.loop.call_soon_threadsafe

        def _counting_css(fn, *args, **kwargs):
            call_soon_count[0] += 1
            return _orig_css(fn, *args, **kwargs)

        downloads.loop.call_soon_threadsafe = _counting_css

        cb = downloads.queue.make_progress_callback(8, "Game", "Plat", "game.bin")

        # First call: monotonic == 0; last_emit starts at 0.0 too, but
        # downloaded < total so the throttle check (now - last_emit <
        # 0.5 AND downloaded < total) returns early. No update.
        cb(100, 1000)
        assert downloads.queue.entries[8]["bytes_downloaded"] == 0
        assert call_soon_count[0] == 0

        # Final call: downloaded == total bypasses the throttle even
        # when no time elapsed — the closure always emits the final
        # completion frame.
        cb(1000, 1000)
        assert downloads.queue.entries[8]["bytes_downloaded"] == 1000
        assert call_soon_count[0] == 1

    def test_progress_callback_handles_zero_total(self, make_downloads):
        """total == 0 must not divide-by-zero — pct/progress fall back to 0."""
        fake_clock = FakeClock()
        downloads = make_downloads(clock=fake_clock, loop=MagicMock(), emit=MagicMock(return_value=None))
        downloads.queue.enqueue(
            9,
            {
                "rom_id": 9,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )

        cb = downloads.queue.make_progress_callback(9, "Game", "Plat", "game.bin")
        # Advance past both throttles so the log + emit branches both
        # execute with total == 0 — exercises the zero-guard branches.
        fake_clock.advance(60)
        cb(0, 0)

        entry = downloads.queue.entries[9]
        assert entry["progress"] == 0
        assert entry["total_bytes"] == 0
        # Emit was still scheduled — final-frame path triggers when
        # downloaded >= total (both zero satisfies the >= check).
        assert downloads.loop.call_soon_threadsafe.call_count == 1


class TestProgressPhase:
    """The downloading and extracting phases share one callback and differ only in
    the frame's ``status``, what they do to the entry's status, the ``resumable``
    they report, and the log line's label."""

    def _eager_loop(self, make_downloads):
        """Build with a loop that runs the marshaled update synchronously; return the harness and the recorded emits."""
        loop = MagicMock()
        loop.call_soon_threadsafe = lambda fn, *a, **k: fn(*a, **k)
        emit_calls: list[tuple[str, dict[str, Any]]] = []

        def _record_emit(event, payload):
            emit_calls.append((event, payload))

            async def _noop():
                return None

            return _noop()

        fake_clock = FakeClock()
        fake_clock.advance(60)  # clear both throttles
        downloads = make_downloads(loop=loop, emit=_record_emit, clock=fake_clock)
        downloads.queue._tasks.spawn = lambda coro, _name: coro.close() or MagicMock()
        return downloads, emit_calls

    def _seed_entry(self, downloads, rom_id, *, status):
        downloads.queue.enqueue(
            rom_id,
            {
                "rom_id": rom_id,
                "status": status,
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
                "resumable": True,
            },
        )

    def test_extracting_flips_the_entry_and_is_never_resumable(self, make_downloads):
        downloads, emit_calls = self._eager_loop(make_downloads)
        self._seed_entry(downloads, 7, status="downloading")

        cb = downloads.queue.make_progress_callback(7, "Mario", "N64", "mario.zip", phase="extracting")
        cb(512, 1024)

        entry = downloads.queue.entries[7]
        assert entry["status"] == "extracting"
        assert entry["bytes_downloaded"] == 512
        assert emit_calls == [
            (
                "download_progress",
                {
                    "rom_id": 7,
                    "rom_name": "Mario",
                    "platform_name": "N64",
                    "file_name": "mario.zip",
                    "status": "extracting",
                    "progress": 0.5,
                    "bytes_downloaded": 512,
                    "total_bytes": 1024,
                    "resumable": False,
                },
            )
        ]

    def test_downloading_leaves_the_entry_status_and_reports_its_resumable(self, make_downloads):
        downloads, emit_calls = self._eager_loop(make_downloads)
        self._seed_entry(downloads, 7, status="paused")

        cb = downloads.queue.make_progress_callback(7, "Mario", "N64", "mario.z64")
        cb(512, 1024)

        entry = downloads.queue.entries[7]
        assert entry["status"] == "paused"
        assert emit_calls == [
            (
                "download_progress",
                {
                    "rom_id": 7,
                    "rom_name": "Mario",
                    "platform_name": "N64",
                    "file_name": "mario.z64",
                    "status": "downloading",
                    "progress": 0.5,
                    "bytes_downloaded": 512,
                    "total_bytes": 1024,
                    "resumable": True,
                },
            )
        ]

    @pytest.mark.parametrize(("phase", "label"), [("downloading", "Download"), ("extracting", "Extract")])
    def test_the_log_line_names_the_phase(self, make_downloads, caplog, phase, label):
        downloads, _ = self._eager_loop(make_downloads)
        self._seed_entry(downloads, 7, status="downloading")

        cb = downloads.queue.make_progress_callback(7, "Mario", "N64", "mario.z64", phase=phase)
        with caplog.at_level(logging.INFO):
            cb(512 * 1024 * 1024, 1024 * 1024 * 1024)

        assert f"{label} progress: Mario — 512.0/1024.0 MB (50%)" in caplog.messages


class TestCleanupPartialDownloadFailureInjection:
    """Tests for DownloadUnpacker.remove_partial_download — adapter raises mid-cleanup.

    The cleanup loop must swallow per-path OSError so one failing
    remove never blocks the others, AND the multi-file remove_tree
    branch must swallow its own failure the same way (logged as a
    warning, no re-raise).
    """

    def test_remove_failures_are_logged_and_other_paths_still_removed(self, make_downloads, caplog, logger):
        from fakes.fake_download_file_store import FakeDownloadFileStore

        fake = FakeDownloadFileStore()
        target = "/roms/n64/game.z64"
        # Stage the two transient candidates plus a pre-existing install at the
        # bare target. Cleanup must remove only the transients (the .tmp variant
        # is marked failing) and NEVER the bare target (#1049 data-loss guard).
        fake.files[target + ZIP_TMP_EXT] = b"junk1"
        fake.files[target + TMP_EXT] = b"junk2"
        fake.files[target] = b"preexisting install"
        fake.remove_failures.add(target + TMP_EXT)
        downloads = make_downloads(download_file_store=fake)

        with caplog.at_level(logging.WARNING, logger=logger.name):
            downloads.unpacker.remove_partial_download(target, False, "")

        # The failing transient is still in the fake (remove raised); the
        # other transient was successfully removed.
        assert (target + TMP_EXT) in fake.files
        assert (target + ZIP_TMP_EXT) not in fake.files
        # The bare target is NEVER touched — a re-download that fails mid-stream
        # must not destroy a pre-existing (or just-committed) install.
        assert target in fake.files
        # Warning mentions the failing path.
        assert any("Cleanup failed for" in rec.message and (target + TMP_EXT) in rec.message for rec in caplog.records)

    def test_remove_tree_failure_is_logged_and_swallowed(self, make_downloads, caplog, logger):
        from fakes.fake_download_file_store import FakeDownloadFileStore

        fake = FakeDownloadFileStore()
        target = "/roms/psx/game.zip"
        extract_dir = "/roms/psx/game"
        fake.make_dirs(extract_dir)
        fake.files[os.path.join(extract_dir, "disc1.bin")] = b"\x00" * 16
        # Inject a remove_tree failure for the extract dir; remove on
        # the three tmp paths is a no-op (paths absent).
        fake.remove_tree_failures.add(extract_dir)
        downloads = make_downloads(download_file_store=fake)

        with caplog.at_level(logging.WARNING, logger=logger.name):
            # Must NOT raise even though remove_tree raises.
            downloads.unpacker.remove_partial_download(target, True, "game")

        # The dir is still present (remove_tree raised before clearing).
        assert extract_dir in fake.dirs
        # Warning mentions the failing directory.
        assert any(
            "Cleanup failed for directory" in rec.message and extract_dir in rec.message for rec in caplog.records
        )


class TestStartDownloadInProgressLeak:
    """An early exception in start_download must release the in-progress flag.

    A raise between taking the claim (``DownloadsStateBox.claim``) and the
    task's creation — an OSError from make_dirs or disk_free (SD card
    unmounted), refused with ``download_start_failed``; a TypeError from a bug
    in the folder lookup; a cancelled call — fails the call and leaves the ROM
    free to be downloaded again, not stuck "Already downloading" until the
    backend restarts.
    """

    @staticmethod
    def _folders(tmp_path) -> FakeRetroDeckFolders:
        return FakeRetroDeckFolders(
            roms=str(tmp_path / "retrodeck" / "roms"),
            bios=str(tmp_path / "retrodeck" / "bios"),
        )

    @pytest.mark.asyncio
    async def test_make_dirs_oserror_releases_flag(self, make_downloads, tmp_path):

        downloads = make_downloads(loop=MagicMock(), retrodeck_folders=self._folders(tmp_path))
        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }
        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _boom(_path):
            raise OSError("SD card unmounted")

        downloads.download_file_store.make_dirs = _boom

        first = downloads.service.start_download(42)
        with pytest.raises(Refused) as refused:
            await first

        assert (refused.value.reason, refused.value.message) == ("download_start_failed", "Failed to start download")
        # The flag is released — the ROM is not stuck "Already downloading".
        assert 42 not in downloads.state.in_progress
        # A second attempt is not rejected as already-downloading; it fails the
        # same way (make_dirs still boom) — proving the flag was discarded.
        second = downloads.service.start_download(42)
        with pytest.raises(Refused) as refused:
            await second
        assert refused.value.reason == "download_start_failed"

    @pytest.mark.asyncio
    async def test_disk_free_oserror_releases_flag(self, make_downloads, tmp_path):

        downloads = make_downloads(loop=MagicMock(), retrodeck_folders=self._folders(tmp_path))
        rom_detail = {
            "id": 43,
            "name": "Mario",
            "fs_name": "mario.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }
        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        def _boom(_path):
            raise OSError("statvfs failed: SD card gone")

        downloads.download_file_store.disk_free = _boom

        coro = downloads.service.start_download(43)
        with pytest.raises(Refused) as refused:
            await coro
        assert refused.value.reason == "download_start_failed"
        assert 43 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_an_oserror_reading_the_target_refuses_and_releases_flag(self, make_downloads, tmp_path):
        """The occupancy gate reads the disk too, and an unreadable card there is the same refusal."""
        from unittest.mock import AsyncMock

        downloads = make_downloads(
            loop=MagicMock(),
            target_gate=AsyncMock(side_effect=OSError("lstat failed: SD card gone")),
            retrodeck_folders=self._folders(tmp_path),
        )
        rom_detail = {
            "id": 45,
            "name": "Kirby",
            "fs_name": "kirby.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }
        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        coro = downloads.service.start_download(45)
        with pytest.raises(Refused) as refused:
            await coro
        assert (refused.value.reason, refused.value.message) == ("download_start_failed", "Failed to start download")
        assert 45 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_a_cancelled_start_releases_flag(self, make_downloads):
        """A start the host cancels mid-await, when the panel's connection drops, frees the ROM too."""
        fetching = asyncio.Event()

        async def _fetch_forever(*_args):
            fetching.set()
            await asyncio.Event().wait()

        downloads = make_downloads(loop=MagicMock())
        downloads.loop.run_in_executor = _fetch_forever
        start = asyncio.ensure_future(downloads.service.start_download(46))
        await fetching.wait()

        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start

        assert 46 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_a_bug_in_the_folder_lookup_releases_flag(self, make_downloads, tmp_path):

        # A TypeError from the folder lookup is a bug that stays one, and still
        # releases the flag.
        paths = FakeRetroDeckFolders(roms="", bios="")

        def broken(_system: str) -> str:
            raise TypeError("expected str, bytes or os.PathLike object, not NoneType")

        paths.download_folder = broken  # type: ignore[method-assign]
        downloads = make_downloads(retrodeck_folders=paths, loop=MagicMock())
        rom_detail = {
            "id": 44,
            "name": "DK",
            "fs_name": "dk.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }
        downloads.loop.run_in_executor = _hops_answering(rom_detail)

        coro = downloads.service.start_download(44)
        with pytest.raises(TypeError):
            await coro
        assert 44 not in downloads.state.in_progress


class TestProgressCallbackEvictionSafe:
    """#973: the progress callback must not KeyError if the entry was evicted.

    The dict mutation now runs on the loop thread via call_soon_threadsafe and
    is guarded by ``.get`` — an evicted rom_id is a no-op (the entry is neither
    resurrected nor mutated, and no emit is scheduled), never a KeyError off the
    executor thread.
    """

    def _eager_loop(self, make_downloads, clock):
        """Build with a loop whose call_soon_threadsafe runs its target synchronously, so the
        marshaled ``_apply_progress`` executes in-test; spawn consumes the
        coroutine. Returns the harness and the recorded emit calls list.
        """
        loop = MagicMock()
        loop.call_soon_threadsafe = lambda fn, *a, **k: fn(*a, **k)
        emit_calls: list[tuple[str, dict[str, Any]]] = []

        def _record_emit(event, payload):
            emit_calls.append((event, payload))

            async def _noop():
                return None

            return _noop()

        downloads = make_downloads(loop=loop, emit=_record_emit, clock=clock)
        downloads.queue._tasks.spawn = lambda coro, _name: coro.close() or MagicMock()
        return downloads, emit_calls

    def test_evicted_entry_progress_tick_is_noop(self, make_downloads):
        fake_clock = FakeClock()
        downloads, emit_calls = self._eager_loop(make_downloads, fake_clock)
        fake_clock.advance(60)  # clear both throttles

        # No queue entry for rom_id 99 — the callback's _apply_progress
        # hits the ``.get`` guard instead of a KeyError.
        cb = downloads.queue.make_progress_callback(99, "Ghost", "N64", "ghost.z64")
        cb(256, 512)  # must NOT raise

        # The entry is not resurrected and no emit was scheduled for it.
        assert 99 not in downloads.queue.entries
        assert emit_calls == []

    def test_present_entry_still_updates(self, make_downloads):
        """Control: a present entry is still updated + an emit scheduled (the guard
        only suppresses the evicted case)."""
        fake_clock = FakeClock()
        downloads, emit_calls = self._eager_loop(make_downloads, fake_clock)
        fake_clock.advance(60)
        downloads.queue.enqueue(
            7,
            {
                "rom_id": 7,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )

        cb = downloads.queue.make_progress_callback(7, "Mario", "N64", "mario.z64")
        cb(512, 1024)

        entry = downloads.queue.entries[7]
        assert entry["bytes_downloaded"] == 512
        assert entry["total_bytes"] == 1024
        assert len(emit_calls) == 1
        assert emit_calls[0][0] == "download_progress"
        assert emit_calls[0][1]["rom_id"] == 7


class _FinishedWorkerExecutor(concurrent.futures.Executor):
    """Runs each job before ``submit`` returns, and holds what the job queues for the loop until :meth:`release`.

    A progress tick a worker queues with ``call_soon_threadsafe`` outlives the download only where the job's
    future settles ahead of it. From 3.13 ``asyncio.futures._chain_future`` copies a finished concurrent future's
    state onto the asyncio future on the spot when it is chained on the running loop, so the ``await`` does not
    yield while the tick waits in the ready queue; 3.12 queues that copy behind the ticks the job queued first.
    On a real worker thread the 3.13 case needs the job to end before the loop thread chains it. Holding the
    ticks until the download ended gives every version that case.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._call_soon_threadsafe = loop.call_soon_threadsafe
        self._held: list[tuple[Callable[..., object], tuple[Any, ...]]] = []
        self._in_job = False

    def call_soon_threadsafe(self, callback: Callable[..., object], *args: Any, context: Any = None) -> Any:
        if not self._in_job:
            return self._call_soon_threadsafe(callback, *args, context=context)
        self._held.append((callback, args))
        return None

    def submit(self, fn, /, *args, **kwargs):
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._in_job = True
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as e:
            future.set_exception(e)
        finally:
            self._in_job = False
        return future

    def release(self) -> None:
        """Queue every tick held so far on the loop, in the order the jobs queued them."""
        held, self._held = self._held, []
        for callback, args in held:
            self._loop.call_soon(callback, *args)


class TestAProgressTickAfterTheDownloadEnded:
    """A tick queued before a download ended and run after it changes nothing and sends no frame."""

    @pytest.fixture
    async def finished_workers(self, monkeypatch):
        loop = asyncio.get_running_loop()
        run_in_executor = loop.run_in_executor
        executor = _FinishedWorkerExecutor(loop)
        monkeypatch.setattr(loop, "run_in_executor", lambda _executor, fn, *args: run_in_executor(executor, fn, *args))
        monkeypatch.setattr(loop, "call_soon_threadsafe", executor.call_soon_threadsafe)
        return executor

    @staticmethod
    async def _drain():
        """Let the queued ticks run, and any emit they schedule after them."""
        for _ in range(3):
            await asyncio.sleep(0)

    @staticmethod
    def _folders(tmp_path) -> FakeRetroDeckFolders:
        return FakeRetroDeckFolders(
            roms=str(tmp_path / "retrodeck" / "roms"),
            bios=str(tmp_path / "retrodeck" / "bios"),
        )

    @pytest.mark.asyncio
    async def test_a_completed_multi_file_download_stays_completed(
        self, make_downloads, tmp_path, emit, finished_workers
    ):
        import zipfile as zf
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))
        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "FF9.zip")
        zip_content_path = tmp_path / "source.zip"
        with zf.ZipFile(str(zip_content_path), "w") as z:
            z.writestr("disc1.cue", "FILE disc1.bin BINARY")
            z.writestr("disc1.bin", b"\x00" * 100)
        zip_bytes = zip_content_path.read_bytes()
        rom_detail = {
            "id": 75,
            "name": "Final Fantasy IX",
            "fs_name": "FF9.zip",
            "fs_name_no_ext": "FF9",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
        }

        def fake_download(_rom_id, _filename, dest, progress_callback, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(zip_bytes)
            progress_callback(len(zip_bytes), len(zip_bytes))

        _seed_rom(downloads.uow, 75, platform_slug="psx")
        downloads.queue.enqueue(75, {"rom_id": 75, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(75, rom_detail, target_path, "psx", "FF9.zip", _admitted(downloads, 75))
        finished_workers.release()
        await self._drain()

        assert [c.args[0] for c in emit.call_args_list] == ["download_complete"]
        entry = downloads.queue.entries[75]
        assert entry["status"] == "completed"
        assert entry["progress"] == 1.0

    @pytest.mark.asyncio
    async def test_a_failed_download_keeps_its_progress(self, make_downloads, tmp_path, emit, finished_workers):
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))
        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")
        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, _dest, progress_callback, *, resume=False, on_meta=None):
            progress_callback(1024, 1024)
            raise OSError("simulated network drop")

        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))
        finished_workers.release()
        await self._drain()

        assert [c.args[0] for c in emit.call_args_list] == ["download_failed"]
        entry = downloads.queue.entries[42]
        assert entry["status"] == "failed"
        assert entry["progress"] == 0

    @pytest.mark.asyncio
    async def test_a_late_resumability_verdict_is_not_applied(self, make_downloads, tmp_path, emit, finished_workers):
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))
        roms_dir = tmp_path / "retrodeck" / "roms" / "gba"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "Game.gba")
        rom_detail = {
            "id": 73,
            "name": "Game",
            "fs_name": "Game.gba",
            "platform_slug": "gba",
            "platform_name": "Game Boy Advance",
            "has_multiple_files": False,
            "files": [{"file_name": "Game.gba"}],
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback, *, resume=False, on_meta):
            on_meta(True)
            with open(dest, "wb") as f:
                f.write(b"\x00" * 100)

        _seed_rom(downloads.uow, 73, platform_slug="gba")
        downloads.queue.enqueue(
            73,
            {
                "rom_id": 73,
                "status": "downloading",
                "progress": 0,
                "resumable": False,
            },
        )

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(73, rom_detail, target_path, "gba", "Game.gba", _admitted(downloads, 73))
        finished_workers.release()
        await self._drain()

        assert [c.args[0] for c in emit.call_args_list] == ["download_complete"]
        entry = downloads.queue.entries[73]
        assert entry["status"] == "completed"
        assert entry["resumable"] is False

    @pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
    @pytest.mark.parametrize("phase", ["downloading", "extracting"])
    @pytest.mark.asyncio
    async def test_an_ended_entry_is_left_alone(self, downloads, emit, status, phase):
        entry = {"rom_id": 7, "status": status, "progress": 0.25, "bytes_downloaded": 256, "total_bytes": 1024}
        downloads.queue.enqueue(7, entry)
        before = dict(entry)

        downloads.queue._apply_progress(phase, 7, "Mario", "N64", "mario.z64", 0.5, 512, 1024)
        await self._drain()

        assert downloads.queue.entries[7] == before
        emit.assert_not_called()


class TestDoDownloadRedownloadPreservesExisting:
    """#1049 scenario 2: a re-download that fails mid-transfer must NOT delete the existing install."""

    @pytest.mark.asyncio
    async def test_failed_redownload_keeps_preexisting_file(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")
        # A pre-existing, fully-installed ROM at the bare target_path.
        with open(target_path, "wb") as f:
            f.write(b"REAL INSTALLED ROM DATA")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, _dest, _progress_callback=None, *, resume=False, on_meta=None):
            raise OSError("network died mid-redownload")

        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        # The pre-existing install must SURVIVE — this was the data-loss bug.
        assert os.path.exists(target_path)
        with open(target_path, "rb") as f:
            assert f.read() == b"REAL INSTALLED ROM DATA"
        assert downloads.queue.entries[42]["status"] == "failed"


class TestDoDownloadCancelReconcile:
    """#1049 scenario 1: a cancel that loses the race to a committed install is
    surfaced as COMPLETED, never torn down.

    These use a REAL ``run_in_executor`` with a commit that blocks until the test
    releases it, so the asyncio future-cancellation semantics are faithful: a
    cancel delivered while awaiting the post-IO future CANCELS that future, so a
    plain re-await raises ``CancelledError`` (not the value) — which is exactly
    why the post-IO await must be ``asyncio.shield``-ed. A non-faithful awaitable
    that returns its value on re-await would green-light the un-shielded bug.
    """

    @pytest.mark.asyncio
    async def test_cancel_during_commit_surfaces_completed_single_file(self, make_downloads, tmp_path, emit):
        import threading
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        _seed_rom(downloads.uow, 42)
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )

        def fake_transfer(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        # The post-IO commit blocks until released, so the test can deliver the
        # cancel while the commit is genuinely in-flight (the #1049 race window),
        # then let the real rename + DB save run to completion.
        started = threading.Event()
        release = threading.Event()
        real_post_io = downloads.transfer._post_download_single_io

        def blocking_post_io(*args):
            started.set()
            release.wait(timeout=5)
            return real_post_io(*args)

        with (
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_transfer),
            patch.object(downloads.transfer, "_post_download_single_io", side_effect=blocking_post_io),
        ):
            task = asyncio.ensure_future(
                downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))
            )
            while not started.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0)  # let the cancel reach the (shielded) post-IO await
            release.set()  # let the real commit finish (rename + DB save)
            with pytest.raises(asyncio.CancelledError):
                await task

        # Committed before the cancel landed → surfaced COMPLETED, file kept, row saved.
        assert downloads.queue.entries[42]["status"] == "completed"
        assert os.path.exists(target_path)
        installed = downloads.uow.rom_installs.get(42)
        assert installed is not None
        assert installed.file_path == target_path
        assert [c for c in emit.call_args_list if c[0][0] == "download_complete"]
        assert not [c for c in emit.call_args_list if c[0][0] == "download_failed"]

    @pytest.mark.asyncio
    async def test_cancel_during_commit_keeps_committed_install_multi_file(self, make_downloads, tmp_path, emit):
        """The data-loss path: a committed multi-file extract dir must SURVIVE a
        racing cancel — cleanup would otherwise ``remove_tree`` the live install."""
        import threading
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "psx"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "game.zip")
        file_name = "game.zip"
        # The extract dir the cleanup would target on a (mis-)cancelled multi-file.
        committed_dir = os.path.join(os.path.dirname(target_path), "game")

        rom_detail = {
            "id": 77,
            "name": "Game",
            "fs_name": "game.zip",
            "platform_slug": "psx",
            "platform_name": "PlayStation",
            "has_multiple_files": True,
            "files": [{"file_name": "a.bin"}, {"file_name": "b.bin"}],
        }

        _seed_rom(downloads.uow, 77, platform_slug="psx")
        downloads.queue.enqueue(
            77,
            {
                "rom_id": 77,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )

        def fake_transfer(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"PK\x05\x06" + b"\x00" * 18)  # zip-ish bytes; the stubbed post-IO never reads them

        started = threading.Event()
        release = threading.Event()

        def blocking_post_io_multi(rom_id, _detail, tpath, _fname, system, extract_dir_name, _reserved_bytes=0):
            # Faithful post-condition of a committed multi-file install: an extract
            # dir with a launch file + a saved RomInstall row.
            started.set()
            release.wait(timeout=5)
            rom_dir = os.path.join(os.path.dirname(tpath), extract_dir_name)
            os.makedirs(rom_dir, exist_ok=True)
            launch_file = os.path.join(rom_dir, "game.m3u")
            with open(launch_file, "wb") as f:
                f.write(b"playlist")
            with downloads.uow as uow:
                uow.rom_installs.save(
                    RomInstall.mark_installed(
                        rom_id=rom_id,
                        file_path=launch_file,
                        rom_dir=rom_dir,
                        platform_slug="psx",
                        system=system,
                        installed_at="2026-01-01T00:00:00+00:00",
                    )
                )
            return (launch_file, None)

        with (
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_transfer),
            patch.object(downloads.transfer, "_post_download_multi_io", side_effect=blocking_post_io_multi),
        ):
            task = asyncio.ensure_future(
                downloads.transfer.run(77, rom_detail, target_path, "psx", file_name, _admitted(downloads, 77))
            )
            while not started.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        # The committed extract dir + its launch file SURVIVE (data-loss regression).
        assert os.path.isdir(committed_dir)
        assert os.path.exists(os.path.join(committed_dir, "game.m3u"))
        # Surfaced as completed, install row present, download_complete emitted.
        assert downloads.queue.entries[77]["status"] == "completed"
        assert downloads.uow.rom_installs.get(77) is not None
        assert [c for c in emit.call_args_list if c[0][0] == "download_complete"]


class TestDoDownloadCancelEmitsEvent:
    """#1017: a clean cancel must emit a terminal download_progress(cancelled) frame."""

    @pytest.mark.asyncio
    async def test_cancel_emits_cancelled_progress_event(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download_cancel(_rom_id, _filename, _dest, _progress_callback=None, *, resume=False, on_meta=None):
            raise asyncio.CancelledError()

        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "downloading",
                "progress": 0.3,
                "bytes_downloaded": 300,
                "total_bytes": 1000,
            },
        )

        with (
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download_cancel),
            pytest.raises(asyncio.CancelledError),
        ):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        # A terminal cancelled frame reached the frontend (was silent before #1017).
        cancelled = [
            c for c in emit.call_args_list if c[0][0] == "download_progress" and c[0][1].get("status") == "cancelled"
        ]
        assert len(cancelled) == 1
        payload = cancelled[0][0][1]
        assert payload["rom_id"] == 42
        assert payload["progress"] == pytest.approx(0.3)
        assert payload["bytes_downloaded"] == 300
        assert payload["total_bytes"] == 1000
        # The terminal frame carries the final progress values from the entry,
        # which is then evicted (#149 downloads-round) — the emit happens first.
        assert 42 not in downloads.queue.entries


class TestConcurrencyReservation:
    """#1053: bounded concurrency + reserved-bytes preflight + queued status."""

    @staticmethod
    def _folders(tmp_path) -> FakeRetroDeckFolders:
        return FakeRetroDeckFolders(
            roms=str(tmp_path / "retrodeck" / "roms"),
            bios=str(tmp_path / "retrodeck" / "bios"),
        )

    @pytest.mark.asyncio
    async def test_reservation_blocks_sibling_that_fits_alone(self, make_downloads, tmp_path):

        downloads = make_downloads(loop=MagicMock(), retrodeck_folders=self._folders(tmp_path))

        # Each ROM is 400MB (+100MB buffer = 500MB required). Free space is
        # 900MB: one fits, two do not (1000MB required, but only 900 free once
        # the first is reserved).
        file_size = 400 * 1024 * 1024

        def _detail(rom_id):
            return {
                "id": rom_id,
                "name": f"Game {rom_id}",
                "fs_name": f"game{rom_id}.z64",
                "fs_size_bytes": file_size,
                "platform_slug": "n64",
                "platform_name": "Nintendo 64",
            }

        def _close_coro_task(coro, _name):
            coro.close()
            return MagicMock()

        downloads.service._tasks.spawn = _close_coro_task
        downloads.download_file_store.disk_free = lambda _path: 900 * 1024 * 1024

        # First download: fits (900 free, needs 500) → reserved.
        downloads.loop.run_in_executor = _hops_answering(_detail(1))
        r1 = await downloads.service.start_download(1)
        assert r1["success"] is True
        assert downloads.state.reserved_bytes[1] == 500 * 1024 * 1024

        # Second download: 900 free - 500 reserved = 400 < 500 needed → rejected.
        downloads.loop.run_in_executor = _hops_answering(_detail(2))
        r2 = downloads.service.start_download(2)
        with pytest.raises(Refused) as refused:
            await r2
        assert refused.value.reason == "insufficient_space"
        assert "disk space" in refused.value.message.lower()
        # The rejected ROM holds no reservation and no in-progress flag.
        assert 2 not in downloads.state.reserved_bytes
        assert 2 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_reservation_released_after_download(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 512)

        _seed_rom(downloads.uow, 42)
        downloads.state.reserved_bytes[42] = 999
        downloads.queue.enqueue(42, {"rom_id": 42, "status": "queued", "progress": 0})

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        assert downloads.queue.entries[42]["status"] == "completed"
        # The reservation is released in the finally.
        assert 42 not in downloads.state.reserved_bytes

    @pytest.mark.asyncio
    async def test_third_download_emits_queued_while_two_run(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)

        # Hold the semaphore so it is fully locked, forcing the third download to
        # emit a "queued" frame before it can acquire.
        sem = downloads.state.semaphore
        await sem.acquire()
        await sem.acquire()
        assert sem.locked()

        rom_detail = {
            "id": 3,
            "name": "Third",
            "fs_name": "third.z64",
            "fs_size_bytes": 1000,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }
        target_path = str(roms_dir / "third.z64")

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 64)

        _seed_rom(downloads.uow, 3)
        downloads.queue.enqueue(3, {"rom_id": 3, "status": "queued", "progress": 0})

        # Run the third download as a task — it must emit "queued" then block on
        # the semaphore (which we hold).
        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            task = asyncio.ensure_future(
                downloads.transfer.run(3, rom_detail, target_path, "n64", "third.z64", _admitted(downloads, 3))
            )
            # Let it reach the semaphore wait.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # It emitted a queued frame and is still waiting (status queued).
            queued = [
                c for c in emit.call_args_list if c[0][0] == "download_progress" and c[0][1].get("status") == "queued"
            ]
            assert len(queued) == 1
            assert queued[0][0][1]["rom_id"] == 3
            assert downloads.queue.entries[3]["status"] == "queued"

            # Release the semaphore so the third can proceed and finish.
            sem.release()
            sem.release()
            await task

        assert downloads.queue.entries[3]["status"] == "completed"


class TestCooperativeCancel:
    """#144: cooperative cancellation — a per-attempt token the progress
    callback polls on the executor thread, raising ``CancelledError`` to abort
    the in-flight HTTP transfer. ``task.cancel()`` alone cannot interrupt the
    executor worker thread; the token is what really stops the bytes.
    """

    def test_progress_callback_raises_once_token_cancelled(self, make_downloads):
        """The callback runs clean while the token is unset; raises after it flips."""
        token = DownloadControl()
        # The queue entry the callback would update if it didn't bail early.
        fake_clock = FakeClock()

        def _record_emit(_event, _payload):
            async def _noop():
                return None

            return _noop()

        downloads = make_downloads(clock=fake_clock, loop=MagicMock(), emit=_record_emit)
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )
        downloads.loop.call_soon_threadsafe = lambda fn, *a, **k: fn(*a, **k)
        downloads.queue._tasks.spawn = lambda coro, _name: coro.close() or MagicMock()

        cb = downloads.queue.make_progress_callback(42, "Zelda", "N64", "zelda.z64", token)

        # Unset token: a normal tick proceeds (no raise). Advance so the
        # final-frame throttle bypass fires and the queue entry updates.
        cb(1024, 1024)
        assert downloads.queue.entries[42]["bytes_downloaded"] == 1024

        # Flip the token; the very next tick aborts the transfer thread.
        token.cancelled = True
        with pytest.raises(asyncio.CancelledError):
            cb(2048, 4096)

    @pytest.mark.asyncio
    async def test_transfer_loop_aborts_when_token_cancelled_mid_stream(self, make_downloads, tmp_path):
        """A real transfer LOOP stops once its token flips — the bytes really halt.

        Models ``download_rom_content`` as a 500-iteration loop calling
        ``progress_callback`` each tick (faking it as instantaneous is exactly
        why the original tests missed #144). At tick 3 the token is cancelled;
        the callback then raises and the loop never reaches 500.
        """
        from unittest.mock import patch

        downloads = make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
            )
        )

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        token = DownloadControl()
        downloads.state.control_tokens[42] = token
        captured: list[int] = []

        def fake_transfer(_rom_id, _filename, _dest, progress_callback=None, *, resume=False, on_meta=None):
            assert progress_callback is not None  # the transfer always threads a real callback
            for i in range(1, 500):
                captured.append(i)
                if i == 3:
                    token.cancelled = True
                # The callback raises CancelledError once the token is set, so
                # the loop never runs to completion — the transfer aborts.
                progress_callback(i, 500)

        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with (
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_transfer),
            pytest.raises(asyncio.CancelledError),
        ):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", token)

        # The loop stopped near tick 3/4 — nowhere near 500. The transfer
        # really halted instead of running to completion.
        assert len(captured) <= 5
        # Cancel evicts its entry rather than leaving a "cancelled" row (#149
        # downloads-round).
        assert 42 not in downloads.queue.entries

    @pytest.mark.asyncio
    async def test_cancel_download_sets_token_and_cancels_task(self, downloads):
        """``cancel_download`` flips the token AND cancels the asyncio task."""
        loop = asyncio.get_running_loop()
        token = DownloadControl()
        downloads.state.control_tokens[42] = token

        async def _never_ending():
            await asyncio.Event().wait()

        task = loop.create_task(_never_ending())
        await asyncio.sleep(0)  # let it start waiting
        downloads.state.tasks[42] = task

        result = downloads.service.cancel_download(42)
        assert result["success"] is True
        # The token is flipped so the executor transfer thread aborts (#144) —
        # not just the asyncio wrapper.
        assert token.cancelled is True
        assert task.cancelled() or task.cancelling()

        # Drain the cancelled task so it doesn't leak as pending.
        with pytest.raises(asyncio.CancelledError):
            await task

    def test_per_attempt_token_isolation(self, make_downloads):
        """A zombie's callback (old token) aborts; a fresh download's continues.

        Each callback closes over its OWN token. A re-download installs a NEW
        token, so the zombie's cancelled token raises while the fresh token's
        callback runs clean — no parallel-download flicker.
        """
        fake_clock = FakeClock()

        def _record_emit(_event, _payload):
            async def _noop():
                return None

            return _noop()

        downloads = make_downloads(clock=fake_clock, loop=MagicMock(), emit=_record_emit)
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
            },
        )
        downloads.loop.call_soon_threadsafe = lambda fn, *a, **k: fn(*a, **k)
        downloads.queue._tasks.spawn = lambda coro, _name: coro.close() or MagicMock()

        old_token = DownloadControl()
        old_token.cancelled = True  # the cancelled zombie's token
        new_token = DownloadControl()  # the fresh re-download's token

        cb_old = downloads.queue.make_progress_callback(42, "Zelda", "N64", "zelda.z64", old_token)
        cb_new = downloads.queue.make_progress_callback(42, "Zelda", "N64", "zelda.z64", new_token)

        # The zombie's callback aborts; the fresh download's proceeds.
        with pytest.raises(asyncio.CancelledError):
            cb_old(512, 1024)
        cb_new(1024, 1024)  # no raise
        assert downloads.queue.entries[42]["bytes_downloaded"] == 1024


class TestPauseResume:
    """#1124: pause keeps the partial .tmp; resume re-begins with resume=True."""

    @staticmethod
    def _folders(tmp_path) -> FakeRetroDeckFolders:
        return FakeRetroDeckFolders(
            roms=str(tmp_path / "retrodeck" / "roms"),
            bios=str(tmp_path / "retrodeck" / "bios"),
        )

    @pytest.mark.asyncio
    async def test_pause_sets_status_and_keeps_tmp(self, make_downloads, tmp_path, emit):
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        control = DownloadControl()

        def fake_pause(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            # Write a partial .tmp, then simulate the progress callback aborting
            # because the control was paused (the real path raises CancelledError).
            with open(dest, "wb") as f:
                f.write(b"\x00" * 256)
            control.paused = True
            raise asyncio.CancelledError()

        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "downloading",
                "progress": 0.25,
                "bytes_downloaded": 256,
                "total_bytes": 1024,
                "resumable": True,
            },
        )

        with (
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_pause),
            pytest.raises(asyncio.CancelledError),
        ):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", control)

        # Status flips to "paused" and the partial .tmp is KEPT for resume.
        assert downloads.queue.entries[42]["status"] == "paused"
        assert os.path.exists(target_path + TMP_EXT)
        # A terminal "paused" frame reached the frontend, carrying resumable.
        paused_frames = [
            c for c in emit.call_args_list if c[0][0] == "download_progress" and c[0][1].get("status") == "paused"
        ]
        assert len(paused_frames) == 1
        assert paused_frames[0][0][1]["resumable"] is True
        assert paused_frames[0][0][1]["bytes_downloaded"] == 256

    @pytest.mark.asyncio
    async def test_cancel_still_deletes_tmp(self, make_downloads, tmp_path):
        """Contrast with pause: a cancel deletes the partial .tmp (no resume)."""
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))
        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        control = DownloadControl()

        def fake_cancel(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"\x00" * 256)
            control.cancelled = True
            raise asyncio.CancelledError()

        downloads.queue.enqueue(42, {"rom_id": 42, "status": "downloading", "progress": 0})

        with (
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_cancel),
            pytest.raises(asyncio.CancelledError),
        ):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", control)

        # Cancel evicts its entry (#149 downloads-round) and deletes the partial
        # .tmp — the contrast with pause, which keeps both for resume.
        assert 42 not in downloads.queue.entries
        assert not os.path.exists(target_path + TMP_EXT)

    @pytest.mark.asyncio
    async def test_on_meta_sets_resumable_and_emits(self, make_downloads, tmp_path, emit):
        """The adapter's on_meta(True) callback flips resumable and emits it live."""
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            if on_meta is not None:
                on_meta(True)  # server proved range support
            with open(dest, "wb") as f:
                f.write(b"\x00" * 256)

        _seed_rom(downloads.uow, 42)
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
                "resumable": False,
            },
        )

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        # on_meta flipped the queue entry's resumable flag...
        assert downloads.queue.entries[42]["resumable"] is True
        # ...and emitted a download_progress frame carrying resumable: True.
        meta_frames = [
            c for c in emit.call_args_list if c[0][0] == "download_progress" and c[0][1].get("resumable") is True
        ]
        assert meta_frames

    @pytest.mark.asyncio
    async def test_non_resumable_stays_false(self, make_downloads, tmp_path, emit):
        """on_meta(False) leaves resumable False (mod_zip / Cloudflare path)."""
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
            "has_multiple_files": False,
        }

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            if on_meta is not None:
                on_meta(False)
            with open(dest, "wb") as f:
                f.write(b"\x00" * 256)

        _seed_rom(downloads.uow, 42)
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "downloading",
                "progress": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
                "resumable": False,
            },
        )

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download):
            await downloads.transfer.run(42, rom_detail, target_path, "n64", "zelda.z64", _admitted(downloads, 42))

        assert downloads.queue.entries[42]["resumable"] is False

    @pytest.mark.asyncio
    async def test_resume_rebegins_with_resume_true(self, make_downloads, tmp_path):
        """resume_download calls the transfer with resume=True (appends, not restarts)."""

        downloads = make_downloads(loop=MagicMock(), retrodeck_folders=self._folders(tmp_path))

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        # A partial .tmp left by the paused download.
        with open(str(roms_dir / "zelda.z64") + TMP_EXT, "wb") as f:
            f.write(b"\x00" * 256)

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        # The entry exists in "paused" state (what a prior pause left behind).
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "paused",
                "progress": 0.25,
                "bytes_downloaded": 256,
                "total_bytes": 1024,
                "resumable": True,
            },
        )

        downloads.loop.run_in_executor = _hops_answering(rom_detail)
        captured_coros = []

        def _capture_task(coro, _name):
            captured_coros.append(coro)
            coro.close()  # don't actually run the download
            return MagicMock()

        downloads.service._tasks.spawn = _capture_task
        downloads.download_file_store.disk_free = lambda _p: 500 * 1024 * 1024

        result = await downloads.service.resume_download(42)

        assert result["success"] is True
        # A fresh task was scheduled (the resumed download).
        assert len(captured_coros) == 1
        # The re-begun download carried resumable across (verdict re-confirmed live).
        assert downloads.queue.entries[42]["resumable"] is True

    @pytest.mark.asyncio
    async def test_resume_threads_resume_true_into_the_transfer(self, make_downloads, tmp_path, emit):
        """End-to-end: the resumed transfer reaches download_rom_content with resume=True."""
        from unittest.mock import patch

        downloads = make_downloads(retrodeck_folders=self._folders(tmp_path))

        roms_dir = tmp_path / "retrodeck" / "roms" / "n64"
        roms_dir.mkdir(parents=True)
        target_path = str(roms_dir / "zelda.z64")
        with open(target_path + TMP_EXT, "wb") as f:
            f.write(b"\x00" * 256)

        rom_detail = {
            "id": 42,
            "name": "Zelda",
            "fs_name": "zelda.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }

        _seed_rom(downloads.uow, 42)
        downloads.queue.enqueue(
            42,
            {
                "rom_id": 42,
                "status": "paused",
                "progress": 0.25,
                "bytes_downloaded": 256,
                "total_bytes": 1024,
                "resumable": True,
            },
        )

        # get_rom is fetched via run_in_executor; the real loop runs it.
        captured_resume: list[bool] = []

        def fake_download(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            captured_resume.append(resume)
            with open(dest, "wb") as f:
                f.write(b"\x00" * 1024)

        with (
            patch.object(downloads.romm_api, "get_rom", return_value=rom_detail),
            patch.object(downloads.romm_api, "download_rom_content", side_effect=fake_download),
        ):
            result = await downloads.service.resume_download(42)
            assert result["success"] is True
            # Drain the scheduled task so the transfer actually runs.
            task = downloads.state.tasks.get(42)
            assert task is not None
            await task

        assert captured_resume == [True]
        assert downloads.queue.entries[42]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_resume_non_paused_is_refused_with_not_paused(self, downloads):
        """resume on a missing or non-paused entry is refused with ``not_paused``."""
        missing = downloads.service.resume_download(999)
        with pytest.raises(Refused) as refused:
            await missing
        assert (refused.value.reason, refused.value.message) == ("not_paused", "No paused download for this ROM")

        # A downloading (not paused) entry is also rejected.
        downloads.queue.enqueue(7, {"rom_id": 7, "status": "downloading"})
        downloading = downloads.service.resume_download(7)
        with pytest.raises(Refused) as refused:
            await downloading
        assert refused.value.reason == "not_paused"

    @pytest.mark.asyncio
    async def test_pause_no_active_is_refused_with_no_active_download(self, downloads):
        with pytest.raises(Refused) as refused:
            downloads.service.pause_download(999)
        assert (refused.value.reason, refused.value.message) == (
            "no_active_download",
            "No active download for this ROM",
        )

    @pytest.mark.asyncio
    async def test_pause_download_sets_paused_flag_and_cancels_task(self, downloads):
        """pause_download flips control.paused (not cancelled) and cancels the task."""
        loop = asyncio.get_running_loop()
        control = DownloadControl()
        downloads.state.control_tokens[42] = control

        async def _never_ending():
            await asyncio.Event().wait()

        task = loop.create_task(_never_ending())
        await asyncio.sleep(0)
        downloads.state.tasks[42] = task

        result = downloads.service.pause_download(42)
        assert result["success"] is True
        assert result["message"] == "Download paused"
        # paused flag set (not cancelled) so the terminal handler keeps the .tmp.
        assert control.paused is True
        assert control.cancelled is False
        assert task.cancelled() or task.cancelling()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_paused_entry_not_pruned(self, downloads):
        """A 'paused' entry survives queue pruning (only terminal states are pruned)."""
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "paused"})
        for i in range(2, 60):
            downloads.queue.enqueue(i, {"rom_id": i, "status": "completed"})
        downloads.queue.prune()
        assert 1 in downloads.queue.entries
        assert downloads.queue.entries[1]["status"] == "paused"


_SUPERSEDE_GROUP = "igdb:100:99"


def _stage_download_prologue(downloads, rom_id: int = 1) -> list[Any]:
    """Let the real ``_begin_download`` run its pre-flights without transferring, on a harness built with a mock loop.

    The #1298 supersede lives inside ``_begin_download``, behind the occupancy
    gate (ADR-0028), so a case that mocks ``_begin_download`` out is no longer
    testing the supersede at all. This stages the surroundings instead: the
    ROM-detail fetch is answered from memory and the transfer task is swallowed.
    The returned list collects the coroutines ``spawn`` was handed, which
    is how "did a download actually start?" is observed.
    """

    started: list[Any] = []

    def _close_coro_task(coro, _name):
        coro.close()
        started.append(coro)
        return MagicMock()

    downloads.loop.run_in_executor = _hops_answering(
        {
            "id": rom_id,
            "name": f"Game {rom_id}",
            "fs_name": f"game_{rom_id}.z64",
            "fs_size_bytes": 1024,
            "platform_slug": "n64",
            "platform_name": "Nintendo 64",
        }
    )
    downloads.service._tasks.spawn = _close_coro_task
    downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024
    return started


class TestSiblingSupersedeSelection:
    """`_conflicting_sibling_install_ids` — which downloaded siblings a download supersedes."""

    def test_selects_unbound_installed_sibling(self, downloads):
        # X (rom 1) is the bound group rep; a fellow unbound version is on disk.
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        assert downloads.service._conflicting_sibling_install_ids(1) == [2]

    def test_skips_grandfathered_and_uninstalled(self, downloads):
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)  # X, bound rep
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)  # → selected
        _seed_group_member(
            downloads.uow, 3, group_key=_SUPERSEDE_GROUP, app_id=99, installed=True
        )  # grandfathered → skip
        _seed_group_member(
            downloads.uow, 4, group_key=_SUPERSEDE_GROUP, app_id=None, installed=False
        )  # not on disk → skip

        assert downloads.service._conflicting_sibling_install_ids(1) == [2]

    def test_solo_group_returns_empty(self, downloads):
        # No sibling_group_key → no siblings, nothing to strip.
        _seed_group_member(downloads.uow, 1, group_key=None, app_id=42, installed=False)
        assert downloads.service._conflicting_sibling_install_ids(1) == []

    def test_unknown_rom_returns_empty(self, downloads):
        assert downloads.service._conflicting_sibling_install_ids(999) == []

    def test_unbound_downloaded_target_still_supersedes_unbound_sibling(self, downloads):
        # X itself unbound (app_id None); a fellow unbound installed sibling is
        # still superseded (both share the "no separate shortcut" state).
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=None, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        assert downloads.service._conflicting_sibling_install_ids(1) == [2]


class TestSiblingSupersedeRemoval:
    """`supersede_sibling_installs` + `start_download` — the removal + abort flow."""

    @pytest.mark.asyncio
    async def test_removes_superseded_sibling(self, make_downloads):
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: remover)
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        remover = AsyncMock(return_value={"success": True, "message": "ROM removed"})

        await downloads.service.supersede_sibling_installs(1)

        remover.assert_awaited_once_with(2)

    @pytest.mark.asyncio
    async def test_a_sibling_already_uninstalled_is_skipped_not_an_abort(self, make_downloads):
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: remover)
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        # A concurrent removal already cleaned it — not_installed is a no-op, not an abort.
        remover = AsyncMock(side_effect=NotInstalled("ROM not installed"))

        await downloads.service.supersede_sibling_installs(1)

        remover.assert_awaited_once_with(2)

    @pytest.mark.asyncio
    async def test_a_removal_refusal_propagates(self, make_downloads):
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: AsyncMock(side_effect=boom))
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        boom = Refused("uninstall_failed", "boom")

        coro = downloads.service.supersede_sibling_installs(1)
        with pytest.raises(Refused) as refused:
            await coro

        assert refused.value is boom

    @pytest.mark.asyncio
    async def test_clean_group_no_remover_call(self, make_downloads):

        # Solo ROM (no group) — the remover provider is never even resolved.
        provider = MagicMock(side_effect=AssertionError("remover must not be resolved when nothing is superseded"))
        downloads = make_downloads(rom_remover=provider)
        _seed_group_member(downloads.uow, 1, group_key=None, app_id=42, installed=False)

        await downloads.service.supersede_sibling_installs(1)
        provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_download_removes_sibling_then_proceeds(self, make_downloads):
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: remover, loop=MagicMock())
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        remover = AsyncMock(return_value={"success": True, "message": "ROM removed"})
        started = _stage_download_prologue(downloads)

        result = await downloads.service.start_download(1)

        assert result == {"success": True, "message": "Download started"}
        remover.assert_awaited_once_with(2)
        assert len(started) == 1

    @pytest.mark.asyncio
    async def test_start_download_aborts_without_starting_when_removal_fails(self, make_downloads):
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: AsyncMock(side_effect=boom), loop=MagicMock())
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        boom = Refused("uninstall_failed", "boom")
        started = _stage_download_prologue(downloads)

        coro = downloads.service.start_download(1)
        with pytest.raises(Refused) as refused:
            await coro

        assert refused.value is boom
        assert started == []

    @pytest.mark.asyncio
    async def test_start_download_second_call_rejected_while_first_mid_supersede(self, make_downloads):
        # B1: the in-progress slot is claimed BEFORE the supersede await, so a
        # second start_download racing in while the first is suspended inside the
        # removal await is rejected with the existing already-downloading shape.
        downloads = make_downloads(rom_remover=lambda: _blocking_remove, loop=MagicMock())
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        entered = asyncio.Event()
        release = asyncio.Event()

        async def _blocking_remove(_rom_id):
            entered.set()
            await release.wait()
            return {"success": True, "message": "ROM removed"}

        _stage_download_prologue(downloads)

        first = asyncio.create_task(downloads.service.start_download(1))
        await entered.wait()  # first call is now suspended inside the removal await

        second = downloads.service.start_download(1)
        with pytest.raises(Refused) as refused:
            await second
        assert (refused.value.reason, refused.value.message) == ("already_downloading", "Already downloading")

        release.set()
        assert await first == {"success": True, "message": "Download started"}

    @pytest.mark.asyncio
    async def test_start_download_releases_in_progress_on_supersede_exception(self, make_downloads):
        # B1: an exception out of the supersede await releases the in-progress
        # claim so the ROM isn't stuck "already downloading" until the backend restarts.
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: AsyncMock(side_effect=RuntimeError("kaboom")))
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        with pytest.raises(RuntimeError):
            await downloads.service.start_download(1)
        assert 1 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_start_download_releases_in_progress_on_removal_failure(self, make_downloads):
        # B1: the removal-abort return path also releases the in-progress claim.
        from unittest.mock import AsyncMock

        downloads = make_downloads(
            rom_remover=lambda: AsyncMock(side_effect=Refused("uninstall_failed", "boom")), loop=MagicMock()
        )
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        _stage_download_prologue(downloads)

        coro = downloads.service.start_download(1)
        with pytest.raises(Refused):
            await coro
        assert 1 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_supersede_evicts_paused_sibling_queue_entry(self, make_downloads):
        # S1: superseding a sibling's install drops its stale paused queue entry
        # too, so a resume can't re-create the version we just removed.
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: AsyncMock(return_value={"success": True, "message": "removed"}))
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        downloads.queue.enqueue(2, {"rom_id": 2, "status": "paused"})

        await downloads.service.supersede_sibling_installs(1)
        assert 2 not in downloads.queue.entries

    @pytest.mark.asyncio
    async def test_supersede_keeps_non_paused_sibling_queue_entry(self, make_downloads):
        # Only a PAUSED sibling row is evicted; a terminal/live row is untouched.
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: AsyncMock(return_value={"success": True, "message": "removed"}))
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        downloads.queue.enqueue(2, {"rom_id": 2, "status": "completed"})

        await downloads.service.supersede_sibling_installs(1)
        assert 2 in downloads.queue.entries

    @pytest.mark.asyncio
    async def test_supersede_logs_both_rom_ids_on_success(self, make_downloads, caplog):
        # S7: a successful supersede logs both the sibling id and the group's rom id.
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: AsyncMock(return_value={"success": True, "message": "removed"}))
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        with caplog.at_level(logging.INFO):
            await downloads.service.supersede_sibling_installs(1)
        assert any("rom 2" in r.message and "rom 1" in r.message and r.levelno == logging.INFO for r in caplog.records)

    @pytest.mark.asyncio
    async def test_supersede_logs_failure_with_message(self, make_downloads, caplog):
        # S7: a failed supersede logs at ERROR with both rom ids and the failure message.
        from unittest.mock import AsyncMock

        downloads = make_downloads(
            rom_remover=lambda: AsyncMock(side_effect=Refused("uninstall_failed", "delete blew up"))
        )
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        coro = downloads.service.supersede_sibling_installs(1)
        with caplog.at_level(logging.INFO), pytest.raises(Refused):
            await coro
        assert any(
            "rom 2" in r.message
            and "rom 1" in r.message
            and "delete blew up" in r.message
            and r.levelno == logging.ERROR
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_supersede_logs_a_folder_refusal_as_it_logs_a_refusal(self, make_downloads, caplog):
        downloads = make_downloads(rom_remover=lambda: AsyncMock(side_effect=refusal))
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        refusal = FolderRefused("no_rom_root", "RetroDECK names no ROM folder.")

        coro = downloads.service.supersede_sibling_installs(1)
        with caplog.at_level(logging.INFO), pytest.raises(FolderRefused):
            await coro
        assert [(r.levelno, r.getMessage()) for r in caplog.records if "Superseding" in r.getMessage()] == [
            (logging.ERROR, "Superseding install of rom 2 (group of rom 1) failed: RetroDECK names no ROM folder.")
        ]


class TestSiblingSupersedeHandOver:
    """`supersede_sibling_installs(..., adopted_path=)` — a sibling recorded at the adopted path keeps its files."""

    @pytest.mark.asyncio
    async def test_a_sibling_recorded_at_the_adopted_path_loses_only_its_record(self, make_downloads):
        provider = MagicMock(side_effect=AssertionError("nothing may be removed"))
        downloads = make_downloads(rom_remover=provider)
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        await downloads.service.supersede_sibling_installs(1, adopted_path="/roms/n64/game_2.z64")

        assert downloads.uow.rom_installs.get(2) is None
        provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_sibling_at_another_path_is_still_removed(self, make_downloads):
        downloads = make_downloads(rom_remover=lambda: remover)
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        _seed_group_member(downloads.uow, 3, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        remover = AsyncMock(return_value={"success": True, "message": "ROM removed"})

        await downloads.service.supersede_sibling_installs(1, adopted_path="/roms/n64/game_3.z64")

        remover.assert_awaited_once_with(2)
        assert downloads.uow.rom_installs.get(3) is None

    @pytest.mark.asyncio
    async def test_a_handed_over_sibling_s_paused_download_is_evicted(self, downloads):
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        downloads.queue.enqueue(2, {"rom_id": 2, "status": "paused"})

        await downloads.service.supersede_sibling_installs(1, adopted_path="/roms/n64/game_2.z64")

        assert 2 not in downloads.queue.entries

    @pytest.mark.asyncio
    async def test_the_hand_over_is_logged_with_both_rom_ids_and_the_path(self, downloads, caplog):
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)

        with caplog.at_level(logging.INFO):
            await downloads.service.supersede_sibling_installs(1, adopted_path="/roms/n64/game_2.z64")

        assert [(r.levelno, r.getMessage()) for r in caplog.records if "Handing" in r.getMessage()] == [
            (logging.INFO, "Handing the install of rom 2 at /roms/n64/game_2.z64 over to rom 1")
        ]


class TestResumeSupersede:
    """`resume_download` — the #1298 S1 stale-resume refusal + supersede-on-resume."""

    @pytest.mark.asyncio
    async def test_resume_refused_when_binding_moved_to_sibling(self, downloads):
        # A switch moved the shortcut to sibling 2 while the download of 3 was
        # paused. Resuming 3 would strand a second install → refused, entry dropped.
        from unittest.mock import AsyncMock

        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=42, installed=True)
        _seed_group_member(downloads.uow, 3, group_key=_SUPERSEDE_GROUP, app_id=None, installed=False)
        downloads.queue.enqueue(3, {"rom_id": 3, "status": "paused"})
        downloads.service._begin_download = AsyncMock()

        coro = downloads.service.resume_download(3)
        with pytest.raises(Refused) as refused:
            await coro
        assert refused.value.reason == "superseded"
        assert len(refused.value.message) <= 45
        assert refused.value.details == {}
        assert 3 not in downloads.queue.entries
        assert 3 not in downloads.state.in_progress
        downloads.service._begin_download.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_runs_supersede_then_rebegins(self, make_downloads):
        # No member owns the shortcut → not superseded; the paused resume still
        # strips a conflicting sibling install first (same guard as start_download).
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: remover, loop=MagicMock())
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=None, installed=False)  # resume target
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)  # to strip
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "paused", "resumable": True})
        remover = AsyncMock(return_value={"success": True, "message": "removed"})
        started = _stage_download_prologue(downloads)

        result = await downloads.service.resume_download(1)
        assert result["success"] is True
        remover.assert_awaited_once_with(2)
        assert len(started) == 1

    @pytest.mark.asyncio
    async def test_resume_bound_target_is_not_superseded(self, downloads):
        # The paused target itself owns the shortcut → not superseded, resumes.
        from unittest.mock import AsyncMock

        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=42, installed=False)
        downloads.queue.enqueue(2, {"rom_id": 2, "status": "paused", "resumable": True})
        downloads.service._begin_download = AsyncMock(return_value={"success": True, "message": "Download started"})

        result = await downloads.service.resume_download(2)
        assert result["success"] is True
        # No stored replace answer on this entry, so the resume carries False —
        # the exemption is scoped to the download that was actually admitted.
        downloads.service._begin_download.assert_awaited_once_with(
            2, resume=True, replace_existing=False, confirmed_paths=None
        )

    @pytest.mark.asyncio
    async def test_resume_aborts_and_releases_in_progress_on_removal_failure(self, make_downloads):
        # S1 + B1: a removal failure aborts the resume and releases the in-progress claim.
        from unittest.mock import AsyncMock

        downloads = make_downloads(rom_remover=lambda: AsyncMock(side_effect=boom), loop=MagicMock())
        _seed_group_member(downloads.uow, 1, group_key=_SUPERSEDE_GROUP, app_id=None, installed=False)
        _seed_group_member(downloads.uow, 2, group_key=_SUPERSEDE_GROUP, app_id=None, installed=True)
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "paused"})
        boom = Refused("uninstall_failed", "boom")
        started = _stage_download_prologue(downloads)

        coro = downloads.service.resume_download(1)
        with pytest.raises(Refused) as refused:
            await coro
        assert refused.value is boom
        assert started == []
        assert 1 not in downloads.state.in_progress

    @pytest.mark.asyncio
    async def test_resume_tells_the_gate_it_is_a_resume(self, make_downloads):
        # A paused multi-file transfer has no extract directory yet, so the gate
        # sees a free path and would run the candidate search — handing back the
        # very file the user declined when they admitted this download, with
        # Cancel (which discards the transferred bytes) as the only exit.
        # The stand-in gate refuses exactly as that search would.
        seen: list[bool] = []

        async def gate(rom_detail, checked_path, *, replace, resume=False, **answer):
            seen.append(resume)
            if resume:
                return
            raise Refused("adoption_candidates", "already on this device")

        downloads = make_downloads(target_gate=gate, loop=MagicMock())
        downloads.queue.enqueue(1, {"rom_id": 1, "status": "paused", "resumable": True})
        started = _stage_download_prologue(downloads)

        result = await downloads.service.resume_download(1)

        assert result["success"] is True
        assert seen == [True]
        assert len(started) == 1

    @pytest.mark.asyncio
    async def test_a_fresh_download_still_faces_the_candidate_search(self, make_downloads):
        # The counterpart: the skip is scoped to a resume, and a first attempt is
        # still refused by the same gate.
        async def gate(rom_detail, checked_path, *, replace, resume=False, **answer):
            if resume:
                return
            raise Refused("adoption_candidates", "already on this device")

        downloads = make_downloads(target_gate=gate, loop=MagicMock())
        started = _stage_download_prologue(downloads)
        starting = downloads.service.start_download(1, False)

        with pytest.raises(Refused) as refused:
            await starting

        assert refused.value.reason == "adoption_candidates"
        assert started == []

    @pytest.mark.asyncio
    async def test_resume_non_paused_short_circuits_before_supersede(self, make_downloads):
        # A non-paused entry returns not_paused without touching the supersede seam.
        from unittest.mock import MagicMock

        provider = MagicMock(side_effect=AssertionError("supersede must not run for a non-paused resume"))
        downloads = make_downloads(rom_remover=provider)
        downloads.queue.enqueue(7, {"rom_id": 7, "status": "downloading"})

        coro = downloads.service.resume_download(7)
        with pytest.raises(Refused) as refused:
            await coro
        assert refused.value.reason == "not_paused"
        provider.assert_not_called()


# ── The start and resume use cases: rules, the retained task, the lease ───────


def _begin_leaving_a_task(state: DownloadsStateBox, release: asyncio.Event, finish=None):
    """A ``_begin_download`` that starts the download's task and answers success, as a started download does."""

    async def begin(rom_id, *, resume, replace_existing=False, **answer):
        async def run() -> None:
            await release.wait()
            if finish is not None:
                await finish(rom_id)

        state.tasks[rom_id] = asyncio.get_running_loop().create_task(run())
        return {"success": True, "message": "Download started"}

    return begin


def _operation_labels(prune_conflicts) -> list[str]:
    return sorted(holder.label for holder in prune_conflicts._operations.values())


async def _let_the_task_end(state: DownloadsStateBox, rom_id: int, prune_conflicts, release: asyncio.Event) -> None:
    release.set()
    await state.tasks[rom_id]
    await asyncio.gather(*prune_conflicts._release_tasks)


class TestAStartedDownloadHoldsAnOperation:
    """The task a start or resume leaves running holds an operation until it ends."""

    async def test_a_started_download_holds_its_operation_until_its_task_ends(self, downloads, monkeypatch):
        service = downloads.service
        release = asyncio.Event()
        monkeypatch.setattr(service, "_begin_download", _begin_leaving_a_task(downloads.state, release))

        result = await service.start_download(42)

        assert result["success"] is True
        assert _operation_labels(downloads.prune_conflicts) == ["start_download"]
        await _let_the_task_end(downloads.state, 42, downloads.prune_conflicts, release)
        assert downloads.prune_conflicts.conflicting_operations == 0

    async def test_a_resumed_download_holds_its_operation_until_its_task_ends(self, downloads, monkeypatch):
        service = downloads.service
        downloads.queue.enqueue(42, {"rom_id": 42, "status": "paused"})
        release = asyncio.Event()
        monkeypatch.setattr(service, "_begin_download", _begin_leaving_a_task(downloads.state, release))

        result = await service.resume_download(42)

        assert result["success"] is True
        assert _operation_labels(downloads.prune_conflicts) == ["resume_download"]
        await _let_the_task_end(downloads.state, 42, downloads.prune_conflicts, release)
        assert downloads.prune_conflicts.conflicting_operations == 0

    async def test_a_start_that_failed_holds_nothing(self, downloads):
        service = downloads.service
        downloads.state.in_progress.add(42)

        coro = service.start_download(42)
        with pytest.raises(Refused) as refused:
            await coro

        assert refused.value.reason == "already_downloading"
        assert downloads.prune_conflicts.conflicting_operations == 0

    @pytest.mark.parametrize("call", ["start_download", "resume_download"])
    @pytest.mark.parametrize(
        ("migration_pending", "cleanup_running", "reason"),
        [(True, False, "blocked_by_migration"), (False, True, "prune_active")],
    )
    async def test_a_download_its_rules_refuse_begins_nothing(
        self, make_downloads, monkeypatch, call, migration_pending, cleanup_running, reason
    ):
        downloads = make_downloads(migration_pending=migration_pending)
        service = downloads.service
        downloads.queue.enqueue(42, {"rom_id": 42, "status": "paused"})
        begun: list[int] = []

        async def begin(rom_id, **_kwargs):
            begun.append(rom_id)
            return {"success": True}

        monkeypatch.setattr(service, "_begin_download", begin)
        if cleanup_running:
            downloads.prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule(reason):
            await getattr(service, call)(42)

        assert begun == []
        assert downloads.prune_conflicts.conflicting_operations == 0


class TestTheDownloadCompleteLease:
    """A bound ROM's ``download_complete`` carries a lease for the frontend's write of its launch command.

    An unbound ROM has no shortcut to write to, so its event carries none; a
    lease on an event nobody heard, or whose emit raised, is given back.
    """

    @staticmethod
    async def _finish(downloads, rom_id: int) -> None:
        downloads.queue.enqueue(rom_id, {"rom_id": rom_id, "status": "downloading", "progress": 0})
        detail = {"id": rom_id, "name": "Zelda", "fs_name": "zelda.z64", "platform_slug": "n64"}
        await downloads.transfer._finalize_download_complete(rom_id, detail, "/roms/n64/zelda.z64", "Zelda", "N64")

    @staticmethod
    def _payload(emit) -> dict[str, Any]:
        (payload,) = [c.args[1] for c in emit.call_args_list if c.args[0] == "download_complete"]
        return payload

    async def test_a_bound_roms_event_carries_a_lease(self, downloads, emit):
        _seed_rom(downloads.uow, 42)
        emit.return_value = True

        await self._finish(downloads, 42)

        assert self._payload(emit)["prune_lease_token"].startswith("download_complete:")
        assert downloads.prune_conflicts.conflicting_operations == 1

    async def test_an_unbound_roms_event_carries_none(self, downloads, emit):
        with downloads.uow:
            downloads.uow.roms.save(
                Rom(
                    rom_id=7,
                    platform_slug="n64",
                    name="Metroid",
                    fs_name="metroid.z64",
                    shortcut_app_id=None,
                    last_synced_at="2025-01-01T00:00:00",
                )
            )

        await self._finish(downloads, 7)

        assert "prune_lease_token" not in self._payload(emit)
        assert downloads.prune_conflicts.conflicting_operations == 0

    async def test_an_event_nobody_heard_gives_its_lease_back(self, downloads, emit):
        _seed_rom(downloads.uow, 42)
        emit.return_value = False

        await self._finish(downloads, 42)

        assert self._payload(emit)["prune_lease_token"].startswith("download_complete:")
        assert downloads.prune_conflicts.conflicting_operations == 0

    async def test_the_lease_is_taken_while_the_downloads_operation_still_holds(self, downloads, emit, monkeypatch):
        """No cleanup can start between the download's own operation and the lease that outlasts it."""
        _seed_rom(downloads.uow, 42)
        emit.return_value = True
        service = downloads.service
        release = asyncio.Event()

        async def finish(rom_id: int) -> None:
            await self._finish(downloads, rom_id)

        monkeypatch.setattr(service, "_begin_download", _begin_leaving_a_task(downloads.state, release, finish))
        seen = _record_operations_at_lease(downloads.prune_conflicts, monkeypatch)

        await service.start_download(42)
        await _let_the_task_end(downloads.state, 42, downloads.prune_conflicts, release)

        assert seen == [["start_download"]]


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    """A real stored ZIP holding *members*, the bytes a single zip on RomM arrives as."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


_SATURN_SET = {
    "Game.cue": b'FILE "Game (Track 1).bin" BINARY\n',
    "Game (Track 1).bin": b"\x01" * 300,
    "Game (Track 2).bin": b"\x02" * 200,
}


def _single_zip_detail(payload: bytes, *, rom_id: int = 77, system: str = "saturn", **extra: Any) -> dict[str, Any]:
    return {
        "id": rom_id,
        "name": "Game",
        "fs_name": "Game.zip",
        "fs_name_no_ext": "Game",
        "fs_size_bytes": len(payload),
        "platform_slug": system,
        "platform_name": system,
        **extra,
    }


class TestASingleZipHoldingADisc:
    """A single ``.zip`` holding a disc image is unpacked like a multi-file download (#910 D1, D5)."""

    @pytest.fixture
    def roms(self, tmp_path):
        return tmp_path / "retrodeck" / "roms"

    @pytest.fixture
    def downloads(self, make_downloads, roms, tmp_path):
        return make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(roms=str(roms), bios=str(tmp_path / "bios")),
            removal_folders=FakeRetroDeckFolders(roms=str(roms)),
        )

    async def _download(self, downloads, roms, members, *, system="saturn", rom_id=77, **extra):
        from unittest.mock import patch

        payload = _zip_bytes(members)
        detail = _single_zip_detail(payload, rom_id=rom_id, system=system, **extra)
        platform_dir = roms / system
        platform_dir.mkdir(parents=True, exist_ok=True)
        target = str(platform_dir / "Game.zip")
        _seed_rom(downloads.uow, rom_id, platform_slug=system)
        downloads.queue.enqueue(rom_id, {"rom_id": rom_id, "status": "downloading", "progress": 0})

        def transfer(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(payload)

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=transfer) as fetched:
            await downloads.transfer.run(rom_id, detail, target, system, "Game.zip", _admitted(downloads, rom_id))
        return target, detail, fetched

    async def test_a_cue_with_its_tracks_is_unpacked_into_its_own_folder(self, downloads, roms):
        target, _detail, _fetched = await self._download(downloads, roms, _SATURN_SET)

        folder = roms / "saturn" / "Game.m3u"
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.rom_dir == str(folder)
        assert installed.file_path == str(folder / "Game.m3u")
        assert (folder / "Game.m3u").read_text() == "Game.cue\n"
        assert (folder / "Game (Track 1).bin").exists()
        assert (folder / "Game (Track 2).bin").exists()
        assert not os.path.exists(target)
        assert not os.path.exists(target + TMP_EXT)

    async def test_on_a_system_without_m3u_it_launches_its_cue(self, downloads, roms):
        downloads.m3u_supported = False

        await self._download(downloads, roms, _SATURN_SET)

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.file_path == str(roms / "saturn" / "Game.cue" / "Game.cue")
        assert installed.rom_dir == str(roms / "saturn" / "Game.cue")

    @pytest.mark.parametrize("image", ["Game.rvz", "Game.wbfs"])
    async def test_a_wii_image_is_unpacked_the_same_way(self, downloads, roms, image):
        target, _detail, _fetched = await self._download(downloads, roms, {image: b"\x00" * 400}, system="wii")

        folder = roms / "wii" / image
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.file_path == str(folder / image)
        assert installed.rom_dir == str(folder)
        assert not os.path.exists(target)

    async def test_a_gdi_set_launches_its_gdi_not_its_largest_track(self, downloads, roms):
        members = {"Game.gdi": b"3\n", "track01.bin": b"\x00" * 50, "track03.raw": b"\x00" * 900}

        await self._download(downloads, roms, members, system="dreamcast")

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert os.path.basename(installed.file_path) == "Game.gdi"

    @pytest.mark.parametrize(
        "members",
        [
            {"Game.gba": b"\x00" * 64},
            {"mslug.rom": b"\x00" * 64, "mslug.c1": b"\x00" * 64},
            {"Game.bin": b"\x00" * 64},
            {"Game.img": b"\x00" * 64, "Game.sub": b"\x00" * 8},
        ],
        ids=["cartridge", "arcade romset", "bin alone", "img without ccd"],
    )
    async def test_a_zip_holding_no_disc_image_is_kept_as_it_is(self, downloads, roms, members):
        target, _detail, _fetched = await self._download(downloads, roms, members, system="gba")

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.file_path == target
        assert installed.rom_dir is None
        assert os.path.isfile(target)
        assert not (roms / "gba" / "Game").exists()

    async def test_a_file_named_zip_that_opens_as_none_is_kept_as_it_is(self, downloads, roms):
        from unittest.mock import patch

        platform_dir = roms / "saturn"
        platform_dir.mkdir(parents=True)
        target = str(platform_dir / "Game.zip")
        detail = _single_zip_detail(b"not a zip at all")
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.queue.enqueue(77, {"rom_id": 77, "status": "downloading", "progress": 0})

        def transfer(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(b"not a zip at all")

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=transfer):
            await downloads.transfer.run(77, detail, target, "saturn", "Game.zip", _admitted(downloads, 77))

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.file_path == target
        assert installed.rom_dir is None

    async def test_a_set_in_a_wrapper_folder_unpacks_flat_and_collapses(self, downloads, roms):
        wrapped = {f"Game/{name}": data for name, data in _SATURN_SET.items()}

        target, _detail, _fetched = await self._download(downloads, roms, wrapped)

        folder = roms / "saturn" / "Game.m3u"
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.rom_dir == str(folder)
        assert installed.file_path == str(folder / "Game.m3u")
        assert (folder / "Game.m3u").read_text() == "Game.cue\n"
        assert (folder / "Game (Track 2).bin").exists()
        assert not (folder / "Game").exists()
        assert not os.path.exists(target)

    async def test_an_rvz_in_a_wrapper_folder_launches_from_the_collapsed_folder(self, downloads, roms):
        await self._download(downloads, roms, {"Game/Game.rvz": b"\x00" * 400}, system="wii")

        folder = roms / "wii" / "Game.rvz"
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.rom_dir == str(folder)
        assert installed.file_path == str(folder / "Game.rvz")

    async def test_a_zip_made_by_macos_finder_unpacks_without_its_resource_forks(self, downloads, roms):
        members = {f"Game/{name}": data for name, data in _SATURN_SET.items()}
        members["__MACOSX/Game/._Game.cue"] = b"fork"

        await self._download(downloads, roms, members)

        folder = roms / "saturn" / "Game.m3u"
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.rom_dir == str(folder)
        assert sorted(p.name for p in folder.iterdir()) == [
            "Game (Track 1).bin",
            "Game (Track 2).bin",
            "Game.cue",
            "Game.m3u",
        ]

    async def test_a_wrapper_beside_a_backslash_folder_entry_unpacks_flat_without_it(self, downloads, roms):
        # zipfile reads only a trailing "/" as a directory, so the local listing
        # keeps "Extras\\" as a member — it must neither hide the wrapper nor land
        # as an empty file in the game's folder.
        members = {f"Game/{name}": data for name, data in _SATURN_SET.items()}
        members["Extras\\"] = b""

        await self._download(downloads, roms, members)

        folder = roms / "saturn" / "Game.m3u"
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.file_path == str(folder / "Game.m3u")
        assert sorted(p.name for p in folder.iterdir()) == [
            "Game (Track 1).bin",
            "Game (Track 2).bin",
            "Game.cue",
            "Game.m3u",
        ]

    async def test_a_dos_game_in_a_wrapper_folder_with_its_cd_is_kept(self, downloads, roms):
        members = {"Game/GAME.EXE": b"MZ", "Game/cd/game.cue": b"FILE", "Game/cd/game.bin": b"\x00" * 64}

        target, _detail, _fetched = await self._download(downloads, roms, members, system="dos")

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.file_path == target
        assert installed.rom_dir is None

    async def test_a_members_name_is_kept_as_the_zip_spells_it(self, downloads, roms):
        members = {"Game.cue": b'FILE "Track%2001.bin" BINARY\n', "Track%2001.bin": b"\x01" * 64}

        await self._download(downloads, roms, members)

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert (Path(installed.rom_dir) / "Track%2001.bin").exists()

    async def test_an_upper_case_cue_counts_as_a_disc_image(self, downloads, roms):
        await self._download(downloads, roms, {"GAME.CUE": b"FILE", "GAME.BIN": b"\x00" * 64})

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.rom_dir is not None

    async def test_uninstalling_it_removes_the_whole_folder(self, downloads, roms):
        await self._download(downloads, roms, _SATURN_SET)
        folder = roms / "saturn" / "Game.m3u"
        assert folder.is_dir()

        result = await downloads.removal.remove_rom(77)

        assert result["success"] is True
        assert not folder.exists()
        assert (roms / "saturn").is_dir()

    async def test_a_replaced_zip_goes_once_the_unpacked_install_is_recorded(self, downloads, roms):
        platform_dir = roms / "saturn"
        platform_dir.mkdir(parents=True)
        (platform_dir / "Game.zip").write_bytes(b"the user's own zip")

        target, _detail, _fetched = await self._download(downloads, roms, _SATURN_SET)

        assert downloads.uow.rom_installs.get(77) is not None
        assert not os.path.exists(target)

    async def test_a_failed_unpack_keeps_the_replaced_zip(self, downloads, roms):
        platform_dir = roms / "saturn"
        platform_dir.mkdir(parents=True)
        (platform_dir / "Game.zip").write_bytes(b"the user's own zip")
        downloads.download_file_store.extract_zip = MagicMock(side_effect=OSError("card pulled"))

        target, _detail, _fetched = await self._download(downloads, roms, _SATURN_SET)

        assert downloads.uow.rom_installs.get(77) is None
        assert Path(target).read_bytes() == b"the user's own zip"
        assert not (platform_dir / "Game").exists()

    async def test_a_listing_that_named_no_disc_does_not_unpack_over_an_unasked_folder(self, downloads, roms):
        # RomM's stale listing said cartridge, so the gate never asked about the
        # folder; the zip that arrived does hold a disc, and a folder is there.
        (roms / "saturn" / "Game").mkdir(parents=True)
        (roms / "saturn" / "Game" / "notes.txt").write_text("mine")
        listing = [{"file_name": "Game.zip", "archive_members": [{"name": "Game.gba", "size": 64}]}]

        target, _detail, _fetched = await self._download(downloads, roms, _SATURN_SET, files=listing)

        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.file_path == target
        assert (roms / "saturn" / "Game" / "notes.txt").read_text() == "mine"


class TestTooLittleSpaceToUnpack:
    """Too little space before unpacking fails, keeps the download and records nothing (#910 D3)."""

    @pytest.fixture
    def roms(self, tmp_path):
        roms = tmp_path / "retrodeck" / "roms"
        (roms / "saturn").mkdir(parents=True)
        return roms

    @pytest.fixture
    def downloads(self, make_downloads, roms, tmp_path):
        return make_downloads(
            retrodeck_folders=FakeRetroDeckFolders(roms=str(roms), bios=str(tmp_path / "bios")),
            removal_folders=FakeRetroDeckFolders(roms=str(roms)),
        )

    async def _run(self, downloads, detail, target, payload, emit):
        from unittest.mock import patch

        downloads.queue.enqueue(detail["id"], {"rom_id": detail["id"], "status": "downloading"})

        def transfer(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(payload)

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=transfer) as fetched:
            await downloads.transfer.run(
                detail["id"], detail, target, "saturn", os.path.basename(target), _admitted(downloads, detail["id"])
            )
        failures = [c.args[1] for c in emit.call_args_list if c.args[0] == "download_failed"]
        return fetched, failures

    @pytest.mark.parametrize("multi", [False, True], ids=["single zip", "multi-file"])
    async def test_it_fails_keeps_the_file_and_a_retry_unpacks_without_downloading(self, downloads, roms, emit, multi):
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        if multi:
            detail = {**detail, "has_multiple_files": True}
        target = str(roms / "saturn" / "Game.zip")
        kept = target + (ZIP_TMP_EXT if multi else TMP_EXT)
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        store = downloads.download_file_store
        store.disk_free = lambda _path: 1024

        _fetched, failures = await self._run(downloads, detail, target, payload, emit)

        assert [f["error_message"] for f in failures] == ["Not enough space to unpack (0MB free, need 100MB)"]
        assert downloads.uow.rom_installs.get(77) is None
        assert Path(kept).read_bytes() == payload
        assert not (roms / "saturn" / "Game").exists()

        store.disk_free = lambda _path: 10 * 1024**3
        fetched, failures = await self._run(downloads, detail, target, payload, emit)

        fetched.assert_not_called()
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert installed.rom_dir == str(roms / "saturn" / "Game.m3u")
        assert not os.path.exists(kept)

    async def test_what_another_download_claimed_is_not_free_for_the_unpack(self, downloads, roms, emit):
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.download_file_store.disk_free = lambda _path: 10 * 1024**3
        downloads.state.reserved_bytes[99] = 10 * 1024**3

        _fetched, failures = await self._run(downloads, detail, target, payload, emit)

        assert [f["error_message"] for f in failures] == ["Not enough space to unpack (0MB free, need 100MB)"]
        assert downloads.uow.rom_installs.get(77) is None
        assert Path(target + TMP_EXT).read_bytes() == payload

    async def test_another_downloads_claim_counts_only_what_it_has_not_written(self, downloads, roms, emit):
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.download_file_store.disk_free = lambda _path: 10 * 1024**3
        # Another download claimed 10 GiB and has transferred all of it, which
        # the free space already lacks.
        downloads.state.reserved_bytes[99] = 10 * 1024**3
        downloads.queue.enqueue(99, {"rom_id": 99, "status": "downloading"})
        downloads.queue._apply_progress("downloading", 99, "Other", "", "other.zip", 100, 10 * 1024**3, 10 * 1024**3)

        _fetched, failures = await self._run(downloads, detail, target, payload, emit)

        assert failures == []
        assert downloads.uow.rom_installs.get(77) is not None

    async def test_what_another_download_has_unpacked_counts_as_written_too(self, downloads, roms, emit):
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.download_file_store.disk_free = lambda _path: 4 * 1024**3 + 200 * 1024 * 1024
        # Claimed 10 GiB, transferred 5 GiB and unpacked 1 GiB of it so far: 4 GiB
        # of the claim are still to come, which leaves 200 MiB for this unpack.
        downloads.state.reserved_bytes[99] = 10 * 1024**3
        downloads.queue.enqueue(99, {"rom_id": 99, "status": "downloading"})
        downloads.queue._apply_progress("downloading", 99, "Other", "", "other.zip", 100, 5 * 1024**3, 5 * 1024**3)
        downloads.queue._apply_progress("extracting", 99, "Other", "", "other.zip", 20, 1024**3, 5 * 1024**3)

        _fetched, failures = await self._run(downloads, detail, target, payload, emit)

        assert failures == []
        assert downloads.uow.rom_installs.get(77) is not None

    async def test_a_resumed_downloads_earlier_bytes_are_not_counted_as_written_again(self, downloads, roms, emit):
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.download_file_store.disk_free = lambda _path: 5 * 1024**3
        # Resumed with 5 GiB already on disk, which its claim of 5 GiB left out;
        # its transfer reports that 5 GiB again before a new byte arrives.
        downloads.state.reserved_bytes[99] = 5 * 1024**3
        downloads.queue.enqueue(
            99,
            {
                "rom_id": 99,
                "status": "downloading",
                "_on_disk_at_start": 5 * 1024**3,
            },
        )
        downloads.queue._apply_progress("downloading", 99, "Other", "", "other.zip", 50, 5 * 1024**3, 10 * 1024**3)

        _fetched, failures = await self._run(downloads, detail, target, payload, emit)

        assert [f["error_message"] for f in failures] == ["Not enough space to unpack (0MB free, need 100MB)"]

    async def test_the_kept_check_runs_off_the_loop(self, downloads, roms, emit, monkeypatch):
        from services.downloads.unpack import DownloadUnpacker

        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        threads: list[int] = []
        real = DownloadUnpacker.kept_download_complete

        def recording(self, *args):
            threads.append(threading.get_ident())
            return real(self, *args)

        monkeypatch.setattr(DownloadUnpacker, "kept_download_complete", recording)

        await self._run(downloads, detail, target, payload, emit)

        assert threads
        assert threading.get_ident() not in threads

    async def test_the_starts_kept_check_runs_off_the_loop(self, make_downloads, roms, tmp_path, monkeypatch):
        from unittest.mock import patch

        from services.downloads.unpack import DownloadUnpacker

        folders = FakeRetroDeckFolders(roms=str(roms), bios=str(tmp_path / "bios"))
        downloads = make_downloads(
            retrodeck_folders=folders, adoption_folders=folders, removal_folders=FakeRetroDeckFolders(roms=str(roms))
        )
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.service._tasks.spawn = lambda coro, _name: coro.close()
        threads: list[int] = []
        real = DownloadUnpacker.kept_download_complete

        def recording(self, *args):
            threads.append(threading.get_ident())
            return real(self, *args)

        monkeypatch.setattr(DownloadUnpacker, "kept_download_complete", recording)

        with patch.object(downloads.romm_api, "get_rom", return_value=detail):
            result = await downloads.service.start_download(77)

        assert result["success"] is True
        assert threads
        assert threading.get_ident() not in threads

    async def test_a_resume_of_a_whole_tmp_asks_for_no_range(self, downloads, roms, emit):
        from unittest.mock import patch

        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        (roms / "saturn" / "Game.zip.tmp").write_bytes(payload)
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.queue.enqueue(77, {"rom_id": 77, "status": "downloading"})

        with patch.object(downloads.romm_api, "download_rom_content") as fetched:
            await downloads.transfer.run(
                77, detail, target, "saturn", "Game.zip", _admitted(downloads, 77), resume=True
            )

        fetched.assert_not_called()
        assert downloads.uow.rom_installs.get(77) is not None

    async def test_a_partial_tmp_is_still_downloaded(self, downloads, roms, emit):
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        (roms / "saturn" / "Game.zip.tmp").write_bytes(payload[:100])
        _seed_rom(downloads.uow, 77, platform_slug="saturn")

        fetched, _failures = await self._run(downloads, detail, target, payload, emit)

        fetched.assert_called_once()
        assert downloads.uow.rom_installs.get(77) is not None

    async def test_a_whole_zip_of_another_size_than_romm_states_is_downloaded_again(self, downloads, roms, emit):
        payload = _zip_bytes(_SATURN_SET)
        detail = _single_zip_detail(payload)
        target = str(roms / "saturn" / "Game.zip")
        (roms / "saturn" / "Game.zip.tmp").write_bytes(_zip_bytes({"Game.cue": b"FILE"}))
        _seed_rom(downloads.uow, 77, platform_slug="saturn")

        fetched, _failures = await self._run(downloads, detail, target, payload, emit)

        fetched.assert_called_once()
        installed = downloads.uow.rom_installs.get(77)
        assert installed is not None
        assert (Path(installed.rom_dir) / "Game (Track 2).bin").exists()

    async def test_the_retrys_pre_flight_counts_only_what_the_unpack_needs(self, make_downloads, roms, tmp_path):
        downloads = make_downloads(
            loop=MagicMock(),
            retrodeck_folders=FakeRetroDeckFolders(roms=str(roms), bios=str(tmp_path / "bios")),
            removal_folders=FakeRetroDeckFolders(roms=str(roms)),
        )
        payload = _zip_bytes(_SATURN_SET)
        listing = [
            {
                "file_name": "Game.zip",
                "archive_members": [{"name": name, "size": 200 * 1024 * 1024} for name in _SATURN_SET],
            }
        ]
        detail = {**_single_zip_detail(payload), "files": listing}
        (roms / "saturn" / "Game.zip.tmp").write_bytes(payload)
        started = _stage_download_prologue(downloads, rom_id=77)
        downloads.loop.run_in_executor = _hops_answering(detail)
        downloads.download_file_store.disk_free = lambda _path: 10 * 1024**3

        result = await downloads.service.start_download(77)

        assert result["success"] is True
        assert len(started) == 1
        # The listed members and the headroom, without the zip already on disk.
        assert downloads.state.reserved_bytes[77] == 700 * 1024 * 1024


class TestTheSpaceCheckBeforeTheDownload:
    """Where RomM lists a single zip's members, the pre-flight counts them too (#910 D3)."""

    @pytest.fixture
    def downloads(self, make_downloads, tmp_path):
        return make_downloads(
            loop=MagicMock(), retrodeck_folders=FakeRetroDeckFolders(roms=str(tmp_path / "roms"), bios=str(tmp_path))
        )

    def _start(self, downloads, tmp_path, detail):
        _stage_download_prologue(downloads, rom_id=77)
        downloads.loop.run_in_executor = _hops_answering(detail)
        downloads.download_file_store.disk_free = lambda _path: 500 * 1024 * 1024
        return downloads.service.start_download(77)

    @staticmethod
    def _detail(members: list[dict[str, Any]] | None) -> dict[str, Any]:
        files: list[dict[str, Any]] = [{"file_name": "Game.zip"}]
        if members is not None:
            files[0]["archive_members"] = members
        return {**_single_zip_detail(b""), "fs_size_bytes": 300 * 1024 * 1024, "files": files}

    async def test_listed_disc_members_are_counted_with_the_zip(self, downloads, tmp_path):
        detail = self._detail([{"name": "Game.cue", "size": 1024}, {"name": "Game.bin", "size": 300 * 1024 * 1024}])

        with pytest.raises(Refused) as refused:
            await self._start(downloads, tmp_path, detail)

        assert refused.value.reason == "insufficient_space"
        assert "need 700MB" in refused.value.message

    async def test_a_nearly_finished_download_claims_only_what_it_has_still_to_write(self, downloads, tmp_path):
        # 500 MiB free after another download wrote 400 MiB of its 410 MiB claim:
        # 10 MiB of it are still to come, which leaves room for this 300 MiB zip.
        downloads.state.reserved_bytes[99] = 410 * 1024 * 1024
        downloads.queue.enqueue(99, {"rom_id": 99, "status": "downloading"})
        downloads.queue._apply_progress(
            "downloading", 99, "Other", "", "other.zip", 97, 400 * 1024 * 1024, 410 * 1024 * 1024
        )

        result = await self._start(downloads, tmp_path, self._detail(None))

        assert result["success"] is True

    @pytest.mark.parametrize(
        "members",
        [None, [{"name": "Game.gba", "size": 300 * 1024 * 1024}]],
        ids=["no listing", "listed cartridge"],
    )
    async def test_otherwise_the_zip_alone_is_counted(self, downloads, tmp_path, members):
        result = await self._start(downloads, tmp_path, self._detail(members))

        assert result["success"] is True


class TestABadChecksumFailsTheUnpack:
    """A member whose CRC-32 does not match fails the unpack and records nothing (#910 D5)."""

    @pytest.mark.parametrize("multi", [False, True], ids=["single zip", "multi-file"])
    async def test_nothing_is_recorded_and_nothing_is_left(self, make_downloads, tmp_path, emit, multi):
        from unittest.mock import patch

        roms = tmp_path / "retrodeck" / "roms"
        downloads = make_downloads(retrodeck_folders=FakeRetroDeckFolders(roms=str(roms), bios=str(tmp_path / "bios")))
        (roms / "saturn").mkdir(parents=True)
        intact = _zip_bytes({"Game.cue": b"FILE", "Game.bin": b"ABCD" * 64})
        payload = intact.replace(b"ABCDABCD", b"ABCEABCD", 1)
        detail = _single_zip_detail(payload)
        if multi:
            detail = {**detail, "has_multiple_files": True}
        target = str(roms / "saturn" / "Game.zip")
        _seed_rom(downloads.uow, 77, platform_slug="saturn")
        downloads.queue.enqueue(77, {"rom_id": 77, "status": "downloading"})

        def transfer(_rom_id, _filename, dest, _progress_callback=None, *, resume=False, on_meta=None):
            with open(dest, "wb") as f:
                f.write(payload)

        with patch.object(downloads.romm_api, "download_rom_content", side_effect=transfer):
            await downloads.transfer.run(77, detail, target, "saturn", "Game.zip", _admitted(downloads, 77))

        (failure,) = [c.args[1] for c in emit.call_args_list if c.args[0] == "download_failed"]
        assert "Bad CRC-32" in failure["error_message"]
        assert downloads.uow.rom_installs.get(77) is None
        assert sorted(os.listdir(roms / "saturn")) == []


class TestAnEarlierUnpackedCopyIsTheSameGame:
    """The target gate claims a single zip's folder as well as its file (#910 D5)."""

    @staticmethod
    def _folders(tmp_path) -> FakeRetroDeckFolders:
        return FakeRetroDeckFolders(roms=str(tmp_path / "roms"), bios=str(tmp_path))

    @pytest.fixture
    def downloads(self, make_downloads, tmp_path):
        return make_downloads(loop=MagicMock(), retrodeck_folders=self._folders(tmp_path))

    def _stage(self, downloads, tmp_path, detail):
        roms = tmp_path / "roms"
        _stage_download_prologue(downloads, rom_id=77)
        downloads.loop.run_in_executor = _hops_answering(detail)
        (roms / "saturn").mkdir(parents=True)
        return roms / "saturn"

    async def test_its_folder_at_the_extract_path_is_the_target_occupied(self, downloads, tmp_path):
        platform_dir = self._stage(downloads, tmp_path, _single_zip_detail(b"zip"))
        (platform_dir / "Game").mkdir()
        (platform_dir / "Game" / "Game.cue").write_text("FILE")

        with pytest.raises(TargetOccupied) as refused:
            await downloads.service.start_download(77)

        assert refused.value.details["existing"]["path"] == str(platform_dir / "Game")
        assert refused.value.details["adoptable"] is True

    async def test_beside_its_zip_the_folder_gets_a_dialog_of_its_own(self, downloads, tmp_path):
        platform_dir = self._stage(downloads, tmp_path, _single_zip_detail(b"zip"))
        (platform_dir / "Game.zip").write_bytes(b"the user's zip")
        (platform_dir / "Game").mkdir()
        (platform_dir / "Game" / "Game.cue").write_text("FILE")

        confirmed = [str(platform_dir / "Game.zip")]

        with pytest.raises(TargetOccupied) as refused:
            await downloads.service.start_download(77, True, None, None, False, confirmed)

        assert refused.value.details["existing"]["path"] == str(platform_dir / "Game")
        assert (platform_dir / "Game" / "Game.cue").exists()

    async def test_a_zip_that_appeared_during_a_pause_gets_its_own_dialog_on_resume(self, make_downloads, tmp_path):
        folders = self._folders(tmp_path)
        downloads = make_downloads(loop=MagicMock(), retrodeck_folders=folders, adoption_folders=folders)
        platform_dir = self._stage(downloads, tmp_path, _single_zip_detail(b"zip"))
        folder = platform_dir / "Game"
        folder.mkdir()
        (folder / "Game.cue").write_text("FILE")
        started = await downloads.service.start_download(77, True, None, None, False, [str(folder)])
        assert started["success"] is True
        downloads.queue.entries[77]["status"] = "paused"
        (platform_dir / "Game.zip").write_bytes(b"appeared while paused")

        with pytest.raises(TargetOccupied) as refused:
            await downloads.service.resume_download(77)

        assert refused.value.details["existing"]["path"] == str(platform_dir / "Game.zip")
        assert (platform_dir / "Game.zip").read_bytes() == b"appeared while paused"

    async def test_a_resume_still_covers_the_zip_the_start_confirmed(self, make_downloads, tmp_path):
        folders = self._folders(tmp_path)
        downloads = make_downloads(loop=MagicMock(), retrodeck_folders=folders, adoption_folders=folders)
        platform_dir = self._stage(downloads, tmp_path, _single_zip_detail(b"zip"))
        (platform_dir / "Game.zip").write_bytes(b"the user's zip")
        zip_path = str(platform_dir / "Game.zip")
        assert (await downloads.service.start_download(77, True, None, None, False, [zip_path]))["success"] is True
        downloads.queue.entries[77]["status"] = "paused"

        resumed = await downloads.service.resume_download(77)

        assert resumed["success"] is True
        assert (platform_dir / "Game.zip").read_bytes() == b"the user's zip"

    async def test_its_collapsed_folder_is_an_adoption_candidate(self, downloads, tmp_path):
        platform_dir = self._stage(downloads, tmp_path, _single_zip_detail(b"zip"))
        (platform_dir / "Game.m3u").mkdir()

        with pytest.raises(DomainRefused) as refused:
            await downloads.service.start_download(77)

        assert refused.value.reason == "adoption_candidates"

    async def test_a_zip_listed_as_a_cartridge_still_calls_a_folder_the_wrong_shape(self, downloads, tmp_path):
        listing = [{"file_name": "Game.zip", "archive_members": [{"name": "Game.gba", "size": 64}]}]
        platform_dir = self._stage(downloads, tmp_path, _single_zip_detail(b"zip", files=listing))
        (platform_dir / "Game.m3u").mkdir()

        with pytest.raises(DomainRefused) as refused:
            await downloads.service.start_download(77)

        assert refused.value.reason == "unusable_namesake"


class TestTheM3uOverTheNewDiscFormats:
    """The generated playlist takes every format in ``DISC_IMAGE_EXTENSIONS``, one entry per disc (#910 D4)."""

    def test_a_cue_and_a_ccd_of_one_disc_are_listed_once(self, downloads, tmp_path):
        for disc in (1, 2):
            (tmp_path / f"Game (Disc {disc}).cue").write_text("FILE")
            (tmp_path / f"Game (Disc {disc}).ccd").write_text("[CloneCD]")
            (tmp_path / f"Game (Disc {disc}).img").write_bytes(b"\x00" * 10)

        downloads.unpacker.generate_m3u(str(tmp_path), {"fs_name_no_ext": "Game"}, True)

        assert (tmp_path / "Game.m3u").read_text() == "Game (Disc 1).cue\nGame (Disc 2).cue\n"

    def test_two_rvz_discs_get_a_playlist(self, downloads, tmp_path):
        (tmp_path / "Game (Disc 1).rvz").write_bytes(b"\x00" * 10)
        (tmp_path / "Game (Disc 2).rvz").write_bytes(b"\x00" * 10)

        downloads.unpacker.generate_m3u(str(tmp_path), {"fs_name_no_ext": "Game"}, True)

        assert (tmp_path / "Game.m3u").read_text() == "Game (Disc 1).rvz\nGame (Disc 2).rvz\n"

    def test_a_two_disc_gdi_set_lists_its_descriptors_only(self, downloads, tmp_path):
        for disc in (1, 2):
            (tmp_path / f"Game (Disc {disc}).gdi").write_text("3")
            (tmp_path / f"track0{disc}.bin").write_bytes(b"\x00" * 10)

        downloads.unpacker.generate_m3u(str(tmp_path), {"fs_name_no_ext": "Game"}, True)

        assert (tmp_path / "Game.m3u").read_text() == "Game (Disc 1).gdi\nGame (Disc 2).gdi\n"

    def test_a_system_without_m3u_gets_none(self, downloads, tmp_path):
        (tmp_path / "Game (Disc 1).rvz").write_bytes(b"\x00" * 10)
        (tmp_path / "Game (Disc 2).rvz").write_bytes(b"\x00" * 10)

        downloads.unpacker.generate_m3u(str(tmp_path), {"fs_name_no_ext": "Game"}, False)

        assert not (tmp_path / "Game.m3u").exists()
