"""Tests for LaunchGateService."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

import pytest

from services.launch_gate import LaunchGateService, LaunchGateServiceConfig

if TYPE_CHECKING:
    from services.protocols.files import SaveFileStore


class FakeDriftReader:
    """In-memory ``LaunchGateDriftReader`` for tests.

    ``files`` maps rom_id → the ``find_local_save_files`` list; ``baselines``
    maps rom_id → the ``last_sync_hashes`` map. ``find_raises`` /
    ``baselines_raises`` arm the internal-error path.
    """

    def __init__(
        self,
        *,
        files: dict[int, list[dict[str, str]]] | None = None,
        baselines: dict[int, dict[str, str | None]] | None = None,
        find_raises: BaseException | None = None,
        baselines_raises: BaseException | None = None,
    ) -> None:
        self.files = files if files is not None else {}
        self.baselines = baselines if baselines is not None else {}
        self.find_raises = find_raises
        self.baselines_raises = baselines_raises

    def find_local_save_files(self, rom_id: int) -> list[dict[str, str]]:
        if self.find_raises is not None:
            raise self.find_raises
        return self.files.get(rom_id, [])

    def last_sync_hashes(self, rom_id: int) -> dict[str, str | None]:
        if self.baselines_raises is not None:
            raise self.baselines_raises
        return self.baselines.get(rom_id, {})


class FakeSaveFileStore:
    """Minimal ``SaveFileStore`` stub — only ``content_hash`` is exercised here.

    ``hashes`` maps path → its content hash; ``content_hash_raises`` arms a
    mid-hash failure (e.g. the file vanished between enumeration and hashing).
    """

    def __init__(
        self,
        *,
        hashes: dict[str, str] | None = None,
        content_hash_raises: BaseException | None = None,
    ) -> None:
        self.hashes = hashes if hashes is not None else {}
        self.content_hash_raises = content_hash_raises
        self.content_hash_calls: list[str] = []

    def content_hash(self, path: str) -> str:
        self.content_hash_calls.append(path)
        if self.content_hash_raises is not None:
            raise self.content_hash_raises
        return self.hashes[path]


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_launch_gate")


class TestCheckLocalDrift:
    def _drift_service(
        self,
        *,
        event_loop: asyncio.AbstractEventLoop,
        logger: logging.Logger,
        drift_reader: FakeDriftReader,
        save_file_store: FakeSaveFileStore | None = None,
    ) -> LaunchGateService:
        return LaunchGateService(
            config=LaunchGateServiceConfig(
                drift_reader=drift_reader,
                save_file_store=cast(
                    "SaveFileStore", save_file_store if save_file_store is not None else FakeSaveFileStore()
                ),
                loop=event_loop,
                logger=logger,
            ),
        )

    def test_not_installed_no_local_files_not_drifted(self, event_loop, logger):
        """No local save files (not installed / nothing on disk) → drifted False, never hashes."""
        drift_reader = FakeDriftReader(files={})
        save_file_store = FakeSaveFileStore()
        service = self._drift_service(
            event_loop=event_loop, logger=logger, drift_reader=drift_reader, save_file_store=save_file_store
        )

        result = event_loop.run_until_complete(service.check_local_drift(99))

        assert result == {"drifted": False, "rom_id": 99}
        assert save_file_store.content_hash_calls == []

    def test_single_file_hash_matches_not_drifted(self, event_loop, logger):
        """One local file whose current hash equals its baseline → drifted False."""
        drift_reader = FakeDriftReader(
            files={42: [{"path": "/saves/game.srm", "filename": "game.srm"}]},
            baselines={42: {"game.srm": "hash-A"}},
        )
        save_file_store = FakeSaveFileStore(hashes={"/saves/game.srm": "hash-A"})
        service = self._drift_service(
            event_loop=event_loop, logger=logger, drift_reader=drift_reader, save_file_store=save_file_store
        )

        result = event_loop.run_until_complete(service.check_local_drift(42))

        assert result == {"drifted": False, "rom_id": 42}
        assert save_file_store.content_hash_calls == ["/saves/game.srm"]

    def test_single_file_hash_mismatch_drifted(self, event_loop, logger):
        """One local file whose current hash differs from its baseline → drifted True."""
        drift_reader = FakeDriftReader(
            files={42: [{"path": "/saves/game.srm", "filename": "game.srm"}]},
            baselines={42: {"game.srm": "hash-A"}},
        )
        save_file_store = FakeSaveFileStore(hashes={"/saves/game.srm": "hash-B"})
        service = self._drift_service(
            event_loop=event_loop, logger=logger, drift_reader=drift_reader, save_file_store=save_file_store
        )

        result = event_loop.run_until_complete(service.check_local_drift(42))

        assert result == {"drifted": True, "rom_id": 42}

    def test_no_baseline_is_not_drift(self, event_loop, logger):
        """A present file whose baseline is None (never synced) → not drift, never hashes it."""
        drift_reader = FakeDriftReader(
            files={42: [{"path": "/saves/game.srm", "filename": "game.srm"}]},
            baselines={42: {"game.srm": None}},
        )
        save_file_store = FakeSaveFileStore(hashes={"/saves/game.srm": "hash-A"})
        service = self._drift_service(
            event_loop=event_loop, logger=logger, drift_reader=drift_reader, save_file_store=save_file_store
        )

        result = event_loop.run_until_complete(service.check_local_drift(42))

        assert result == {"drifted": False, "rom_id": 42}
        # No baseline → nothing to compare, so the file is never hashed.
        assert save_file_store.content_hash_calls == []

    def test_missing_baseline_key_is_not_drift(self, event_loop, logger):
        """A present file with no baseline entry at all → not drift (baselines.get → None)."""
        drift_reader = FakeDriftReader(
            files={42: [{"path": "/saves/game.srm", "filename": "game.srm"}]},
            baselines={42: {}},
        )
        save_file_store = FakeSaveFileStore(hashes={"/saves/game.srm": "hash-A"})
        service = self._drift_service(
            event_loop=event_loop, logger=logger, drift_reader=drift_reader, save_file_store=save_file_store
        )

        result = event_loop.run_until_complete(service.check_local_drift(42))

        assert result == {"drifted": False, "rom_id": 42}
        assert save_file_store.content_hash_calls == []

    def test_multi_file_one_drifted_is_drifted(self, event_loop, logger):
        """Multiple component files, one drifted → drifted True."""
        drift_reader = FakeDriftReader(
            files={
                7: [
                    {"path": "/saves/game.bkr", "filename": "game.bkr"},
                    {"path": "/saves/game.bcr", "filename": "game.bcr"},
                ]
            },
            baselines={7: {"game.bkr": "bkr-base", "game.bcr": "bcr-base"}},
        )
        save_file_store = FakeSaveFileStore(
            hashes={"/saves/game.bkr": "bkr-base", "/saves/game.bcr": "bcr-CHANGED"},
        )
        service = self._drift_service(
            event_loop=event_loop, logger=logger, drift_reader=drift_reader, save_file_store=save_file_store
        )

        result = event_loop.run_until_complete(service.check_local_drift(7))

        assert result == {"drifted": True, "rom_id": 7}

    def test_internal_error_during_enumeration_is_not_drifted(self, event_loop, logger, caplog):
        """``find_local_save_files`` raising → drifted False (no raise), logged."""
        drift_reader = FakeDriftReader(find_raises=RuntimeError("uow boom"))
        service = self._drift_service(event_loop=event_loop, logger=logger, drift_reader=drift_reader)

        with caplog.at_level("WARNING", logger="test_launch_gate"):
            result = event_loop.run_until_complete(service.check_local_drift(42))

        assert result == {"drifted": False, "rom_id": 42}
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "rom_id=42" in warnings[0].getMessage()

    def test_internal_error_during_hash_is_not_drifted(self, event_loop, logger):
        """``content_hash`` raising mid-hash → drifted False (no raise)."""
        drift_reader = FakeDriftReader(
            files={42: [{"path": "/saves/game.srm", "filename": "game.srm"}]},
            baselines={42: {"game.srm": "hash-A"}},
        )
        save_file_store = FakeSaveFileStore(content_hash_raises=OSError("file vanished"))
        service = self._drift_service(
            event_loop=event_loop, logger=logger, drift_reader=drift_reader, save_file_store=save_file_store
        )

        result = event_loop.run_until_complete(service.check_local_drift(42))

        assert result == {"drifted": False, "rom_id": 42}
