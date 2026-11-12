"""Shared factories and fakes for the SaveService test suite."""

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from _factories import _make_conflict_rules, _make_retry
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_hostname_reader import FakeHostnameReader
from fakes.fake_machine_id_reader import FakeMachineIdReader
from fakes.fake_platform_systems import FakePlatformSystems
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_running_rom_ids import FakeRunningRomIds
from fakes.fake_save_api import FakeSaveApi
from fakes.fake_save_location_reader import FakeSaveLocationReader
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock

from adapters.gavel_native import GavelNativeAdapter
from adapters.save_file import SaveFileAdapter
from bootstrap import SaveParts, SaveWiring, wire_saves
from domain.live_device_id import LiveDeviceId
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from services.saves import SaveService
from services.saves._save_state import read_save_state_or_new, write_save_state
from services.saves.sync_engine._sweep_step import RomSyncResult
from services.saves.sync_engine.matrix import MatrixExecutor

# One ctypes load for the whole SaveService suite — the adapter is stateless,
# so every service built here can share the same instance.
_GAVEL = GavelNativeAdapter()


# RomM slugs whose normalized system is a different string. Anything else is
# its own system here, which keeps the bulk of these tests reading plainly.
_TEST_SYSTEMS = {"sega-saturn": "saturn", "commodore-amiga": "amiga"}


async def _noop_emit(_event: str, /, *_args: object) -> None:
    """Default emitter for SaveService tests — drops all events."""


def make_parts(
    tmp_path, fake_api=None, *, emit=None, matrix_executor_type: type[MatrixExecutor] = MatrixExecutor, **overrides
) -> tuple[SaveParts, FakeSaveApi]:
    """Build the save package through ``wire_saves``, the way ``bootstrap/`` does, over test defaults.

    Each keyword in *overrides* replaces one field of the :class:`SaveWiring`;
    *matrix_executor_type* is the stand-in executor class handed in where the engine
    is built.
    """
    save_file_store = SaveFileAdapter(logger=logging.getLogger("test"))
    fake: FakeSaveApi = fake_api or FakeSaveApi(save_file_store=save_file_store)
    # Tests that build their own FakeSaveApi without wiring the adapter get
    # the same instance as the service so download_save_content materializes
    # bytes onto the shared filesystem view.
    if fake.save_file_store is None:
        fake.save_file_store = save_file_store
    wiring_kwargs: dict[str, Any] = {
        "romm_api": fake,
        "retry": _make_retry(),
        "resolve_upload_conflict": _GAVEL,
        "compute_sync_action": _GAVEL.compute_sync_action,
        "settings": {"log_level": "debug"},
        "settings_persister": FakeSettingsPersister(),
        "save_file_store": save_file_store,
        "loop": running_loop(),
        "logger": logging.getLogger("test"),
        "clock": FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC)),
        "retrodeck_folders": FakeRetroDeckFolders(
            saves=str(tmp_path / "saves"),
            roms=str(tmp_path / "retrodeck" / "roms"),
        ),
        "active_core": FakeActiveCoreResolver(default=(None, None)),
        # The stock RetroDECK layout: content-sorted under the saves root.
        "save_locations": FakeSaveLocationReader(saves_root=str(tmp_path / "saves")),
        # A slug that DIFFERS from its system for the two the tests use, so a
        # site that leaks the raw RomM slug is caught rather than hidden behind
        # an identity map.
        "platform_systems": FakePlatformSystems(_TEST_SYSTEMS),
        "hostname_provider": FakeHostnameReader(),
        "machine_id_provider": FakeMachineIdReader(),
        "log_debug": lambda _msg: None,
        "emit": emit if emit is not None else _noop_emit,
        "is_update_in_progress": lambda: False,
        "is_retrodeck_migration_pending": lambda: False,
        "conflict_rules": _make_conflict_rules(),
        "running_rom_ids": FakeRunningRomIds(),
        "uow_factory": FakeUnitOfWorkFactory(),
    }
    wiring_kwargs.update(overrides)
    parts = wire_saves(SaveWiring(**wiring_kwargs), matrix_executor_type=matrix_executor_type)
    return parts, fake


def executor_answering(sync: Callable[..., RomSyncResult]) -> type[MatrixExecutor]:
    """A stand-in matrix executor whose ``sync_rom_saves`` answers what *sync* answers for the same arguments.

    Handed to :func:`make_parts` or :func:`make_service` as ``matrix_executor_type``.
    """

    class _StandIn(MatrixExecutor):
        def sync_rom_saves(self, rom_id: int, *args: Any, **kwargs: Any) -> RomSyncResult:
            return sync(rom_id, *args, **kwargs)

    return _StandIn


def make_service(tmp_path, fake_api=None, **kwargs: Any) -> tuple[SaveService, FakeSaveApi]:
    """Create a SaveService through :func:`make_parts`, for a test that needs no part the façade does not hold."""
    parts, fake = make_parts(tmp_path, fake_api, **kwargs)
    return parts.service, fake


def _no_save_directory(svc, system: str = "gba") -> None:
    """Make *system*'s emulator answer with no resolved placement — no save directory at all."""
    save_locations = svc._rom_info._save_locations
    assert isinstance(save_locations, FakeSaveLocationReader)
    save_locations.refuse(system)


def _uow(svc) -> FakeUnitOfWork:
    """Return the shared in-memory unit of work backing *svc*."""
    return svc._uow_factory.uow


def _seed_rom(svc, rom_id: int, *, platform_slug: str = "gba", fs_name: str | None = None) -> None:
    """Seed a ``Rom`` registry row so per-rom child writes pass the commit-time FK.

    ``fs_name`` is the RomM content filename; pass it when the test needs two
    rows to agree (or disagree) on the save identity they project onto.
    """
    with _uow(svc) as uow:
        uow.roms.save(
            Rom.synced(
                rom_id=rom_id,
                platform_slug=platform_slug,
                platform_fs_slug=platform_slug,
                name=f"rom-{rom_id}",
                fs_name=fs_name if fs_name is not None else f"rom-{rom_id}",
                shortcut_app_id=rom_id,
                synced_at="2026-01-01T00:00:00",
            )
        )


def _seed_save_state(svc, rom_id: int, state: RomSaveSyncState, *, platform_slug: str = "gba") -> None:
    """Seed a ``RomSaveSyncState`` aggregate for *rom_id*, seeding its ``Rom`` FK first."""
    _seed_rom(svc, rom_id, platform_slug=platform_slug)
    with _uow(svc) as uow:
        uow.rom_save_sync_states.save(rom_id, state)


def _get_save_state(svc, rom_id: int) -> RomSaveSyncState | None:
    """Read back the persisted ``RomSaveSyncState`` for *rom_id*, or ``None``."""
    with _uow(svc) as uow:
        return uow.rom_save_sync_states.get(rom_id)


def _require_save_state(svc, rom_id: int) -> RomSaveSyncState:
    """Read back the persisted ``RomSaveSyncState`` for *rom_id*, asserting it exists.

    Member-access narrowing twin of :func:`_get_save_state` for the common
    case where a test has just seeded/run a flow and the state is known to be
    present — keeps the call site free of a per-line ``assert ... is not None``.
    """
    state = _get_save_state(svc, rom_id)
    assert state is not None
    return state


def rom_save_sync_state_from_dict(data: dict[str, Any]) -> RomSaveSyncState:
    """Build a ``RomSaveSyncState`` from the legacy dict shape used across saves tests.

    The SQLite aggregate has no ``from_dict``; this test helper preserves the
    ergonomic ``{"files": {fn: {...}}, "active_slot": ...}`` literal the old
    JSON aggregate accepted so the large status/versions/matrix suites migrate
    by import-swap rather than per-literal rewrites.
    """
    _FILE_FIELDS = {
        "tracked_save_id",
        "last_sync_hash",
        "last_sync_server_hash",
        "last_sync_at",
        "last_sync_server_updated_at",
        "last_sync_server_save_id",
        "last_sync_server_size",
        "last_sync_local_mtime",
        "last_sync_local_size",
    }
    raw_files = data.get("files", {})
    files = {fn: FileSyncState(**{k: v for k, v in fs.items() if k in _FILE_FIELDS}) for fn, fs in raw_files.items()}
    return RomSaveSyncState(
        active_slot=data.get("active_slot"),
        slot_confirmed=bool(data.get("slot_confirmed", False)),
        emulator=str(data.get("emulator", "retroarch")),
        system=str(data.get("system", "") or ""),
        last_synced_core=data.get("last_synced_core"),
        own_upload_ids=data.get("own_upload_ids"),
        slots=data.get("slots", {}),
        files=files,
        last_sync_check_at=data.get("last_sync_check_at"),
    )


def _seed_save_state_dict(svc, rom_id: int, data: dict[str, Any], *, platform_slug: str = "gba") -> None:
    """Seed a ``RomSaveSyncState`` from the legacy dict shape (seeds the ``Rom`` FK)."""
    _seed_save_state(svc, rom_id, rom_save_sync_state_from_dict(data), platform_slug=platform_slug)


def _do_sync(svc, rom_id: int):
    """Run the matrix sync worker for *rom_id* the way the op-entry does.

    Loads the ``RomSaveSyncState`` from the shared UoW and the stored device id
    (:func:`_stored_device_id`), resolves the active core, runs the matrix
    executor's ``sync_rom_saves``, persists the mutated aggregate, and returns
    ``(uploaded, downloaded, errors, conflicts)``.
    """
    engine = svc._sync_engine
    matrix = engine._matrix
    if engine._rom_info.get_rom_save_info(rom_id) is None:
        # Not installed → nothing to sync and no roms row to anchor a write.
        return matrix.sync_rom_saves(rom_id, RomSaveSyncState(), _stored_device_id(svc), None, _default_slot(svc))
    save_state, device_id = read_save_state_or_new(engine._uow_factory, rom_id), _stored_device_id(svc)
    core_so = engine.resolve_core(rom_id)
    default_slot = _default_slot(svc)
    result = matrix.sync_rom_saves(rom_id, save_state, device_id, core_so, default_slot)
    write_save_state(engine._uow_factory, rom_id, save_state)
    return result


def _stored_device_id(svc) -> LiveDeviceId:
    """The stored device id, handed to a sync worker as the live one an operation would hand it.

    The worker tests drive the workers below the entry (``SyncEngine.live_device_id``),
    so the id is whatever the test seeded, ``None`` included.
    """
    return cast("LiveDeviceId", svc._sync_engine.get_device_id())


def _default_slot(svc):
    """Resolve the configured default slot from the service's settings."""
    from services.saves._settings import resolve_default_slot

    return resolve_default_slot(svc._config.settings)


def _do_upload(parts: SaveParts, rom_id, file_path, filename, system, *, server_save=None, core_so=None):
    """Run the upload worker the way the op-entry does and return the mutated state.

    Loads the ``RomSaveSyncState`` from the shared UoW and the stored device id, calls
    ``SaveTransfer.upload_save_file`` (which mutates the aggregate in memory), persists it, and
    returns the in-memory state so the test can assert on the same object.
    """
    svc = parts.service
    engine = svc._sync_engine
    save_state, device_id = read_save_state_or_new(engine._uow_factory, rom_id), _stored_device_id(svc)
    parts.save_transfer.upload_save_file(
        rom_id, file_path, filename, save_state, device_id, system, core_so, server_save, _default_slot(svc)
    )
    write_save_state(engine._uow_factory, rom_id, save_state)
    return save_state


def _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba"):
    """Register a ROM install in the ``rom_installs`` aggregate (WS3).

    Seeds the ``Rom`` registry row first so the per-rom child write passes the
    commit-time foreign key the FakeUnitOfWork enforces.
    """
    _seed_rom(svc, rom_id, platform_slug=system)
    with _uow(svc) as uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=rom_id,
                file_path=str(tmp_path / "retrodeck" / "roms" / system / file_name),
                rom_dir=None,
                platform_slug=system,
                system=system,
                installed_at="2026-01-01T00:00:00",
            )
        )


def _seed_install(
    svc, rom_id: int, *, file_path: str, system: str, platform_slug: str, rom_dir: str | None = None, allow_empty=False
) -> None:
    """Seed a ``RomInstall`` with arbitrary fields (for path/system edge-case tests).

    Seeds the ``Rom`` FK first. Builds the aggregate directly (not via
    ``mark_installed``) when *allow_empty* is set so empty system/path edge cases
    can be exercised.
    """
    _seed_rom(svc, rom_id, platform_slug=platform_slug or "unknown")
    install = RomInstall(
        rom_id=rom_id,
        file_path=file_path,
        rom_dir=rom_dir,
        platform_slug=platform_slug,
        system=system,
        installed_at="2026-01-01T00:00:00",
    )
    with _uow(svc) as uow:
        uow.rom_installs.save(install)


def _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"\x00" * 1024, ext=".srm"):
    """Create a save file on disk and return its path."""
    saves_dir = tmp_path / "saves" / system
    saves_dir.mkdir(parents=True, exist_ok=True)
    save_file = saves_dir / (rom_name + ext)
    save_file.write_bytes(content)
    return save_file


_SERVER_SAVE_SENTINEL = object()


def _server_save(
    save_id=100,
    rom_id=42,
    filename="pokemon.srm",
    updated_at="2026-02-17T06:00:00Z",
    file_size_bytes=1024,
    slot=_SERVER_SAVE_SENTINEL,
    file_name_no_tags=None,
):
    if file_name_no_tags is None:
        # Strip extension to approximate RomM's file_name_no_tags
        file_name_no_tags = filename.rsplit(".", 1)[0] if "." in filename else filename
    result = {
        "id": save_id,
        "rom_id": rom_id,
        "file_name": filename,
        "file_name_no_tags": file_name_no_tags,
        "updated_at": updated_at,
        "file_size_bytes": file_size_bytes,
        "emulator": "retroarch",
        "download_path": f"/saves/{filename}",
    }
    if slot is not _SERVER_SAVE_SENTINEL:
        result["slot"] = slot
    return result


def _file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _enable_sync_with_device(svc, device_id: str = "device-1") -> None:
    """Flip on save sync and bind a server device id (matches FakeSaveApi).

    Writes ``kv_config["device_id"]`` directly (a test backdoor that bypasses
    the :class:`DeviceRegistry`), so the registry cache is invalidated to keep
    the seeded id observable through ``get_device_id``.
    """
    svc._config.settings["save_sync_enabled"] = True
    with _uow(svc) as uow:
        uow.kv_config.set("device_id", device_id)
    svc._sync_engine._devices.invalidate_device_id_cache()


def _seed_confirmed_rom(
    svc,
    tmp_path,
    *,
    rom_id: int = 42,
    system: str = "gba",
    rom_name: str = "pokemon",
    file_name: str = "pokemon.gba",
    content: bytes = b"save data",
) -> None:
    """Enable sync with a device, and install a ROM with a confirmed named slot and a local-only save file."""
    _enable_sync_with_device(svc)
    _install_rom(svc, tmp_path, rom_id=rom_id, system=system, file_name=file_name)
    _seed_save_state(
        svc, rom_id, RomSaveSyncState(system=system, slot_confirmed=True, active_slot="default"), platform_slug=system
    )
    _create_save(tmp_path, system=system, rom_name=rom_name, content=content)


def _set_device_id(svc, device_id: str | None) -> None:
    """Set or clear the server device id in ``kv_config`` (None deletes it).

    A test backdoor that bypasses the :class:`DeviceRegistry`; invalidates the
    registry cache so the change is observable through ``get_device_id``.
    """
    with _uow(svc) as uow:
        if device_id is None:
            uow.kv_config.delete("device_id")
        else:
            uow.kv_config.set("device_id", device_id)
    svc._sync_engine._devices.invalidate_device_id_cache()


def _get_device_id(svc) -> str | None:
    """Read the persisted server device id from ``kv_config``."""
    with _uow(svc) as uow:
        return uow.kv_config.get("device_id")


def _server_save_with_syncs(
    *,
    save_id: int = 100,
    rom_id: int = 42,
    filename: str = "pokemon.srm",
    updated_at: str = "2026-02-17T06:00:00Z",
    file_size_bytes: int = 1024,
    device_syncs: list[dict[str, Any]] | None = None,
    slot: str | None = None,
) -> dict[str, Any]:
    """Build a server-save dict with explicit device_syncs (no FakeApi shimming)."""
    base = _server_save(
        save_id=save_id,
        rom_id=rom_id,
        filename=filename,
        updated_at=updated_at,
        file_size_bytes=file_size_bytes,
    )
    if slot is not None:
        base["slot"] = slot
    base["device_syncs"] = device_syncs if device_syncs is not None else []
    return base


def _seed_two_synced_saves(svc, tmp_path):
    """Install ROM 42 with ``pokemon.srm`` and ``pokemon.rtc`` on disk, both synced under slot ``default``.

    Returns the two save paths.
    """
    _install_rom(svc, tmp_path)
    srm = _create_save(tmp_path, ext=".srm", content=b"srm bytes")
    rtc = _create_save(tmp_path, ext=".rtc", content=b"rtc bytes")
    _seed_save_state(
        svc,
        42,
        RomSaveSyncState(
            active_slot="default",
            slot_confirmed=True,
            files={
                "pokemon.srm": FileSyncState(last_sync_hash=_file_md5(str(srm)), tracked_save_id=100),
                "pokemon.rtc": FileSyncState(last_sync_hash=_file_md5(str(rtc)), tracked_save_id=101),
            },
        ),
    )
    return srm, rtc


def _fail_renames_from_call(svc, monkeypatch, failing_call: int) -> None:
    """Make the save-file store's ``rename`` raise ``PermissionError`` from its *failing_call*-th call on."""
    store = svc._save_file_store
    original = store.rename
    calls = {"n": 0}

    def rename(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] >= failing_call:
            raise PermissionError("saves folder is read-only")
        original(src, dst)

    monkeypatch.setattr(store, "rename", rename)


def _fail_save_backups(parts: SaveParts, monkeypatch) -> None:
    """Make every save backup raise ``PermissionError``: a download is never put in place and the local file stays."""

    def back_up_save_file(*_args: object, **_kwargs: object) -> bool:
        raise PermissionError(".romm-backup is read-only")

    monkeypatch.setattr(parts.save_backup, "back_up_save_file", back_up_save_file)
