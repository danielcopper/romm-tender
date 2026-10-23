import asyncio
import hashlib
import inspect
import logging
import os
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _factories import _make_conflict_rules, _refused_by_conflict_rule
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_core_info_provider import (
    FakeCoreInfoProvider,
    FakeSandboxLauncher,
    libretro_option,
    standalone_option,
)
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_firmware_file_store import FakeFirmwareFileStore
from fakes.fake_firmware_resolver import FakeFirmwareResolver
from fakes.fake_path_exists_reader import FakePathExistsReader
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_platform_systems import RETRODECK_SOURCE, FakePlatformSystems
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock

from adapters.firmware_file import FirmwareFileAdapter
from domain.bios_file import BiosFile
from domain.bios_status import BiosFileEntry
from domain.emulator_commands import EmulatorOption, option_to_invocation, select_default_option
from domain.firmware_cache import FirmwareCacheEntry
from domain.firmware_store import StoredFirmware
from domain.firmware_wants import (
    IDENTITY_FILE,
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_OPEN,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    CoreFirmwareVerdict,
    EmulatorNote,
    FolderFile,
    FolderVerdict,
)
from domain.platform_system import NO_SYSTEM, SWITCHED_OFF, PlatformSystem
from domain.retrodeck_folders import GAME_DOWNLOAD, finding_refusal, no_bios_root, switched_off
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.shortcut_data import EmulatorInvocation
from lib.errors import Refused, RommConnectionError, ServerUnreachable
from services.active_core_resolver import ActiveCoreResolver, ActiveCoreResolverConfig
from services.cores import CoreService, CoreServiceConfig
from services.firmware import FirmwareService, FirmwareServiceConfig
from services.firmware.deletion import FirmwareDeletionIncomplete
from services.firmware.emulator_answers import EmulatorAnswer, emulator_fields
from services.firmware.status import FirmwareStatusReader
from services.firmware.store import FetchOutcome, FirmwareStore, FirmwareStoreConfig
from services.game_detail import GameDetailService, GameDetailServiceConfig


def _make_clock() -> FakeClock:
    """Return a fresh FakeClock pinned to a synthetic instant."""
    return FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC))


def _seed_rom(uow: FakeUnitOfWork, *, rom_id: int, platform_slug: str, app_id: int | None = 1) -> None:
    """Seed one ``Rom`` so firmware's synced-platform read sees the platform.

    ``app_id=None`` seeds an *unbound* ROM (no Steam shortcut) — its platform
    does not count as synced.
    """
    uow.roms.save(
        Rom(
            rom_id=rom_id,
            platform_slug=platform_slug,
            name=f"rom-{rom_id}",
            fs_name=f"rom-{rom_id}.zip",
            shortcut_app_id=app_id,
            last_synced_at="2026-01-01T00:00:00+00:00",
        )
    )


def _seed_firmware_cache(uow: FakeUnitOfWork, entries: list[FirmwareCacheEntry]) -> None:
    """Replace the fake firmware-cache repo's contents in one shot."""
    uow.firmware_cache.replace_all(entries)


def _stored(
    uow: FakeUnitOfWork,
    tender_folder: str,
    platform_slug: str,
    file_name: str,
    data: bytes = b"\x00",
    store: FakeFirmwareFileStore | None = None,
) -> str:
    """Record *file_name* in *platform_slug*'s store folder under *tender_folder*, and answer its path.

    Where *store* is given the bytes are put there too; otherwise the test puts
    them on disk itself, or leaves the record naming a file that is gone.
    """
    path = os.path.join(tender_folder, "bios", platform_slug, file_name)
    uow.firmware_store.save(
        StoredFirmware.mark_downloaded(
            platform_slug=platform_slug,
            file_name=file_name,
            folder=tender_folder,
            md5=hashlib.md5(data).hexdigest(),
            size=len(data),
            downloaded_at="2026-01-01T00:00:00",
        )
    )
    if store is not None:
        store.files[path] = data
    return path


def _id(core_so: str) -> str:
    """The identity a libretro entry carries — its core file's own basename.

    The tests seed a core by its ``core_so`` stem, which is what an emulator
    option's ``core_so`` field holds; a firmware answer is keyed on the identity
    instead, and for a libretro entry that is the same name with its extension.
    Spelled apart here rather than folded, because a service reading ``core_so``
    where it should read the identity is exactly the defect this seam removed —
    and with one string for both, that bug would pass.
    """
    return f"{core_so}.so"


def _pick(emulator: str | None, label: str | None = None) -> EmulatorInvocation:
    """One resolved emulator, as the per-game path hands it to the BIOS check.

    The check takes the PICK rather than an identity beside a name, so a test
    asking it about an emulator hands it one value too. ``EmulatorInvocation`` is
    what the ROM path really passes; its ``kind`` plays no part in a firmware
    answer, which is keyed on the identity alone.
    """
    return EmulatorInvocation(kind="libretro", label=label, emulator=emulator)


def _make_firmware_service(
    *,
    romm_api=None,
    uow_factory: FakeUnitOfWorkFactory | None = None,
    clock: FakeClock | None = None,
    firmware_file_store=None,
    firmware_resolver: FakeFirmwareResolver | None = None,
    retrodeck_folders: FakeRetroDeckFolders | None = None,
    core_info: FakeCoreInfoProvider | None = None,
    platform_systems: FakePlatformSystems | None = None,
    platform_core_reader: FakePlatformCoreReader | None = None,
    active_core: FakeActiveCoreResolver | None = None,
    logger=None,
    conflict_rules=None,
    tender_folder: str | None = None,
    data_dir: str | None = None,
    identifier=None,
) -> FirmwareService:
    """Build a ``FirmwareService`` over fake adapters + a fake Unit of Work.

    Mirrors the SQLite wiring: persistence flows entirely through
    ``uow_factory`` (no state dict, no persisters). Defaults keep every
    call-site terse; pass overrides only for the axis under test. The default
    resolver declares nothing and reports every emulator read, so an unseeded
    test sees a machine that genuinely wants no firmware.

    The resolver is pointed at the same BIOS root and file store the service
    gets, because on a real machine they are one directory read by two
    readers — so a test that puts a file where the platform expects it gets
    the same ``present`` from both. A test that hands in a resolver with a root
    of its own keeps it, and one that wants the two to disagree says so per
    file (``FakeFirmwareResolver.declare(present=...)``).

    The firmware store lies in *tender_folder*, which a write finds ready as it
    is; the backups go under *data_dir*, beside it unless named. A service
    built without a *tender_folder* refuses every write into the store, so a
    test that downloads over the real file adapter has to name a folder of its
    own rather than land in the user's home.
    """

    store = firmware_file_store if firmware_file_store is not None else FirmwareFileAdapter()
    paths = retrodeck_folders if retrodeck_folders is not None else FakeRetroDeckFolders()
    resolver = firmware_resolver if firmware_resolver is not None else FakeFirmwareResolver()
    if resolver.bios_root is None:
        resolver.bios_root = paths.bios
        resolver.present_probe = store.exists
    api = romm_api if romm_api is not None else MagicMock()
    uows = uow_factory if uow_factory is not None else FakeUnitOfWorkFactory()
    clock = clock if clock is not None else _make_clock()
    log = logger if logger is not None else logging.getLogger("test")

    def prepare_folder() -> str:
        assert tender_folder is not None, "this test writes into Tender's folder: pass tender_folder"
        return tender_folder

    firmware_store = FirmwareStore(
        config=FirmwareStoreConfig(
            romm_api=api,
            firmware_file_store=store,
            platform_firmware_resolver=resolver,
            platform_systems=platform_systems if platform_systems is not None else FakePlatformSystems(),
            prepare_folder=prepare_folder,
            uow_factory=uows,
            clock=clock,
            data_dir=data_dir
            if data_dir is not None
            else os.path.join(os.path.dirname(tender_folder or "/nowhere/Tender"), "data"),
            logger=log,
        )
    )

    return FirmwareService(
        config=FirmwareServiceConfig(
            romm_api=api,
            loop=running_loop(),
            logger=log,
            clock=clock,
            firmware_file_store=store,
            platform_firmware_resolver=resolver,
            firmware_identifier=identifier if identifier is not None else (lambda md5: None),
            store=firmware_store,
            retrodeck_folders=paths,
            core_info=core_info if core_info is not None else FakeCoreInfoProvider(),
            platform_systems=platform_systems if platform_systems is not None else FakePlatformSystems(),
            platform_core_reader=platform_core_reader if platform_core_reader is not None else FakePlatformCoreReader(),
            active_core=active_core if active_core is not None else FakeActiveCoreResolver(),
            uow_factory=uows,
            conflict_rules=conflict_rules if conflict_rules is not None else _make_conflict_rules(),
            settings={},
            settings_persister=FakeSettingsPersister(),
        ),
    )


def _set_loop(fw: FirmwareService, loop) -> None:
    """Point every sub-service that offloads work at *loop*.

    The loop is config-carried, so each sub-service holds its own reference and
    the façade holds none. A test that swaps the loop has to reach all three, or
    the swap lands on nothing and the offloaded hop runs on the loop the fixture
    happened to construct the service under.
    """
    fw._status._loop = loop
    fw._downloads._loop = loop
    fw._replacer._loop = loop
    fw._deletion._loop = loop


def _inline_executor(fw: FirmwareService) -> None:
    """Run every ``run_in_executor`` hop inline, on the calling task.

    The service offloads several distinct reads — the machine's firmware demand,
    the synced-platform slugs, the RomM listing — and which one a hop is running
    matters. A blanket ``AsyncMock`` answering one canned value for all of them
    would pass whatever the service asked for, so the shim dispatches on the
    function instead of on the call count.
    """

    async def run(_executor, fn, *args):
        return fn(*args)

    loop = MagicMock()
    loop.run_in_executor = run
    _set_loop(fw, loop)


def _core_info(fw: FirmwareService) -> FakeCoreInfoProvider:
    """The fake core-info provider behind *fw*, for tests that seed or read it after construction."""
    core_info = fw._config.core_info
    assert isinstance(core_info, FakeCoreInfoProvider)
    return core_info


async def _entries(fw: FirmwareService, *slugs: str) -> dict[str, dict[str, Any]]:
    """Each named platform's whole entry, asked the way the page asks: one platform at a time.

    The page asks ``get_platform_firmware_status`` for every platform its list
    shows, so the tests that are about what a pane SHOWS ask the same call
    rather than each learning the walk. Deliberately serial and complete,
    which the page is not: the frontend reorders around the focused row and
    stops when the reader leaves, and neither of those changes an answer.
    """
    entries: dict[str, dict[str, Any]] = {}
    for slug in slugs:
        answer = await fw.get_platform_firmware_status(slug)
        assert answer["success"] is True
        entries[slug] = answer["platform"]
    return entries


def _platform_core_reader(fw: FirmwareService) -> FakePlatformCoreReader:
    """The fake per-platform override map behind *fw*.

    Taken off the service rather than built a second time, for the tests that
    hand the same map to a peer: two readers seeded alike agree by coincidence,
    which is the shape those tests exist to refuse.
    """
    reader = fw._config.platform_core_reader
    assert isinstance(reader, FakePlatformCoreReader)
    return reader


def _set_platform_emulator(fw: FirmwareService, core_so: str, label: str) -> None:
    """Make *core_so* the one emulator ES-DE offers for every system, on the fake behind *fw*.

    Seeds both of the fake's inputs: ``options``, which the platform's own pick
    is resolved from, and ``active_core``, the libretro-only system reading. They
    are separate seeds answering different questions, so a test that means "this
    platform launches with mGBA" has to state it in the list rather than in the
    system reading alone.
    """
    core_info = _core_info(fw)
    core_info.options = [libretro_option(core_so, label)]
    core_info.active_core = (core_so, label)


def _resolver(fw: FirmwareService) -> FakeFirmwareResolver:
    """The fake resolver behind *fw*, for tests that seed it after construction."""
    resolver = fw._demand._platform_firmware_resolver
    assert isinstance(resolver, FakeFirmwareResolver)
    return resolver


def _resolver_reads(fw: FirmwareService, bios_dir) -> None:
    """Point the resolver at *bios_dir* as well — one directory, two readers.

    For a test that swaps the service's BIOS root after construction, where
    ``_make_firmware_service`` could not wire the pair up itself.
    """
    _resolver(fw).bios_root = str(bios_dir)


def _platforms(firmware_list: list[dict[str, Any]], folders: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """RomM's platform list carrying *firmware_list*, each row on the platform its ``platform_slug`` names.

    A row states its platform the way RomM does, by the platform's ``id`` as
    ``platform_id``; ``platform_slug`` is only how a test names it. A platform's
    server folder (``fs_slug``) is its slug unless *folders* names another.
    """
    folders = folders or {}
    platforms: dict[str, dict[str, Any]] = {}
    for row in firmware_list:
        fields = {key: value for key, value in row.items() if key != "platform_slug"}
        slug = row["platform_slug"]
        platform = platforms.setdefault(
            slug, {"id": len(platforms) + 1, "slug": slug, "fs_slug": folders.get(slug, slug), "firmware": []}
        )
        platform["firmware"].append({**fields, "platform_id": platform["id"]})
    return list(platforms.values())


def _stub_listing(fw: FirmwareService, firmware_list: list[dict[str, Any]]) -> None:
    """Answer ``list_platforms`` with the platforms carrying *firmware_list* on the service's API stub."""
    api = fw._config.romm_api
    assert isinstance(api, MagicMock)
    api.list_platforms.return_value = _platforms(firmware_list)


_TEST_CORE = "testcore_libretro"


def _test_core_info() -> FakeCoreInfoProvider:
    """ES-DE offering exactly the one libretro core ``_declare`` attributes its wants to.

    The scope must be non-empty and must contain that core. A platform ES-DE
    offers no libretro core for reads ``unknown`` by design (35 of its 172
    systems are in that position), so a fixture left on the bare
    ``FakeCoreInfoProvider()`` would exercise the no-scope path while its tests
    read as if they were about a complete reading.
    """
    return FakeCoreInfoProvider(options=[libretro_option(_TEST_CORE, "Test Core")])


@dataclass
class FirmwareHarness:
    """The firmware service and the seams its tests seed or assert against.

    ``uow`` is the fake unit of work firmware persistence flows through; tests
    inspect its repositories after the service has run. ``romm_api`` is the
    RomM API the service lists and fetches firmware from.
    """

    service: FirmwareService
    uow: FakeUnitOfWork
    romm_api: MagicMock


@pytest.fixture
def firmware(tmp_path) -> FirmwareHarness:
    romm_api = MagicMock()
    uow = FakeUnitOfWork()
    service = _make_firmware_service(
        romm_api=romm_api,
        uow_factory=FakeUnitOfWorkFactory(uow),
        clock=_make_clock(),
        core_info=_test_core_info(),
        tender_folder=str(tmp_path / "Tender"),
    )
    return FirmwareHarness(service=service, uow=uow, romm_api=romm_api)


@pytest.fixture(autouse=True)
async def _set_event_loop(fw):
    """Ensure the firmware sub-services match the running event loop."""
    _set_loop(fw, asyncio.get_running_loop())


@pytest.fixture
def fw(firmware):
    return firmware.service


# What the façade holds. Everything else the service needs lives on a sub-service.
_FACADE_ATTRIBUTES = frozenset(
    {
        "_config",
        "_rules",
        "_identifier",
        "_store",
        "_listing",
        "_demand",
        "_status",
        "_downloads",
        "_replacer",
        "_deletion",
    }
)


@pytest.fixture(autouse=True)
def _no_dead_rebind_on_the_facade(fw):
    """Fail an assignment to a name the façade does not have.

    Python answers such an assignment by creating the name, so ``fw._loop = loop``
    — a line meaning to swap the loop the offloaded hops run on — lands on the
    façade, does nothing, and leaves the test green against whichever loop the
    fixture built the service under. Nothing else notices: the call it was meant
    to redirect still works, just against the old object. This makes the
    assignment itself the failure.

    Two assignments stay allowed, and only one of them is safe. Shadowing a name
    the façade already answers to is deliberate — ``fw.check_platform_bios = mock``
    replaces a real method rather than inventing one. Rebinding one of the six
    construction slots is NOT: ``fw._listing = FakeListing()`` reaches
    ``invalidate_firmware_cache`` and nothing else, because every sub-service bound
    its own reference to the listing when it was built, and the same holds for
    ``_demand``. A swap has to reach every holder, so build the service around the
    fake rather than rebinding it afterwards.
    """
    yield
    stray = sorted(name for name in vars(fw) if name not in _FACADE_ATTRIBUTES and not hasattr(type(fw), name))
    assert not stray, (
        f"assigned {stray} to the façade, which holds no such name — the assignment did nothing. "
        f"Firmware state lives on a sub-service: assign inside fw._listing / fw._demand / fw._status / "
        f"fw._downloads / fw._deletion, or _set_loop(fw, loop) for the event loop."
    )


def _declare(fw: FirmwareService, *specs: tuple[str, str, bool]) -> None:
    """State the machine's demand as ``(file_name, description, required)`` triples.

    Every want is attributed to an owning emulator, because a placement without
    one is exactly what the four-value model removes. These tests leave the
    active core unresolved, where a file required by any emulator counts as
    required for the launch, so the core's name is not the axis under test — the
    tests that DO test per-core filtering name their cores themselves.
    """
    for file_name, description, required in specs:
        _resolver(fw).declare(
            file_name,
            required_by=[_id(_TEST_CORE)] if required else [],
            optional_for=[] if required else [_TEST_CORE],
            description=description,
        )


class TestTheFacadeOnlyDelegates:
    """The façade wires the sub-services and delegates. It implements nothing itself.

    Stated as the whole surface rather than as a list of banned names, so it fails
    in both directions: a helper the split relocated cannot come back here, and a
    new method cannot arrive without someone deciding it belongs on the façade
    rather than in the module that owns its job. A second implementation here is
    what the split cost — presence, the destination it is read at, and the RomM
    listing each have one owner now, and a convenience wrapper on the façade would
    answer from whatever it happened to hold.
    """

    def test_the_facade_defines_exactly_its_delegations(self):
        own = {name for name, value in vars(FirmwareService).items() if callable(value) and not name.startswith("__")}
        assert own == {
            "invalidate_firmware_cache",
            "get_firmware_status",
            "get_platform_firmware_status",
            "check_platform_bios",
            "download_all_firmware",
            "download_platform_firmware_file",
            "download_required_firmware",
            "update_bios_links",
            "replace_bios_file",
            "identify_library_firmware",
            "delete_platform_bios",
            "delete_bios_file",
            "is_downloading",
        }

    def test_the_facade_holds_only_its_sub_services(self, fw):
        """Construction leaves the config and the five sub-services, and nothing else."""
        assert set(vars(fw)) == _FACADE_ATTRIBUTES


class TestPresenceComesFromTheReading:
    """Who answers "is the file there": the reading for a declared row, us for the rest."""

    _CORE = "flycast_libretro"

    def _service(self, firmware, tmp_path, resolver, store: FakeFirmwareFileStore | None = None):
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store if store is not None else FakeFirmwareFileStore(),
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "Flycast"), options=[libretro_option(self._CORE, "Flycast")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)
        _stub_listing(
            fw,
            [{"id": 1, "file_name": "dc_boot.bin", "platform_slug": "dc", "file_size_bytes": 8}],
        )
        return fw

    @staticmethod
    def _store_holding(tmp_path, name: str) -> FakeFirmwareFileStore:
        """A file store with *name* sitting in the BIOS root, whatever the reading says."""
        return FakeFirmwareFileStore({os.path.join(str(tmp_path / "bios"), name): b"\x00"})

    @pytest.mark.asyncio
    async def test_a_declared_row_is_there_when_the_reading_says_so(self, firmware, tmp_path):
        """Nothing in our own store, and the row still reads present.

        The reading followed the symlinks to the destination the emulator will
        open; a probe of the path assembled here answers about a different
        place, and that divergence is what put a satisfied LRPS2 requirement on
        the page as a red missing row.
        """
        resolver = FakeFirmwareResolver()
        resolver.declare("dc_boot.bin", required_by=[_id(self._CORE)], present=True)
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("dc")

        assert result["files"][0]["downloaded"] is True
        assert result["required_downloaded"] == 1

    @pytest.mark.asyncio
    async def test_a_declared_row_is_absent_when_the_reading_says_so(self, firmware, tmp_path):
        """And the other direction: our store holding it does not override the reading."""
        resolver = FakeFirmwareResolver()
        resolver.declare("dc_boot.bin", required_by=[_id(self._CORE)], present=False)
        fw = self._service(firmware, tmp_path, resolver, self._store_holding(tmp_path, "dc_boot.bin"))

        result = await fw.check_platform_bios("dc")

        assert result["files"][0]["downloaded"] is False

    @pytest.mark.asyncio
    async def test_a_destination_the_reading_could_not_look_at_is_not_a_claim(self, firmware, tmp_path):
        """ "Could not look" is not "it is there" — the row stays missing."""
        resolver = FakeFirmwareResolver()
        resolver.declare("dc_boot.bin", required_by=[_id(self._CORE)])
        resolver.bios_root = ""  # no place for the fake to take a reading, so it withholds one
        # The store holds it, so a fallback to our own probe would read green here.
        fw = self._service(firmware, tmp_path, resolver, self._store_holding(tmp_path, "dc_boot.bin"))

        result = await fw.check_platform_bios("dc")

        assert result["files"][0]["downloaded"] is False

    @pytest.mark.asyncio
    async def test_a_row_nothing_declares_is_ours_to_probe(self, firmware, tmp_path):
        """The row our own look covers: a library file no emulator asked for, there once it lies in the store."""
        resolver = FakeFirmwareResolver()
        resolver.describe_emulator(_id(self._CORE))
        store = FakeFirmwareFileStore()
        _stored(firmware.uow, str(tmp_path / "Tender"), "dc", "dc_boot.bin", store=store)
        fw = self._service(firmware, tmp_path, resolver, store)

        result = await fw.check_platform_bios("dc")

        assert result["files"][0]["downloaded"] is True
        assert result["files"][0]["wanted"] == "not_needed"

    @pytest.mark.asyncio
    async def test_a_placement_with_no_place_is_the_readings_too(self, firmware, tmp_path):
        """A declaration the resolver gives no place for is still the reading's to answer.

        The store holding the file says nothing about where the emulator opens
        it, so Tender's own look never stands in for the reading there.
        """
        resolver = FakeFirmwareResolver()
        resolver.declare("dc_boot.bin", required_by=[_id(self._CORE)], relative_path=None, present=False)
        store = FakeFirmwareFileStore()
        _stored(firmware.uow, str(tmp_path / "Tender"), "dc", "dc_boot.bin", store=store)
        fw = self._service(firmware, tmp_path, resolver, store)

        result = await fw.check_platform_bios("dc")

        assert result["files"][0]["downloaded"] is False


class TestWhileRetroDeckIsSwitchedOff:
    """RetroDECK switched off: the resolver's answers stand, Tender looks into its store alone, downloads land there."""

    _CORE = "flycast_libretro"
    _ROW: ClassVar[dict[str, Any]] = {
        "id": 1,
        "file_name": "dc_boot.bin",
        "platform_slug": "dc",
        "file_size_bytes": 8,
        "md5_hash": hashlib.md5(b"\x00").hexdigest(),
    }

    def _service(self, firmware, tmp_path, resolver, folders: FakeRetroDeckFolders):
        # RetroDECK's BIOS folder holds the file, so a look of Tender's there would read it present.
        store = FakeFirmwareFileStore({os.path.join(str(tmp_path / "bios"), "dc_boot.bin"): b"\x00"})
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "Flycast"), options=[libretro_option(self._CORE, "Flycast")]
            ),
            retrodeck_folders=folders,
            tender_folder=str(tmp_path / "Tender"),
        )

        def download(_firmware_id, _name, path):
            store.files[path] = b"\x00"

        firmware.romm_api.download_firmware.side_effect = download
        _inline_executor(fw)
        _stub_listing(fw, [dict(self._ROW)])
        return fw

    @staticmethod
    def _switched_off(tmp_path) -> FakeRetroDeckFolders:
        return FakeRetroDeckFolders(bios=str(tmp_path / "bios"), download_refusal=switched_off(GAME_DOWNLOAD))

    @pytest.mark.asyncio
    async def test_a_row_nothing_declares_could_not_be_established(self, firmware, tmp_path):
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), self._switched_off(tmp_path))

        row = (await fw.check_platform_bios("dc"))["files"][0]

        assert row["satisfied"] is None
        assert row["downloaded"] is False
        assert row["local_path"] is None

    @pytest.mark.asyncio
    async def test_a_row_that_could_not_be_established_is_left_out_of_the_stated_ratio(self, firmware, tmp_path):
        # The ratio's own pair counts only established rows; the level's pair
        # still counts the row, as the level, the label and the buttons did.
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), self._switched_off(tmp_path))

        result = await fw.check_platform_bios("dc")

        assert (result["ratio_server_count"], result["ratio_local_count"]) == (0, 0)
        assert (result["server_count"], result["local_count"]) == (1, 0)

    @pytest.mark.asyncio
    async def test_the_stated_ratio_counts_the_rows_that_were_established(self, firmware, tmp_path):
        resolver = FakeFirmwareResolver()
        resolver.declare("dc_boot.bin", required_by=[_id(self._CORE)], present=True)
        fw = self._service(firmware, tmp_path, resolver, self._switched_off(tmp_path))
        _stub_listing(
            fw,
            [dict(self._ROW), {**self._ROW, "id": 2, "file_name": "dc_flash.bin", "platform_slug": "dc"}],
        )

        result = await fw.check_platform_bios("dc")

        assert (result["ratio_server_count"], result["ratio_local_count"]) == (1, 1)
        assert (result["server_count"], result["local_count"]) == (2, 1)

    @pytest.mark.asyncio
    async def test_a_row_the_resolver_answers_keeps_its_answer(self, firmware, tmp_path):
        resolver = FakeFirmwareResolver()
        resolver.declare("dc_boot.bin", required_by=[_id(self._CORE)], present=True)
        fw = self._service(firmware, tmp_path, resolver, self._switched_off(tmp_path))

        row = (await fw.check_platform_bios("dc"))["files"][0]

        assert row["downloaded"] is True
        assert row["satisfied"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("press", "args"),
        [
            ("download_all_firmware", ("dc",)),
            ("download_platform_firmware_file", ("dc", "dc_boot.bin")),
        ],
    )
    async def test_every_download_press_lands_in_the_store(self, firmware, tmp_path, press, args):
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), self._switched_off(tmp_path))

        result = await getattr(fw, press)(*args)

        assert result["downloaded"] == 1
        stored = firmware.uow.firmware_store.get("dc", "dc_boot.bin")
        assert stored is not None
        assert stored.folder == str(tmp_path / "Tender")

    @pytest.mark.asyncio
    async def test_a_delete_still_removes_what_tender_put_there(self, firmware, tmp_path):
        bios = tmp_path / "bios" / "dc_boot.bin"
        bios.parent.mkdir(parents=True)
        bios.write_bytes(b"\x00")
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="dc",
                file_name="dc_boot.bin",
                file_path=str(bios),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=1,
            )
        )
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), self._switched_off(tmp_path))
        fw._deletion._files = FirmwareFileAdapter()

        result = await fw.delete_bios_file("dc", "dc_boot.bin")

        assert isinstance(result, dict)
        assert result["deleted_count"] == 1
        assert not bios.exists()

    @pytest.mark.asyncio
    async def test_while_retrodeck_s_folders_are_defaults_a_delete_leaves_the_older_download_and_names_it(
        self, firmware, tmp_path
    ):
        bios = tmp_path / "bios" / "dc_boot.bin"
        bios.parent.mkdir(parents=True)
        bios.write_bytes(b"\x00")
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="dc",
                file_name="dc_boot.bin",
                file_path=str(bios),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=1,
            )
        )
        folders = FakeRetroDeckFolders(bios=str(tmp_path / "bios"), refusal=finding_refusal("marker-invalid", {}))
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), folders)
        fw._deletion._files = FirmwareFileAdapter()

        result = await fw.delete_bios_file("dc", "dc_boot.bin")
        entry = (await fw.get_platform_firmware_status("dc"))["platform"]

        assert isinstance(result, dict)
        assert result["kept"] == [
            {
                "kind": "file",
                "place": str(bios),
                "folder": str(bios.parent),
                "source": "retrodeck",
                "file_name": "dc_boot.bin",
                "why": "settings_unreadable",
            }
        ]
        assert "Nothing to delete" not in result["message"]
        assert bios.exists()
        assert firmware.uow.bios_files.get("dc", "dc_boot.bin") is not None
        # Still counted, so its Delete stays offered.
        assert entry["deletable_count"] == 1

    def _older_download(self, firmware, path):
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="dc",
                file_name="dc_boot.bin",
                file_path=str(path),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=1,
            )
        )

    @pytest.mark.asyncio
    async def test_without_retrodeck_a_delete_leaves_the_older_download_counted_and_says_why(self, firmware, tmp_path):
        bios = tmp_path / "bios" / "dc_boot.bin"
        bios.parent.mkdir(parents=True)
        bios.write_bytes(b"\x00")
        self._older_download(firmware, bios)
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), FakeRetroDeckFolders())
        fw._deletion._files = FirmwareFileAdapter()

        result = await fw.delete_bios_file("dc", "dc_boot.bin")
        entry = (await fw.get_platform_firmware_status("dc"))["platform"]

        assert isinstance(result, dict)
        assert [k["why"] for k in result["kept"]] == ["not_installed"]
        assert bios.exists()
        assert firmware.uow.bios_files.get("dc", "dc_boot.bin") is not None
        assert entry["deletable_count"] == 1

    @pytest.mark.asyncio
    async def test_where_retrodeck_names_no_bios_folder_a_delete_leaves_the_older_download_counted_and_says_why(
        self, firmware, tmp_path
    ):
        bios = tmp_path / "bios" / "dc_boot.bin"
        bios.parent.mkdir(parents=True)
        bios.write_bytes(b"\x00")
        self._older_download(firmware, bios)
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), FakeRetroDeckFolders(refusal=no_bios_root()))
        fw._deletion._files = FirmwareFileAdapter()

        result = await fw.delete_bios_file("dc", "dc_boot.bin")
        entry = (await fw.get_platform_firmware_status("dc"))["platform"]

        assert isinstance(result, dict)
        assert [k["why"] for k in result["kept"]] == ["no_bios_folder"]
        assert bios.exists()
        assert firmware.uow.bios_files.get("dc", "dc_boot.bin") is not None
        assert entry["deletable_count"] == 1

    @pytest.mark.asyncio
    async def test_an_older_download_outside_retrodeck_s_bios_folder_stays_and_its_record_goes(
        self, firmware, tmp_path
    ):
        elsewhere = tmp_path / "elsewhere" / "dc_boot.bin"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_bytes(b"\x00")
        (tmp_path / "bios").mkdir()
        self._older_download(firmware, elsewhere)
        fw = self._service(
            firmware, tmp_path, FakeFirmwareResolver(), FakeRetroDeckFolders(bios=str(tmp_path / "bios"))
        )
        fw._deletion._files = FirmwareFileAdapter()

        result = await fw.delete_bios_file("dc", "dc_boot.bin")
        entry = (await fw.get_platform_firmware_status("dc"))["platform"]

        assert isinstance(result, dict)
        assert result["kept"] == [
            {
                "kind": "file",
                "place": str(elsewhere),
                "folder": str(elsewhere.parent),
                "source": "retrodeck",
                "file_name": "dc_boot.bin",
                "why": "outside_bios_folder",
            }
        ]
        assert elsewhere.exists()
        assert firmware.uow.bios_files.get("dc", "dc_boot.bin") is None
        assert entry["deletable_count"] == 0

    @pytest.mark.asyncio
    async def test_an_older_download_whose_file_is_gone_loses_its_record_without_a_word(self, firmware, tmp_path):
        # Even while RetroDECK's settings cannot be read: a gone file is nothing to keep.
        self._older_download(firmware, tmp_path / "bios" / "dc_boot.bin")
        folders = FakeRetroDeckFolders(bios=str(tmp_path / "bios"), refusal=finding_refusal("marker-invalid", {}))
        fw = self._service(firmware, tmp_path, FakeFirmwareResolver(), folders)
        fw._deletion._files = FirmwareFileAdapter()

        result = await fw.delete_bios_file("dc", "dc_boot.bin")

        assert isinstance(result, dict)
        assert result["kept"] == []
        assert result["deleted_count"] == 0
        assert firmware.uow.bios_files.get("dc", "dc_boot.bin") is None


class TestDestinationReadingsReachBothSurfaces:
    """``supplied_by`` and ``declared_kind`` travel to the game page and the platform detail."""

    _CORE = "flycast_libretro"

    def _service(self, firmware, tmp_path, resolver):
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "Flycast"), options=[libretro_option(self._CORE, "Flycast")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)
        return fw

    @pytest.mark.asyncio
    async def test_the_game_page_row_names_the_supplying_distribution(self, firmware, tmp_path):
        resolver = FakeFirmwareResolver()
        resolver.declare("codehandler.bin", required_by=[_id(self._CORE)], present=True, supplied_by="RetroDECK")
        fw = self._service(firmware, tmp_path, resolver)
        _stub_listing(fw, [])

        result = await fw.check_platform_bios("dc")

        assert result["files"][0]["supplied_by"] == "RetroDECK"
        assert result["files"][0]["declared_kind"] == "file"

    @pytest.mark.asyncio
    async def test_the_system_page_row_names_the_folder_declaration_it_lies_in(self, firmware, tmp_path):
        _seed_rom(firmware.uow, rom_id=51, platform_slug="dc", app_id=1)
        resolver = FakeFirmwareResolver()
        image = str(tmp_path / "bios" / "pcsx2" / "bios" / "scph39001.bin")
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=True,
            declares_directory=True,
            folder_files=(FolderFile(path=image, satisfied=True),),
        )
        fw = self._service(firmware, tmp_path, resolver)
        _stub_listing(fw, [])

        result = await _entries(fw, "dc")
        row = result["dc"]["files"][0]

        assert row["in_folder"] == "pcsx2/bios"
        assert row["declared_kind"] == "file"
        assert row["supplied_by"] is None
        assert row["downloaded"] is True


class TestTheOverviewRowsAreAlphabetical:
    """The pane's file list is ordered as it reads, not as the two halves arrive."""

    _CORE = "dolphin_libretro"

    @pytest.mark.asyncio
    async def test_local_only_rows_are_not_simply_appended(self, firmware, tmp_path):
        """A file Tender holds sorts among the library's, not below them.

        The rows come from two sources — the library's listing, then what the
        emulators want beyond it — and the second was appended, so
        ``codehandler.bin`` sat under ``gc-pal-12.bin`` on a GameCube pane for
        no reason a reader could see.
        """
        store = FakeFirmwareFileStore()
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            core_info=_test_core_info(),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)
        # Declared by an emulator and NOT in the library, so it is appended.
        _declare(fw, ("cx4.data.rom", "CX4 data", True))
        _stub_listing(
            fw,
            [
                {
                    "id": 1,
                    "file_name": "sgb_boot.bin",
                    "platform_slug": "gc",
                    "file_size_bytes": 8,
                    "md5_hash": "",
                },
                {
                    "id": 2,
                    "file_name": "SGB1.sfc",
                    "platform_slug": "gc",
                    "file_size_bytes": 8,
                    "md5_hash": "",
                },
            ],
        )

        result = await _entries(fw, "gc")
        names = [f["file_name"] for f in result["gc"]["files"]]

        # Case-folded, so a capitalised name does not sort above every lowercase
        # one: the SNES pane really does mix `SGB1.sfc` with `sgb_boot.bin`, and
        # a case-sensitive sort puts every capital first, which reads as no sort
        # at all. `cx4.data.rom` is the appended one and lands between them.
        assert names == ["cx4.data.rom", "SGB1.sfc", "sgb_boot.bin"]


class TestAFileInAFolderCountsItsOwnDelete:
    """A declared folder is a row per file in it, and each file's ``deletable_count`` is its own.

    The folder has no name a record could carry, but each file in it has: a
    file Tender downloaded there is offered its Delete, and one the user put
    there is not.
    """

    _CORE = "pcsx2_libretro"

    def _service(self, firmware, tmp_path, resolver, store):
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "LRPS2"), options=[libretro_option(self._CORE, "LRPS2")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        return fw

    @pytest.mark.asyncio
    async def test_the_folder_row_counts_the_files_beneath_it(self, firmware, tmp_path):
        _seed_rom(firmware.uow, rom_id=61, platform_slug="dc", app_id=1)
        inside = os.path.join(str(tmp_path / "bios"), "pcsx2", "bios", "scph39001.bin")
        hand_placed = os.path.join(str(tmp_path / "bios"), "pcsx2", "bios", "scph70012.bin")
        elsewhere = os.path.join(str(tmp_path / "bios"), "scph5501.bin")
        store = FakeFirmwareFileStore({inside: b"\x00" * 8, hand_placed: b"\x00" * 8, elsewhere: b"\x00" * 8})
        for name, path in (("scph39001.bin", inside), ("scph5501.bin", elsewhere)):
            firmware.uow.bios_files.save(
                BiosFile.mark_downloaded(
                    platform_slug="dc",
                    file_name=name,
                    file_path=path,
                    downloaded_at="2026-01-01T00:00:00+00:00",
                    firmware_id=None,
                )
            )
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=True,
            declares_directory=True,
            folder_files=(FolderFile(path=inside, satisfied=True), FolderFile(path=hand_placed, satisfied=True)),
        )
        fw = self._service(firmware, tmp_path, resolver, store)

        result = await _entries(fw, "dc")
        rows = {row["file_name"]: row for row in result["dc"]["files"] if row["in_folder"] == "pcsx2/bios"}

        # The file Tender downloaded into the folder is offered its Delete; the
        # hand-placed one beside it is not ours.
        assert rows["scph39001.bin"]["deletable_count"] == 1
        assert rows["scph70012.bin"]["deletable_count"] == 0

    @pytest.mark.asyncio
    async def test_two_records_naming_one_file_are_one_deletion(self, firmware, tmp_path):
        """The row promises unlinks, and the same path unlinks once.

        A file downloaded twice — once under ``psx``, once under ``ps``, the two
        spellings RomM files one platform's firmware under — leaves two records
        at one path. Counting records there offers ``Delete (2)`` and takes one
        file away, which is the platform count's own rule read one layer in.
        """
        _seed_rom(firmware.uow, rom_id=62, platform_slug="psx", app_id=1)
        inside = os.path.join(str(tmp_path / "bios"), "pcsx2", "bios", "scph39001.bin")
        store = FakeFirmwareFileStore({inside: b"\x00" * 8})
        for slug in ("psx", "ps"):
            firmware.uow.bios_files.save(
                BiosFile.mark_downloaded(
                    platform_slug=slug,
                    file_name="scph39001.bin",
                    file_path=inside,
                    downloaded_at="2026-01-01T00:00:00+00:00",
                    firmware_id=None,
                )
            )
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=True,
            declares_directory=True,
            folder_files=(FolderFile(path=inside, satisfied=True),),
        )
        fw = self._service(firmware, tmp_path, resolver, store)

        result = await _entries(fw, "psx")
        plat = result["psx"]
        (row,) = [row for row in plat["files"] if row["in_folder"] == "pcsx2/bios"]

        assert row["deletable_count"] == 1
        # And the platform button, which has counted paths all along, agrees.
        assert plat["deletable_count"] == 1


class TestWhatBecameOfARowsBytes:
    """``checked`` reaches the wire, because the verdict beside it cannot carry it.

    A withheld verdict has several causes and one of them is not a withholding at
    all: a file the emulator READ and does not recognise was checked, and the
    surfaces said it could not be. The resolver states which, and the row has to
    carry the word rather than a reading of it — the vocabulary is upstream's and
    narrowing it here would be our own claim about it.
    """

    _CORE = "swanstation"

    def _page(self, firmware, tmp_path, checked: str | None):
        resolver = FakeFirmwareResolver()
        resolver.declare("scph1001.bin", required_by=[_id(self._CORE)], present=True, checked=checked)
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "SwanStation"), options=[libretro_option(self._CORE, "SwanStation")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        return fw.check_platform_bios("psx")

    @pytest.mark.asyncio
    async def test_the_resolvers_own_word_reaches_the_row(self, firmware, tmp_path):
        result = await self._page(firmware, tmp_path, "unrecognised")
        row = next(row for row in result["files"] if row["file_name"] == "scph1001.bin")

        assert row["checked"] == "unrecognised"

    @pytest.mark.asyncio
    async def test_a_different_reading_travels_as_itself(self, firmware, tmp_path):
        """The non-vacuity check: a row hardcoding one value would pass the first."""
        result = await self._page(firmware, tmp_path, "unread")
        row = next(row for row in result["files"] if row["file_name"] == "scph1001.bin")

        assert row["checked"] == "unread"

    @pytest.mark.asyncio
    async def test_a_reading_that_asked_no_byte_question_says_nothing(self, firmware, tmp_path):
        """The ordinary case, and it must stay a stated absence rather than a word."""
        result = await self._page(firmware, tmp_path, None)
        row = next(row for row in result["files"] if row["file_name"] == "scph1001.bin")

        assert row["checked"] is None

    @pytest.mark.asyncio
    async def test_a_file_it_could_not_recognise_is_not_established_though_it_is_there(self, firmware, tmp_path):
        """The row's verdict is the resolver's, and ``unrecognised`` is one it does not establish.

        The bytes were read and the requirement is still unsettled, so the row
        is there and not met-or-unmet: ``downloaded`` says the first, the
        verdict the second.
        """
        result = await self._page(firmware, tmp_path, "unrecognised")
        row = next(row for row in result["files"] if row["file_name"] == "scph1001.bin")

        assert row["satisfied"] is None
        assert row["downloaded"] is True

    @pytest.mark.asyncio
    async def test_the_platform_pane_gets_the_same_word(self, firmware, tmp_path):
        """The two pages are built by two row builders, and this asks the other one.

        The game page's rows come off ``asdict`` and carry every field the entry
        has; the platform pane's are a hand-written projection
        (``_wanted_fields``), so a field added to the entry reaches that page
        only if somebody adds it there too. The two surfaces word this from ONE
        module, so a field on one and not the other means one of them silently
        words a state it cannot see.
        """
        resolver = FakeFirmwareResolver()
        resolver.declare("scph1001.bin", required_by=[_id(self._CORE)], present=True, checked="unrecognised")
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "SwanStation"), options=[libretro_option(self._CORE, "SwanStation")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms([])):
            result = await fw.get_platform_firmware_status("psx")

        row = next(row for row in result["platform"]["files"] if row["file_name"] == "scph1001.bin")
        assert row["checked"] == "unrecognised"


class TestAFolderRequirementIsAnsweredByItsContents:
    """LRPS2's ``pcsx2/bios`` is answered by what is inside it, never by its being there.

    RetroDECK links that folder onto the BIOS root, so it is present on every
    install: reading presence as the verdict reports "All required ready" over a
    PS2 system with no BIOS file at all, and reading absence reports red over a
    folder that is plainly there. The verified per-platform read settles it, and
    the readiness verdict declines only where that read established nothing.
    """

    _CORE = "pcsx2_libretro"
    _IMAGES = ("Europe  v02.00(14/06/2004)  Console 20040614-100914",)

    def _service(self, firmware, tmp_path, resolver):
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "LRPS2"), options=[libretro_option(self._CORE, "LRPS2")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        return fw

    def _resolver(self, folder: FolderVerdict | None, *, caveats=(), folder_files=()) -> FakeFirmwareResolver:
        """What LRPS2 asks for: a folder that is there, and a file beside it."""
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=True,
            declares_directory=True,
            folder=folder,
            caveats=caveats,
            folder_files=folder_files,
        )
        resolver.declare(
            "GameIndex.yaml",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/resources/GameIndex.yaml",
            present=True,
        )
        return resolver

    @pytest.mark.asyncio
    async def test_an_image_in_the_folder_completes_the_required_ratio(self, firmware, tmp_path):
        resolver = self._resolver(FolderVerdict(satisfied=True, images=self._IMAGES))
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")

        assert result["required_count"] == 2
        assert result["required_downloaded"] == 2
        assert result["required_withheld"] == 0
        assert result["bios_level"] == "ok"

    def _in_folder(self, tmp_path, name: str) -> str:
        return str(tmp_path / "bios" / "pcsx2" / "bios" / name)

    @pytest.mark.asyncio
    async def test_each_image_the_read_identified_is_a_row_of_its_own(self, firmware, tmp_path):
        image = FolderFile(path=self._in_folder(tmp_path, "scph70004.bin"), satisfied=True, description=self._IMAGES[0])
        resolver = self._resolver(FolderVerdict(satisfied=True, images=self._IMAGES), folder_files=(image,))
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")
        row = next(row for row in result["files"] if row["file_name"] == "scph70004.bin")

        assert row["satisfied"] is True
        assert list(row["images"]) == list(self._IMAGES)
        assert row["declared_kind"] == "file"
        assert row["in_folder"] == "pcsx2/bios"
        assert all(row["file_name"] != "bios" for row in result["files"])

    @pytest.mark.asyncio
    async def test_each_file_in_the_folder_reads_its_own_check_never_the_folders(self, firmware, tmp_path):
        """The folder's own reading compared nothing (``unknown``); each file the read stated something about did."""
        identified = FolderFile(path=self._in_folder(tmp_path, "scph70004.bin"), satisfied=True)
        rejected = FolderFile(path=self._in_folder(tmp_path, "notabios.bin"), satisfied=False)
        contradicted = FolderFile(path=self._in_folder(tmp_path, "odd.bin"), satisfied=None)
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=True,
            declares_directory=True,
            folder=FolderVerdict(satisfied=True, images=self._IMAGES),
            checked="unknown",
            folder_files=(identified, rejected, contradicted),
        )
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")
        checked = {row["file_name"]: row["checked"] for row in result["files"]}

        assert checked == {"scph70004.bin": "verified", "notabios.bin": "rejected", "odd.bin": "unknown"}

    @pytest.mark.asyncio
    async def test_a_folder_linked_onto_the_bios_root_answers_its_images_by_their_resolved_paths(
        self, firmware, tmp_path
    ):
        """RetroDECK links ``pcsx2/bios`` onto the BIOS root; the resolver states the folder and its images there."""
        root = tmp_path / "bios"
        root.mkdir()
        images = [
            FolderFile(path=str(root / name), satisfied=True, description=description)
            for name, description in (
                ("ps2-0200a-20040614.bin", "USA     v02.00(14/06/2004)  Console 20040614-100909"),
                ("ps2-0200e-20040614.bin", "Europe  v02.00(14/06/2004)  Console 20040614-100914"),
            )
        ]
        for image in images:
            open(image.path, "wb").close()
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            place=str(root),
            present=True,
            declares_directory=True,
            folder=FolderVerdict(satisfied=True, images=self._IMAGES),
            checked="unknown",
            folder_files=tuple(images),
        )
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")
        rows = [row for row in result["files"] if row.get("in_folder") == "pcsx2/bios"]

        assert sorted(row["declared_path"] for row in rows) == [
            "pcsx2/bios/ps2-0200a-20040614.bin",
            "pcsx2/bios/ps2-0200e-20040614.bin",
        ]
        assert {(row["satisfied"], row["checked"]) for row in rows} == {(True, "verified")}

    @pytest.mark.asyncio
    async def test_a_folder_holding_no_image_is_a_requirement_shown_to_be_unmet(self, firmware, tmp_path):
        """Not a declined verdict: the read happened and the answer is no, for the folder and for the file in it."""
        rejected = FolderFile(path=self._in_folder(tmp_path, "notabios.bin"), satisfied=False)
        resolver = self._resolver(
            FolderVerdict(satisfied=False), caveats=("firmware-directory-holds-no-image",), folder_files=(rejected,)
        )
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")
        row = next(row for row in result["files"] if row["file_name"] == "notabios.bin")

        assert result["required_downloaded"] == 1
        assert result["required_withheld"] == 0
        assert result["bios_level"] == "missing"
        assert row["satisfied"] is False

    @pytest.mark.asyncio
    async def test_a_read_that_established_nothing_still_declines_the_verdict(self, firmware, tmp_path):
        """The resolver could not answer, so neither ready nor missing is a claim."""
        fw = self._service(firmware, tmp_path, self._resolver(None))

        result = await fw.check_platform_bios("ps2")

        assert result["required_downloaded"] == 1
        assert result["required_withheld"] == 1
        assert result["bios_level"] == "unknown"
        assert result["bios_label"] == "Unknown"

    @pytest.mark.asyncio
    async def test_presence_never_stands_in_for_the_verdict(self, firmware, tmp_path):
        """The folder is there on every stock RetroDECK, and that says nothing.

        Reading ``downloaded`` where the verdict belongs is what reported "All
        required ready" over a PS2 install holding no BIOS image at all.
        """
        rejected = FolderFile(path=self._in_folder(tmp_path, "notabios.bin"), satisfied=False)
        fw = self._service(firmware, tmp_path, self._resolver(FolderVerdict(satisfied=False), folder_files=(rejected,)))

        result = await fw.check_platform_bios("ps2")
        row = next(row for row in result["files"] if row["file_name"] == "notabios.bin")

        assert row["downloaded"] is True
        assert row["satisfied"] is False

    @pytest.mark.asyncio
    async def test_the_rows_beside_the_folder_keep_their_own_answers(self, firmware, tmp_path):
        resolver = self._resolver(FolderVerdict(satisfied=True, images=self._IMAGES))
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")
        rows = {row["file_name"]: row for row in result["files"]}

        assert rows["GameIndex.yaml"]["downloaded"] is True
        assert rows["GameIndex.yaml"]["satisfied"] is True
        assert rows["GameIndex.yaml"]["declared_kind"] == "file"
        assert rows["GameIndex.yaml"]["wanted"] == "needed"

    @pytest.mark.asyncio
    async def test_an_absent_folder_is_a_requirement_shown_to_be_unmet(self, firmware, tmp_path):
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=False,
            declares_directory=True,
            folder=FolderVerdict(satisfied=False),
        )
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")

        assert result["required_downloaded"] == 0
        assert result["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_folder_holding_nothing_is_one_row_with_its_verdict_and_its_code(self, firmware, tmp_path):
        """With nothing in the folder and no library file for it, one row stands for the folder, counted once, unmet.

        The row carries the code that words why, and offers nothing to fetch
        or delete.
        """
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            required_by=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=True,
            declares_directory=True,
            folder=FolderVerdict(satisfied=False),
            caveats=("firmware-directory-holds-no-candidate",),
        )
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")

        (row,) = result["files"]
        assert (row["declared_kind"], row["declared_path"]) == ("directory", "pcsx2/bios")
        assert row["satisfied"] is False
        assert row["caveats"] == ("firmware-directory-holds-no-candidate",)
        assert (row["fetch_for_required"], row["required_by_active"]) == (False, False)
        assert (result["required_count"], result["required_downloaded"]) == (1, 0)
        assert result["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_the_folders_own_row_goes_once_a_file_lies_there(self, firmware, tmp_path):
        image = FolderFile(path=self._in_folder(tmp_path, "scph70004.bin"), satisfied=True, description=self._IMAGES[0])
        fw = self._service(firmware, tmp_path, self._resolver(FolderVerdict(satisfied=True), folder_files=(image,)))

        result = await fw.check_platform_bios("ps2")

        assert [row["file_name"] for row in result["files"] if row["declared_kind"] == "directory"] == []

    @pytest.mark.asyncio
    async def test_the_system_page_and_the_game_page_read_one_verdict(self, firmware, tmp_path):
        """One reading per platform, and both surfaces answer off it."""
        _seed_rom(firmware.uow, rom_id=52, platform_slug="ps2", app_id=2)
        resolver = self._resolver(FolderVerdict(satisfied=True, images=self._IMAGES))
        fw = self._service(firmware, tmp_path, resolver)

        result = await _entries(fw, "ps2")
        platform = result["ps2"]

        assert platform["bios_level"] == "ok"
        assert platform["required_count"] == 2
        assert platform["required_downloaded"] == 2
        assert platform["required_withheld"] == 0

    @pytest.mark.asyncio
    async def test_a_folder_no_installed_core_requires_leaves_the_verdict_alone(self, firmware, tmp_path):
        """The scope is the launching emulator's requirement, not every folder on the page."""
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "bios",
            optional_for=[_id(self._CORE)],
            relative_path="pcsx2/bios",
            present=True,
            declares_directory=True,
        )
        fw = self._service(firmware, tmp_path, resolver)

        result = await fw.check_platform_bios("ps2")

        assert result["required_withheld"] == 0
        assert result["bios_level"] == "ok"


class TestAFileWithSomethingElseAtItsDestination:
    """A directory in a declared file's way settles nothing about the requirement.

    The resolver states one shape at the destination and it is not the file, so
    neither "there" nor "absent" is a claim the reading supports — the mirror
    image of the folder whose contents could not be read.
    """

    _CORE = "flycast_libretro"

    @pytest.mark.asyncio
    async def test_an_obstructed_file_declines_the_verdict_rather_than_reading_as_present(self, firmware, tmp_path):
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "dc_boot.bin",
            required_by=[_id(self._CORE)],
            present=True,
            caveats=("firmware-path-obstructed",),
        )
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(self._CORE, "Flycast"), options=[libretro_option(self._CORE, "Flycast")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])

        result = await fw.check_platform_bios("dc")
        row = result["files"][0]

        assert row["downloaded"] is True
        assert row["satisfied"] is None
        assert row["caveats"] == ("firmware-path-obstructed",)
        assert result["required_downloaded"] == 0
        assert result["required_withheld"] == 1
        assert result["bios_level"] == "unknown"


class TestTheOverviewSaysOnlyWhetherTheListingCouldBeRead:
    """``get_firmware_status`` answers whether RomM's firmware listing could be read, and no more.

    The reading a platform's state costs is a live per-system one — 59-175 ms
    each on the reference machine at emu-atlas 0.22, and the 25 platforms of its
    library add up to 1.8 s of them, which the overview would put in front of
    the first row if it paid them all. So the overview pays none, and which
    platforms are read is the page's list rather than this payload's.

    A field on this payload is a field the page can render before any reading
    has happened, which is why the whole answer is pinned rather than sampled: a
    ``bios_level``, a ``files`` list or a platform list leaking back onto it
    would be rendered as an answer about platforms nobody has asked about yet.
    """

    @staticmethod
    def _service(firmware, **kwargs) -> FirmwareService:
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            **kwargs,
        )
        _set_loop(fw, asyncio.get_running_loop())
        return fw

    @pytest.mark.asyncio
    async def test_it_answers_that_the_listing_was_read_and_nothing_else(self, firmware):
        _seed_rom(firmware.uow, rom_id=44, platform_slug="nes", app_id=1)
        fw = self._service(firmware)

        with patch.object(
            firmware.romm_api,
            "list_platforms",
            return_value=_platforms([{"id": 1, "file_name": "a.bin", "platform_slug": "dc", "file_size_bytes": 1}]),
        ):
            result = await fw.get_firmware_status()

        assert result == {"success": True, "server_offline": False}

    @pytest.mark.asyncio
    async def test_it_asks_the_resolver_and_the_catalogue_about_nothing(self, firmware):
        """The absence the whole split is for, stated as one.

        Neither seam is reached for any platform — not the firmware reading, and
        not the emulator catalogue, which is a per-system read of its own.
        """
        _seed_rom(firmware.uow, rom_id=44, platform_slug="nes", app_id=1)
        resolver = FakeFirmwareResolver()
        core_info = _test_core_info()
        fw = self._service(firmware, firmware_resolver=resolver, core_info=core_info)

        with patch.object(
            firmware.romm_api,
            "list_platforms",
            return_value=_platforms([{"id": 1, "file_name": "a.bin", "platform_slug": "dc", "file_size_bytes": 1}]),
        ):
            await fw.get_firmware_status()

        assert resolver.calls == []
        assert core_info.emulator_options_calls == []
        assert core_info.active_core_calls == []

    @pytest.mark.asyncio
    async def test_an_unreachable_server_answers_that_it_is_offline(self, firmware):
        """What an unreachable RomM costs the page is the downloads, which stand on this flag."""
        _seed_rom(firmware.uow, rom_id=44, platform_slug="nes", app_id=1)
        fw = self._service(firmware)

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await fw.get_firmware_status()

        assert result == {"success": True, "server_offline": True}


class TestOnePlatformsOwnEntry:
    """``get_platform_firmware_status`` — the half that pays the reading, one platform at a time.

    What it answers is the entry the page renders, for every platform it is
    asked about: whether the library holds firmware for it, whether a game of
    it is synced, and whether its reading happened at all are each said by the
    entry, never by leaving it out.
    """

    @staticmethod
    def _service(firmware, **kwargs) -> FirmwareService:
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            **kwargs,
        )
        _set_loop(fw, asyncio.get_running_loop())
        return fw

    @pytest.mark.asyncio
    async def test_it_answers_with_the_entry_the_pane_renders(self, firmware, tmp_path):
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        (bios_dir / "req1.bin").write_bytes(b"\x00" * 100)
        fw = self._service(
            firmware,
            firmware_resolver=_dc_resolver(),
            core_info=_dc_core_info(),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
        )

        with patch.object(
            firmware.romm_api,
            "list_platforms",
            return_value=_platforms(
                [{"id": 1, "file_name": "req1.bin", "platform_slug": "dc", "file_size_bytes": 100}]
            ),
        ):
            result = await fw.get_platform_firmware_status("dc")

        entry = result["platform"]
        assert result["success"] is True
        assert entry["platform_slug"] == "dc"
        assert entry["active_core"] == _id(_DC_CORE)
        assert {f["file_name"] for f in entry["files"]} == {"req1.bin", "req2.bin", "opt1.bin"}
        assert (entry["required_count"], entry["required_downloaded"]) == (2, 1)
        assert entry["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_it_reads_only_the_platform_it_was_asked_about(self, firmware):
        """The whole point of asking one at a time, stated as the reading it did not do."""
        resolver = FakeFirmwareResolver()
        fw = self._service(firmware, firmware_resolver=resolver)

        with patch.object(
            firmware.romm_api,
            "list_platforms",
            return_value=_platforms(
                [
                    {"id": 1, "file_name": "a.bin", "platform_slug": "dc", "file_size_bytes": 1},
                    {"id": 2, "file_name": "b.bin", "platform_slug": "psx", "file_size_bytes": 1},
                ]
            ),
        ):
            await fw.get_platform_firmware_status("dc")

        assert resolver.calls == ["dc"]

    @pytest.mark.asyncio
    async def test_it_does_not_read_every_rom_to_answer_has_games(self, firmware):
        """Which platforms are synced is one repository read, not a scan of the library."""
        _seed_rom(firmware.uow, rom_id=42, platform_slug="dc", app_id=1)
        _seed_rom(firmware.uow, rom_id=43, platform_slug="psx", app_id=None)
        fw = self._service(firmware, core_info=_test_core_info())

        with (
            patch.object(firmware.uow.roms, "iter_all", side_effect=AssertionError("read every ROM row")),
            patch.object(
                firmware.romm_api,
                "list_platforms",
                return_value=_platforms(
                    [
                        {"id": 1, "file_name": "a.bin", "platform_slug": "dc", "file_size_bytes": 1},
                        {"id": 2, "file_name": "b.bin", "platform_slug": "psx", "file_size_bytes": 1},
                    ]
                ),
            ),
        ):
            dc = await fw.get_platform_firmware_status("dc")
            psx = await fw.get_platform_firmware_status("psx")

        assert dc["platform"]["has_games"] is True
        assert psx["platform"]["has_games"] is False

    @pytest.mark.asyncio
    async def test_it_takes_only_its_own_platforms_rows_from_the_listing(self, firmware):
        """The listing is the library's whole firmware; an entry is one key's slice."""
        fw = self._service(firmware, core_info=_test_core_info())

        with patch.object(
            firmware.romm_api,
            "list_platforms",
            return_value=_platforms(
                [
                    {"id": 1, "file_name": "a.bin", "platform_slug": "dc", "file_size_bytes": 1},
                    {"id": 2, "file_name": "b.bin", "platform_slug": "psx", "file_size_bytes": 1},
                ]
            ),
        ):
            result = await fw.get_platform_firmware_status("dc")

        assert [f["file_name"] for f in result["platform"]["files"]] == ["a.bin"]

    @pytest.mark.asyncio
    async def test_a_platform_nothing_wants_answers_with_its_entry(self, firmware):
        """A finished reading that wants nothing is the answer that the emulator needs nothing.

        Neither listed nor synced, and no row to show: the entry still comes
        back, with the launching emulator read and its verdict met, so the page
        words it as needing nothing rather than as knowing nothing.
        """
        resolver = FakeFirmwareResolver()
        resolver.describe_emulator(_id(_TEST_CORE))
        fw = self._service(firmware, firmware_resolver=resolver, core_info=_test_core_info())

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms([])):
            result = await fw.get_platform_firmware_status("nes")

        entry = result["platform"]
        assert entry["files"] == []
        assert entry["has_games"] is False
        assert entry["emulator_read"] is True
        assert entry["active_core"] == _id(_TEST_CORE)
        assert entry["bios_level"] == "ok"

    @pytest.mark.asyncio
    async def test_a_platform_with_no_synced_game_and_no_library_file_answers_what_its_emulator_needs(self, firmware):
        """A platform with games on RomM, none synced and no file in the library.

        Its emulator's own requirement is the row, offered as one the library
        does not hold, and its verdict is not met.
        """
        resolver = FakeFirmwareResolver()
        resolver.declare("ps2_bios.bin", required_by=[_id(_TEST_CORE)], description="PS2 BIOS", present=False)
        fw = self._service(firmware, firmware_resolver=resolver, core_info=_test_core_info())

        with patch.object(
            firmware.romm_api,
            "list_platforms",
            return_value=_platforms([{"id": 1, "file_name": "a.bin", "platform_slug": "dc", "file_size_bytes": 1}]),
        ):
            result = await fw.get_platform_firmware_status("ps2")

        entry = result["platform"]
        assert entry["has_games"] is False
        assert [(f["file_name"], f["on_server"], f["required_by_active"]) for f in entry["files"]] == [
            ("ps2_bios.bin", False, True)
        ]
        assert entry["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_platform_whose_reading_did_not_happen_answers_that_nothing_was_established(self, firmware):
        """The resolver gave no answer for the system: the entry says so, and never "needs nothing"."""
        fw = self._service(
            firmware, firmware_resolver=FakeFirmwareResolver(resolved=False), core_info=_test_core_info()
        )

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms([])):
            result = await fw.get_platform_firmware_status("nes")

        entry = result["platform"]
        assert entry["files"] == []
        assert entry["emulator_read"] is False
        assert entry["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_synced_platform_nothing_could_answer_for_keeps_its_entry(self, firmware):
        """No emulator to ask is no answer, and the entry says so.

        ``ps3``'s only ES-DE entry is RPCS3, so there is no core to ask and the
        empty file list is silence rather than an answer: the level is unknown,
        never met.
        """
        _seed_rom(firmware.uow, rom_id=45, platform_slug="ps3", app_id=1)
        fw = self._service(
            firmware, firmware_resolver=FakeFirmwareResolver(), core_info=FakeCoreInfoProvider(options=[])
        )

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await fw.get_platform_firmware_status("ps3")

        assert result["platform"]["files"] == []
        assert result["platform"]["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_platform_the_library_holds_firmware_for_keeps_its_entry(self, firmware):
        """A listed platform answers even where every row falls away.

        Its one server row carries a traversing name, so the path guard drops it
        and the reading wants nothing else. The library holding firmware filed
        under this platform is itself the thing to say, and a page that showed no
        block would leave the reader looking for a platform RomM does list.
        """
        fw = self._service(firmware, firmware_resolver=FakeFirmwareResolver(), core_info=_test_core_info())

        with patch.object(
            firmware.romm_api,
            "list_platforms",
            return_value=_platforms(
                [{"id": 1, "file_name": "../escape.bin", "platform_slug": "dc", "file_size_bytes": 1}]
            ),
        ):
            result = await fw.get_platform_firmware_status("dc")

        assert result["platform"] is not None
        assert result["platform"]["files"] == []


class TestGetFirmwareStatus:
    @pytest.mark.asyncio
    async def test_each_platform_holds_its_own_listing_rows(self, fw, tmp_path):
        firmware_list = [
            {
                "id": 1,
                "file_name": "bios_dc.bin",
                "platform_slug": "dc",
                "file_size_bytes": 2048,
                "md5_hash": "abc123",
            },
            {
                "id": 2,
                "file_name": "flash_dc.bin",
                "platform_slug": "dc",
                "file_size_bytes": 1024,
                "md5_hash": "def456",
            },
            {
                "id": 3,
                "file_name": "scph.bin",
                "platform_slug": "ps2",
                "file_size_bytes": 4096,
                "md5_hash": "",
            },
        ]

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        result = await _entries(fw, "dc", "ps2")

        dc_plat = result["dc"]
        assert len(dc_plat["files"]) == 2
        assert all(not f["downloaded"] for f in dc_plat["files"])
        assert [f["file_name"] for f in result["ps2"]["files"]] == ["scph.bin"]

    @pytest.mark.asyncio
    async def test_enrich_resolves_system_for_cores_keeps_raw_slug_for_platform(self, tmp_path):
        """Per-platform core reads get the NORMALIZED system; entry slug stays raw.

        ``_enrich_platform`` keys ``platform_slug`` / ``has_games`` / the
        library's files on the RomM platform slug (ADR-0010 §4) but must feed
        the resolved RetroDECK system to the ``get_active_core`` /
        ``get_emulator_options`` seams (ADR-0010 §2).
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        core_info = FakeCoreInfoProvider(
            active_core=("flycast_libretro", "Flycast"),
            options=[libretro_option("flycast_libretro", "Flycast")],
        )
        resolver = FakePlatformSystems(mapping={"dc": "dreamcast"})
        fw = _make_firmware_service(core_info=core_info, platform_systems=resolver)

        firmware_list = [
            {
                "id": 1,
                "file_name": "bios_dc.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
        ]
        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        result = await _entries(fw, "dc")

        dc_plat = result["dc"]
        # Entry identity stays on the RAW slug.
        assert dc_plat["platform_slug"] == "dc"
        # Active-core data resolved under the NORMALIZED system surfaces on the entry.
        assert dc_plat["active_core"] == _id("flycast_libretro")
        assert dc_plat["emulator_data_available"] is True
        assert dc_plat["emulators"] == [
            {
                "label": "Flycast",
                "labels": ["Flycast"],
                "name": "Flycast",
                "kind": "libretro",
                "core_so": "flycast_libretro",
                "emulator": _id("flycast_libretro"),
                "is_default": True,
                "bakeable": True,
                "reason": None,
            }
        ]
        # The core read seam received the NORMALIZED system, not the raw slug.
        assert core_info.emulator_options_calls == ["dreamcast"]
        assert resolver.calls == [("dc", None)]

    async def _psp_active_core_label(self, platform_core_reader):
        """Run ``get_platform_firmware_status`` for a psp platform whose default is a standalone.

        Seeds a standalone-first options list (the PPSSPP flip) so the *default*
        emulator label ("PPSSPP (Standalone)") differs from the libretro
        ``active_core`` label ("PPSSPP") — the exact case the platform detail's
        label must reflect. Returns the resolved ``active_core_label``.
        """
        from tests.fakes.fake_core_info_provider import libretro_option, standalone_option

        core_info = FakeCoreInfoProvider(
            active_core=("ppsspp_libretro", "PPSSPP"),
            options=[
                standalone_option("%EMULATOR_PPSSPP% -b %ROM%", "PPSSPP (Standalone)"),
                libretro_option("ppsspp_libretro", "PPSSPP"),
            ],
        )
        fw = _make_firmware_service(core_info=core_info, platform_core_reader=platform_core_reader)
        firmware_list = [
            {
                "id": 1,
                "file_name": "ppsspp.bin",
                "platform_slug": "psp",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
        ]
        _stub_listing(fw, firmware_list)
        _inline_executor(fw)
        result = await _entries(fw, "psp")
        psp = result["psp"]
        return psp["active_core_label"]

    @pytest.mark.asyncio
    async def test_active_core_label_is_default_emulator_not_libretro_core(self):
        """No override → the label is the default EMULATOR (standalone), not the libretro core.

        The libretro ``active_core`` stays "PPSSPP" (BIOS filter), but the
        displayed label follows the bakeable default, so a standalone-default
        system reads its standalone label instead of the libretro one (#1210).
        """
        label = await self._psp_active_core_label(FakePlatformCoreReader())
        assert label == "PPSSPP (Standalone)"

    @pytest.mark.asyncio
    async def test_active_core_label_reflects_per_platform_override(self):
        """A per-platform pin surfaces on the platform detail's label immediately (#1305)."""
        label = await self._psp_active_core_label(FakePlatformCoreReader({"psp": "PPSSPP"}))
        assert label == "PPSSPP"

    @pytest.mark.asyncio
    async def test_active_core_label_degrades_when_override_stale(self):
        """An override that no longer resolves falls back to the default emulator label."""
        label = await self._psp_active_core_label(FakePlatformCoreReader({"psp": "No Longer Here"}))
        assert label == "PPSSPP (Standalone)"

    @pytest.mark.asyncio
    async def test_has_games_reflects_bound_roms(self, firmware, fw):
        """``has_games`` is True only for platforms with a ROM bound to a shortcut.

        - ``dc``: one bound ROM (``shortcut_app_id`` set) -> True.
        - ``ps2``: only an *unbound* ROM (``shortcut_app_id is None``) -> False.
        - ``gba``: no ROM rows at all -> False.
        """
        firmware_list = [
            {"id": 1, "file_name": "bios_dc.bin", "platform_slug": "dc", "file_size_bytes": 100},
            {"id": 2, "file_name": "scph.bin", "platform_slug": "ps2", "file_size_bytes": 200},
            {"id": 3, "file_name": "gba_bios.bin", "platform_slug": "gba", "file_size_bytes": 300},
        ]
        # A real loop so the executor-run reads hit the shared fake UoW.
        _set_loop(fw, asyncio.get_running_loop())
        # "dc": a bound ROM. "ps2": only an unbound ROM. "gba": no ROM rows.
        _seed_rom(firmware.uow, rom_id=42, platform_slug="dc", app_id=1)
        _seed_rom(firmware.uow, rom_id=43, platform_slug="ps2", app_id=None)

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)):
            result = await _entries(fw, "dc", "ps2", "gba")

        dc_plat = result["dc"]
        ps2_plat = result["ps2"]
        gba_plat = result["gba"]
        assert dc_plat["has_games"] is True
        assert ps2_plat["has_games"] is False
        assert gba_plat["has_games"] is False

    @pytest.mark.asyncio
    async def test_detects_downloaded_files(self, firmware, fw, tmp_path):
        # Nothing declares the file, so it is downloaded once the platform's
        # store folder holds it — a copy lying in the BIOS root says nothing.
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        stored = _stored(firmware.uow, str(tmp_path / "Tender"), "dc", "bios_dc.bin", b"\x00" * 100)
        os.makedirs(os.path.dirname(stored))
        with open(stored, "wb") as f:
            f.write(b"\x00" * 100)

        firmware_list = [
            {
                "id": 1,
                "file_name": "bios_dc.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
        ]

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        with patch.object(_resolver(fw), "bios_root", str(bios_dir)):
            result = await _entries(fw, "dc")
        assert result["dc"]["files"][0]["downloaded"] is True

    @pytest.mark.asyncio
    async def test_handles_api_error_with_offline_fallback(self, firmware, fw):
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("Connection refused")):
            result = await fw.get_firmware_status()

        assert result == {"success": True, "server_offline": True}


_DC_CORE = "flycast_libretro"


def _dc_resolver() -> FakeFirmwareResolver:
    """Two files the dc core will not run without, and one it merely accepts."""
    resolver = FakeFirmwareResolver()
    resolver.declare("req1.bin", required_by=[_id(_DC_CORE)], description="Required BIOS 1")
    resolver.declare("req2.bin", required_by=[_id(_DC_CORE)], description="Required BIOS 2")
    resolver.declare("opt1.bin", optional_for=[_id(_DC_CORE)], description="Optional firmware")
    return resolver


def _dc_core_info() -> FakeCoreInfoProvider:
    """A dc platform whose launching core is the one declaring those files."""
    from tests.fakes.fake_core_info_provider import libretro_option

    return FakeCoreInfoProvider(
        active_core=(_DC_CORE, "Flycast"),
        options=[libretro_option(_DC_CORE, "Flycast")],
    )


class TestGetFirmwareStatusBiosAggregates:
    """``get_platform_firmware_status`` ships per-platform BIOS aggregates + ``bios_level``.

    The platform detail reads the unknown/ok/partial/missing decision and display
    counts off this payload instead of re-deriving the threshold logic in the
    frontend (#461). The level is computed by the same
    ``domain.bios_status.compute_bios_level`` the game-detail path uses, from the
    same classified files.
    """

    @staticmethod
    def _firmware(*names: str) -> list[dict[str, Any]]:
        return [
            {
                "id": i + 1,
                "file_name": name,
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            }
            for i, name in enumerate(names)
        ]

    async def _run(self, tmp_path, firmware_list, downloaded: set[str]):
        """Run get_platform_firmware_status against the dc demand and the given downloads."""
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True, exist_ok=True)
        for name in downloaded:
            (bios_dir / name).write_bytes(b"\x00" * 100)

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(firmware_list)
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=_dc_resolver(),
            core_info=_dc_core_info(),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
        )
        _inline_executor(fw)

        result = await _entries(fw, "dc")
        return result["dc"]

    @pytest.mark.asyncio
    async def test_all_required_ready_is_ok(self, tmp_path):
        """All required files downloaded → bios_level 'ok' + matching counts."""
        plat = await self._run(
            tmp_path, self._firmware("req1.bin", "req2.bin", "opt1.bin"), downloaded={"req1.bin", "req2.bin"}
        )
        assert plat["bios_level"] == "ok"
        assert plat["required_count"] == 2
        assert plat["required_downloaded"] == 2
        assert plat["server_count"] == 3
        assert plat["local_count"] == 2

    @pytest.mark.asyncio
    async def test_some_required_downloaded_is_missing_and_counts_what_is_there(self, tmp_path):
        """One of two required files present → the resolver says not met: 'missing', with the count beside it."""
        plat = await self._run(tmp_path, self._firmware("req1.bin", "req2.bin", "opt1.bin"), downloaded={"req1.bin"})
        assert plat["bios_level"] == "missing"
        assert plat["required_count"] == 2
        assert plat["required_downloaded"] == 1

    @pytest.mark.asyncio
    async def test_no_required_downloaded_is_missing(self, tmp_path):
        """No required file present → bios_level 'missing'."""
        plat = await self._run(tmp_path, self._firmware("req1.bin", "req2.bin", "opt1.bin"), downloaded=set())
        assert plat["bios_level"] == "missing"
        assert plat["required_count"] == 2
        assert plat["required_downloaded"] == 0
        assert plat["local_count"] == 0

    @pytest.mark.asyncio
    async def test_a_wanted_file_the_library_lacks_is_still_a_row(self, tmp_path):
        """The third row kind: wanted, missing, and not downloadable from here.

        The listing carries only the optional file; the two the dc core will not
        run without are wanted all the same, so they are shown — marked
        ``on_server`` false, which is the field the page's buttons and totals
        filter on, and carrying no server id because there is no server record.
        """
        plat = await self._run(tmp_path, self._firmware("opt1.bin"), downloaded={"opt1.bin"})

        rows = {f["file_name"]: f for f in plat["files"]}
        assert set(rows) == {"opt1.bin", "req1.bin", "req2.bin"}
        assert rows["opt1.bin"]["on_server"] is True
        for name in ("req1.bin", "req2.bin"):
            assert rows[name]["on_server"] is False
            assert rows[name]["id"] is None
            assert rows[name]["wanted"] == "needed"

        # They are missing prerequisites, so the platform is not ready.
        assert plat["required_count"] == 2
        assert plat["required_downloaded"] == 0
        assert plat["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_unanswerable_platform_projects_unknown_level(self, tmp_path):
        """Overview projection: server files nothing could answer for → 'unknown'.

        A platform one of whose emulators could not be read classifies every
        unmatched file ``unknown`` (known_count 0), so the aggregate stamps
        ``bios_level == "unknown"`` instead of the false ``"ok"`` (#1520). It is
        never flagged as a BIOS-needed platform (required_count is 0).
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "vita.bin",
                    "platform_slug": "psvita",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=FakeFirmwareResolver(unread_emulators=frozenset({_id("vita_libretro")})),
            core_info=FakeCoreInfoProvider(options=[libretro_option("vita_libretro", "Vita")]),
        )
        _inline_executor(fw)

        result = await _entries(fw, "psvita")

        plat = result["psvita"]
        assert plat["bios_level"] == "unknown"
        assert plat["required_count"] == 0
        assert plat["server_count"] == 1

    @pytest.mark.asyncio
    async def test_platform_whose_emulators_were_all_read_is_not_unknown(self, tmp_path):
        """Every emulator read and none asks for the file → an answer, not a gap.

        The counterpart to the case above, and the whole point of the four-value
        model: the same empty match set means "nothing needs this" here and
        "nothing could be established" there.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "stray.bin",
                    "platform_slug": "snes",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        resolver = FakeFirmwareResolver()
        resolver.describe_emulator(_id("snes9x_libretro"))
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(options=[libretro_option("snes9x_libretro", "Snes9x")]),
        )
        _inline_executor(fw)

        result = await _entries(fw, "snes")

        plat = result["snes"]
        assert [f["wanted"] for f in plat["files"]] == ["not_needed"]
        assert plat["bios_level"] == "ok"

    @pytest.mark.asyncio
    async def test_the_library_ratio_counts_only_what_the_library_holds(self, tmp_path):
        """ "N of M files" is a progress bar over a set the user can complete.

        Optional files no library holds are shown as rows but stay out of the
        ratio — folding them in reports work outstanding on a system that needs
        nothing. Measured: a stock RetroDECK's SNES emulators declare 26 optional
        files, so the header would have read "0 / 26 files, 26 missing" for a
        system no core requires anything from.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {"id": 1, "file_name": "held.bin", "platform_slug": "snes", "file_size_bytes": 1, "md5_hash": ""},
            ]
        )
        resolver = FakeFirmwareResolver()
        resolver.declare("held.bin", optional_for=[_id("snes9x_libretro")])
        for name in ("absent1.bin", "absent2.bin"):
            resolver.declare(name, optional_for=[_id("snes9x_libretro")])
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(options=[libretro_option("snes9x_libretro", "Snes9x")]),
        )
        _inline_executor(fw)

        result = await _entries(fw, "snes")
        plat = result["snes"]

        # All three are listed — the machine wants them and the user should see so.
        assert len(plat["files"]) == 3
        # Only the one the library holds is in the ratio.
        assert plat["server_count"] == 1
        assert plat["local_count"] == 0
        # None is required, so the badge stays quiet either way.
        assert plat["required_count"] == 0

    @pytest.mark.asyncio
    async def test_a_required_file_the_library_lacks_still_counts_as_required(self, tmp_path):
        """The other side of the same split: readiness is not a progress bar.

        A required file nobody can fetch is still a prerequisite, so it raises
        ``required_count`` and holds the level down — the download affordance is
        what withholds itself, not the count.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms([])
        resolver = FakeFirmwareResolver()
        resolver.declare("lynxboot.img", required_by=[_id("handy_libretro")], description="Boot ROM")
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=("handy_libretro", "Handy"),
                options=[libretro_option("handy_libretro", "Handy")],
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path)),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("atarilynx")

        assert result["required_count"] == 1
        assert result["required_downloaded"] == 0
        assert result["bios_level"] == "missing"
        assert result["server_count"] == 0
        assert result["files"][0]["on_server"] is False

    @pytest.mark.asyncio
    async def test_a_file_the_library_files_under_another_platform_is_not_called_absent(self, tmp_path):
        """ "Not in your library" is a claim about the library, not about a directory.

        A core that serves several systems declares the same file for each of
        them while RomM files it under one platform. Checking only this
        platform's slice of the listing would tell the user to upload a file they
        already have: the whole library fills a requirement, whichever platform
        RomM files the file under.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "bios.gg",
                    "platform_slug": "gamegear",
                    "file_size_bytes": 1,
                    "md5_hash": "",
                },
            ]
        )
        resolver = FakeFirmwareResolver()
        resolver.declare("bios.gg", optional_for=[_id("genesis_plus_gx_libretro")], description="Game Gear BIOS")
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(options=[libretro_option("genesis_plus_gx_libretro", "Genesis Plus GX")]),
        )
        _inline_executor(fw)

        result = await _entries(fw, "gamegear", "megadrive")

        # The platform the listing files it under carries the row; the other
        # platform the core serves carries its own requirement, filled by that
        # same library file rather than called absent.
        assert [f["on_server"] for f in result["gamegear"]["files"]] == [True]
        (row,) = result["megadrive"]["files"]
        assert row["on_server"] is True
        assert (row["library"]["file_name"], row["library"]["platform_slug"]) == ("bios.gg", "gamegear")

    @pytest.mark.asyncio
    async def test_the_demand_is_read_once_per_platform(self, tmp_path):
        """One reading per platform the page renders, and exactly one.

        A reading is per platform because which emulators can run a system is the
        only thing that says whether a standalone emulator's declarations belong
        to it — a whole-machine reading enumerates installed cores and carries
        none of them. What must not happen is a second reading WITHIN a platform:
        the rows, the counts and the verdict all come off one answer, so two
        readings could disagree about the same file on one page.
        """
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {"id": 1, "file_name": "a.bin", "platform_slug": "dc", "file_size_bytes": 1, "md5_hash": ""},
                {"id": 2, "file_name": "b.bin", "platform_slug": "psx", "file_size_bytes": 1, "md5_hash": ""},
                {"id": 3, "file_name": "c.bin", "platform_slug": "gba", "file_size_bytes": 1, "md5_hash": ""},
            ]
        )
        resolver = FakeFirmwareResolver()
        fw = _make_firmware_service(romm_api=romm_api, firmware_resolver=resolver)
        _inline_executor(fw)

        result = await _entries(fw, "dc", "psx", "gba")

        assert set(result) == {"dc", "gba", "psx"}
        assert sorted(resolver.calls) == ["dc", "gba", "psx"]

    @pytest.mark.asyncio
    async def test_server_offline_still_answers_readiness(self, firmware, tmp_path):
        """Readiness needs no server: ES-DE, the resolver and the disk answer it.

        What an unreachable RomM costs is the files only it knows about and the
        ability to download — not the requirement, and not the platform.
        """
        _seed_rom(firmware.uow, rom_id=42, platform_slug="dc", app_id=1)
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True, exist_ok=True)
        (bios_dir / "req1.bin").write_bytes(b"\x00" * 100)

        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=_dc_resolver(),
            core_info=_dc_core_info(),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
        )
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            overview = await fw.get_firmware_status()
            result = await _entries(fw, "dc")

        assert overview["server_offline"] is True
        dc = result["dc"]
        assert dc["has_games"] is True
        assert {f["file_name"] for f in dc["files"]} == {"req1.bin", "req2.bin", "opt1.bin"}
        assert all(f["on_server"] is False for f in dc["files"])
        # One of the two required files is on disk — a real answer: not met, with the count.
        assert dc["required_count"] == 2
        assert dc["required_downloaded"] == 1
        assert dc["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_synced_platform_nothing_wants_reads_as_needing_nothing(self, firmware, tmp_path):
        """The core the page can ask is offered AND read, so the empty list is a finished answer.

        It stays on the page as that answer — met, with no row — rather than
        being dropped and read as a platform nothing is known about.
        """
        _seed_rom(firmware.uow, rom_id=44, platform_slug="nes", app_id=1)
        resolver = FakeFirmwareResolver()
        resolver.describe_emulator(_id(_TEST_CORE))
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=resolver,
            core_info=_test_core_info(),
        )
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await _entries(fw, "nes")

        assert result["nes"]["files"] == []
        assert result["nes"]["emulator_read"] is True
        assert result["nes"]["bios_level"] == "ok"

    @pytest.mark.asyncio
    async def test_a_synced_platform_nothing_could_answer_for_stays_on_the_page(self, firmware, tmp_path):
        """A platform ES-DE offers no libretro core for keeps its block, reading unknown.

        The counterpart to the one above. ``ps3``'s only ES-DE entry is
        RPCS3, so there is no core to ask and the empty file list is silence
        rather than an answer. Reading it as met would say the system needs
        nothing — the exact claim the four-value vocabulary exists to refuse —
        over firmware RPCS3 will not boot without.
        """
        _seed_rom(firmware.uow, rom_id=45, platform_slug="ps3", app_id=1)
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=FakeFirmwareResolver(),
            core_info=FakeCoreInfoProvider(options=[]),
        )
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await _entries(fw, "ps3")

        ps3 = result["ps3"]
        assert ps3["files"] == []
        assert ps3["bios_level"] == "unknown"
        assert ps3["required_count"] == 0
        assert ps3["server_count"] == 0

    @pytest.mark.asyncio
    async def test_no_exists_read_escapes_the_bios_directory(self, firmware, fw, tmp_path):
        """#966 NIT2: a server-supplied traversal name is skipped, not joined.

        The listing is server-controlled, so a ``file_name`` carrying ``..`` must
        be dropped (log-and-skip) rather than steering an ``exists()`` read
        outside the BIOS sandbox.
        """
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True, exist_ok=True)
        (bios_dir / "good.bin").write_bytes(b"\x00" * 100)
        (tmp_path / "retrodeck" / "evil.bin").write_bytes(b"\x00" * 100)

        firmware_list = [
            {"id": 1, "file_name": "good.bin", "platform_slug": "dc", "file_size_bytes": 1, "md5_hash": ""},
            {
                "id": 2,
                "file_name": "../evil.bin",
                "platform_slug": "dc",
                "file_size_bytes": 1,
                "md5_hash": "",
            },
        ]
        _set_loop(fw, asyncio.get_running_loop())

        real_exists = fw._config.firmware_file_store.exists
        checked: list[str] = []

        def _tracking_exists(path):
            checked.append(path)
            return real_exists(path)

        fw._config.firmware_file_store.exists = _tracking_exists

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await _entries(fw, "dc")

        plat = result["dc"]
        assert {f["file_name"] for f in plat["files"]} == {"good.bin"}
        real_bios = os.path.realpath(str(bios_dir))
        for path in checked:
            assert os.path.realpath(path).startswith(real_bios + os.sep)


class TestGetFirmwareStatusDeletableCount:
    """``get_platform_firmware_status`` ships the count the platform-wide delete button stands on.

    The delete is authorised by the download record, so the button's number has
    to be a record count. ``local_count`` is the library's progress ratio over a
    different set and is wrong in both directions: it counts a hand-placed file
    that shares a server file's name and drops our own download the moment RomM
    stops listing it — the direction that used to hide the button entirely over
    files it could still remove.
    """

    @pytest.mark.asyncio
    async def test_the_delete_count_counts_records_not_library_files(self, firmware, tmp_path):
        """Both directions in one platform, so the two counts cannot coincide.

        ``IPL.bin`` and ``card.bin`` are in the library and on disk but were put
        there by hand; ``retired.bin`` we downloaded and RomM no longer offers;
        ``ours.bin`` we downloaded and it is still listed. The library ratio
        counts the one library file at its destination — ``IPL.bin``, where the
        emulator opens it; ``card.bin`` and ``ours.bin`` are asked for by no
        emulator and lie in no store — the delete sees the two we placed, and
        per row only ``ours.bin`` offers a button, because ``retired.bin`` has
        no row at all.
        """
        bios_dir = tmp_path / "bios"
        ipl = os.path.join(str(bios_dir), "IPL.bin")
        card = os.path.join(str(bios_dir), "card.bin")
        retired = os.path.join(str(bios_dir), "retired.bin")
        ours = os.path.join(str(bios_dir), "ours.bin")
        store = FakeFirmwareFileStore({ipl: b"\x00" * 8, card: b"\x00" * 8, retired: b"\x00" * 8, ours: b"\x00" * 8})
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=_test_core_info(),
        )
        _inline_executor(fw)
        _declare(fw, ("IPL.bin", "GameCube IPL", True))
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="gc",
                file_name="retired.bin",
                file_path=retired,
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=None,
            )
        )
        # Ours, and still offered by the library, so it has a row of its own.
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="gc",
                file_name="ours.bin",
                file_path=ours,
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=9,
            )
        )
        _stub_listing(
            fw,
            [
                {"id": 7, "file_name": "IPL.bin", "platform_slug": "gc", "file_size_bytes": 8, "md5_hash": ""},
                {
                    "id": 8,
                    "file_name": "card.bin",
                    "platform_slug": "gc",
                    "file_size_bytes": 8,
                    "md5_hash": "",
                },
                {
                    "id": 9,
                    "file_name": "ours.bin",
                    "platform_slug": "gc",
                    "file_size_bytes": 8,
                    "md5_hash": "",
                },
            ],
        )

        result = await _entries(fw, "gc")
        plat = result["gc"]

        assert plat["local_count"] == 1
        assert plat["deletable_count"] == 2
        # And per row, which is what a row's own Delete button reads. `IPL.bin`
        # is the shape that matters: in the library, on disk, `downloaded: True`
        # — and not ours, so it must offer nothing. Deriving the field from
        # `downloaded` instead of from the records flips exactly this row, and
        # it is the shape that destroyed `codehandler.bin` on a real device.
        rows = {f["file_name"]: f for f in plat["files"]}
        assert rows["IPL.bin"]["downloaded"] is True
        assert rows["IPL.bin"]["deletable_count"] == 0
        assert rows["card.bin"]["deletable_count"] == 0
        # And the positive direction, which is the one that fails silently: a row
        # Tender DID place must offer its button. Without it, forcing every
        # file row to 0 leaves the suite green and the button simply never
        # appears.
        assert rows["ours.bin"]["deletable_count"] == 1
        # `retired.bin` has no row at all: nothing declares it and the library no
        # longer offers it. So the platform count and the rows legitimately
        # differ — the count is over records, the rows are what the pane has to
        # show, and only the platform-wide button can reach this one.
        assert "retired.bin" not in rows
        # And the count means the delete: it removes ours, leaves theirs.
        deleted = await fw.delete_platform_bios("gc")
        assert isinstance(deleted, dict)
        assert deleted["deleted_count"] == 2
        assert retired not in store.files
        assert ipl in store.files
        assert card in store.files

    @pytest.mark.asyncio
    async def test_a_record_whose_file_is_gone_is_not_offered(self, firmware, tmp_path):
        """Nothing to unlink, nothing to offer — the button must not promise a deletion."""
        bios_dir = tmp_path / "bios"
        held = os.path.join(str(bios_dir), "IPL.bin")
        store = FakeFirmwareFileStore({held: b"\x00" * 8})
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=_test_core_info(),
        )
        _inline_executor(fw)
        _declare(fw, ("IPL.bin", "GameCube IPL", True))
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="gc",
                file_name="gone.bin",
                file_path=os.path.join(str(bios_dir), "gone.bin"),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=None,
            )
        )
        _stub_listing(
            fw,
            [{"id": 7, "file_name": "IPL.bin", "platform_slug": "gc", "file_size_bytes": 8, "md5_hash": ""}],
        )

        result = await _entries(fw, "gc")
        plat = result["gc"]

        assert plat["local_count"] == 1
        assert plat["deletable_count"] == 0


class TestTheGamePageAnswerCarriesWhatItsButtonsStandOn:
    """``check_platform_bios`` carries the delete counts and the listing's state, as the platform entry does.

    The game page's BIOS tab offers the platform page's buttons, and they stand
    on the same fields: a row's Delete on ``delete_name``, the platform-wide
    delete on ``deletable_count``, and every download on ``server_offline``.
    """

    @staticmethod
    def _service(firmware, tmp_path, files: dict[str, bytes]) -> tuple[FirmwareService, str]:
        bios_dir = str(tmp_path / "bios")
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=FakeFirmwareFileStore({os.path.join(bios_dir, n): b for n, b in files.items()}),
            retrodeck_folders=FakeRetroDeckFolders(bios=bios_dir),
            core_info=_test_core_info(),
        )
        _inline_executor(fw)
        return fw, bios_dir

    @pytest.mark.asyncio
    async def test_it_counts_tenders_downloads_for_the_platform_and_per_row(self, firmware, tmp_path):
        """``IPL.bin`` was placed by hand and ``ours.bin`` downloaded by Tender: only ours is deletable."""
        fw, bios_dir = self._service(firmware, tmp_path, {"IPL.bin": b"\x00" * 8, "ours.bin": b"\x00" * 8})
        _declare(fw, ("IPL.bin", "GameCube IPL", True))
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="gc",
                file_name="ours.bin",
                file_path=os.path.join(bios_dir, "ours.bin"),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=9,
            )
        )
        _stub_listing(
            fw,
            [
                {"id": 7, "file_name": "IPL.bin", "platform_slug": "gc", "file_size_bytes": 8, "md5_hash": ""},
                {"id": 9, "file_name": "ours.bin", "platform_slug": "gc", "file_size_bytes": 8, "md5_hash": ""},
            ],
        )

        result = await fw.check_platform_bios("gc")

        assert result["deletable_count"] == 1
        rows = {f["file_name"]: f for f in result["files"]}
        assert rows["IPL.bin"]["downloaded"] is True
        assert rows["IPL.bin"]["deletable_count"] == 0
        assert rows["IPL.bin"]["delete_name"] is None
        assert rows["ours.bin"]["deletable_count"] == 1
        assert rows["ours.bin"]["delete_name"] == "ours.bin"

    @pytest.mark.asyncio
    async def test_it_says_whether_the_librarys_listing_could_be_read(self, firmware, tmp_path):
        fw, _ = self._service(firmware, tmp_path, {})
        _declare(fw, ("IPL.bin", "GameCube IPL", True))
        _stub_listing(fw, [])

        reachable = await fw.check_platform_bios("gc")
        api = fw._config.romm_api
        assert isinstance(api, MagicMock)
        api.list_platforms.side_effect = RuntimeError("offline")
        fw._listing.invalidate()
        offline = await fw.check_platform_bios("gc")

        assert reachable["server_offline"] is False
        # Still an answer: the machine's demand is read locally either way.
        assert offline["files"][0]["file_name"] == "IPL.bin"
        assert offline["server_offline"] is True


class TestCheckPlatformBiosUnknown:
    """``check_platform_bios`` surfaces the 'unknown' state for unanswerable platforms.

    A platform one of whose emulators could not be read has every unmatched file
    classified ``unknown`` (known_count 0), so the per-game BIOS payload ships
    ``bios_level == "unknown"`` instead of a false ``"ok"`` all-clear (#1520).
    """

    @pytest.mark.asyncio
    async def test_unanswerable_platform_is_unknown(self, tmp_path):
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "vita.bin",
                    "platform_slug": "psvita",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=FakeFirmwareResolver(unread_emulators=frozenset({_id("vita_libretro")})),
            core_info=FakeCoreInfoProvider(options=[libretro_option("vita_libretro", "Vita")]),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("psvita")

        assert result["needs_bios"] is True
        assert result["server_count"] == 1
        assert result["known_count"] == 0
        assert result["unknown_count"] == 1
        assert result["required_count"] == 0
        assert result["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_launching_emulator_missing_from_the_answer_leaves_library_files_unknown(self):
        """An emulator the answer holds no entry for counts as not read.

        Its library files are then ``unknown`` (a grey ``?``), never
        ``not_needed`` beside a header that says nothing could be established.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [{"id": 1, "file_name": "stray.bin", "platform_slug": "snes", "file_size_bytes": 100}]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=FakeFirmwareResolver(),
            core_info=FakeCoreInfoProvider(options=[libretro_option("snes9x_libretro", "Snes9x")]),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("snes")

        assert result["emulator_read"] is False
        assert result["bios_level"] == "unknown"
        assert [f["wanted"] for f in result["files"]] == ["unknown"]

    @pytest.mark.asyncio
    async def test_an_unread_core_outside_the_platform_does_not_make_it_unknown(self, tmp_path):
        """The doubt is scoped to the emulators THIS platform offers.

        Measured on a stock RetroDECK (211 cores, 172 ES-DE systems): five
        installed cores ship without a ``.info``, and exactly one of them —
        ``amiarcadia_libretro`` — is offered by any system. Letting an unread
        core silence platforms that do not offer it would make ``not_needed``
        unreachable and put the four-value model back at three.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "stray.bin",
                    "platform_slug": "snes",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        resolver = FakeFirmwareResolver(unread_emulators=frozenset({_id("amiarcadia_libretro")}))
        resolver.describe_emulator(_id("snes9x_libretro"))
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(options=[libretro_option("snes9x_libretro", "Snes9x")]),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("snes")

        assert result["unknown_count"] == 0
        assert [f["wanted"] for f in result["files"]] == ["not_needed"]

    @pytest.mark.asyncio
    async def test_the_platform_that_does_offer_the_unread_core_is_unknown(self, tmp_path):
        """The other half of the same machine, and the reason the value exists.

        ``arcadia`` (Emerson Arcadia 2001) is the one system of 172 that offers
        ``amiarcadia_libretro``, whose ``.info`` RetroDECK does not ship. It is
        the whole population of ``unknown`` on a stock install — rare, and
        reachable, which is what the fourth value is for.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "stray.bin",
                    "platform_slug": "arcadia",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=FakeFirmwareResolver(unread_emulators=frozenset({_id("amiarcadia_libretro")})),
            core_info=FakeCoreInfoProvider(options=[libretro_option("amiarcadia_libretro", "Amiarcadia")]),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("arcadia")

        assert result["unknown_count"] == 1
        assert [f["wanted"] for f in result["files"]] == ["unknown"]
        assert result["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_row_the_library_does_not_hold_cannot_cancel_the_unknown_verdict(self, tmp_path):
        """``known_count`` is about server files, so a union row must not raise it.

        A beyond-server row exists only because an emulator declared the file,
        so it is always ``needed``/``optional`` — always "known". Counted, one of
        them cancels ``unknown`` for a platform whose every server file went
        unanswered: the headline goes green while the row underneath still says
        nothing could answer for it.
        """
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {"id": 1, "file_name": "stray.bin", "platform_slug": "snes", "file_size_bytes": 100},
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=FakeFirmwareResolver(unread_emulators=frozenset({_id("snes9x_libretro")})),
            core_info=FakeCoreInfoProvider(
                options=[libretro_option("snes9x_libretro", "Snes9x"), libretro_option("bsnes_libretro", "bsnes")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        # Declared by the core that WAS read, and absent from the library — the
        # union row. The server's own file stays unanswerable either way.
        _resolver(fw).declare("extra.bin", required_by=[_id("bsnes_libretro")])
        _inline_executor(fw)

        result = await fw.check_platform_bios("snes")

        wanted = {f["file_name"]: f["wanted"] for f in result["files"]}
        assert wanted == {"stray.bin": "unknown", "extra.bin": "needed"}
        assert result["server_count"] == 1
        assert result["known_count"] == 0
        assert result["unknown_count"] == 1
        assert result["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_an_unreadable_emulator_list_leaves_the_scope_unestablished(self, tmp_path):
        """No ``es_systems.xml`` → the scope itself is unknown, so nothing is ruled out."""
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "stray.bin",
                    "platform_slug": "snes",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=FakeFirmwareResolver(),
            core_info=FakeCoreInfoProvider(available=False),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("snes")

        assert result["unknown_count"] == 1
        assert result["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_resolver_that_could_not_answer_never_reads_as_needing_none(self, tmp_path):
        """The adapter's failure shape must not clear a real requirement."""
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "stray.bin",
                    "platform_slug": "snes",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=FakeFirmwareResolver(resolved=False),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("snes")

        assert result["bios_level"] == "unknown"
        assert result["known_count"] == 0

    @pytest.mark.asyncio
    async def test_answered_platform_threads_known_count_and_is_not_unknown(self, tmp_path):
        """A platform with declared files ships known_count > 0 and keeps its level."""
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {"id": 1, "file_name": "req1.bin", "platform_slug": "dc", "file_size_bytes": 100, "md5_hash": ""},
                {"id": 2, "file_name": "opt1.bin", "platform_slug": "dc", "file_size_bytes": 100, "md5_hash": ""},
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=_dc_resolver(),
            core_info=_dc_core_info(),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path)),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("dc")

        # Three declared files, but only the two the listing carries count:
        # ``count_wanted`` is scoped to ``on_server`` rows, the same subset
        # ``server_count`` is taken over, so the row the library does not hold is
        # in neither. Nothing weighs the two against each other — they agree here
        # because one scoping produced both.
        assert result["known_count"] == 2
        assert result["server_count"] == 2
        assert result["bios_level"] != "unknown"


_PSX_CORE = "swanstation_libretro"
_PSX_ALTERNATIVE_CORE = "pcsx_rearmed_libretro"
_PSX_IMAGES = ("scph5500.bin", "scph5501.bin", "scph5502.bin")


def _psx_service(
    *,
    core_so: str = _PSX_CORE,
    system_firmware: str | None = SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    requirements_met: bool | None = False,
    held: str | None = None,
    bios_dir=None,
) -> FirmwareService:
    """A PlayStation platform as the machine really answers it.

    Every image the core declares is ``optional`` — that is all a libretro
    ``.info`` can say — so the file counts alone report nothing required. What
    separates a console that boots from one that does not is the resolver's
    ``system_firmware`` entry, which is why it is seeded per core here rather
    than per file. ``requirements_met`` is seeded beside it because it is the
    verdict the colour is taken from: the default is the ``False`` the
    reference device reports for every core in this state.
    """
    romm_api = MagicMock()
    romm_api.list_platforms.return_value = _platforms(
        [
            {
                "id": index,
                "file_name": name,
                "platform_slug": "psx",
                "file_size_bytes": 100,
                "md5_hash": "",
            }
            for index, name in enumerate(_PSX_IMAGES, start=1)
        ]
    )
    resolver = FakeFirmwareResolver(bios_root=str(bios_dir) if bios_dir is not None else None)
    for name in _PSX_IMAGES:
        resolver.declare(name, optional_for=[_id(core_so)], present=(name == held) or None)
    resolver.record_system(_id(core_so), system_firmware=system_firmware, requirements_met=requirements_met)
    fw = _make_firmware_service(
        romm_api=romm_api,
        firmware_resolver=resolver,
        core_info=FakeCoreInfoProvider(
            active_core=(core_so, "PS1 core"),
            options=[libretro_option(core_so, "PS1 core")],
        ),
    )
    _inline_executor(fw)
    return fw


class TestTheConsolesOwnFirmwareDemand:
    """A requirement no ``.info`` can state, weighed by the resolver and never by Tender.

    A libretro declaration marks each file required or optional, and nothing
    else. So SwanStation, whose console will not boot without a BIOS image,
    marks all of its images optional. The resolver weighs the console's demand
    into ``requirements_met``, so the colour is that verdict; what travels
    beside it is only that the console needs an image (``console_needs_image``),
    for the sentence — never a value of Tender's own about whether one is there.
    """

    @pytest.mark.asyncio
    async def test_a_console_needing_an_image_with_none_in_place_is_not_green(self):
        fw = _psx_service()

        result = await fw.check_platform_bios("psx")

        assert result["required_count"] == 0
        assert "system_image" not in result
        assert result["console_needs_image"] is True
        assert result["bios_level"] == "missing"
        # Never a required-file ratio: there are three files and one
        # requirement, and "0/3 required" would state the wrong set.
        assert result["bios_label"] == "Missing"

    @pytest.mark.asyncio
    async def test_one_image_in_place_reads_as_the_resolver_weighs_it(self, tmp_path):
        """The resolver answers the disjunction; the colour is its answer, whatever the rows look like."""
        bios_dir = tmp_path / "bios"
        bios_dir.mkdir()
        (bios_dir / "scph5501.bin").write_bytes(b"bios")
        met = await _psx_service(held="scph5501.bin", bios_dir=bios_dir, requirements_met=True).check_platform_bios(
            "psx"
        )
        not_met = await _psx_service(held="scph5501.bin", bios_dir=bios_dir).check_platform_bios("psx")

        assert met["bios_level"] == "ok"
        assert not_met["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_the_verdict_flips_with_the_core_the_game_launches_with(self):
        """PCSX ReARMed carries its own HLE BIOS over the identical machine.

        The two cores see the same three files and the same empty BIOS
        directory; the only thing that has ever separated their answers is a
        fact no file list holds.
        """
        needs = await _psx_service(core_so=_PSX_CORE).check_platform_bios("psx")
        carries = await _psx_service(
            core_so=_PSX_ALTERNATIVE_CORE,
            system_firmware=SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
            requirements_met=True,
        ).check_platform_bios("psx")

        assert (needs["console_needs_image"], needs["bios_level"]) == (True, "missing")
        assert (carries["console_needs_image"], carries["bios_level"]) == (False, "ok")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "state", [SYSTEM_FIRMWARE_RUNS_WITHOUT, SYSTEM_FIRMWARE_OPEN, SYSTEM_FIRMWARE_CORE_ALTERNATIVE, None]
    )
    async def test_no_other_recording_says_the_console_needs_an_image(self, state):
        """Including ``None`` — nothing recorded about this console, an unasked question.

        The level is the resolver's verdict either way; what no other recording
        may do is put the console's demand into the sentence.
        """
        fw = _psx_service(system_firmware=state, requirements_met=True)

        result = await fw.check_platform_bios("psx")

        assert result["console_needs_image"] is False
        assert result["bios_level"] == "ok"
        assert result["bios_label"] == "OK"

    @pytest.mark.asyncio
    async def test_a_demand_nothing_could_settle_declines_rather_than_clearing(self):
        """No row to read the disjunction off — grey, and never "nothing required"."""
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms([])
        resolver = FakeFirmwareResolver()
        resolver.record_system(
            _id(_PSX_CORE), system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=None
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                active_core=(_PSX_CORE, "PS1 core"), options=[libretro_option(_PSX_CORE, "PS1 core")]
            ),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("psx")

        assert result == {"needs_bios": False, "bios_status_unknown": True}

    @pytest.mark.asyncio
    async def test_the_platform_entry_stamps_the_same_answer_the_game_page_reads(self):
        """One builder, so a platform and its games cannot disagree about it."""
        fw = _psx_service()

        entries = await _entries(fw, "ps")
        page = await fw.check_platform_bios("psx")

        platform = entries["ps"]
        assert platform["console_needs_image"] is page["console_needs_image"] is True
        assert platform["bios_level"] == page["bios_level"] == "missing"
        assert platform["required_count"] == 0


# The PlayStation as the pinned resolver states it for two of its cores. Beetle
# PSX lists one image per region as a one-of group, and the same three files as
# plain rows of its own ``.info`` — which marks them required, and which the
# resolver states ``optional`` because the group speaks for those images.
# SwanStation states a group of one option — the image its search found —
# serving every region.
_BEETLE = "mednafen_psx_libretro"
_BEETLE_OPTIONS = (("scph5500.bin", ("ntsc-j",)), ("scph5501.bin", ("ntsc-u",)), ("scph5502.bin", ("pal",)))


def _one_of_service(*, launching: str = _BEETLE, present: tuple[str, ...] = ()) -> FirmwareService:
    """A PlayStation whose launching emulator states a one-of group over its images.

    *present* names the option files that are in place, which is both their
    rows' reading and their options' verdicts — one reading, two halves.
    Under ``_PSX_CORE`` the group is SwanStation's: one found image serving
    every region, in place.
    """
    romm_api = MagicMock()
    romm_api.list_platforms.return_value = _platforms(
        [
            {"id": index, "file_name": name, "platform_slug": "psx", "file_size_bytes": 100, "md5_hash": ""}
            for index, (name, _) in enumerate(_BEETLE_OPTIONS, start=1)
        ]
    )
    resolver = FakeFirmwareResolver()
    for name, _ in _BEETLE_OPTIONS:
        resolver.declare(name, optional_for=[_id(_BEETLE), _id(_PSX_CORE)], present=name in present)
    resolver.declare("scph1001.bin", optional_for=[_id(_PSX_CORE)], present=True)
    resolver.state_group(_id(_BEETLE), *((name, regions, name in present) for name, regions in _BEETLE_OPTIONS))
    resolver.state_group(_id(_PSX_CORE), ("scph1001.bin", ("ntsc-j", "ntsc-u", "pal"), True))
    # The resolver folds each emulator's group into its verdict: Beetle PSX's
    # is met with every image, not met with none, and open with some — open
    # because the disc's region decides, which it says so.
    resolver.describe_emulator(
        _id(_BEETLE),
        system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
        world_knowledge=True,
        region_decided_by_disc=True,
    )
    resolver.describe_emulator(_id(_PSX_CORE), system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, world_knowledge=True)
    fw = _make_firmware_service(
        romm_api=romm_api,
        firmware_resolver=resolver,
        core_info=FakeCoreInfoProvider(
            active_core=(launching, launching), options=[libretro_option(launching, launching)]
        ),
    )
    _inline_executor(fw)
    return fw


def _rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["file_name"]: f for f in payload["files"]}


class TestAOneOfGroupIsOneRequirement:
    """A group counts once, its options are rows that borrow nothing from it, and its state decides the level."""

    @pytest.mark.asyncio
    async def test_one_regions_image_is_a_partial_requirement(self):
        page = await _one_of_service(present=("scph5501.bin",)).check_platform_bios("psx")

        assert (page["required_count"], page["required_downloaded"]) == (1, 0)
        # Nothing reads a count of partly covered groups; the group's own state says it.
        assert "required_partial" not in page
        # The token counts plain required files only, and this core has none.
        assert (page["bios_level"], page["bios_label"]) == ("partial", "OK")
        assert page["one_of_groups"] == [
            {
                "state": "partial",
                "covered": ["ntsc-u"],
                "missing": ["ntsc-j", "pal"],
                "unchecked": [],
                "unstated": [],
                "game_regions": [],
                "regions": ["ntsc-j", "ntsc-u", "pal"],
                "options": [
                    {"file_name": "scph5500.bin", "regions": ["ntsc-j"], "satisfied": False},
                    {"file_name": "scph5501.bin", "regions": ["ntsc-u"], "satisfied": True},
                    {"file_name": "scph5502.bin", "regions": ["pal"], "satisfied": False},
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_every_option_is_its_own_row_and_none_is_required(self):
        page = await _one_of_service(present=("scph5501.bin",)).check_platform_bios("psx")

        rows = _rows(page)
        assert {name: rows[name]["one_of"] for name, _ in _BEETLE_OPTIONS} == {
            name: {"regions": list(regions), "every_region": False} for name, regions in _BEETLE_OPTIONS
        }
        assert not any(row["required_by_active"] for row in rows.values())

    @pytest.mark.asyncio
    async def test_every_regions_image_meets_it(self):
        page = await _one_of_service(present=tuple(name for name, _ in _BEETLE_OPTIONS)).check_platform_bios("psx")

        assert (page["required_count"], page["required_downloaded"], page["bios_level"]) == (1, 1, "ok")

    @pytest.mark.asyncio
    async def test_nothing_in_place_is_missing(self):
        page = await _one_of_service().check_platform_bios("psx")

        assert (page["bios_level"], page["bios_label"]) == ("missing", "Missing")
        assert page["one_of_groups"][0]["state"] == "unmet"

    @pytest.mark.asyncio
    async def test_an_image_serving_every_region_meets_it_and_says_so_on_its_row(self):
        page = await _one_of_service(launching=_PSX_CORE).check_platform_bios("psx")

        assert (page["required_count"], page["required_downloaded"], page["bios_level"]) == (1, 1, "ok")
        assert _rows(page)["scph1001.bin"]["one_of"] == {"regions": ["ntsc-j", "ntsc-u", "pal"], "every_region": True}
        assert _rows(page)["scph5501.bin"]["one_of"] is None

    @pytest.mark.asyncio
    async def test_the_game_page_answer_carries_no_field_no_surface_reads(self):
        """The verdict reaches a surface as ``bios_level``; what keeps it open, as sentences.

        ``requirements_met``, ``undetermined`` and a count of partly covered
        groups were on the wire with no reader, which is how a reader comes to
        re-derive what the level already says.
        """
        page = await _one_of_service(present=("scph5501.bin",)).check_platform_bios("psx")

        assert {"requirements_met", "undetermined", "required_partial"}.isdisjoint(page)

    @pytest.mark.asyncio
    async def test_a_row_carries_no_per_emulator_lines(self):
        """Every emulator has an entry of its own, so no surface reads per-emulator entries on a row."""
        page = await _one_of_service(launching=_PSX_CORE).check_platform_bios("psx")

        assert all("cores" not in row for row in page["files"])
        assert all("cores" not in row for tab in page["emulator_tabs"] for row in tab.get("rows", []))

    @pytest.mark.asyncio
    async def test_the_platform_entry_stamps_the_same_answer_the_game_page_reads(self):
        fw = _one_of_service(present=("scph5501.bin",))

        entries = await _entries(fw, "psx")
        page = await fw.check_platform_bios("psx")

        platform = entries["psx"]
        assert _emulator_dependent(platform) == _emulator_dependent(page)
        assert platform["one_of_groups"][0]["state"] == "partial"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rom_regions", "state", "level"),
        [
            (("USA",), "met", "ok"),
            (("Japan",), "unmet", "missing"),
            (("Japan", "USA"), "met", "ok"),
            (("World",), "partial", "partial"),
            ((), "partial", "partial"),
        ],
    )
    async def test_a_game_is_judged_by_its_own_regions(self, rom_regions, state, level):
        """Only a region that maps narrows the answer; World and no region leave the platform's."""
        page = await _one_of_service(present=("scph5501.bin",)).check_platform_bios(
            "psx", None, rom_regions=rom_regions
        )

        assert (page["one_of_groups"][0]["state"], page["bios_level"]) == (state, level)


_PSX_DEFAULT_LABEL = "SwanStation"
_PSX_ALTERNATIVE_LABEL = "PCSX ReARMed"
_PSX_STANDALONE_LABEL = "DuckStation (Standalone)"


def _psx_emulators() -> list[EmulatorOption]:
    """The three ES-DE lists for a PlayStation, in document order."""
    return [
        libretro_option(_PSX_CORE, _PSX_DEFAULT_LABEL),
        libretro_option(_PSX_ALTERNATIVE_CORE, _PSX_ALTERNATIVE_LABEL),
        standalone_option("%EMULATOR_DUCKSTATION% %ROM%", _PSX_STANDALONE_LABEL),
    ]


class _DeclaredDefaultCoreInfo(FakeCoreInfoProvider):
    """ES-DE's own default, rendered the way the live adapter renders it.

    :class:`FakeCoreInfoProvider` builds the default invocation out of its
    ``active_core`` tuple, which carries no identity — and the identity is the
    one field a firmware answer is keyed on, so a fixture on the bare fake
    resolves an unpinned ROM to a ``None`` where production resolves it to an
    emulator. The live adapter renders the first bakeable OPTION instead
    (``AtlasCatalogueAdapter.get_default_emulator``), identity and all.
    """

    def get_default_emulator(self, system_name: str, *, reading: Any = None) -> EmulatorInvocation | None:
        self.emulator_options_calls.append(system_name)
        return option_to_invocation(select_default_option(self.options))


def _psx_platform_service(
    *, platform_core: str | None = None, options: list[EmulatorOption] | None = None, grouped: bool = False
) -> FirmwareService:
    """A PlayStation ES-DE lists three emulators for, one of them standalone.

    SwanStation is the es_systems default — first bakeable in document order —
    and its console will not start without one of the images it declares. PCSX
    ReARMed declares the same three and carries its own substitute. DuckStation
    is standalone, so it names no core and nothing here can answer for it. The
    files, the empty BIOS directory and the reading are identical under all
    three: the only thing that moves is which emulator the platform resolves to,
    which is what makes a disagreement between two surfaces attributable.

    *options* replaces the emulator list for a test about a pin that no longer
    bakes; every other caller takes ES-DE's three.

    *grouped* has SwanStation state a one-of group over the three images with
    only the NTSC-U one in place — the answer a group moves with the pick.

    The listing files the images on ``psx``, the slug the game page asks about.
    """
    romm_api = MagicMock()
    romm_api.list_platforms.return_value = _platforms(
        [
            {
                "id": index,
                "file_name": name,
                "platform_slug": "psx",
                "file_size_bytes": 100,
                "md5_hash": "",
            }
            for index, name in enumerate(_PSX_IMAGES, start=1)
        ]
    )
    resolver = FakeFirmwareResolver()
    for name in _PSX_IMAGES:
        resolver.declare(name, optional_for=[_id(_PSX_CORE), _id(_PSX_ALTERNATIVE_CORE)])
    if grouped:
        resolver.state_group(
            _id(_PSX_CORE),
            *((name, regions, name == "scph5501.bin") for name, regions in _BEETLE_OPTIONS),
        )
        # With one region's image in place the resolver leaves the verdict open
        # because the disc's region decides it, and says so.
        resolver.describe_emulator(
            _id(_PSX_CORE),
            system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            world_knowledge=True,
            region_decided_by_disc=True,
        )
    else:
        resolver.record_system(
            _id(_PSX_CORE), system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=False
        )
    resolver.record_system(
        _id(_PSX_ALTERNATIVE_CORE), system_firmware=SYSTEM_FIRMWARE_CORE_ALTERNATIVE, requirements_met=True
    )
    fw = _make_firmware_service(
        romm_api=romm_api,
        firmware_resolver=resolver,
        core_info=_DeclaredDefaultCoreInfo(
            active_core=(_PSX_CORE, _PSX_DEFAULT_LABEL),
            options=options if options is not None else _psx_emulators(),
        ),
        platform_core_reader=FakePlatformCoreReader({"psx": platform_core} if platform_core is not None else None),
    )
    _inline_executor(fw)
    return fw


def _emulator_dependent(payload: dict[str, Any]) -> dict[str, Any]:
    """The part of a BIOS answer that moves with the emulator the platform is about."""
    return {
        "console_needs_image": payload["console_needs_image"],
        "bios_level": payload["bios_level"],
        "required_count": payload["required_count"],
        "one_of_groups": payload["one_of_groups"],
        "required_by_active": {f["file_name"]: f["required_by_active"] for f in payload["files"]},
        "one_of": {f["file_name"]: f["one_of"] for f in payload["files"]},
    }


# Every way a platform can arrive at an emulator: no pick at all, a pick that is
# each of the three ES-DE offers, and the two pins that no longer resolve — one
# naming an emulator the catalogue does not list, one naming an installed
# emulator whose command cannot be baked. The last two degrade to the default,
# and the whole point is that they degrade the same way on both surfaces.
_PLATFORM_PICKS = [
    None,
    _PSX_DEFAULT_LABEL,
    _PSX_ALTERNATIVE_LABEL,
    _PSX_STANDALONE_LABEL,
    "An emulator ES-DE no longer lists",
]


class TestAStandaloneEmulatorIsAskedLikeAnyOther:
    """The identity is what makes a standalone pick answerable, and what scopes the doubt.

    Measured on the reference device before this: a PlayStation launching
    DuckStation read a red ``0 / 3 required files ready`` naming three files
    Beetle PSX requires and DuckStation does not, because a standalone pick named
    no core and the rows fell back to every declaring emulator. PS2 read a green
    "All required ready" for the opposite reason — LRPS2's folder was satisfied,
    and nobody had asked PCSX2.
    """

    _CARDED = "DUCKSTATION"
    _UNCARDED = "PCSX2"
    _CORE = "mednafen_psx_libretro"

    def _service(
        self, *, pick_label: str, resolver: FakeFirmwareResolver, uow: FakeUnitOfWork | None = None
    ) -> FirmwareService:
        fw = _make_firmware_service(
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                options=[
                    standalone_option("%EMULATOR_DUCKSTATION% %ROM%", "DuckStation", emulator=self._CARDED),
                    standalone_option("%EMULATOR_PCSX2% %ROM%", "PCSX2", emulator=self._UNCARDED),
                    libretro_option(self._CORE, "Beetle PSX"),
                ]
            ),
            platform_core_reader=FakePlatformCoreReader({"psx": pick_label}),
            uow_factory=FakeUnitOfWorkFactory(uow) if uow is not None else None,
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        return fw

    def _resolver(self) -> FakeFirmwareResolver:
        """One console, three emulators: one carded, one that established nothing, one core."""
        resolver = FakeFirmwareResolver(unread_emulators=frozenset({self._UNCARDED}))
        resolver.declare("ps2-0200a.bin", required_by=[self._CARDED], present=True)
        for name in ("scph5500.bin", "scph5501.bin", "scph5502.bin"):
            resolver.declare(name, required_by=[_id(self._CORE)], present=False)
        return resolver

    @pytest.mark.asyncio
    async def test_a_carded_standalone_pick_answers_for_its_own_declaration(self):
        """Its file, and only its file — the libretro core's three are not this launch's."""
        result = await self._service(pick_label="DuckStation", resolver=self._resolver()).check_platform_bios("psx")

        assert result["required_count"] == 1
        assert result["required_downloaded"] == 1
        assert result["bios_level"] == "ok"
        rows = {row["file_name"]: row for row in result["files"]}
        assert rows["ps2-0200a.bin"]["required_by_active"] is True
        assert rows["scph5500.bin"]["required_by_active"] is False

    @pytest.mark.asyncio
    async def test_the_libretro_core_beside_it_still_answers_for_itself(self):
        """Same platform, same reading, the other emulator picked — three required, none held."""
        result = await self._service(pick_label="Beetle PSX", resolver=self._resolver()).check_platform_bios("psx")

        assert (result["required_count"], result["required_downloaded"]) == (3, 0)
        assert result["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_standalone_pick_nothing_could_answer_for_declines(self):
        """The other emulators' rows are on the page and are not an all-clear.

        Nothing established what PCSX2 wants, so ``required_by_active`` is zero
        by construction and the counts would read "nothing required" over a
        console nobody asked about.
        """
        result = await self._service(pick_label="PCSX2", resolver=self._resolver()).check_platform_bios("psx")

        assert result["required_count"] == 0
        assert result["bios_level"] == "unknown"
        assert result["bios_label"] == "Unknown"

    @pytest.mark.asyncio
    async def test_an_emulator_the_resolver_could_not_identify_declines_too(self):
        """No identity, so nothing can be scoped to it — the same silence, said the same way."""
        fw = _make_firmware_service(
            firmware_resolver=self._resolver(),
            core_info=FakeCoreInfoProvider(
                options=[standalone_option("%EMULATOR_CITRA% %ROM%", "Citra", emulator=None)]
            ),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])

        result = await fw.check_platform_bios("psx")

        assert result["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_an_unread_emulator_the_launch_does_not_use_leaves_the_answer_alone(self):
        """PCSX2 is unreadable and irrelevant: this game launches with DuckStation."""
        result = await self._service(pick_label="DuckStation", resolver=self._resolver()).check_platform_bios("psx")

        assert result["bios_level"] == "ok"

    @pytest.mark.asyncio
    async def test_the_platform_entry_and_the_game_page_agree_on_the_standalone_pick(self):
        """One pick, one reading — the pane names DuckStation and judges by it."""
        uow = FakeUnitOfWork()
        _seed_rom(uow, rom_id=9, platform_slug="psx")
        fw = self._service(pick_label="DuckStation", resolver=self._resolver(), uow=uow)

        entries = await _entries(fw, "psx")
        platform = entries["psx"]
        page = await fw.check_platform_bios("psx")

        assert platform["active_core"] == self._CARDED
        assert platform["active_core_label"] == "DuckStation"
        assert (platform["required_count"], platform["required_downloaded"]) == (
            page["required_count"],
            page["required_downloaded"],
        )
        assert platform["bios_level"] == page["bios_level"]


class TestOnePlatformOneEmulator:
    """A platform's surfaces answer for ONE emulator, whichever way it was picked.

    The device pass that found this: with the PlayStation's emulator set to PCSX
    ReARMed, the game page read ``ok`` — the core carries its own HLE BIOS —
    while the platform pane read ``missing`` and displayed "PCSX ReARMed" in
    its own header. One platform, two surfaces, opposite answers, because the
    pane named the resolved pick and judged by the system default beside it.

    Pinning the corrected value alone would not hold: a third resolution added
    anywhere here would answer today's value for today's configuration and
    diverge on some other one. So each case is asserted as an AGREEMENT — every
    way of arriving at an emulator, both surfaces, one answer — and the pane is
    additionally held to judging by the emulator it names.
    """

    @staticmethod
    async def _both(platform_core: str | None, *, grouped: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        """The platform's own entry, and the game page's answer for it."""
        fw = _psx_platform_service(platform_core=platform_core, grouped=grouped)
        entries = await _entries(fw, "psx")
        page = await fw.check_platform_bios("psx")
        return entries["psx"], page

    @pytest.mark.parametrize("platform_core", _PLATFORM_PICKS)
    @pytest.mark.asyncio
    async def test_the_pane_and_the_game_page_answer_for_one_emulator(self, platform_core):
        platform, page = await self._both(platform_core)

        assert _emulator_dependent(platform) == _emulator_dependent(page)

    @pytest.mark.parametrize("platform_core", _PLATFORM_PICKS)
    @pytest.mark.asyncio
    async def test_the_pane_judges_by_the_emulator_it_names(self, platform_core):
        """The header's name and the filter's identity are two projections of one pick.

        Read off the pane's own emulator list rather than off a constant, so the
        assertion is the RELATION rather than today's value — which is what makes
        it hold for a standalone pick too, where there is no core to name and the
        identity is the only field that answers.
        """
        platform, _ = await self._both(platform_core)

        named = next(e for e in platform["emulators"] if e["label"] == platform["active_core_label"])
        assert platform["active_core"] == named["emulator"]

    @pytest.mark.asyncio
    async def test_the_pick_moves_the_answer_on_both_surfaces(self):
        """The measurement itself: picking PCSX ReARMed clears the console's demand.

        Without it the platform resolves to SwanStation, whose console will not
        start without an image and holds none. Both surfaces have to move
        together, and the pane moving alone is what a green agreement over an
        unmoved answer would hide.
        """
        default_platform, default_page = await self._both(None)
        picked_platform, picked_page = await self._both(_PSX_ALTERNATIVE_LABEL)

        for payload in (default_platform, default_page):
            assert (payload["console_needs_image"], payload["bios_level"]) == (True, "missing")
        for payload in (picked_platform, picked_page):
            assert (payload["console_needs_image"], payload["bios_level"]) == (False, "ok")

    @pytest.mark.asyncio
    async def test_a_pin_that_no_longer_bakes_degrades_rather_than_answering_for_it(self):
        """A ``needs_setup`` emulator is not a pick — the platform falls back to its default."""
        fw = _psx_platform_service(
            platform_core="Vita3K (Standalone)",
            options=[
                libretro_option(_PSX_CORE, _PSX_DEFAULT_LABEL),
                standalone_option(
                    "%EMULATOR_VITA3K% %INJECT%", "Vita3K (Standalone)", status="needs_setup", reason="inject"
                ),
            ],
        )

        entries = await _entries(fw, "psx")
        page = await fw.check_platform_bios("psx")

        platform = entries["psx"]
        assert platform["active_core_label"] == _PSX_DEFAULT_LABEL
        assert platform["active_core"] == _id(_PSX_CORE)
        assert _emulator_dependent(platform) == _emulator_dependent(page)

    @pytest.mark.parametrize("platform_core", _PLATFORM_PICKS)
    @pytest.mark.asyncio
    async def test_the_pane_and_the_game_page_agree_about_a_group(self, platform_core):
        platform, page = await self._both(platform_core, grouped=True)

        assert _emulator_dependent(platform) == _emulator_dependent(page)

    @pytest.mark.asyncio
    async def test_the_group_moves_with_the_pick_on_both_surfaces(self):
        """The non-vacuity check for the agreement above: the group is the default's, and only its."""
        default_platform, default_page = await self._both(None, grouped=True)
        picked_platform, picked_page = await self._both(_PSX_ALTERNATIVE_LABEL, grouped=True)

        for payload in (default_platform, default_page):
            assert [group["state"] for group in payload["one_of_groups"]] == ["partial"]
            assert payload["bios_level"] == "partial"
        for payload in (picked_platform, picked_page):
            assert payload["one_of_groups"] == []

    @pytest.mark.asyncio
    async def test_each_emulators_entry_carries_its_own_group_membership(self):
        """Where a file stands in a group is the emulator's own word, on its own entry.

        Both mark every image ``optional``; only SwanStation lists them as a
        group, so only its row names the region the file serves — and the row
        carries no per-emulator entries any more.
        """
        _, page = await self._both(None, grouped=True)

        row = next(f for f in page["files"] if f["file_name"] == "scph5500.bin")
        assert row["one_of"] == {"regions": ["ntsc-j"], "every_region": False}
        assert "cores" not in row
        alternative = next(tab for tab in page["emulator_tabs"] if tab["emulator"] == _id(_PSX_ALTERNATIVE_CORE))
        assert next(r for r in alternative["rows"] if r["file_name"] == "scph5500.bin")["one_of"] is None


class TestTheAnswerNamesTheEmulatorItJudgedBy:
    """The BIOS answer carries the NAME of the pick its counts were filtered by.

    The game page heads its BIOS section with a sentence about what "the
    launching emulator" requires, and had no way to say which one: the payload
    carried the filter's identity nowhere and no name at all, so a name there
    would have had to come from the core read beside it on the page — a second
    resolution of the same question, and the split that once had a pane display
    PCSX ReARMed while judging the platform by the libretro default.

    So the label is a projection of the pick this very answer was scoped to, and
    the tests below assert that RELATION rather than today's value: the name has
    to move when the pick does, and it has to follow the pick the caller handed
    in even where the platform would have resolved to somebody else.
    """

    @staticmethod
    async def _page(platform_core: str | None = None, pick: EmulatorInvocation | None = None) -> dict[str, Any]:
        return await _psx_platform_service(platform_core=platform_core).check_platform_bios("psx", pick)

    @pytest.mark.asyncio
    async def test_it_names_the_platforms_own_pick(self):
        assert (await self._page())["active_core_label"] == _PSX_DEFAULT_LABEL

    @pytest.mark.asyncio
    async def test_the_name_moves_with_the_pick(self):
        """The non-vacuity check: a page that hardcoded the default would pass the first."""
        assert (await self._page(_PSX_ALTERNATIVE_LABEL))["active_core_label"] == _PSX_ALTERNATIVE_LABEL

    @pytest.mark.asyncio
    async def test_a_pick_that_could_not_be_made_names_nobody(self):
        """ES-DE offers this platform nothing bakeable, so there is no name to print.

        The field is still stated: the surface words its sentence without a name,
        and an absent key and an absent name are the same thing to it.
        """
        page = await _psx_platform_service(platform_core=None, options=[]).check_platform_bios("psx")

        assert page["active_core_label"] is None

    @pytest.mark.asyncio
    async def test_the_name_and_the_identity_it_judged_by_are_one_pick(self):
        """Two labels can be one emulator, so the pair cannot be assembled from two calls.

        ES-DE lists one ``pcsx2_libretro.so`` as both ``LRPS2`` and ``PCSX2``,
        which is why a label names a launch row and never an emulator. Handed a
        pick the platform itself would not have made, the answer has to print
        THAT pick's name and judge by THAT pick's identity — the platform's own
        resolution is a third emulator here, so a name taken from it, or an
        identity taken from it, shows up immediately.

        The two picks below differ only in their label, which is what makes the
        second assertion say something: the name moves, the judgment does not.
        """
        alternative = _id(_PSX_ALTERNATIVE_CORE)
        under_one_name = await self._page(pick=_pick(alternative, "PCSX ReARMed"))
        under_another = await self._page(pick=_pick(alternative, "PCSX-ReARMed (Interpreter)"))
        platforms_own = await self._page()

        assert under_one_name["active_core_label"] == "PCSX ReARMed"
        assert under_another["active_core_label"] == "PCSX-ReARMed (Interpreter)"
        assert platforms_own["active_core_label"] == _PSX_DEFAULT_LABEL
        # One emulator under two names: the answer is the same one, and it is
        # not the one the platform would have resolved to on its own.
        assert _emulator_dependent(under_one_name) == _emulator_dependent(under_another)
        assert _emulator_dependent(under_one_name) != _emulator_dependent(platforms_own)

    def test_the_seam_carries_the_pick_and_never_a_name_beside_it(self):
        """There is one emulator argument, which is what makes the pair unrepresentable.

        The test above shows both projections following one pick; this is why
        they must — a second parameter for the name is all it would take to hand
        the filter one emulator and the sentence another, and the caller that
        did so would be perfectly well typed.
        """
        parameters = list(inspect.signature(FirmwareStatusReader.check_platform_bios).parameters)

        assert parameters == ["self", "platform_slug", "launching_emulator", "rom_regions", "install"]


_PSX_ROM_ID = 501
_PSX_STANDALONE_ID = "DUCKSTATION"

# Every way a ROM arrives at an emulator, as ``(per-game pin, platform pick)``:
# nothing pinned anywhere, the platform's own pick, a per-game override, the
# override winning over a platform pick that names something else, a standalone
# pick, and a stale pin that no longer resolves. The last degrades to the layer
# below, and the point is that it degrades the same way on both halves.
_ROM_PICKS = [
    (None, None),
    (None, _PSX_ALTERNATIVE_LABEL),
    (_PSX_ALTERNATIVE_LABEL, None),
    (_PSX_DEFAULT_LABEL, _PSX_ALTERNATIVE_LABEL),
    (_PSX_STANDALONE_LABEL, None),
    ("An emulator ES-DE no longer lists", None),
]


def _rom_scoped_surfaces(
    *, game_pick: str | None, platform_pick: str | None, grouped: bool = False, regions: tuple[str, ...] = ()
) -> tuple[CoreService, GameDetailService, FirmwareService]:
    """The game page's two halves over one PlayStation, one ROM, one resolution.

    Both halves resolve the ROM's emulator through the real
    :class:`ActiveCoreResolver` — the seam they share in production — reading one
    emulator list, one per-platform override map and one ROM row. So the only
    thing a disagreement between them can come from is one half reading a
    different field of the one answer, which is exactly the defect this pins.
    """

    # ES-DE's three, but with the standalone one IDENTIFIED. The platform-scoped
    # fixture leaves it unidentified on purpose, which makes a standalone pick
    # and an unresolvable one the same ``None`` — and a ROM pinned to it would
    # then agree with the picker vacuously, both halves holding nothing.
    logger = logging.getLogger("test")
    fw = _psx_platform_service(
        platform_core=platform_pick,
        grouped=grouped,
        options=[
            libretro_option(_PSX_CORE, _PSX_DEFAULT_LABEL),
            libretro_option(_PSX_ALTERNATIVE_CORE, _PSX_ALTERNATIVE_LABEL),
            standalone_option("%EMULATOR_DUCKSTATION% %ROM%", _PSX_STANDALONE_LABEL, emulator=_PSX_STANDALONE_ID),
        ],
    )
    core_info = _core_info(fw)
    uow = FakeUnitOfWork()
    uow.roms.save(
        Rom(
            rom_id=_PSX_ROM_ID,
            platform_slug="psx",
            name=f"rom-{_PSX_ROM_ID}",
            fs_name=f"rom-{_PSX_ROM_ID}.chd",
            shortcut_app_id=1,
            last_synced_at="2026-01-01T00:00:00+00:00",
            emulator_override=game_pick,
            regions=regions,
        )
    )
    uow_factory = FakeUnitOfWorkFactory(uow)
    platform_systems = FakePlatformSystems()
    active_core = ActiveCoreResolver(
        config=ActiveCoreResolverConfig(
            uow_factory=uow_factory,
            core_info=core_info,
            sandbox_launcher=FakeSandboxLauncher(),
            platform_core_reader=_platform_core_reader(fw),
            platform_systems=platform_systems,
            logger=logger,
        )
    )
    cores = CoreService(
        config=CoreServiceConfig(
            loop=asyncio.get_running_loop(),
            logger=logger,
            core_info=core_info,
            platform_systems=platform_systems,
            settings={},
            settings_persister=FakeSettingsPersister(),
            uow_factory=uow_factory,
            active_core=active_core,
            disc_resolver=FakeDiscResolver(),
            conflict_rules=_make_conflict_rules(),
        )
    )
    detail = GameDetailService(
        config=GameDetailServiceConfig(
            settings={},
            loop=asyncio.get_running_loop(),
            logger=logger,
            clock=_make_clock(),
            uow_factory=uow_factory,
            bios_checker=fw,
            achievements=MagicMock(),
            active_core=active_core,
            path_exists=FakePathExistsReader(),
            retrodeck_folders=FakeRetroDeckFolders(),
            platform_systems=platform_systems,
            candidate_probe=lambda platform_slug, fs_name: False,
        )
    )
    return cores, detail, fw


class TestOneRomOneEmulator:
    """A ROM's two halves answer for ONE emulator, whichever way the ROM arrived at it.

    The ROM-scoped sibling of :class:`TestOnePlatformOneEmulator`. The game page
    is assembled from two services that each ask "which emulator launches this
    ROM?" for themselves: ``services/cores.py`` names it in the picker, and
    ``services/game_detail.py`` scopes the BIOS question to it. They read one
    seam today, and nothing said they must — the picker reading
    ``active_core_for_rom`` instead would answer ``None`` for every standalone
    pick and send the BIOS rows back to the platform's own, which is the
    platform-scoped defect one layer in.

    Asserted as an AGREEMENT rather than as today's value, for the reason its
    sibling is: a second resolution added on either side would answer today's
    value for today's configuration and diverge on some other one.
    """

    @staticmethod
    async def _both(
        game_pick: str | None, platform_pick: str | None, *, grouped: bool = False, regions: tuple[str, ...] = ()
    ) -> tuple[dict[str, Any], dict[str, Any], Any]:
        """The picker payload, the BIOS half's answer, and the firmware service behind them."""
        cores, detail, fw = _rom_scoped_surfaces(
            game_pick=game_pick, platform_pick=platform_pick, grouped=grouped, regions=regions
        )
        picker = await cores.get_platform_core_info(_PSX_ROM_ID)
        bios = (await detail.get_bios_status(_PSX_ROM_ID))["bios_status"]
        return picker, bios, fw

    @pytest.mark.parametrize(("game_pick", "platform_pick"), _ROM_PICKS)
    @pytest.mark.asyncio
    async def test_the_bios_half_answers_for_the_emulator_the_picker_names(self, game_pick, platform_pick):
        picker, bios, fw = await self._both(game_pick, platform_pick)

        # Asked with the pick the picker itself published — its two projections,
        # which the rule above says are one resolution, handed over as one value.
        named = await fw.check_platform_bios("psx", _pick(picker["active_core"], picker["active_core_label"]))
        assert _emulator_dependent(bios) == _emulator_dependent(named)

    @pytest.mark.parametrize(("game_pick", "platform_pick"), _ROM_PICKS)
    @pytest.mark.asyncio
    async def test_the_bios_half_judges_the_named_emulators_group_for_the_roms_regions(self, game_pick, platform_pick):
        picker, bios, fw = await self._both(game_pick, platform_pick, grouped=True, regions=("Japan",))

        named = await fw.check_platform_bios(
            "psx", _pick(picker["active_core"], picker["active_core_label"]), rom_regions=("Japan",)
        )
        assert _emulator_dependent(bios) == _emulator_dependent(named)

    @pytest.mark.asyncio
    async def test_the_roms_own_region_decides_its_group(self):
        """The non-vacuity check for the agreement above: the game's region moves the answer."""
        _, japanese, _ = await self._both(None, None, grouped=True, regions=("Japan",))
        _, american, _ = await self._both(None, None, grouped=True, regions=("USA",))
        _, worldwide, _ = await self._both(None, None, grouped=True, regions=("World",))

        assert [(g["state"], g["game_regions"]) for g in japanese["one_of_groups"]] == [("unmet", ["ntsc-j"])]
        assert [(g["state"], g["game_regions"]) for g in american["one_of_groups"]] == [("met", ["ntsc-u"])]
        assert [(g["state"], g["game_regions"]) for g in worldwide["one_of_groups"]] == [("partial", [])]

    @pytest.mark.parametrize(("game_pick", "platform_pick"), _ROM_PICKS)
    @pytest.mark.asyncio
    async def test_the_picker_keys_on_the_emulator_it_names(self, game_pick, platform_pick):
        """The page's label and the identity its BIOS answer is scoped to are one pick.

        Read off the payload's own emulator list rather than off a constant, so
        what is asserted is the RELATION — which is what makes it hold for the
        standalone pick, where there is no core to name and the identity is the
        only field that answers.
        """
        picker, _, _ = await self._both(game_pick, platform_pick)

        named = next(e for e in picker["emulators"] if e["label"] == picker["active_core_label"])
        assert picker["active_core"] == named["emulator"]

    @pytest.mark.asyncio
    async def test_a_per_game_pin_moves_the_answer_on_both_halves(self):
        """The non-vacuity check: the two agree about an answer that actually moves.

        Unpinned, the ROM launches with the es_systems default, whose console
        will not start without an image and holds none. Pinned to PCSX ReARMed —
        which carries its own substitute — the same three files stop being a
        requirement. Both halves have to move together, and an agreement over an
        answer that never moved would hide either of them standing still.
        """
        default_picker, default_bios, _ = await self._both(None, None)
        pinned_picker, pinned_bios, _ = await self._both(_PSX_ALTERNATIVE_LABEL, None)

        assert default_picker["active_core_label"] == _PSX_DEFAULT_LABEL
        assert (default_bios["console_needs_image"], default_bios["bios_level"]) == (True, "missing")
        assert pinned_picker["active_core_label"] == _PSX_ALTERNATIVE_LABEL
        assert (pinned_bios["console_needs_image"], pinned_bios["bios_level"]) == (False, "ok")

    @pytest.mark.asyncio
    async def test_a_standalone_pin_is_carried_by_the_identity_alone(self):
        """The pick both halves would lose by reading the core rather than the identity.

        DuckStation has no ``core_so``, so the ``.so``-space projection answers
        ``None`` for it — which is the platform's own pick, a different emulator
        and a different answer.
        """
        picker, bios, fw = await self._both(_PSX_STANDALONE_LABEL, None)

        assert picker["active_core_label"] == _PSX_STANDALONE_LABEL
        assert picker["active_core"] is not None
        platform_pick = await fw.check_platform_bios("psx", None)
        assert _emulator_dependent(bios) != _emulator_dependent(platform_pick)


class TestDownloadFirmware:
    @pytest.mark.asyncio
    async def test_downloads_and_verifies_md5(self, firmware, fw, tmp_path):
        content = b"firmware data here"
        expected_md5 = hashlib.md5(content).hexdigest()

        fw_detail = {
            "id": 10,
            "file_name": "bios.bin",
            "file_size_bytes": len(content),
            "md5_hash": expected_md5,
        }
        _stub_listing(fw, [{**fw_detail, "platform_slug": "n64"}])

        bios_dir = tmp_path / "bios"
        bios_dir.mkdir()

        def fake_download(firmware_id, filename, dest):
            with open(dest, "wb") as f:
                f.write(content)

        _resolver_reads(fw, str(bios_dir))
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "download_firmware", side_effect=fake_download):
            result = await fw.download_platform_firmware_file("n64", "bios.bin")

        assert result["success"] is True
        assert result["downloaded"] == 1
        stored = tmp_path / "Tender" / "bios" / "n64" / "bios.bin"
        assert stored.read_bytes() == content
        # The store record persisted via the Unit of Work, under the platform
        # the listing states for the file, with the checksum it was checked against.
        record = firmware.uow.firmware_store.get("n64", "bios.bin")
        assert record is not None
        assert record.firmware_id == 10
        assert record.md5 == expected_md5
        assert record.folder == str(tmp_path / "Tender")

    @pytest.mark.asyncio
    async def test_rejects_traversal_in_server_file_name(self, firmware, fw, tmp_path):
        """#966: a server ``file_name`` of ``../evil.desktop`` is rejected, nothing written outside the store."""
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        # The traversal target lives a sibling of the platform's store folder.
        escape_target = tmp_path / "Tender" / "bios" / "evil.desktop"

        fw_detail = {
            "id": 10,
            "file_name": "../evil.desktop",
            "file_size_bytes": 10,
            "md5_hash": "a" * 32,
        }
        _stub_listing(fw, [{**fw_detail, "platform_slug": "n64"}])

        _resolver_reads(fw, str(bios_dir))
        _set_loop(fw, asyncio.get_running_loop())

        download_called = []

        def fake_download(firmware_id, filename, dest):
            download_called.append(dest)
            with open(dest, "wb") as f:
                f.write(b"evil")

        with patch.object(firmware.romm_api, "download_firmware", side_effect=fake_download):
            download = fw.download_platform_firmware_file("n64", "../evil.desktop")
            with pytest.raises(Refused) as refused:
                await download

        assert refused.value.reason == "path_traversal"
        assert refused.value.message
        # No download was ever attempted (rejected before make_dirs / fetch).
        assert download_called == []
        # Nothing written outside the store.
        assert not escape_target.exists()
        # No store record persisted.
        assert firmware.uow.firmware_store.get("n64", "../evil.desktop") is None


class TestWhetherADownloadIsInFlight:
    """The reader the update install asks: a download is awaited inside the call that asked for it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("entry", "inner", "args"),
        [
            ("download_all_firmware", "download_all_firmware", ("dc",)),
            ("download_platform_firmware_file", "download_platform_firmware_file", ("dc", "a.bin")),
            ("download_required_firmware", "download_required_firmware", ("dc",)),
        ],
    )
    async def test_it_is_in_flight_for_as_long_as_the_call_runs(self, fw, entry, inner, args):
        seen: list[bool] = []

        async def downloading(*_args: Any) -> dict[str, Any]:
            seen.append(fw.is_downloading())
            return {"success": True}

        assert fw.is_downloading() is False
        with patch.object(fw._downloads, inner, side_effect=downloading):
            await getattr(fw, entry)(*args)

        assert seen == [True]
        assert fw.is_downloading() is False

    @pytest.mark.asyncio
    async def test_a_call_that_raises_is_no_longer_in_flight(self, fw):
        with (
            patch.object(fw._downloads, "download_all_firmware", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await fw.download_all_firmware("dc")

        assert fw.is_downloading() is False

    @pytest.mark.asyncio
    async def test_a_refused_call_was_never_in_flight(self, fw):
        seen: list[bool] = []
        fw._rules = _make_conflict_rules(update_in_progress=True)

        with (
            patch.object(fw._downloads, "download_all_firmware", side_effect=lambda *_a: seen.append(True)),
            _refused_by_conflict_rule("blocked_by_update"),
        ):
            await fw.download_all_firmware("dc")

        assert seen == []


_EMPTY_MD5 = hashlib.md5(b"").hexdigest()


def _writes(content: bytes):
    """A ``download_firmware`` stand-in that writes *content* to the path it is handed, and logs each id asked."""
    asked: list[int] = []

    def download(firmware_id, _filename, dest):
        asked.append(firmware_id)
        with open(dest, "wb") as f:
            f.write(content)

    return download, asked


class TestDownloadAllFirmware:
    @pytest.mark.asyncio
    async def test_a_batch_reads_the_platform_list_once_whatever_it_fetches(self, fake_romm_api, tmp_path):
        """Every download invalidates the listing, so a per-file lookup would read the platform list per file."""
        fake_romm_api.seed_firmware(
            "dc",
            *({"id": index, "file_name": f"bios{index}.bin", "md5_hash": _EMPTY_MD5} for index in (1, 2, 3)),
        )
        fw = _make_firmware_service(
            romm_api=fake_romm_api,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
            tender_folder=str(tmp_path / "Tender"),
        )
        _set_loop(fw, asyncio.get_running_loop())

        result = await fw.download_all_firmware("dc")

        assert result["downloaded"] == 3
        assert [name for name, _args, _kwargs in fake_romm_api.call_log].count("list_platforms") == 1

    @pytest.mark.asyncio
    async def test_downloads_missing_only(self, firmware, fw, tmp_path):
        """A file the platform's store folder already holds, with RomM's checksum, is not fetched again."""
        existing = b"\x00" * 50
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        stored = _stored(firmware.uow, str(tmp_path / "Tender"), "dc", "existing.bin", existing)
        os.makedirs(os.path.dirname(stored))
        with open(stored, "wb") as f:
            f.write(existing)

        firmware_list = [
            {
                "id": 1,
                "file_name": "existing.bin",
                "platform_slug": "dc",
                "file_size_bytes": 50,
                "md5_hash": hashlib.md5(existing).hexdigest(),
            },
            {
                "id": 2,
                "file_name": "missing.bin",
                "platform_slug": "dc",
                "file_size_bytes": 0,
                "md5_hash": _EMPTY_MD5,
            },
        ]

        _set_loop(fw, asyncio.get_running_loop())
        download, asked = _writes(b"")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)),
            patch.object(firmware.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_all_firmware("dc")

        assert result["success"] is True
        assert result["downloaded"] == 1
        assert asked == [2]

    @pytest.mark.asyncio
    async def test_a_file_of_another_platform_under_the_same_name_is_not_fetched(self, firmware, fw, tmp_path):
        """A platform's download fetches only the files RomM states for it.

        ``ps`` here is a platform of its own, not a second folder of ``psx``:
        its copy of the name is never this platform's, even where the first
        download failed and left the store empty.
        """
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        firmware_list = [
            {"id": 1, "file_name": "scph5501.bin", "platform_slug": "psx", "file_size_bytes": 1, "md5_hash": "a" * 32},
            {"id": 2, "file_name": "scph5501.bin", "platform_slug": "ps", "file_size_bytes": 1, "md5_hash": "a" * 32},
        ]
        _set_loop(fw, asyncio.get_running_loop())
        download_called_ids = []

        def failing_download(fw_id, _filename, _dest):
            download_called_ids.append(fw_id)
            raise RommConnectionError("connection refused")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)),
            patch.object(firmware.romm_api, "download_firmware", side_effect=failing_download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_all_firmware("psx")

        assert download_called_ids == [1]
        assert result["message"] == "Downloaded 0 firmware files (1 failed: scph5501.bin)"

    @pytest.mark.asyncio
    async def test_a_file_named_like_a_folder_declaration_lands_in_the_store_and_nowhere_else(
        self, firmware, fw, tmp_path
    ):
        """A library file named like the folder the emulator lists is a file in the store, never that folder.

        The folder is ABSENT here: a download into the BIOS root would write a
        file where the core opens a directory. In the store it is linked only
        where the resolver recognises its content for the folder, which it does
        not here.
        """
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        firmware_list = [
            {"id": 3, "file_name": "bios", "platform_slug": "ps2", "file_size_bytes": 0, "md5_hash": _EMPTY_MD5}
        ]
        resolver = _resolver(fw)
        resolver.declare(
            "bios",
            required_by=[_id("pcsx2_libretro")],
            relative_path="pcsx2/bios",
            present=False,
            declares_directory=True,
            folder=FolderVerdict(satisfied=False),
        )
        _set_loop(fw, asyncio.get_running_loop())
        download, asked = _writes(b"")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)),
            patch.object(firmware.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_all_firmware("ps2")

        assert asked == [3]
        assert result["downloaded"] == 1
        assert (tmp_path / "Tender" / "bios" / "ps2" / "bios").is_file()
        assert not (bios_dir / "pcsx2").exists()


class TestAPlaceThatCouldNotBeLookedAt:
    """No link lands where the resolver could not look; the download still lands in the store.

    A link there could stand where a file nobody could see lies, and a place
    the resolver cannot read is usually one the emulator cannot read either.
    """

    @staticmethod
    def _service(fw, tmp_path):
        bios_dir = tmp_path / "retrodeck" / "bios"
        (bios_dir / "kronos").mkdir(parents=True)
        _resolver(fw).declare(
            "saturn_bios.bin",
            required_by=[_id("kronos_libretro")],
            relative_path="kronos/saturn_bios.bin",
            present=None,
            satisfied=None,
            caveats=("firmware-path-inaccessible",),
        )
        _set_loop(fw, asyncio.get_running_loop())
        listing = [{"id": 3, "file_name": "saturn_bios.bin", "platform_slug": "saturn", "md5_hash": _EMPTY_MD5}]
        return bios_dir, listing

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("press", "args"),
        [
            ("download_all_firmware", ("saturn",)),
            ("download_platform_firmware_file", ("saturn", "saturn_bios.bin")),
        ],
    )
    async def test_a_download_places_no_link_there(self, firmware, fw, tmp_path, press, args):
        bios_dir, listing = self._service(fw, tmp_path)
        download, asked = _writes(b"")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(listing)),
            patch.object(firmware.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await getattr(fw, press)(*args)

        assert asked == [3]
        assert result["placed"] == 0
        assert (tmp_path / "Tender" / "bios" / "saturn" / "saturn_bios.bin").is_file()
        assert not os.path.lexists(bios_dir / "kronos" / "saturn_bios.bin")


class TestDownloadPlatformFirmwareFile:
    """The per-row Download button (#164): one named file, scoped to its platform."""

    @staticmethod
    def _listing() -> list[dict[str, Any]]:
        return [
            {
                "id": 1,
                "file_name": "existing.bin",
                "platform_slug": "dc",
                "file_size_bytes": 0,
                "md5_hash": _EMPTY_MD5,
            },
            {
                "id": 2,
                "file_name": "missing.bin",
                "platform_slug": "dc",
                "file_size_bytes": 0,
                "md5_hash": _EMPTY_MD5,
            },
            {
                "id": 3,
                "file_name": "missing.bin",
                "platform_slug": "n64",
                "file_size_bytes": 0,
                "md5_hash": _EMPTY_MD5,
            },
        ]

    @staticmethod
    def _never(fw_id, _filename, _dest):
        raise AssertionError(f"nothing to fetch, but firmware {fw_id} was requested")

    @pytest.mark.asyncio
    async def test_downloads_the_named_file_of_that_platform(self, firmware, fw, tmp_path):
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        _set_loop(fw, asyncio.get_running_loop())
        download, fetched = _writes(b"")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._listing())),
            patch.object(firmware.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_platform_firmware_file("dc", "missing.bin")

        assert result["success"] is True
        assert result["downloaded"] == 1
        # id 3 carries the same file name under another platform's folder.
        assert fetched == [2]

    @pytest.mark.asyncio
    async def test_the_row_s_library_file_is_fetched_by_its_id_where_two_share_its_name(self, firmware, fw, tmp_path):
        # The row shows the n64 folder's file, matched by content; the platform's
        # own file of that name is another one.
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        _set_loop(fw, asyncio.get_running_loop())
        download, fetched = _writes(b"")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._listing())),
            patch.object(firmware.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_platform_firmware_file("dc", "missing.bin", 3)

        assert result["downloaded"] == 1
        assert fetched == [3]

    @pytest.mark.asyncio
    async def test_an_id_the_listing_no_longer_holds_under_the_name_falls_back_to_the_name(
        self, firmware, fw, tmp_path
    ):
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        _set_loop(fw, asyncio.get_running_loop())
        download, fetched = _writes(b"")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._listing())),
            patch.object(firmware.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            # id 1 is another name's file: an id never fetches a file of another name.
            result = await fw.download_platform_firmware_file("dc", "missing.bin", 1)

        assert result["downloaded"] == 1
        assert fetched == [2]

    @pytest.mark.asyncio
    async def test_a_file_already_in_the_store_is_not_refetched(self, firmware, fw, tmp_path):
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        stored = _stored(firmware.uow, str(tmp_path / "Tender"), "dc", "existing.bin", b"")
        os.makedirs(os.path.dirname(stored))
        open(stored, "wb").close()
        _set_loop(fw, asyncio.get_running_loop())

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._listing())),
            patch.object(firmware.romm_api, "download_firmware", side_effect=self._never),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_platform_firmware_file("dc", "existing.bin")

        assert result["success"] is True
        assert result["downloaded"] == 0
        assert "already here" in result["message"]

    @pytest.mark.asyncio
    async def test_a_name_the_platform_does_not_hold_is_refused(self, firmware, fw, tmp_path):
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        _set_loop(fw, asyncio.get_running_loop())

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._listing())),
            patch.object(firmware.romm_api, "download_firmware", side_effect=self._never),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            download = fw.download_platform_firmware_file("dc", "nowhere.bin")
            with pytest.raises(Refused) as refused:
                await download

        assert refused.value.reason == "not_in_library"

    @pytest.mark.asyncio
    async def test_the_one_fetch_s_own_refusal_reaches_the_caller(self, firmware, fw, tmp_path):
        # One press wants the reason: the single fetch's refusal reaches the
        # caller intact rather than folded into a count of errors.
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        _set_loop(fw, asyncio.get_running_loop())

        def unreachable(_fw_id, _filename, _dest):
            raise ServerUnreachable("RomM is unreachable")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._listing())),
            patch.object(firmware.romm_api, "download_firmware", side_effect=unreachable),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            download = fw.download_platform_firmware_file("dc", "missing.bin")
            with pytest.raises(ServerUnreachable) as refused:
                await download

        assert refused.value.reason == "server_unreachable"
        assert refused.value.message == "RomM is unreachable"

    @pytest.mark.asyncio
    async def test_a_file_named_like_a_folder_declaration_lands_in_the_store(self, firmware, fw, tmp_path):
        """The emulator opens that name as a directory; the library file of that name is a file in the store.

        The folder is ABSENT here: a download into the BIOS root would write a
        file where the core opens a directory. The store keeps it as a file,
        and nothing is written where the emulator looks.
        """
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        firmware_list = [
            {"id": 3, "file_name": "bios", "platform_slug": "ps2", "file_size_bytes": 0, "md5_hash": _EMPTY_MD5}
        ]
        _resolver(fw).declare(
            "bios",
            required_by=[_id("pcsx2_libretro")],
            relative_path="pcsx2/bios",
            present=False,
            declares_directory=True,
            folder=FolderVerdict(satisfied=False),
        )
        _set_loop(fw, asyncio.get_running_loop())
        download, fetched = _writes(b"")

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)),
            patch.object(firmware.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_platform_firmware_file("ps2", "bios")

        assert fetched == [3]
        assert result["downloaded"] == 1
        assert (tmp_path / "Tender" / "bios" / "ps2" / "bios").is_file()
        assert not (bios_dir / "pcsx2").exists()

    @pytest.mark.asyncio
    async def test_a_failed_listing_fetch_propagates(self, firmware, fw):
        _set_loop(fw, asyncio.get_running_loop())
        fw._listing._firmware_cache = None
        with patch.object(firmware.romm_api, "list_platforms", side_effect=OSError("Connection reset")):
            download = fw.download_platform_firmware_file("dc", "missing.bin")
            with pytest.raises(OSError, match="Connection reset"):
                await download

        assert fw._listing._firmware_cache is None


class TestDeleteOneBiosFile:
    """The per-row Delete button's backend half: one file, by its name in RomM.

    It runs the platform-wide worker under a name, so what is pinned here is
    which records it selects — the authority itself is the same loop
    ``TestDeletePlatformBios`` covers. The files here are downloads made into
    RetroDECK's BIOS folder before the store existed, bounded by that folder;
    the store's own files are ``tests/services/test_firmware_store.py``'s.
    """

    @pytest.fixture(autouse=True)
    def _retrodeck_bios(self, fw, tmp_path):
        fw._deletion._retrodeck_folders = FakeRetroDeckFolders(bios=str(tmp_path / "retrodeck" / "bios"))

    @pytest.mark.asyncio
    async def test_deletes_our_own_download_at_the_recorded_path(self, firmware, fw, tmp_path):
        """The record names the file and the path, and both are what is used."""
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        ours = bios_dir / "gc-pal-12.bin"
        ours.write_bytes(b"\x00" * 512)
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="gc",
                file_name="gc-pal-12.bin",
                file_path=str(ours),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=7,
            )
        )

        result = await fw.delete_bios_file("gc", "gc-pal-12.bin")

        assert result["success"] is True
        assert result["deleted_count"] == 1
        assert not ours.exists()
        assert firmware.uow.bios_files.get("gc", "gc-pal-12.bin") is None

    @pytest.mark.asyncio
    async def test_takes_only_the_named_record_of_several(self, firmware, fw, tmp_path):
        """One row's button removes one file, not the platform's other downloads.

        The platform-wide delete is the one that takes them all; this is the
        per-row twin and the name is the whole of the difference between them.
        """
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        for name in ("gc-pal-12.bin", "gc-ntsc-12.bin"):
            path = bios_dir / name
            path.write_bytes(b"\x00" * 32)
            firmware.uow.bios_files.save(
                BiosFile.mark_downloaded(
                    platform_slug="gc",
                    file_name=name,
                    file_path=str(path),
                    downloaded_at="2026-01-01T00:00:00+00:00",
                    firmware_id=1,
                )
            )

        result = await fw.delete_bios_file("gc", "gc-pal-12.bin")

        assert result["deleted_count"] == 1
        assert not (bios_dir / "gc-pal-12.bin").exists()
        assert (bios_dir / "gc-ntsc-12.bin").exists()
        assert firmware.uow.bios_files.get("gc", "gc-ntsc-12.bin") is not None

    @pytest.mark.asyncio
    async def test_deletes_our_downloads_inside_a_declared_folder(self, firmware, fw, tmp_path):
        """PS2's `pcsx2/bios` is a folder declaration, and what we put in it is ours.

        Each file in it is a row of its own, so a recorded download under the
        folder is removed by its name, and a file we never placed beside it is
        not.
        """
        folder = tmp_path / "retrodeck" / "bios" / "pcsx2" / "bios"
        folder.mkdir(parents=True)
        ours = folder / "scph39001.bin"
        ours.write_bytes(b"\x00" * 128)
        theirs = folder / "scph70012.bin"
        theirs.write_bytes(b"\x01" * 128)
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="ps2",
                file_name="scph39001.bin",
                file_path=str(ours),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=3,
            )
        )

        result = await fw.delete_bios_file("ps2", "scph39001.bin")

        assert result["success"] is True
        assert result["deleted_count"] == 1
        assert not ours.exists()
        # The hand-placed file, and the folder itself, are not ours to remove.
        assert theirs.exists()
        assert folder.is_dir()

    @pytest.mark.asyncio
    async def test_a_file_with_no_record_is_never_touched(self, firmware, fw, tmp_path):
        """The GameCube pane's other row: RetroDECK's own copy, present on disk.

        It sits one row above a real download, it is `downloaded: True`, and no
        RomM library can hand it back — authorising on presence deleted exactly
        this file on a real device. With no record naming it, the delete must
        remove nothing and report nothing removed.
        """
        sys_dir = tmp_path / "retrodeck" / "bios" / "dolphin-emu" / "Sys"
        sys_dir.mkdir(parents=True)
        theirs = sys_dir / "codehandler.bin"
        theirs.write_bytes(b"\x01" * 64)

        result = await fw.delete_bios_file("gc", "codehandler.bin")

        assert result["success"] is True
        assert result["deleted_count"] == 0
        assert theirs.exists()

    @pytest.mark.asyncio
    async def test_unlinks_where_the_record_points_not_where_the_row_would(self, firmware, fw, tmp_path):
        """A placement that moved after the download: the record still rules.

        The row's ``local_path`` is recomputed from today's placement, so for a
        file fetched before an emu-atlas bump it names whatever now occupies the
        new destination. Only the recorded path may be unlinked.
        """
        bios_dir = tmp_path / "retrodeck" / "bios"
        (bios_dir / "dc").mkdir(parents=True)
        written = bios_dir / "dc_boot.bin"
        written.write_bytes(b"\x02" * 16)
        elsewhere = bios_dir / "dc" / "dc_boot.bin"
        elsewhere.write_bytes(b"\x03" * 16)
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="dc",
                file_name="dc_boot.bin",
                file_path=str(written),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=9,
            )
        )

        result = await fw.delete_bios_file("dc", "dc_boot.bin")

        assert result["deleted_count"] == 1
        assert not written.exists()
        assert elsewhere.exists()


class TestDeletePlatformBios:
    @pytest.mark.asyncio
    async def test_delete_platform_bios_happy_path(self, firmware, fw, tmp_path):
        """Deleting platform BIOS removes downloaded files and state entries.

        ``check_platform_bios`` returns its ``files`` as ``asdict`` dicts
        (``[asdict(f) for f in files]``), so the mock mirrors that shape — not
        bare ``BiosFileEntry`` objects. Driving the real output shape is what
        guards the delete path against the #750 dict/attribute mismatch.
        """
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        bios_file = bios_dir / "scph5501.bin"
        bios_file.write_bytes(b"\x00" * 512)

        # Pre-populate the BIOS registry via the Unit of Work
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="psx",
                file_name="scph5501.bin",
                file_path=str(bios_file),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=42,
            )
        )

        # Mock check_platform_bios with the REAL output shape: asdict dicts.
        async def mock_check(slug, launching_emulator=None):
            return {
                "needs_bios": True,
                "server_count": 1,
                "local_count": 1,
                "all_downloaded": True,
                "files": [
                    asdict(
                        BiosFileEntry(
                            file_name="scph5501.bin",
                            downloaded=True,
                            local_path=str(bios_file),
                            declared_path="scph5501.bin",
                            description="PS1 BIOS",
                            wanted="needed",
                            required_by_active=True,
                            cores={},
                            used_by_active=True,
                        )
                    ),
                ],
            }

        fw.check_platform_bios = mock_check
        fw._deletion._retrodeck_folders = FakeRetroDeckFolders(bios=str(bios_dir))

        result = await fw.delete_platform_bios("psx")
        assert result["success"] is True
        assert result["deleted_count"] == 1
        assert not bios_file.exists()
        # Verify BIOS record removed from the registry
        assert firmware.uow.bios_files.get("psx", "scph5501.bin") is None

    @pytest.mark.asyncio
    async def test_delete_platform_bios_real_check_output_shape(self, firmware, tmp_path):
        """Regression for #750: delete works against the real asdict dict shape.

        Drives ``delete_platform_bios`` end-to-end through the *real*
        ``check_platform_bios`` (server-offline registry fallback), so the
        ``files`` list is the genuine ``[asdict(f) for f in files]`` payload
        the use case hands to the removal worker (``_delete_recorded_io``).
        Before the fix that worker read ``f.downloaded`` / ``f.local_path`` /
        ``f.file_name`` as
        attributes on those dicts, raising ``AttributeError`` in the executor
        and deleting nothing — the "Failed to delete BIOS files" the modal showed.
        """
        bios_dir = tmp_path / "bios"
        bios_dir.mkdir(parents=True)
        # One downloaded file (store sees it) + one never-downloaded.
        store = FakeFirmwareFileStore({str(bios_dir / "scph5501.bin"): b"\x00" * 512})

        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
        )
        _set_loop(fw, asyncio.get_running_loop())
        _declare(fw, ("scph5501.bin", "PS1 US BIOS", True), ("scph5502.bin", "PS1 EU BIOS", True))

        # The downloaded file has a BiosFile record to prune (filed under "psx").
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="psx",
                file_name="scph5501.bin",
                file_path=str(bios_dir / "scph5501.bin"),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=42,
            )
        )

        firmware_list = [
            {
                "id": 1,
                "file_name": "scph5501.bin",
                "platform_slug": "psx",
                "file_size_bytes": 512,
                "md5_hash": "",
            },
            {
                "id": 2,
                "file_name": "scph5502.bin",
                "platform_slug": "psx",
                "file_size_bytes": 512,
                "md5_hash": "",
            },
        ]
        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)):
            # Precondition: files really are dicts, not BiosFileEntry objects —
            # subscripting a string key would raise on a BiosFileEntry instance.
            status: dict[str, Any] = await fw.check_platform_bios("psx")
            assert status["files"][0]["file_name"] == "scph5501.bin"
            assert status["files"][0]["downloaded"] is True

            result = await fw.delete_platform_bios("psx")

        assert isinstance(result, dict)
        # (b) success/deleted_count response is correct: only the one downloaded.
        assert result["success"] is True
        assert result["deleted_count"] == 1
        # (a) the downloaded file is removed via the firmware file store...
        assert str(bios_dir / "scph5501.bin") not in store.files
        # ...and its BiosFile record is pruned.
        assert firmware.uow.bios_files.get("psx", "scph5501.bin") is None

    @staticmethod
    def _gamecube_service(firmware, tmp_path):
        """A GameCube platform whose BIOS folder holds one of each kind of file.

        ``IPL.bin`` is in the RomM library; ``codehandler.bin`` is what RetroDECK
        ships beside its RetroArch component, wanted by the same core and held by
        no library. Both are declared, so both reach the delete as rows marked
        downloaded — which is the whole of what the old guard looked at.

        No download record is written here. Which file Tender is supposed to
        have fetched is the axis under test, so each case states its own records
        (``_record_download``) — including the one that deliberately states none.
        """
        bios_dir = tmp_path / "bios"
        shipped = os.path.join(str(bios_dir), "dolphin-emu", "Sys", "codehandler.bin")
        ipl = os.path.join(str(bios_dir), "IPL.bin")
        store = FakeFirmwareFileStore({shipped: b"\x00" * 8, ipl: b"\x00" * 8})

        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=_test_core_info(),
        )
        _set_loop(fw, asyncio.get_running_loop())
        _resolver(fw).declare("IPL.bin", required_by=[_id(_TEST_CORE)], description="GameCube IPL")
        _resolver(fw).declare(
            "codehandler.bin",
            optional_for=[_id(_TEST_CORE)],
            relative_path="dolphin-emu/Sys/codehandler.bin",
            description="Dolphin code handler",
        )
        return fw, store, shipped, ipl

    @staticmethod
    def _gamecube_listing() -> list[dict[str, Any]]:
        return [
            {
                "id": 7,
                "file_name": "IPL.bin",
                "platform_slug": "gc",
                "file_size_bytes": 8,
                "md5_hash": "",
            },
        ]

    @staticmethod
    def _record_download(firmware, file_name: str, file_path: str) -> None:
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="gc",
                file_name=file_name,
                file_path=file_path,
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=7,
            )
        )

    @pytest.mark.asyncio
    async def test_an_emulator_shipped_file_survives_the_delete(self, firmware, tmp_path):
        """Deleting a platform's BIOS never touches a file Tender did not fetch.

        The reported data loss: pressing Delete BIOS on GameCube removed
        ``dolphin-emu/Sys/codehandler.bin``, which RetroDECK ships with its own
        RetroArch component. It is on the list because an installed core asks for
        it, and its ``downloaded`` flag is nothing but ``os.path.exists`` — so the
        old guard, which read that flag alone, deleted a file nothing here could
        ever fetch back.
        """
        fw, store, shipped, ipl = self._gamecube_service(firmware, tmp_path)
        self._record_download(firmware, "IPL.bin", ipl)

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._gamecube_listing())):
            status: dict[str, Any] = await fw.check_platform_bios("gc")
            # Precondition: the shipped file reaches the delete looking deletable.
            row = next(f for f in status["files"] if f["file_name"] == "codehandler.bin")
            assert row["downloaded"] is True

            result = await fw.delete_platform_bios("gc")

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["deleted_count"] == 1
        assert shipped in store.files
        # Tender's own download still goes, record and all.
        assert ipl not in store.files
        assert firmware.uow.bios_files.get("gc", "IPL.bin") is None

    @pytest.mark.asyncio
    async def test_a_file_with_no_download_record_survives_the_delete(self, firmware, tmp_path):
        """A hand-placed file is not Tender's to delete, even under a server name.

        This file IS in the RomM library, so it looks like every other
        downloadable row and a guard reading the library would remove it.
        Nothing here put it on disk, so nothing here removes it.
        """
        fw, store, shipped, ipl = self._gamecube_service(firmware, tmp_path)

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._gamecube_listing())):
            status: dict[str, Any] = await fw.check_platform_bios("gc")
            row = next(f for f in status["files"] if f["file_name"] == "IPL.bin")
            assert row["downloaded"] is True
            assert row["on_server"] is True

            result = await fw.delete_platform_bios("gc")

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["deleted_count"] == 0
        assert ipl in store.files
        assert shipped in store.files

    @pytest.mark.asyncio
    async def test_our_own_download_still_goes_after_it_leaves_the_library(self, firmware, tmp_path):
        """A file we downloaded stays deletable once RomM no longer holds it.

        The library is not the authority here — the record is. Dropping a
        firmware file from RomM flips its row to ``on_server: False``, and a
        guard that also demanded ``on_server`` would strand our own download on
        disk with nothing in the UI able to remove it. The row is otherwise
        indistinguishable from the emulator-shipped one beside it; only the
        record tells them apart, and only the record decides.
        """
        fw, store, shipped, ipl = self._gamecube_service(firmware, tmp_path)
        self._record_download(firmware, "IPL.bin", ipl)

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms([])):
            status: dict[str, Any] = await fw.check_platform_bios("gc")
            # Both rows now come off the machine's demand, not the listing.
            assert {f["file_name"]: f["on_server"] for f in status["files"]} == {
                "IPL.bin": False,
                "codehandler.bin": False,
            }

            result = await fw.delete_platform_bios("gc")

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["deleted_count"] == 1
        assert ipl not in store.files
        assert firmware.uow.bios_files.get("gc", "IPL.bin") is None
        # The one without a record is still not ours.
        assert shipped in store.files

    @pytest.mark.asyncio
    async def test_a_moved_placement_does_not_redirect_the_delete(self, firmware, tmp_path):
        """The delete unlinks where the download wrote, never where the placement now points.

        ``codehandler.bin`` was fetched while nothing declared it, so it landed
        flat in the BIOS root and the record says so. A later emu-atlas bump
        gives the file a subdirectory — the very path RetroDECK's own copy
        occupies. A delete that recomputed the destination would still match our
        record by name, unlink RetroDECK's file, drop the row, and leave ours on
        disk with nothing left that could remove it.
        """
        fw, store, shipped, _ipl = self._gamecube_service(firmware, tmp_path)
        flat = os.path.join(str(tmp_path / "bios"), "codehandler.bin")
        store.files[flat] = b"\x00" * 8
        self._record_download(firmware, "codehandler.bin", flat)

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._gamecube_listing())):
            status: dict[str, Any] = await fw.check_platform_bios("gc")
            # Precondition: today's placement puts the row on the shipped copy.
            row = next(f for f in status["files"] if f["file_name"] == "codehandler.bin")
            assert row["local_path"] == shipped

            result = await fw.delete_platform_bios("gc")

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["deleted_count"] == 1
        assert flat not in store.files
        assert shipped in store.files
        assert firmware.uow.bios_files.get("gc", "codehandler.bin") is None

    @pytest.mark.asyncio
    async def test_a_record_whose_file_is_gone_is_pruned_without_a_deletion(self, firmware, tmp_path):
        """A row standing over an absent file is dropped, counted as nothing, reported as no error.

        The user deleted it by hand, or a previous run took it. There is nothing
        to unlink, and leaving the row would keep offering a file that is not
        there.
        """
        fw, store, shipped, ipl = self._gamecube_service(firmware, tmp_path)
        self._record_download(firmware, "IPL.bin", ipl)
        del store.files[ipl]

        with patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(self._gamecube_listing())):
            result = await fw.delete_platform_bios("gc")

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["deleted_count"] == 0
        assert firmware.uow.bios_files.get("gc", "IPL.bin") is None
        assert shipped in store.files

    @pytest.mark.asyncio
    async def test_a_record_under_the_platform_s_old_folder_name_is_deleted_once_with_it(self, firmware, tmp_path):
        """A record filed under the server folder ``ps`` names the file ``psx`` records too.

        The first read of the listing drops it as the duplicate it is, so the
        platform's delete takes the file once and leaves no record behind under
        either name.
        """
        bios_dir = tmp_path / "bios"
        path = os.path.join(str(bios_dir), "scph5501.bin")
        store = FakeFirmwareFileStore({path: b"\x00" * 8})
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_file_store=store,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=_test_core_info(),
        )
        _set_loop(fw, asyncio.get_running_loop())
        for slug in ("psx", "ps"):
            firmware.uow.bios_files.save(
                BiosFile.mark_downloaded(
                    platform_slug=slug,
                    file_name="scph5501.bin",
                    file_path=path,
                    downloaded_at="2026-01-01T00:00:00+00:00",
                    firmware_id=1,
                )
            )

        listing = _platforms([{"id": 1, "file_name": "scph5501.bin", "platform_slug": "psx"}], folders={"psx": "ps"})
        with patch.object(firmware.romm_api, "list_platforms", return_value=listing):
            await fw.get_platform_firmware_status("psx")
            result = await fw.delete_platform_bios("psx")

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["deleted_count"] == 1
        assert path not in store.files
        assert firmware.uow.bios_files.get("psx", "scph5501.bin") is None
        assert firmware.uow.bios_files.get("ps", "scph5501.bin") is None


class TestCheckPlatformBiosRequired:
    @pytest.mark.asyncio
    async def test_required_counts(self, fw, tmp_path):
        """check_platform_bios includes required_count/required_downloaded."""
        firmware_list = [
            {
                "id": 1,
                "file_name": "required1.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
            {
                "id": 2,
                "file_name": "required2.bin",
                "platform_slug": "dc",
                "file_size_bytes": 200,
                "md5_hash": "",
            },
            {
                "id": 3,
                "file_name": "optional1.bin",
                "platform_slug": "dc",
                "file_size_bytes": 300,
                "md5_hash": "",
            },
        ]

        _declare(
            fw,
            ("required1.bin", "Required BIOS 1", True),
            ("required2.bin", "Required BIOS 2", True),
            ("optional1.bin", "Optional firmware", False),
        )
        _resolver_reads(fw, tmp_path)

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        result = await fw.check_platform_bios("dc")
        assert result["needs_bios"] is True
        assert result["required_count"] == 2
        assert result["required_downloaded"] == 0
        assert result["server_count"] == 3
        # No required file downloaded → bios_level 'missing' (single source of
        # truth: domain.bios_status.compute_bios_level, threaded off this payload, #461).
        assert result["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_only_the_files_romm_states_for_the_platform_are_its_library_rows(self, fw):
        """A file belongs to the platform RomM states for it, never to one whose name its folder resembles.

        Another platform's file still fills a requirement here, whichever
        platform RomM files it under; one that fills nothing here is no row of
        this platform's library section.
        """
        firmware_list = [
            {"id": 1, "file_name": "scph5501.bin", "platform_slug": "psx", "file_size_bytes": 1},
            {"id": 2, "file_name": "scph5501.bin", "platform_slug": "ps", "file_size_bytes": 1},
            {"id": 3, "file_name": "scph5502.bin", "platform_slug": "ps", "file_size_bytes": 1},
            {"id": 4, "file_name": "scph7001.bin", "platform_slug": "ps", "file_size_bytes": 1},
        ]
        _declare(fw, ("scph5501.bin", "PS1 BIOS (US)", True), ("scph5502.bin", "PS1 BIOS (EU)", False))
        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        result = await fw.check_platform_bios("psx")

        assert (result["server_count"], result["required_count"]) == (2, 1)
        assert sorted(f["file_name"] for f in result["files"] if f["on_server"]) == ["scph5501.bin", "scph5502.bin"]
        assert [f["file_name"] for f in result["files"] if f["declared_by"] is None] == []

    @pytest.mark.asyncio
    async def test_all_required_downloaded(self, fw, tmp_path):
        """When all required files are downloaded, counts reflect this."""
        # Flat in the bios root: the declarations below name no subdirectory.
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        (bios_dir / "required1.bin").write_bytes(b"\x00" * 100)
        (bios_dir / "required2.bin").write_bytes(b"\x00" * 200)
        _resolver_reads(fw, bios_dir)
        # Leave optional1.bin not downloaded

        firmware_list = [
            {
                "id": 1,
                "file_name": "required1.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
            {
                "id": 2,
                "file_name": "required2.bin",
                "platform_slug": "dc",
                "file_size_bytes": 200,
                "md5_hash": "",
            },
            {
                "id": 3,
                "file_name": "optional1.bin",
                "platform_slug": "dc",
                "file_size_bytes": 300,
                "md5_hash": "",
            },
        ]

        _declare(
            fw,
            ("required1.bin", "Required BIOS 1", True),
            ("required2.bin", "Required BIOS 2", True),
            ("optional1.bin", "Optional firmware", False),
        )

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        with patch.object(_resolver(fw), "bios_root", str(bios_dir)):
            result = await fw.check_platform_bios("dc")
        assert result["needs_bios"] is True
        assert result["required_count"] == 2
        assert result["required_downloaded"] == 2
        assert result["local_count"] == 2
        # all_downloaded is False because optional1.bin is not downloaded
        assert result["all_downloaded"] is False
        # All required files present → bios_level 'ok' (the required-file branch
        # of compute_bios_level wins over all_downloaded, #461).
        assert result["bios_level"] == "ok"

    @pytest.mark.asyncio
    async def test_some_required_downloaded_bios_level_missing(self, fw, tmp_path):
        """One of two required files downloaded → the resolver says not met: 'missing', with the count."""
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        (bios_dir / "required1.bin").write_bytes(b"\x00" * 100)
        _resolver_reads(fw, bios_dir)
        # Leave required2.bin not downloaded

        firmware_list = [
            {
                "id": 1,
                "file_name": "required1.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
            {
                "id": 2,
                "file_name": "required2.bin",
                "platform_slug": "dc",
                "file_size_bytes": 200,
                "md5_hash": "",
            },
        ]

        _declare(fw, ("required1.bin", "Required BIOS 1", True), ("required2.bin", "Required BIOS 2", True))

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        with patch.object(_resolver(fw), "bios_root", str(bios_dir)):
            result = await fw.check_platform_bios("dc")
        assert result["needs_bios"] is True
        assert result["required_count"] == 2
        assert result["required_downloaded"] == 1
        # One required file present, the other missing → not met, 'missing'.
        assert result["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_per_file_required_and_description(self, fw, tmp_path):
        """Individual files carry the declaring core's required flag and description."""
        firmware_list = [
            {"id": 1, "file_name": "bios.bin", "platform_slug": "dc", "file_size_bytes": 100, "md5_hash": ""},
        ]

        _declare(fw, ("bios.bin", "Dreamcast BIOS", True))

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        result = await fw.check_platform_bios("dc")
        assert result["files"][0]["required_by_active"] is True
        assert result["files"][0]["description"] == "Dreamcast BIOS"

    @pytest.mark.asyncio
    async def test_files_no_emulator_asks_for_are_answered_not_unknown(self, fw, tmp_path):
        """Every emulator read and two files unclaimed → they are answered for.

        The ``fw`` fixture's ES-DE offers exactly the libretro core ``_declare``
        attributes its wants to, so the scope is real and complete — which is
        what makes ``not_needed`` the honest answer here. On an empty scope the
        same listing must read ``unknown``
        (:meth:`test_a_platform_with_no_libretro_core_answers_for_nothing`), and
        this assertion would hold vacuously.
        """
        firmware_list = [
            {
                "id": 1,
                "file_name": "known.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
            {
                "id": 2,
                "file_name": "mystery.bin",
                "platform_slug": "dc",
                "file_size_bytes": 200,
                "md5_hash": "",
            },
            {
                "id": 3,
                "file_name": "alien.bin",
                "platform_slug": "dc",
                "file_size_bytes": 300,
                "md5_hash": "",
            },
        ]

        # Only "known.bin" is declared by an installed emulator.
        _declare(fw, ("known.bin", "Known BIOS", True))

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        result = await fw.check_platform_bios("dc")
        assert result["needs_bios"] is True
        assert result["unknown_count"] == 0
        assert result["known_count"] == 1
        wanted = {f["file_name"]: f["wanted"] for f in result["files"]}
        assert wanted["known.bin"] == "needed"
        assert wanted["mystery.bin"] == "not_needed"
        assert wanted["alien.bin"] == "not_needed"

    @pytest.mark.asyncio
    async def test_the_same_files_read_unknown_when_an_emulator_could_not_be_asked(self, tmp_path):
        """The identical listing, answered against a platform with an unread emulator.

        Same server files, same declarations — only the reading changes, and the
        two unclaimed files go from a finished "nothing needs these" to "nothing
        could be established". Under the collapsed boolean both said the same
        thing.
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {"id": 1, "file_name": "known.bin", "platform_slug": "dc", "file_size_bytes": 1, "md5_hash": ""},
                {
                    "id": 2,
                    "file_name": "mystery.bin",
                    "platform_slug": "dc",
                    "file_size_bytes": 1,
                    "md5_hash": "",
                },
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            core_info=FakeCoreInfoProvider(options=[libretro_option("flycast_libretro", "Flycast")]),
            firmware_resolver=FakeFirmwareResolver(unread_emulators=frozenset({_id("flycast_libretro")})),
        )
        _declare(fw, ("known.bin", "Known BIOS", True))
        _inline_executor(fw)

        result = await fw.check_platform_bios("dc")

        wanted = {f["file_name"]: f["wanted"] for f in result["files"]}
        assert wanted["known.bin"] == "needed"
        assert wanted["mystery.bin"] == "unknown"
        assert result["unknown_count"] == 1

    @pytest.mark.asyncio
    async def test_a_platform_with_no_libretro_core_answers_for_nothing(self, tmp_path):
        """A standalone-only system reads grey, never a green "nothing needs these".

        ES-DE is readable and offers this system one emulator — a standalone
        one, so the libretro scope is empty. 35 of ES-DE's 172 systems are in
        that position, ``ps3`` (RPCS3) among them, and it is a mapped RomM
        platform: answering the empty scope as a complete reading classified
        every server file ``not_needed``, put ``required_count`` at 0 and
        reported a green "Nothing required" over firmware RPCS3 will not boot
        without.
        """
        from tests.fakes.fake_core_info_provider import standalone_option

        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {"id": 1, "file_name": "PS3UPDAT.PUP", "platform_slug": "ps3", "file_size_bytes": 1},
            ]
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            core_info=FakeCoreInfoProvider(options=[standalone_option("%EMULATOR_RPCS3% %ROM%", "RPCS3")]),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        _inline_executor(fw)

        result = await fw.check_platform_bios("ps3")

        assert [f["wanted"] for f in result["files"]] == ["unknown"]
        assert result["known_count"] == 0
        assert result["unknown_count"] == 1
        assert result["bios_level"] == "unknown"


class TestCheckPlatformBiosSlugNormalization:
    """check_platform_bios resolves slug→system for the active-core INPUT, keeps the RomM slug for BIOS.

    The library's firmware and the download records are keyed on the RomM
    platform slug (ADR-0010 §4). The active-core read used
    to filter the firmware list must instead receive the resolved RetroDECK system.
    The method no longer reads ``get_available_cores`` — core info is served via the
    dedicated ``get_platform_core_info`` path (#923).
    """

    @pytest.mark.parametrize(
        ("slug", "system"),
        [
            ("dc", "dreamcast"),
            ("sms", "mastersystem"),
            ("neo-geo-pocket", "ngp"),
            ("gba", "gba"),  # identity: slug already equals system
        ],
    )
    @pytest.mark.asyncio
    async def test_resolves_system_for_cores_keeps_raw_slug_for_bios(self, slug, system):
        core_info = FakeCoreInfoProvider(
            active_core=("flycast_libretro", "Flycast"),
            available_cores=[{"label": "Flycast", "so": "flycast_libretro"}],
        )
        resolver = FakePlatformSystems(mapping={"dc": "dreamcast", "sms": "mastersystem", "neo-geo-pocket": "ngp"})
        fw = _make_firmware_service(core_info=core_info, platform_systems=resolver)

        firmware_list = [
            {
                "id": 1,
                "file_name": "boot.bin",
                "platform_slug": slug,
                "file_size_bytes": 512,
                "md5_hash": "",
            },
        ]
        _declare(fw, ("boot.bin", "Boot", True))

        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        result = await fw.check_platform_bios(slug)

        # The RomM slug matched the file's platform, so a file is found.
        assert result["needs_bios"] is True
        assert result["server_count"] == 1
        # The core read seam received the NORMALIZED system.
        assert core_info.emulator_options_calls == [system]
        assert resolver.calls == [(slug, None)]


class TestCheckPlatformBiosNoCoreFields:
    """check_platform_bios returns BIOS status only — never the picker's data (#923).

    Which emulators a platform offers, and which of them it launches with, are
    served through the dedicated ``get_platform_core_info`` path and never from
    the BIOS payload: ``active_core`` and ``available_cores`` are absent from
    every branch.

    The one field that does ride along is ``active_core_label``, and it is not an
    exception to that rule but the answer's own: it is the NAME of the pick THIS
    payload's counts were filtered by, so the sentence over them can say which
    emulator they are about without a second resolution of the question. It is
    stamped wherever there is an answer to name, the answer that no BIOS is
    needed included.
    """

    @pytest.mark.asyncio
    async def test_server_reachable_no_firmware_omits_core_fields(self):
        """Server reachable, no firmware for the platform → no core fields."""
        core_info = FakeCoreInfoProvider(
            active_core=("genesisplusgx_libretro", "Genesis Plus GX"),
            available_cores=[
                {"label": "Genesis Plus GX", "core_so": "genesisplusgx_libretro"},
                {"label": "PicoDrive", "core_so": "picodrive_libretro"},
            ],
        )
        fw = _make_firmware_service(core_info=core_info)
        # The launching emulator was read and declares nothing here.
        _resolver(fw).describe_emulator(_id("genesisplusgx_libretro"))

        # No firmware on the server matches the platform and nothing is
        # declared, so there are no rows: the empty-files needs_bios=False branch.
        _stub_listing(fw, [])
        _inline_executor(fw)

        result = await fw.check_platform_bios("sms")

        assert result["needs_bios"] is False
        assert result["bios_level"] == "ok"
        assert result["files"] == []
        assert "active_core" not in result
        assert "available_cores" not in result

    @pytest.mark.asyncio
    async def test_offline_with_no_demand_omits_core_fields(self, firmware, fw):
        """Server unreachable and no emulator wants anything → no core fields."""
        core_info = FakeCoreInfoProvider(
            active_core=("genesisplusgx_libretro", "Genesis Plus GX"),
            available_cores=[
                {"label": "Genesis Plus GX", "core_so": "genesisplusgx_libretro"},
                {"label": "PicoDrive", "core_so": "picodrive_libretro"},
            ],
        )
        fw = _make_firmware_service(romm_api=firmware.romm_api, core_info=core_info)
        # The launching emulator was read and declares nothing here.
        _resolver(fw).describe_emulator(_id("genesisplusgx_libretro"))
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await fw.check_platform_bios("sms")

        # No emulator wants anything and the whole scope was read, so "needs
        # none" is a real answer even with the listing unavailable — and it
        # still carries no core fields.
        assert result["needs_bios"] is False
        assert result["bios_level"] == "ok"
        assert "active_core" not in result
        assert "available_cores" not in result

    @pytest.mark.asyncio
    async def test_needs_bios_true_omits_core_fields(self, tmp_path):
        """needs_bios=True branch carries BIOS counts/files only — no core fields."""
        core_info = FakeCoreInfoProvider(
            active_core=("gpsp_libretro", "gpSP"),
            available_cores=[
                {"label": "gpSP", "so": "gpsp_libretro"},
                {"label": "mGBA", "so": "mgba_libretro"},
            ],
        )
        fw = _make_firmware_service(core_info=core_info)

        firmware_list = [
            {
                "id": 1,
                "file_name": "gba_bios.bin",
                "platform_slug": "gba",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
        ]
        _declare(fw, ("gba_bios.bin", "GBA BIOS", True))
        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        with patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")):
            result = await fw.check_platform_bios("gba")

        assert result["needs_bios"] is True
        assert result["server_count"] == 1
        assert "active_core" not in result
        assert "available_cores" not in result
        # ES-DE offers this platform no command here, so no pick could be made
        # and there is no name to print — stated as an empty answer rather than
        # left out, because the surface words the nameless sentence for it.
        assert result["active_core_label"] is None


class TestDownloadRequiredFirmware:
    @pytest.mark.asyncio
    async def test_downloads_required_only(self, firmware, fw, tmp_path):
        """Only downloads files marked required, skips optional."""

        firmware_list = [
            {
                "id": 1,
                "file_name": "required.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
            {
                "id": 2,
                "file_name": "optional.bin",
                "platform_slug": "dc",
                "file_size_bytes": 200,
                "md5_hash": "",
            },
        ]

        fw._config.core_info.active_core = (_TEST_CORE, "Test Core")
        _declare(fw, ("required.bin", "Required BIOS", True), ("optional.bin", "Optional firmware", False))
        _resolver_reads(fw, tmp_path / "bios")

        _set_loop(fw, asyncio.get_running_loop())

        download_called_ids = []

        def fake_download_firmware(_platform_slug, firmware):
            download_called_ids.append(firmware["id"])
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)),
            patch.object(fw._store, "do_fetch", side_effect=fake_download_firmware),
        ):
            result = await fw.download_required_firmware("dc")

        assert result["success"] is True
        assert result["downloaded"] == 1
        assert 1 in download_called_ids
        assert 2 not in download_called_ids

    @pytest.mark.asyncio
    async def test_a_platform_whose_pick_has_no_identity_fetches_nothing(self):
        """With no emulator identified, no emulator's requirement is guessed at.

        The bare fake resolves the pick to no identity; the file is one an
        emulator of the platform requires, and the answer is the one an emulator
        requiring nothing gets.
        """
        fw = _make_firmware_service(core_info=FakeCoreInfoProvider(active_core=("flycast_libretro", "Flycast")))
        _resolver(fw).declare("boot.bin", required_by=[_id("flycast_libretro")])
        _stub_listing(fw, [{"id": 1, "file_name": "boot.bin", "platform_slug": "dc", "md5_hash": ""}])
        _inline_executor(fw)

        with patch.object(fw._store, "do_fetch") as fetch:
            result = await fw.download_required_firmware("dc")

        fetch.assert_not_called()
        assert result == {
            "success": True,
            "message": "Downloaded 0 required firmware files",
            "downloaded": 0,
            "failed": [],
            "backups": [],
            "placed": 0,
            "left_in_place": 0,
            "removed": 0,
            "link_failures": [],
        }

    @staticmethod
    async def _download_under(platform_core: str | None) -> tuple[list[int], int]:
        """Fetch the required set for a gba platform picked *platform_core*, and the pane's count.

        gpSP will not run without the GBA BIOS and mGBA will, so which of the two
        the platform resolves to decides the whole answer.
        """
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 1,
                    "file_name": "gba_bios.bin",
                    "platform_slug": "gba",
                    "file_size_bytes": 100,
                    "md5_hash": "",
                },
            ]
        )
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "gba_bios.bin",
            required_by=[_id("gpsp_libretro")],
            optional_for=[_id("mgba_libretro")],
            description="GBA BIOS",
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(
                options=[libretro_option("gpsp_libretro", "gpSP"), libretro_option("mgba_libretro", "mGBA")],
            ),
            platform_core_reader=FakePlatformCoreReader({"gba": platform_core} if platform_core is not None else None),
            retrodeck_folders=FakeRetroDeckFolders(bios="/retrodeck/bios"),
        )
        _inline_executor(fw)

        asked: list[int] = []

        def fake_download_firmware(_platform_slug, firmware):
            asked.append(firmware["id"])
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        with patch.object(fw._store, "do_fetch", side_effect=fake_download_firmware):
            await fw.download_required_firmware("gba")
        page = await fw.check_platform_bios("gba")
        return asked, page["required_count"]

    @pytest.mark.asyncio
    async def test_it_fetches_what_the_platforms_own_pick_calls_required(self):
        """The button and the count above it read one emulator.

        The button sits on the pane that states the requirement, so a resolution
        of its own would offer to fetch a set the page never showed — or, under
        the pick that needs nothing, offer to fetch anything at all.
        """
        assert await self._download_under(None) == ([1], 1)
        assert await self._download_under("gpSP") == ([1], 1)
        assert await self._download_under("mGBA") == ([], 0)

    @pytest.mark.asyncio
    async def test_a_file_whose_place_could_not_be_looked_at_is_neither_counted_nor_fetched(self):
        """A download there could overwrite a file nobody could see.

        gpSP requires the file, so without the unreadable place it would be both
        counted on the button and fetched by it.
        """
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [{"id": 1, "file_name": "gba_bios.bin", "platform_slug": "gba", "file_size_bytes": 100}]
        )
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "gba_bios.bin",
            required_by=[_id("gpsp_libretro")],
            present=None,
            satisfied=None,
            caveats=("firmware-path-inaccessible",),
        )
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(options=[libretro_option("gpsp_libretro", "gpSP")]),
        )
        _inline_executor(fw)
        asked: list[int] = []

        def fake_download_firmware(_platform_slug, firmware):
            asked.append(firmware["id"])
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        entries = await _entries(fw, "gba")
        with patch.object(fw._store, "do_fetch", side_effect=fake_download_firmware):
            await fw.download_required_firmware("gba")

        platform = entries["gba"]
        assert [f["fetch_for_required"] for f in platform["files"]] == [False]
        assert asked == []

    @staticmethod
    async def _download_group(**service) -> tuple[set[int], set[int]]:
        """What "Download required" fetches for a grouped PlayStation, and the rows the page counts for it."""
        fw = _one_of_service(**service)
        _resolver_reads(fw, "/retrodeck/bios")
        fetched: set[int] = set()

        def fake_download_firmware(_platform_slug, firmware):
            fetched.add(firmware["id"])
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        entries = await _entries(fw, "psx")
        with patch.object(fw._store, "do_fetch", side_effect=fake_download_firmware):
            await fw.download_required_firmware("psx")
        platform = entries["psx"]
        counted = {f["id"] for f in platform["files"] if f["fetch_for_required"] and f["on_server"]}
        return fetched, counted

    @pytest.mark.asyncio
    async def test_it_fetches_the_options_of_the_regions_not_yet_covered(self):
        """NTSC-U is in place, so the Japanese and European images are what the group still lacks."""
        fetched, counted = await self._download_group(present=("scph5501.bin",))

        assert fetched == {1, 3}
        assert counted == {1, 3}

    @pytest.mark.asyncio
    async def test_it_fetches_nothing_of_a_covered_group(self):
        """SwanStation's found image serves every region; the three named images are not fetched."""
        fetched, counted = await self._download_group(launching=_PSX_CORE)

        assert fetched == set()
        assert counted == set()

    @pytest.mark.asyncio
    async def test_with_nothing_in_place_every_option_is_fetched(self):
        fetched, counted = await self._download_group()

        assert fetched == counted == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_resolves_system_for_active_core_keeps_raw_slug_for_filter(self):
        """Active-core read gets the NORMALIZED system; the firmware filter stays raw.

        ``download_required_firmware`` keys the library filter on the RomM
        platform slug (ADR-0010 §4) but must resolve the slug to a
        RetroDECK system before reading the platform's emulators (ADR-0010 §2) so
        the per-core required flags use the correct active core.
        """
        core_info = _DeclaredDefaultCoreInfo(
            active_core=("flycast_libretro", "Flycast"), options=[libretro_option("flycast_libretro", "Flycast")]
        )
        resolver = FakePlatformSystems(mapping={"dc": "dreamcast"})
        fw = _make_firmware_service(
            core_info=core_info,
            platform_systems=resolver,
            retrodeck_folders=FakeRetroDeckFolders(bios="/retrodeck/bios"),
        )

        firmware_list = [
            {
                "id": 1,
                "file_name": "boot.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
        ]
        # The requiring core is the one the NORMALIZED system resolves to, in
        # Tender's own bare identifier space (no ".so").
        _resolver(fw).declare("boot.bin", required_by=[_id("flycast_libretro")], description="Boot")
        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

        download_called_ids: list[int] = []

        def fake_download_firmware(_platform_slug, firmware):
            download_called_ids.append(firmware["id"])
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        with patch.object(fw._store, "do_fetch", side_effect=fake_download_firmware):
            result = await fw.download_required_firmware("dc")

        # The RomM slug matched the file's platform, so the file is considered.
        # The per-core required flag (keyed on the active core from the NORMALIZED
        # system) marked it required, so it was downloaded.
        assert result["downloaded"] == 1
        assert download_called_ids == [1]
        # The core read the required-only filter resolves through received the
        # NORMALIZED system, not the raw slug.
        assert core_info.emulator_options_calls == ["dreamcast"]
        assert resolver.calls == [("dc", None)]

    @pytest.mark.asyncio
    async def test_skips_already_downloaded_required(self, firmware, fw, tmp_path):
        """Skips required files that are already downloaded."""

        # Pre-create one required file so it's skipped (flat in bios root)
        bios_dir = tmp_path / "retrodeck" / "bios"
        bios_dir.mkdir(parents=True)
        (bios_dir / "existing.bin").write_bytes(b"\x00" * 100)

        firmware_list = [
            {
                "id": 1,
                "file_name": "existing.bin",
                "platform_slug": "dc",
                "file_size_bytes": 100,
                "md5_hash": "",
            },
            {
                "id": 2,
                "file_name": "missing.bin",
                "platform_slug": "dc",
                "file_size_bytes": 200,
                "md5_hash": "",
            },
        ]

        _declare(fw, ("existing.bin", "Already downloaded", True), ("missing.bin", "Not yet downloaded", True))

        _set_loop(fw, asyncio.get_running_loop())

        download_called_ids = []

        def fake_download_firmware(_platform_slug, firmware):
            download_called_ids.append(firmware["id"])
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms(firmware_list)),
            patch.object(fw._store, "do_fetch", side_effect=fake_download_firmware),
            patch.object(_resolver(fw), "bios_root", str(bios_dir)),
        ):
            result = await fw.download_required_firmware("dc")

        assert result["success"] is True
        assert result["downloaded"] == 1
        assert 2 in download_called_ids
        assert 1 not in download_called_ids


class TestCheckPlatformBiosOffline:
    """What ``check_platform_bios`` answers when the RomM listing cannot be fetched.

    Readiness is assembled from three local inputs — which emulator is active
    (ES-DE), what it wants (the resolver), what is on disk — so an unreachable
    server does not take the answer away. It takes away the files only the server
    knows about, and the ability to download anything.
    """

    @pytest.mark.asyncio
    async def test_offline_still_answers_from_the_machine(self, firmware, tmp_path):
        """The emulators' demand is local, so the requirement survives the outage."""
        bios_dir = tmp_path / "bios"
        bios_dir.mkdir(parents=True)
        (bios_dir / "req1.bin").write_bytes(b"\x00" * 512)

        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            firmware_resolver=_dc_resolver(),
            core_info=_dc_core_info(),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
        )
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await fw.check_platform_bios("dc")

        assert result["needs_bios"] is True
        assert result["required_count"] == 2
        assert result["required_downloaded"] == 1
        assert result["bios_level"] == "missing"
        # Every row came from the machine, so none of them can be fetched.
        assert all(f["on_server"] is False for f in result["files"])
        # The answer is real, so nothing is flagged as unestablished (#1693).
        assert "bios_status_unknown" not in result

    @pytest.mark.asyncio
    async def test_offline_with_a_complete_reading_and_no_demand_is_a_real_negative(self, firmware, fw, tmp_path):
        """Every emulator read, none wants anything — "needs none" is an answer.

        The server could still be holding files for this platform, but none of
        them is a requirement, so there is no warning to withhold.
        """
        _resolver(fw).describe_emulator(_id(_TEST_CORE))
        with (
            patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")),
            patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")),
        ):
            result = await fw.check_platform_bios("n64")

        assert result["needs_bios"] is False
        assert result["bios_level"] == "ok"
        assert "bios_status_unknown" not in result

    @pytest.mark.asyncio
    async def test_offline_with_an_incomplete_reading_says_it_does_not_know(self, firmware, tmp_path):
        """Nothing to show AND nothing established — the one payload that answers nothing.

        Reporting a confident "needs none" here would clear a shown requirement
        on ignorance (#1693).
        """
        from tests.fakes.fake_core_info_provider import libretro_option

        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            firmware_resolver=FakeFirmwareResolver(unread_emulators=frozenset({_id("n64_libretro")})),
            core_info=FakeCoreInfoProvider(options=[libretro_option("n64_libretro", "Mupen64")]),
        )
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await fw.check_platform_bios("n64")

        assert result == {"needs_bios": False, "bios_status_unknown": True}

    @pytest.mark.asyncio
    async def test_a_cached_listing_still_contributes_its_own_files(self, firmware, fw, tmp_path):
        """The listing cache is what keeps the server-only rows available offline."""
        fw._listing._firmware_cache = [
            {
                "id": 1,
                "file_name": "scph5501.bin",
                "platform_slug": "psx",
                "file_size_bytes": 512,
                "md5_hash": "",
            },
        ]
        fw._listing._firmware_cache_epoch = fw._config.clock.time()
        _declare(fw, ("scph5501.bin", "PS1 US BIOS", True))

        with patch.object(firmware.romm_api, "list_platforms", side_effect=Exception("offline")):
            result = await fw.check_platform_bios("psx")

        assert result["needs_bios"] is True
        assert result["required_count"] == 1
        assert result["files"][0]["on_server"] is True

    @pytest.mark.asyncio
    async def test_online_no_firmware_is_a_real_negative(self, firmware, fw, tmp_path):
        """A successful fetch finding no firmware answers needs_bios False, unflagged.

        The counterpart to the offline cases above: this negative IS an answer, so
        it stays unflagged and consumers may clear a shown requirement on it.
        """
        _resolver(fw).describe_emulator(_id(_TEST_CORE))
        with (
            patch.object(firmware.romm_api, "list_platforms", return_value=_platforms([])),
            patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")),
        ):
            result = await fw.check_platform_bios("n64")

        assert result["needs_bios"] is False
        assert "bios_status_unknown" not in result


class TestAGameThatNeedsNoBios:
    """A met verdict with no row to show is answered whole, so the game page draws it like any other answer."""

    @staticmethod
    def _service():
        core_info = FakeCoreInfoProvider(
            options=[
                libretro_option("genesisplusgx_libretro", "Genesis Plus GX"),
                libretro_option("picodrive_libretro", "PicoDrive"),
            ],
            active_core=("genesisplusgx_libretro", "Genesis Plus GX"),
        )
        fw = _make_firmware_service(core_info=core_info)
        _resolver(fw).describe_emulator(_id("genesisplusgx_libretro"))
        _resolver(fw).describe_emulator(_id("picodrive_libretro"))
        _stub_listing(fw, [])
        _inline_executor(fw)
        return fw

    @pytest.mark.asyncio
    async def test_it_carries_the_verdict_the_sentence_is_read_off(self):
        result = await self._service().check_platform_bios("sms")

        assert result["needs_bios"] is False
        assert result["emulator_read"] is True
        assert result["bios_level"] == "ok"
        assert result["required_count"] == 0
        assert result["files"] == []
        assert "bios_status_unknown" not in result

    @pytest.mark.asyncio
    async def test_it_carries_an_entry_for_every_emulator_the_selector_offers(self):
        result = await self._service().check_platform_bios("sms")

        entries = [(entry["emulator"], entry["launching"]) for entry in result["emulator_tabs"]]
        assert entries == [(_id("genesisplusgx_libretro"), True), (_id("picodrive_libretro"), False)]


class TestPerCoreFiltering:
    """Per-core BIOS filtering: what the machine wants vs. what THIS launch needs.

    The two axes travel together on every file. ``wanted`` is the machine's
    answer and does not move with the core the user picked; ``required_by_active``
    and ``used_by_active`` are the launching core's, and they are what the
    missing-BIOS badge counts. A file three other cores demand is not a missing
    prerequisite for a launch on a core that never opens it.
    """

    @staticmethod
    def _gba_resolver() -> FakeFirmwareResolver:
        """The real GBA shape: gpSP will not run without the BIOS, mGBA will."""
        resolver = FakeFirmwareResolver()
        resolver.declare(
            "gba_bios.bin",
            required_by=[_id("gpsp_libretro")],
            optional_for=[_id("mgba_libretro")],
            description="GBA BIOS",
        )
        resolver.declare(
            "gb_bios.bin",
            optional_for=[_id("gambatte_libretro"), _id("mgba_libretro")],
            description="GB BIOS",
        )
        resolver.declare("sgb_bios.bin", optional_for=[_id("mgba_libretro")], description="SGB BIOS")
        return resolver

    @staticmethod
    def _gba_firmware() -> list[dict[str, Any]]:
        return [
            {
                "id": i + 1,
                "file_name": name,
                "platform_slug": "gba",
                "file_size_bytes": 100 * (i + 1),
                "md5_hash": "",
            }
            for i, name in enumerate(("gba_bios.bin", "gb_bios.bin", "sgb_bios.bin"))
        ]

    def _service(self, active_core: tuple[str, str] | tuple[None, None]) -> FirmwareService:
        """A gba platform whose one emulator is *active_core*, or none at all.

        Seeded on both of the fake's inputs, because the service reads both and
        they answer different questions: ``options`` is the emulator list the
        platform's own pick is resolved from, ``active_core`` the libretro-only
        system reading. A test meaning "this platform launches with gpSP" has to
        say it in the list. ``(None, None)`` is the platform ES-DE offers nothing
        bakeable for, which is an empty list rather than a named emulator.
        """
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(self._gba_firmware())
        core_so, label = active_core
        options = [libretro_option(core_so, label)] if core_so is not None and label is not None else []
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=self._gba_resolver(),
            core_info=FakeCoreInfoProvider(active_core=active_core, options=options),
        )
        _inline_executor(fw)
        return fw

    @pytest.mark.asyncio
    async def test_the_launching_core_decides_what_counts_as_required(self, tmp_path):
        """gpSP requires the GBA BIOS, and only the files gpSP opens are counted."""
        fw = self._service(("gpsp_libretro", "gpSP"))
        with patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")):
            result = await fw.check_platform_bios("gba")

        assert result["needs_bios"] is True
        assert result["server_count"] == 3
        # The picker's data is never served from the BIOS payload (#923). The
        # label beside it is: it names the pick these very counts were filtered
        # by, which is this answer's own field rather than the picker's.
        assert "active_core" not in result
        assert result["active_core_label"] == "gpSP"

        gba_file = next(f for f in result["files"] if f["file_name"] == "gba_bios.bin")
        assert gba_file["required_by_active"] is True
        assert gba_file["used_by_active"] is True
        assert gba_file["wanted"] == "needed"

        gb_file = next(f for f in result["files"] if f["file_name"] == "gb_bios.bin")
        assert gb_file["used_by_active"] is False
        assert gb_file["required_by_active"] is False
        # No per-emulator entries reach the wire: each emulator has an entry of its own.
        assert "cores" not in gb_file

        assert result["required_count"] == 1
        assert result["required_downloaded"] == 0

    @pytest.mark.asyncio
    async def test_a_core_that_needs_none_of_them_counts_none(self):
        """mGBA opens all three and demands none — nothing is a prerequisite."""
        fw = self._service(("mgba_libretro", "mGBA"))
        result = await fw.check_platform_bios("gba")

        assert result["server_count"] == 3
        assert result["required_count"] == 0
        for f in result["files"]:
            assert f["required_by_active"] is False
            assert f["used_by_active"] is True

    @pytest.mark.asyncio
    async def test_wanted_is_the_launching_emulators_own_need(self):
        """A row the launching emulator declares is its requirement, so ``wanted`` is that emulator's need.

        gpSP requires ``gba_bios.bin`` and mGBA only accepts it: the row reads
        ``needed`` under gpSP and ``optional`` under mGBA, never the other
        emulator's requirement of the same name.
        """
        gpsp = await self._service(("gpsp_libretro", "gpSP")).check_platform_bios("gba")
        mgba = await self._service(("mgba_libretro", "mGBA")).check_platform_bios("gba")

        assert next(f["wanted"] for f in gpsp["files"] if f["file_name"] == "gba_bios.bin") == "needed"
        assert next(f["wanted"] for f in mgba["files"] if f["file_name"] == "gba_bios.bin") == "optional"
        assert gpsp["required_count"] != mgba["required_count"]

    @pytest.mark.asyncio
    async def test_an_unresolved_core_is_answered_as_not_established(self):
        """No resolvable core → no row is presented as its requirement, and nothing reads as needed.

        No other emulator stands in for the one nothing identified. The empty
        required set that leaves must not read as "nothing required": the
        verdict for no identity is none, so the level is unknown and the
        emulator counts as not read, which is what the summary words.
        """
        fw = self._service((None, None))
        result = await fw.check_platform_bios("gba")

        assert result["server_count"] == 3
        assert "active_core" not in result
        for f in result["files"]:
            assert f["required_by_active"] is False
            assert f["used_by_active"] is False
        assert result["required_count"] == 0
        assert result["bios_level"] == "unknown"
        assert result["emulator_read"] is False

    @pytest.mark.asyncio
    async def test_a_core_no_declaration_names_requires_nothing(self):
        """An active core no placement mentions opens none of these files."""
        fw = self._service(("snes9x_libretro", "Snes9x"))
        result = await fw.check_platform_bios("gba")

        assert result["required_count"] == 0
        for f in result["files"]:
            assert f["used_by_active"] is False
            assert f["required_by_active"] is False


class TestCheckPlatformBiosPreResolvedCore:
    """R8: ``check_platform_bios`` takes a pre-resolved ``active_core_so``.

    The per-game game-detail path resolves the active ``.so`` upstream (folding
    the ``emulator_override`` pin) and passes it in; the platform-level callers
    pass ``None`` to mean "use the system default". The filter's ``required_count``
    must follow the pre-resolved core, not the system default.
    """

    def _gba_two_core_service(self, fw, firmware_list):
        """Wire a gba demand where gpSP requires gba_bios.bin and mGBA does not."""
        _resolver(fw).declare(
            "gba_bios.bin",
            required_by=[_id("gpsp_libretro")],
            optional_for=[_id("mgba_libretro")],
            description="GBA BIOS",
        )
        _stub_listing(fw, firmware_list)
        _inline_executor(fw)

    @pytest.mark.asyncio
    async def test_pre_resolved_core_drives_filter_over_system_default(self, fw, tmp_path):
        """A passed-in ``active_core_so`` overrides the system default for required_count.

        The system default (set on the fake) is mGBA — which treats gba_bios.bin as
        optional. Passing the per-game override ``gpsp_libretro`` flips the file to
        required, proving the pre-resolved core (not the system default) feeds the
        filter and that ``get_active_core`` is NOT consulted when a core is supplied.
        """
        firmware_list = [
            {"id": 1, "file_name": "gba_bios.bin", "platform_slug": "gba", "md5_hash": ""},
        ]
        self._gba_two_core_service(fw, firmware_list)
        # System default = mGBA (optional). The per-game override should win.
        fw._config.core_info.active_core = ("mgba_libretro", "mGBA")

        with patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")):
            result = await fw.check_platform_bios("gba", launching_emulator=_pick(_id("gpsp_libretro"), "gpSP"))

        assert result["needs_bios"] is True
        assert result["required_count"] == 1  # gpSP requires gba_bios.bin
        # The pre-resolved core short-circuits the system-default read entirely.
        assert fw._config.core_info.active_core_calls == []

    @pytest.mark.asyncio
    async def test_none_falls_back_to_system_default(self, fw, tmp_path):
        """``launching_emulator=None`` resolves the platform's own emulator.

        Same declarations, no per-game core: the platform-level path resolves the
        platform's pick (mGBA → optional) so ``required_count`` is 0 — the
        opposite of the override case, locking in the result-flip.
        """
        firmware_list = [
            {"id": 1, "file_name": "gba_bios.bin", "platform_slug": "gba", "md5_hash": ""},
        ]
        self._gba_two_core_service(fw, firmware_list)
        _set_platform_emulator(fw, "mgba_libretro", "mGBA")

        with patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")):
            result = await fw.check_platform_bios("gba")

        assert result["needs_bios"] is True
        assert result["required_count"] == 0  # mGBA treats gba_bios.bin as optional
        # None → the platform's emulators were read once, for the system "gba".
        assert _core_info(fw).emulator_options_calls == ["gba"]


class TestDownloadFirmwareErrors:
    """Tests for a single download's error handling over the store."""

    @pytest.mark.asyncio
    async def test_a_file_listed_without_a_platform_refuses_and_persists_nothing(self, firmware, fw, tmp_path):
        """A listed file with an empty platform fails the StoredFirmware invariant.

        The listing never files a file on no platform, so this is the guard
        behind it: the record is built before anything is fetched, so the
        aggregate's ValueError refuses with ``invalid_firmware``, nothing lands
        in the store and no record persists.
        """
        content = b"firmware bytes"
        fw_detail = {
            "id": 7,
            "file_name": "orphan.bin",
            "file_size_bytes": len(content),
            "md5_hash": hashlib.md5(content).hexdigest(),
        }
        fw._listing._firmware_cache = [{**fw_detail, "platform_slug": ""}]
        fw._listing._firmware_cache_epoch = fw._config.clock.time()

        bios_dir = tmp_path / "bios"
        bios_dir.mkdir()
        download, asked = _writes(content)

        _resolver_reads(fw, str(bios_dir))
        _set_loop(fw, asyncio.get_running_loop())

        with patch.object(firmware.romm_api, "download_firmware", side_effect=download):
            fetch = fw.download_platform_firmware_file("", "orphan.bin")
            with pytest.raises(Refused) as refused:
                await fetch

        assert refused.value.reason == "invalid_firmware"
        assert "Invalid firmware metadata" in refused.value.message
        assert asked == []
        # Nothing landed in the store — nothing left dangling.
        assert not (tmp_path / "Tender").exists() or not any((tmp_path / "Tender").rglob("orphan.bin*"))
        assert list(firmware.uow.firmware_store.iter_all()) == []


class _UnpreparableStore(FakeFirmwareFileStore):
    """A file store on a device where no folder can be created."""

    def make_dirs(self, path: str) -> None:
        raise PermissionError(f"simulated: cannot create {path}")


class TestASingleDownloadThatFails:
    """What one firmware download raises when the folder, the transfer or the metadata fetch fails."""

    _FW: ClassVar[dict[str, Any]] = {
        "id": 4,
        "file_name": "dc_boot.bin",
        "md5_hash": "a" * 32,
    }
    _TMP = "/fake/Tender/bios/dc/dc_boot.bin.tmp"

    def _service(self, fake_romm_api, store: FakeFirmwareFileStore) -> FirmwareService:
        fake_romm_api.seed_firmware("dc", dict(self._FW))
        fw = _make_firmware_service(
            romm_api=fake_romm_api,
            firmware_file_store=store,
            retrodeck_folders=FakeRetroDeckFolders(bios="/fake/bios"),
            tender_folder="/fake/Tender",
        )
        _set_loop(fw, asyncio.get_running_loop())
        return fw

    @pytest.mark.asyncio
    async def test_a_folder_this_device_cannot_create_refuses_and_leaves_no_tmp(self, fake_romm_api):
        store = _UnpreparableStore({self._TMP: b"partial"})
        fw = self._service(fake_romm_api, store)

        download = fw.download_platform_firmware_file("dc", "dc_boot.bin")
        with pytest.raises(Refused) as refused:
            await download

        assert refused.value.reason == "bios_download_failed"
        assert refused.value.message == "dc_boot.bin could not be downloaded: it could not be written on this device"
        assert isinstance(refused.value.__cause__, PermissionError)
        assert self._TMP not in store.files
        assert "download_firmware" not in [name for name, _args, _kwargs in fake_romm_api.call_log]

    @pytest.mark.asyncio
    async def test_a_romm_error_in_the_transfer_propagates_and_leaves_no_tmp(self, fake_romm_api):
        store = FakeFirmwareFileStore({self._TMP: b"partial"})
        fw = self._service(fake_romm_api, store)
        fake_romm_api.download_firmware_side_effect = RommConnectionError("connection refused")

        download = fw.download_platform_firmware_file("dc", "dc_boot.bin")
        with pytest.raises(RommConnectionError, match="connection refused"):
            await download

        assert self._TMP not in store.files

    @pytest.mark.asyncio
    async def test_a_romm_error_in_the_transfer_is_left_to_whoever_answers_it_to_log(self, fake_romm_api, caplog):
        fw = self._service(fake_romm_api, FakeFirmwareFileStore({self._TMP: b"partial"}))
        fake_romm_api.download_firmware_side_effect = RommConnectionError("connection refused")
        caplog.set_level(logging.DEBUG)

        download = fw.download_platform_firmware_file("dc", "dc_boot.bin")
        with pytest.raises(RommConnectionError):
            await download

        assert not [r for r in caplog.records if "Failed to download firmware" in r.getMessage()]

    @pytest.mark.asyncio
    async def test_a_folder_this_device_cannot_create_is_logged_once(self, fake_romm_api, caplog):
        fw = self._service(fake_romm_api, _UnpreparableStore({self._TMP: b"partial"}))
        caplog.set_level(logging.DEBUG)

        download = fw.download_platform_firmware_file("dc", "dc_boot.bin")
        with pytest.raises(Refused):
            await download

        lines = [(r.levelno, r.getMessage()) for r in caplog.records if "Failed to download firmware" in r.getMessage()]
        assert lines == [
            (logging.ERROR, "Failed to download firmware dc_boot.bin: simulated: cannot create /fake/Tender/bios/dc")
        ]

    @pytest.mark.asyncio
    async def test_any_other_failure_in_the_transfer_propagates_and_leaves_no_tmp(self, fake_romm_api):
        store = FakeFirmwareFileStore({self._TMP: b"partial"})
        fw = self._service(fake_romm_api, store)
        fake_romm_api.download_firmware_side_effect = RuntimeError("a bug")

        download = fw.download_platform_firmware_file("dc", "dc_boot.bin")
        with pytest.raises(RuntimeError, match="a bug"):
            await download

        assert self._TMP not in store.files

    @pytest.mark.asyncio
    async def test_a_romm_error_reading_the_listing_propagates(self, fake_romm_api):
        fw = self._service(fake_romm_api, FakeFirmwareFileStore())
        fake_romm_api.list_platforms_side_effect = RommConnectionError("connection refused")

        download = fw.download_platform_firmware_file("dc", "dc_boot.bin")
        with pytest.raises(RommConnectionError, match="connection refused"):
            await download

        assert "download_firmware" not in [name for name, _args, _kwargs in fake_romm_api.call_log]


class TestABatchDownloadCarriesOnPastAFailedFile:
    """A file whose download this service refuses (``Refused``) or that meets a RomM error is logged, named in the
    errors and passed over; anything else ends the batch, Tender's folder that cannot be reached included — no later
    file could land either.
    """

    _NAMES = ("refused", "romm_error", "fetched")

    def _service(self, tmp_path) -> FirmwareService:
        fw = _make_firmware_service(
            core_info=_DeclaredDefaultCoreInfo(
                active_core=("flycast_libretro", "Flycast"), options=[libretro_option("flycast_libretro", "Flycast")]
            ),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(tmp_path / "bios")),
        )
        for name in self._NAMES:
            _resolver(fw).declare(f"{name}.bin", required_by=[_id("flycast_libretro")])
        _stub_listing(
            fw,
            [
                {"id": n, "file_name": f"{name}.bin", "platform_slug": "dc", "md5_hash": ""}
                for n, name in enumerate(self._NAMES, start=1)
            ],
        )
        _inline_executor(fw)
        return fw

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entry", ["download_all_firmware", "download_required_firmware"])
    async def test_it_names_each_failed_file_and_fetches_the_rest(self, tmp_path, entry, caplog):
        fw = self._service(tmp_path)
        attempted = []

        def fake_fetch(_platform_slug, firmware):
            attempted.append(firmware["id"])
            if firmware["id"] == 1:
                raise Refused("bios_download_failed", "refused.bin could not be downloaded")
            if firmware["id"] == 2:
                raise RommConnectionError("connection refused")
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        with (
            patch.object(fw._store, "do_fetch", side_effect=fake_fetch),
            caplog.at_level(logging.INFO),
        ):
            result = await getattr(fw, entry)("dc")

        assert attempted == [1, 2, 3]
        assert result["success"] is True
        assert result["downloaded"] == 1
        assert result["message"].endswith("(2 failed: refused.bin, romm_error.bin)")
        assert result["failed"] == ["refused.bin", "romm_error.bin"]
        logged = [(record.levelno, record.getMessage()) for record in caplog.records]
        # A refusal is logged once, where the download refuses it; the batch adds no second line.
        assert not [line for _level, line in logged if "refused.bin" in line]
        assert (
            logging.INFO,
            "Failed to download firmware romm_error.bin: RommConnectionError: connection refused",
        ) in logged

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entry", ["download_all_firmware", "download_required_firmware"])
    async def test_tenders_folder_that_cannot_be_reached_ends_the_batch(self, tmp_path, entry):
        fw = self._service(tmp_path)
        attempted = []

        def fake_fetch(_platform_slug, firmware):
            attempted.append(firmware["id"])
            if firmware["id"] == 2:
                raise Refused("folder_unreachable", "Tender's folder cannot be reached.")
            return FetchOutcome(file_name=firmware["file_name"], downloaded=True)

        with patch.object(fw._store, "do_fetch", side_effect=fake_fetch):
            download = getattr(fw, entry)("dc")
            with pytest.raises(Refused) as refused:
                await download

        assert refused.value.reason == "folder_unreachable"
        assert attempted == [1, 2]

    @pytest.mark.asyncio
    async def test_an_unexpected_exception_ends_the_batch(self, tmp_path):
        fw = self._service(tmp_path)
        attempted = []

        def fake_fetch(_platform_slug, firmware):
            attempted.append(firmware["id"])
            raise RuntimeError("a bug")

        with patch.object(fw._store, "do_fetch", side_effect=fake_fetch):
            download = fw.download_all_firmware("dc")
            with pytest.raises(RuntimeError, match="a bug"):
                await download

        assert attempted == [1]


# ── Firmware list cache tests ─────────────────────────────


class TestThePlatformRommStatesForAFile:
    """A library file belongs to the platform RomM states for it (``platform_id``), whatever folder holds it."""

    # RomM's firmware structure ``bios_from_user/{platform}``: every platform's
    # files sit below one folder, and the folder names no platform.
    _FROM_USER: ClassVar[list[dict[str, Any]]] = [
        {"id": 1, "file_name": "scph5501.bin", "file_path": "bios_from_user/psx", "platform_slug": "psx"},
        {"id": 2, "file_name": "gba_bios.bin", "file_path": "bios_from_user/gba", "platform_slug": "gba"},
    ]

    @pytest.mark.asyncio
    async def test_with_firmware_under_bios_from_user_every_platform_finds_its_files(self, fw):
        _stub_listing(fw, self._FROM_USER)
        _inline_executor(fw)

        entries = await _entries(fw, "psx", "gba")

        assert [f["file_name"] for f in entries["psx"]["files"] if f["on_server"]] == ["scph5501.bin"]
        assert [f["file_name"] for f in entries["gba"]["files"] if f["on_server"]] == ["gba_bios.bin"]

    @pytest.mark.asyncio
    async def test_with_firmware_under_bios_from_user_download_bios_fetches_the_platform_s_files(self, fw, tmp_path):
        _stub_listing(fw, [{**row, "md5_hash": _EMPTY_MD5} for row in self._FROM_USER])
        _set_loop(fw, asyncio.get_running_loop())
        download, fetched = _writes(b"")

        with (
            patch.object(fw._config.romm_api, "download_firmware", side_effect=download),
            patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")),
        ):
            result = await fw.download_all_firmware("psx")

        assert (result["downloaded"], fetched) == (1, [1])
        assert (tmp_path / "Tender" / "bios" / "psx" / "scph5501.bin").is_file()

    @pytest.mark.asyncio
    async def test_a_platform_whose_server_folder_differs_from_its_slug_keeps_its_rows(self, fw):
        api = fw._config.romm_api
        assert isinstance(api, MagicMock)
        api.list_platforms.return_value = _platforms(
            [{"id": 1, "file_name": "scph5501.bin", "platform_slug": "psx"}], folders={"psx": "ps"}
        )
        _inline_executor(fw)

        entries = await _entries(fw, "psx")

        assert [f["file_name"] for f in entries["psx"]["files"] if f["on_server"]] == ["scph5501.bin"]

    @pytest.mark.asyncio
    async def test_a_platform_whose_server_folder_differs_from_its_slug_downloads_and_deletes(self, firmware, tmp_path):
        """The record a download writes is filed under the platform, so the platform's delete finds it."""
        bios_dir = tmp_path / "bios"
        bios_dir.mkdir()
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=_test_core_info(),
            tender_folder=str(tmp_path / "Tender"),
        )
        _set_loop(fw, asyncio.get_running_loop())
        row = {"id": 1, "file_name": "scph5501.bin", "md5_hash": hashlib.md5(b"bios").hexdigest(), "file_size_bytes": 4}
        firmware.romm_api.list_platforms.return_value = _platforms(
            [{**row, "platform_slug": "psx"}], folders={"psx": "ps"}
        )

        def write(_firmware_id, _file_name, dest):
            with open(dest, "wb") as f:
                f.write(b"bios")

        firmware.romm_api.download_firmware.side_effect = write

        downloaded = await fw.download_platform_firmware_file("psx", "scph5501.bin")
        recorded = firmware.uow.firmware_store.get("psx", "scph5501.bin")
        deleted = await fw.delete_bios_file("psx", "scph5501.bin")

        assert downloaded["downloaded"] == 1
        assert recorded is not None
        assert isinstance(deleted, dict)
        assert deleted["deleted_count"] == 1
        assert not (tmp_path / "Tender" / "bios" / "psx" / "scph5501.bin").exists()
        assert list(firmware.uow.firmware_store.iter_all()) == []

    @pytest.mark.asyncio
    async def test_a_record_filed_under_the_server_folder_name_is_deleted_with_its_platform(self, firmware, tmp_path):
        bios_dir = tmp_path / "bios"
        bios_dir.mkdir()
        (bios_dir / "scph5501.bin").write_bytes(b"bios")
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=_test_core_info(),
        )
        _set_loop(fw, asyncio.get_running_loop())
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="ps",
                file_name="scph5501.bin",
                file_path=str(bios_dir / "scph5501.bin"),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=1,
            )
        )
        firmware.romm_api.list_platforms.return_value = _platforms(
            [{"id": 1, "file_name": "scph5501.bin", "platform_slug": "psx"}], folders={"psx": "ps"}
        )

        entry = (await fw.get_platform_firmware_status("psx"))["platform"]
        deleted = await fw.delete_platform_bios("psx")

        assert entry["deletable_count"] == 1
        assert isinstance(deleted, dict)
        assert deleted["deleted_count"] == 1
        assert not (bios_dir / "scph5501.bin").exists()


class TestTheListingMovesOlderEntries:
    """A fresh read of the listing moves Tender's older entries; a cached read and a failed move cost nothing."""

    def _service(self, uow: FakeUnitOfWork) -> FirmwareService:
        api = MagicMock()
        api.list_platforms.return_value = _platforms(
            [{"id": 1, "file_name": "scph5501.bin", "platform_slug": "psx"}], folders={"psx": "ps"}
        )
        uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="ps",
                file_name="scph5501.bin",
                file_path="/bios/scph5501.bin",
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=1,
            )
        )
        return _make_firmware_service(romm_api=api, uow_factory=FakeUnitOfWorkFactory(uow))

    def test_a_fresh_read_moves_a_record_to_its_platform(self):
        uow = FakeUnitOfWork()
        fw = self._service(uow)

        fw._listing.get_firmware_list()

        assert [(r.platform_slug, r.file_name) for r in uow.bios_files.iter_all()] == [("psx", "scph5501.bin")]

    def test_a_cached_read_moves_nothing(self):
        uow = FakeUnitOfWork()
        fw = self._service(uow)
        fw._listing._firmware_cache = []
        fw._listing._firmware_cache_epoch = fw._config.clock.time()

        fw._listing.get_firmware_list()

        assert [(r.platform_slug, r.file_name) for r in uow.bios_files.iter_all()] == [("ps", "scph5501.bin")]

    def test_a_move_that_fails_costs_nothing_of_the_listing(self, caplog):
        uow = FakeUnitOfWork()
        fw = self._service(uow)

        with (
            patch.object(uow.bios_files, "iter_all", side_effect=OSError("db locked")),
            caplog.at_level(logging.WARNING),
        ):
            result = fw._listing.get_firmware_list()

        assert [row["file_name"] for row in result] == ["scph5501.bin"]
        assert "Failed to move older BIOS records and per-platform emulator overrides to their platforms" in caplog.text


class TestFirmwareListCache:
    """Tests for _get_firmware_list caching behaviour."""

    def _make_service(self, romm_api, uow_factory=None):
        return _make_firmware_service(romm_api=romm_api, uow_factory=uow_factory)

    def test_firmware_list_cached(self):
        """Second call returns cached data without hitting the API again."""
        api = MagicMock()
        api.list_platforms.return_value = _platforms([{"id": 1, "file_name": "bios.bin", "platform_slug": "dc"}])
        fw = self._make_service(api)

        result1 = fw._listing.get_firmware_list()
        result2 = fw._listing.get_firmware_list()

        assert result1 == [{"id": 1, "file_name": "bios.bin", "platform_id": 1, "platform_slug": "dc"}]
        assert result2 == result1
        assert api.list_platforms.call_count == 1

    def test_firmware_cache_ttl_expired(self):
        """After TTL expires, _get_firmware_list re-fetches from the API."""
        api = MagicMock()
        api.list_platforms.side_effect = [
            _platforms([{"id": 1, "platform_slug": "dc"}]),
            _platforms([{"id": 1, "platform_slug": "dc"}, {"id": 2, "platform_slug": "dc"}]),
        ]
        fw = self._make_service(api)

        result1 = fw._listing.get_firmware_list()
        assert len(result1) == 1
        assert api.list_platforms.call_count == 1

        # Simulate TTL expiry by backdating the wall-clock cache epoch
        fw._listing._firmware_cache_epoch = fw._config.clock.time() - 3601

        result2 = fw._listing.get_firmware_list()
        assert len(result2) == 2
        assert api.list_platforms.call_count == 2

    def test_firmware_cache_ttl_uses_wall_clock_across_restart(self):
        """Cache restored from the DB with stale ``cached_at`` must re-fetch.

        The TTL is measured on the wall clock ``cached_at`` is stamped with, so
        a cache restored after a backend restart is as old as its row says.
        """
        clock = _make_clock()
        # Pin the cache epoch two hours before the clock's current wall time —
        # well past _FIRMWARE_CACHE_TTL (1 h).
        stale_epoch = clock.time() - 7200
        uow = FakeUnitOfWork()
        _seed_firmware_cache(
            uow,
            [
                FirmwareCacheEntry.cached(
                    id=1, name="bios.bin", platform_slug="dc", file_size_bytes=2048, cached_at=stale_epoch
                )
            ],
        )

        api = MagicMock()
        api.list_platforms.return_value = _platforms([{"id": 2, "file_name": "fresh.bin", "platform_slug": "dc"}])
        fw = _make_firmware_service(romm_api=api, uow_factory=FakeUnitOfWorkFactory(uow), clock=clock)

        # The restored in-memory cache is reconstructed from the thin aggregate,
        # its platform kept and its md5 dropped.
        assert fw._listing._firmware_cache == [
            {
                "id": 1,
                "file_name": "bios.bin",
                "platform_slug": "dc",
                "file_size_bytes": 2048,
                "md5_hash": "",
            }
        ]
        assert fw._listing._firmware_cache_epoch == stale_epoch

        result = fw._listing.get_firmware_list()

        assert result == [{"id": 2, "file_name": "fresh.bin", "platform_id": 1, "platform_slug": "dc"}]
        assert api.list_platforms.call_count == 1

    def test_firmware_cache_invalidate(self):
        """Explicit invalidation triggers a re-fetch on next call."""
        api = MagicMock()
        api.list_platforms.side_effect = [
            _platforms([{"id": 1, "platform_slug": "dc"}]),
            _platforms([{"id": 1, "platform_slug": "dc"}, {"id": 2, "platform_slug": "dc"}]),
        ]
        fw = self._make_service(api)

        fw._listing.get_firmware_list()
        assert api.list_platforms.call_count == 1

        fw.invalidate_firmware_cache()
        result = fw._listing.get_firmware_list()
        assert len(result) == 2
        assert api.list_platforms.call_count == 2

    def test_firmware_cache_fallback_on_error(self):
        """HTTP error returns stale cached data instead of raising."""
        api = MagicMock()
        api.list_platforms.side_effect = [
            _platforms([{"id": 1, "file_name": "bios.bin", "platform_slug": "dc"}]),
            Exception("connection refused"),
        ]
        fw = self._make_service(api)

        result1 = fw._listing.get_firmware_list()
        assert len(result1) == 1

        # Expire the cache so it tries to re-fetch (must be far enough in the past
        # to exceed TTL even when system uptime is short)
        fw._listing._firmware_cache_epoch = fw._config.clock.time() - 7200

        result2 = fw._listing.get_firmware_list()
        assert result2 == result1  # Falls back to stale cache
        assert api.list_platforms.call_count == 2

    def test_firmware_cache_error_no_cache_raises(self):
        """HTTP error with no prior cache re-raises so callers can detect offline."""
        api = MagicMock()
        api.list_platforms.side_effect = Exception("connection refused")
        fw = self._make_service(api)

        with pytest.raises(Exception, match="connection refused"):
            fw._listing.get_firmware_list()


class TestFirmwareCachePersistence:
    """Tests for the SQLite firmware-cache round-trip via the Unit of Work."""

    def test_cache_loaded_from_db_on_init(self):
        """Firmware cache restored from the DB when entries are present."""
        uow = FakeUnitOfWork()
        _seed_firmware_cache(
            uow,
            [
                FirmwareCacheEntry.cached(
                    id=1, name="bios.bin", platform_slug="dc", file_size_bytes=512, cached_at=1000.0, md5_hash="ab12"
                )
            ],
        )

        fw = _make_firmware_service(uow_factory=FakeUnitOfWorkFactory(uow))

        # Reconstructed thin dict: the file's platform and its checksum kept.
        assert fw._listing._firmware_cache == [
            {
                "id": 1,
                "file_name": "bios.bin",
                "platform_slug": "dc",
                "file_size_bytes": 512,
                "md5_hash": "ab12",
            }
        ]
        assert fw._listing._firmware_cache_epoch == 1000.0

    def test_empty_db_cache_leaves_memory_none(self):
        """Empty DB cache doesn't populate the in-memory cache."""
        fw = _make_firmware_service(uow_factory=FakeUnitOfWorkFactory(FakeUnitOfWork()))
        assert fw._listing._firmware_cache is None

    def test_db_read_failure_handled_gracefully(self):
        """A repo error during restore doesn't crash init."""
        uow = FakeUnitOfWork()
        with patch.object(uow.firmware_cache, "iter_all", side_effect=OSError("db locked")):
            fw = _make_firmware_service(uow_factory=FakeUnitOfWorkFactory(uow))
        assert fw._listing._firmware_cache is None

    def test_cache_persisted_after_http_fetch(self, firmware, fw):
        """Firmware cache written to the DB after a successful HTTP fetch."""
        firmware_list = [
            {"id": 1, "file_name": "bios.bin", "platform_slug": "dc", "file_size_bytes": 512, "md5_hash": "ab12"}
        ]
        _stub_listing(fw, firmware_list)
        fw._listing._firmware_cache = None  # Force refetch

        result = fw._listing.get_firmware_list()

        assert result == [{**firmware_list[0], "platform_id": 1}]
        assert firmware.uow.firmware_cache.replace_count == 1
        # The thin aggregate carries the file's platform ("dc") and name.
        stored = firmware.uow.firmware_cache.get("dc", "bios.bin")
        assert stored is not None
        assert stored.id == 1
        assert stored.file_size_bytes == 512
        assert stored.md5_hash == "ab12"
        assert stored.cached_at == fw._listing._firmware_cache_epoch

    @pytest.mark.asyncio
    async def test_a_restored_listing_still_matches_a_library_file_by_content(self, firmware, fw):
        """After a restart with the server unreachable, the cached checksum is what fills a requirement."""
        content = b"saturn bios"
        _resolver(fw).declare(
            "saturn_bios.bin",
            required_by=[_id(_TEST_CORE)],
            identity_md5=hashlib.md5(content).hexdigest(),
            identity_kind=IDENTITY_FILE,
        )
        _stub_listing(
            fw,
            [
                {
                    "id": 1,
                    "file_name": "sega_100.bin",
                    "platform_slug": "saturn",
                    "file_size_bytes": len(content),
                    "md5_hash": hashlib.md5(content).hexdigest(),
                }
            ],
        )
        _inline_executor(fw)
        fw._listing.get_firmware_list()
        restarted = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=_resolver(fw),
            core_info=_test_core_info(),
        )
        _inline_executor(restarted)
        firmware.romm_api.list_platforms.side_effect = RommConnectionError("connection refused")

        rows = (await _entries(restarted, "saturn"))["saturn"]["files"]

        (row,) = [f for f in rows if f["file_name"] == "saturn_bios.bin"]
        assert row["library"]["file_name"] == "sega_100.bin"
        assert row["library"]["matched"] == "content"

    def test_invalidate_clears_persisted_cache(self, firmware, fw):
        """invalidate_firmware_cache drops every DB cache row."""
        _seed_firmware_cache(
            firmware.uow,
            [FirmwareCacheEntry.cached(id=1, name="x.bin", platform_slug="dc", file_size_bytes=10, cached_at=1.0)],
        )
        fw._listing._firmware_cache = [{"id": 1}]
        fw._listing._firmware_cache_epoch = 1.0

        fw.invalidate_firmware_cache()

        assert fw._listing._firmware_cache is None
        assert list(firmware.uow.firmware_cache.iter_all()) == []

    def test_persist_failure_does_not_crash_fetch(self, firmware, fw):
        """A DB write failure during fetch doesn't break the return value."""
        firmware_list = [{"id": 1, "file_name": "bios.bin", "platform_slug": "dc", "file_size_bytes": 512}]
        _stub_listing(fw, firmware_list)
        fw._listing._firmware_cache = None

        with patch.object(firmware.uow.firmware_cache, "replace_all", side_effect=OSError("disk full")):
            result = fw._listing.get_firmware_list()

        assert result == [{**firmware_list[0], "platform_id": 1}]
        assert fw._listing._firmware_cache == result


class TestDeletePlatformBiosIOLogsWarnings:
    """Coverage for the OSError-warning path in ``PlatformBiosDeleter._delete_io``.

    Reached here through the platform-wide button, and shared with the row's —
    both run that loop under different names.
    """

    @pytest.mark.asyncio
    async def test_logs_warning_and_collects_error_when_remove_fails(self, firmware, fw, caplog):
        """A per-file OSError surfaces as a logger.warning and an error entry."""
        import logging

        fake_files = FakeFirmwareFileStore({"/fake/bios/scph5501.bin": b"\x00", "/fake/bios/scph5502.bin": b"\x00"})
        fake_files.remove_failures.add("/fake/bios/scph5501.bin")
        fw._deletion._files = fake_files
        fw._deletion._retrodeck_folders = FakeRetroDeckFolders(bios="/fake/bios")

        for name in ("scph5501.bin", "scph5502.bin"):
            firmware.uow.bios_files.save(
                BiosFile.mark_downloaded(
                    platform_slug="psx",
                    file_name=name,
                    file_path=f"/fake/bios/{name}",
                    downloaded_at="2026-01-01T00:00:00+00:00",
                    firmware_id=None,
                )
            )

        with caplog.at_level(logging.WARNING):
            result = await fw.delete_platform_bios("psx")

        # One file deleted (the second), one failed with a logged warning.
        assert result == FirmwareDeletionIncomplete(
            reason="delete_incomplete", message="Deleted 1 file(s), 1 error(s)", deleted_count=1
        )
        assert any("scph5501.bin" in record.getMessage() for record in caplog.records)
        # The failing file's BIOS record must remain (it wasn't actually removed).
        assert firmware.uow.bios_files.get("psx", "scph5501.bin") is not None
        # The successful file's BIOS record is cleared.
        assert firmware.uow.bios_files.get("psx", "scph5502.bin") is None


class TestAPartialBiosDeleteIsIncomplete:
    """Each of the two Delete buttons answers a delete that did not remove every file with what it did remove."""

    @staticmethod
    def _record(firmware, name: str, path: str) -> None:
        firmware.uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="ps2",
                file_name=name,
                file_path=path,
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=None,
            )
        )

    def _two_downloads_one_stuck(self, firmware, fw, folder: str) -> FakeFirmwareFileStore:
        store = FakeFirmwareFileStore({f"{folder}/scph39001.bin": b"\x00", f"{folder}/scph70012.bin": b"\x00"})
        store.remove_failures.add(f"{folder}/scph39001.bin")
        fw._deletion._files = store
        fw._deletion._retrodeck_folders = FakeRetroDeckFolders(bios="/fake/bios")
        self._record(firmware, "scph39001.bin", f"{folder}/scph39001.bin")
        self._record(firmware, "scph70012.bin", f"{folder}/scph70012.bin")
        return store

    @pytest.mark.asyncio
    async def test_the_platform_delete_answers_what_it_removed(self, firmware, fw):
        store = self._two_downloads_one_stuck(firmware, fw, "/fake/bios/pcsx2/bios")

        result = await fw.delete_platform_bios("ps2")

        assert result == FirmwareDeletionIncomplete(
            reason="delete_incomplete", message="Deleted 1 file(s), 1 error(s)", deleted_count=1
        )
        assert list(store.files) == ["/fake/bios/pcsx2/bios/scph39001.bin"]

    @pytest.mark.asyncio
    async def test_the_file_delete_answers_that_it_removed_nothing(self, firmware, fw):
        store = self._two_downloads_one_stuck(firmware, fw, "/fake/bios/pcsx2/bios")

        result = await fw.delete_bios_file("ps2", "scph39001.bin")

        assert result == FirmwareDeletionIncomplete(
            reason="delete_incomplete",
            message="Could not delete scph39001.bin: simulated remove failure: /fake/bios/pcsx2/bios/scph39001.bin",
            deleted_count=0,
        )
        assert len(store.files) == 2


class TestBadPathFirmwareUseCases:
    """Coverage for three firmware error paths.

    Each test wires a fresh ``FirmwareService`` against the seeded
    ``FakeRommApi`` fixture instead of the ``firmware`` fixture's ``MagicMock``
    so the failure injection runs through the real Protocol surface.
    """

    def _build_service(self, fake_romm_api, *, uow=None):
        """Build a fresh ``FirmwareService`` wired against the supplied fake API."""
        if uow is None:
            uow = FakeUnitOfWork()
        return _make_firmware_service(
            romm_api=fake_romm_api,
            uow_factory=FakeUnitOfWorkFactory(uow),
            firmware_file_store=FakeFirmwareFileStore(),
        )

    def test_invalidate_cache_logs_warning_when_db_clear_fails(self, fake_romm_api, caplog):
        """A DB clear raising ``OSError`` is swallowed with a warning log."""
        import logging

        uow = FakeUnitOfWork()
        fw = self._build_service(fake_romm_api, uow=uow)
        fw._listing._firmware_cache = [{"id": 1, "file_name": "bios.bin"}]
        fw._listing._firmware_cache_epoch = 1.0

        with (
            patch.object(uow.firmware_cache, "clear", side_effect=OSError("disk full")),
            caplog.at_level(logging.WARNING),
        ):
            fw.invalidate_firmware_cache()  # must not raise

        # In-memory cache cleared regardless of DB failure.
        assert fw._listing._firmware_cache is None
        assert fw._listing._firmware_cache_epoch == 0
        # The failure surfaced as a warning.
        assert any("disk full" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_download_all_firmware_propagates_a_failed_list_fetch(self, fake_romm_api, caplog):
        """An initial listing failure propagates before anything is fetched."""
        import logging

        fw = self._build_service(fake_romm_api)
        _set_loop(fw, asyncio.get_running_loop())
        fake_romm_api.fail_on_next(OSError("connection reset"))

        with caplog.at_level(logging.ERROR):
            download = fw.download_all_firmware("dc")
            with pytest.raises(OSError, match="connection reset"):
                await download

        # The cache was not populated by the failed fetch.
        assert fw._listing._firmware_cache is None

    @pytest.mark.asyncio
    async def test_download_required_firmware_propagates_a_failed_list_fetch(self, fake_romm_api, caplog):
        """An initial listing failure propagates before anything is fetched."""
        import logging

        fw = self._build_service(fake_romm_api)
        _set_loop(fw, asyncio.get_running_loop())
        fake_romm_api.fail_on_next(OSError("connection reset"))

        with caplog.at_level(logging.ERROR):
            download = fw.download_required_firmware("dc")
            with pytest.raises(OSError, match="connection reset"):
                await download

        # The cache was not populated by the failed fetch.
        assert fw._listing._firmware_cache is None


class TestAPendingMigrationRefusesEveryDownloadAndDelete:
    """The façade checks each endpoint's migration rule before a sub-service is reached."""

    @pytest.mark.parametrize(
        ("method", "sub_service", "args"),
        [
            ("download_all_firmware", "_downloads", ("n64",)),
            ("download_required_firmware", "_downloads", ("n64",)),
            ("download_platform_firmware_file", "_downloads", ("n64", "pifdata.bin")),
            ("delete_platform_bios", "_deletion", ("n64",)),
            ("delete_bios_file", "_deletion", ("n64", "pifdata.bin")),
        ],
    )
    async def test_it_refuses_before_the_sub_service_runs(self, method, sub_service, args, monkeypatch):
        service = _make_firmware_service(conflict_rules=_make_conflict_rules(migration_pending=True))
        reached = AsyncMock()
        monkeypatch.setattr(getattr(service, sub_service), method, reached)

        with _refused_by_conflict_rule("blocked_by_migration"):
            await getattr(service, method)(*args)

        reached.assert_not_awaited()


class TestTheEmulatorListIsReadOffTheLoop:
    """The catalogue read is a live read of the resolver, so no BIOS answer asks it on the event loop."""

    class _ThreadRecordingCoreInfo(FakeCoreInfoProvider):
        def __init__(self) -> None:
            super().__init__(options=[libretro_option(_TEST_CORE, "Test Core")])
            self.threads: list[int] = []

        def get_emulator_options(self, system_name: str, *, reading: Any = None) -> dict[str, Any]:
            self.threads.append(threading.get_ident())
            return super().get_emulator_options(system_name, reading=reading)

    async def _asked_threads(self, call) -> list[int]:
        core_info = self._ThreadRecordingCoreInfo()
        service = _make_firmware_service(core_info=core_info, uow_factory=FakeUnitOfWorkFactory(FakeUnitOfWork()))
        _set_loop(service, asyncio.get_running_loop())
        with patch.object(service._listing, "get_firmware_list", return_value=[]):
            await call(service)
        return core_info.threads

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "call",
        [
            lambda service: service.get_platform_firmware_status("dc"),
            lambda service: service.check_platform_bios("dc"),
            lambda service: service.download_required_firmware("dc"),
        ],
        ids=["platform status", "per-game check", "required download"],
    )
    async def test_each_bios_answer_reads_the_list_on_a_worker_thread(self, call):
        threads = await self._asked_threads(call)

        assert threads, "the answer asked the emulator list"
        assert threading.get_ident() not in threads


class TestThePlatformsSystem:
    """The BIOS answers ask the system the download does: the platform's, or an installed game's recorded one."""

    _CORE = "flycast_libretro"

    def _service(self, firmware, platform_systems: FakePlatformSystems, resolver: FakeFirmwareResolver):
        core_info = FakeCoreInfoProvider(
            active_core=(self._CORE, "Flycast"), options=[libretro_option(self._CORE, "Flycast")]
        )
        fw = _make_firmware_service(
            romm_api=firmware.romm_api,
            uow_factory=FakeUnitOfWorkFactory(firmware.uow),
            firmware_resolver=resolver,
            core_info=core_info,
            platform_systems=platform_systems,
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        return fw, core_info

    @pytest.mark.asyncio
    async def test_the_game_page_asks_an_installed_games_recorded_system(self, firmware):
        resolver = FakeFirmwareResolver()
        fw, core_info = self._service(firmware, FakePlatformSystems({"dc": "dreamcast"}), resolver)
        install = RomInstall(
            rom_id=1,
            file_path="/roms/naomi/a.zip",
            rom_dir=None,
            platform_slug="dc",
            system="naomi",
            installed_at="2026-01-01T00:00:00+00:00",
        )

        await fw.check_platform_bios("dc", install=install)

        assert core_info.emulator_options_calls == ["naomi"]
        assert resolver.calls == ["naomi"]

    @pytest.mark.asyncio
    async def test_a_game_not_installed_asks_the_platforms_system(self, firmware):
        resolver = FakeFirmwareResolver()
        fw, core_info = self._service(firmware, FakePlatformSystems({"dc": "dreamcast"}), resolver)

        await fw.check_platform_bios("dc")

        assert core_info.emulator_options_calls == ["dreamcast"]
        assert resolver.calls == ["dreamcast"]

    @pytest.mark.asyncio
    async def test_a_platform_with_no_system_reads_no_firmware_and_says_why(self, firmware):
        resolver = FakeFirmwareResolver()
        platform = PlatformSystem(NO_SYSTEM, "dc", "Dreamcast", source=RETRODECK_SOURCE)
        fw, core_info = self._service(firmware, FakePlatformSystems(answers={"dc": platform}), resolver)
        _stub_listing(fw, [{"id": 1, "file_name": "dc_boot.bin", "platform_slug": "dc"}])

        answer = (await fw.get_platform_firmware_status("dc"))["platform"]

        assert core_info.emulator_options_calls == []
        assert resolver.calls == []
        assert answer["emulator_data_reason"] == "no_platform_system"
        assert answer["platform_system"] == {
            "state": "no_system",
            "source": "retrodeck",
            "system": None,
            "platform": "Dreamcast",
        }

    @pytest.mark.asyncio
    async def test_a_downloaded_games_bios_download_places_for_its_recorded_system(self, firmware, tmp_path):
        resolver = FakeFirmwareResolver()
        fw, _ = self._service(firmware, FakePlatformSystems({"dc": "dreamcast"}), resolver)
        _stub_listing(fw, [{"id": 1, "file_name": "dc_boot.bin", "platform_slug": "dc"}])
        with firmware.uow:
            firmware.uow.roms.save(
                Rom(
                    rom_id=7,
                    platform_slug="dc",
                    name="Game",
                    fs_name="g.zip",
                    shortcut_app_id=None,
                    last_synced_at="2026-01-01T00:00:00",
                )
            )
            firmware.uow.rom_installs.save(
                RomInstall(
                    rom_id=7,
                    file_path="/roms/naomi/g.zip",
                    rom_dir=None,
                    platform_slug="dc",
                    system="naomi",
                    installed_at="2026-01-01T00:00:00+00:00",
                )
            )

        with (
            patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")),
            patch.object(fw._store, "do_fetch", return_value=FetchOutcome(file_name="dc_boot.bin", downloaded=True)),
        ):
            await fw.download_all_firmware("dc", 7)

        assert resolver.calls == ["naomi"]

    @pytest.mark.asyncio
    async def test_the_platform_pages_bios_download_follows_the_current_system(self, firmware, tmp_path):
        resolver = FakeFirmwareResolver()
        fw, _ = self._service(firmware, FakePlatformSystems({"dc": "dreamcast"}), resolver)
        _stub_listing(fw, [{"id": 1, "file_name": "dc_boot.bin", "platform_slug": "dc"}])

        with (
            patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")),
            patch.object(fw._store, "do_fetch", return_value=FetchOutcome(file_name="dc_boot.bin", downloaded=True)),
        ):
            await fw.download_all_firmware("dc", None)

        assert resolver.calls == ["dreamcast"]

    @pytest.mark.parametrize("state", [NO_SYSTEM, SWITCHED_OFF])
    @pytest.mark.asyncio
    async def test_its_bios_download_lands_in_the_store_only(self, firmware, tmp_path, state):
        """A platform with no system to link for still downloads; nothing asks an emulator source about it."""
        resolver = FakeFirmwareResolver()
        platform = PlatformSystem(state, "dc", "Dreamcast", system="dreamcast", source=RETRODECK_SOURCE)
        fw, _ = self._service(firmware, FakePlatformSystems(answers={"dc": platform}), resolver)
        _stub_listing(fw, [{"id": 1, "file_name": "dc_boot.bin", "platform_slug": "dc"}])

        with (
            patch.object(_resolver(fw), "bios_root", str(tmp_path / "bios")),
            patch.object(
                fw._store, "do_fetch", return_value=FetchOutcome(file_name="dc_boot.bin", downloaded=True)
            ) as fetch,
        ):
            result = await fw.download_all_firmware("dc")

        assert fetch.call_count == 1
        assert result["downloaded"] == 1
        assert result["placed"] == 0
        assert resolver.calls == []


_YABASANSHIRO = "yabasanshiro_libretro"
_KRONOS = "kronos_libretro"


class TestEachEmulatorsOwnPlace:
    """A row is the launching emulator's own requirement, at its own place.

    Kronos opens ``saturn_bios.bin`` under ``kronos/``; YabaSanshiro opens it
    directly under the BIOS root. The file lies only under ``kronos/``, so the
    answer turns on which of the two launches — and a fold by file name, which
    took the first declarer's place, read the platform as ready under either.
    """

    @staticmethod
    def _service(
        tmp_path,
        pick: str,
        *,
        kronos_first: bool = True,
        active_core: FakeActiveCoreResolver | None = None,
        uow: FakeUnitOfWork | None = None,
        options: list[EmulatorOption] | None = None,
    ) -> tuple[FirmwareService, FakeFirmwareResolver]:
        """Saturn with the file only under ``kronos/``; *kronos_first* is which emulator the answer names first.

        The order is a parameter because a fold by name takes the first declarer's place:
        each pick is asked under both orders, so neither can pass by being first.
        """
        bios_dir = tmp_path / "bios"
        (bios_dir / "kronos").mkdir(parents=True)
        (bios_dir / "kronos" / "saturn_bios.bin").write_bytes(b"\x00" * 8)
        romm_api = MagicMock()
        romm_api.list_platforms.return_value = _platforms(
            [
                {
                    "id": 7,
                    "file_name": "saturn_bios.bin",
                    "platform_slug": "saturn",
                    "file_size_bytes": 8,
                    "md5_hash": hashlib.md5(b"\x00" * 8).hexdigest(),
                }
            ]
        )
        romm_api.download_firmware.side_effect = _writes(b"\x00" * 8)[0]
        resolver = FakeFirmwareResolver()
        declarations = [(_KRONOS, "kronos/saturn_bios.bin"), (_YABASANSHIRO, "saturn_bios.bin")]
        for core, place in declarations if kronos_first else reversed(declarations):
            resolver.declare("saturn_bios.bin", required_by=[_id(core)], relative_path=place)
        fw = _make_firmware_service(
            romm_api=romm_api,
            firmware_resolver=resolver,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=_DeclaredDefaultCoreInfo(
                options=options
                if options is not None
                else [libretro_option(_KRONOS, "Kronos"), libretro_option(_YABASANSHIRO, "YabaSanshiro")],
            ),
            platform_core_reader=FakePlatformCoreReader({"saturn": pick}),
            active_core=active_core,
            uow_factory=FakeUnitOfWorkFactory(uow) if uow is not None else None,
            tender_folder=str(tmp_path / "Tender"),
        )
        _inline_executor(fw)
        return fw, resolver

    @staticmethod
    def _linked(tmp_path, place: str) -> bool:
        """Is *place* below the BIOS root a link to the platform's store copy?"""
        path = tmp_path / "bios" / place
        stored = tmp_path / "Tender" / "bios" / "saturn" / "saturn_bios.bin"
        return path.is_symlink() and os.readlink(path) == str(stored)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kronos_first", [True, False])
    async def test_with_yabasanshiro_picked_the_file_under_kronos_does_not_count(self, tmp_path, kronos_first):
        fw, _ = self._service(tmp_path, "YabaSanshiro", kronos_first=kronos_first)

        page = await fw.check_platform_bios("saturn")
        row = _rows(page)["saturn_bios.bin"]

        assert page["bios_level"] == "missing"
        assert row["declared_path"] == "saturn_bios.bin"
        assert row["satisfied"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kronos_first", [True, False])
    async def test_with_kronos_picked_the_same_file_is_met(self, tmp_path, kronos_first):
        fw, _ = self._service(tmp_path, "Kronos", kronos_first=kronos_first)

        page = await fw.check_platform_bios("saturn")
        row = _rows(page)["saturn_bios.bin"]

        assert page["bios_level"] == "ok"
        assert row["declared_path"] == "kronos/saturn_bios.bin"
        assert row["satisfied"] is True

    @pytest.mark.asyncio
    async def test_the_pane_answers_for_the_picked_emulators_place_too(self, tmp_path):
        fw, _ = self._service(tmp_path, "YabaSanshiro")

        entry = (await fw.get_platform_firmware_status("saturn"))["platform"]
        row = next(f for f in entry["files"] if f["file_name"] == "saturn_bios.bin")

        assert entry["bios_level"] == "missing"
        assert row["declared_path"] == "saturn_bios.bin"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("pick", "lands_at"), [("YabaSanshiro", "saturn_bios.bin"), ("Kronos", "kronos/saturn_bios.bin")]
    )
    @pytest.mark.parametrize("kronos_first", [True, False])
    async def test_download_required_fetches_what_the_pick_requires_and_links_it_where_the_pick_opens_it(
        self, tmp_path, pick, lands_at, kronos_first
    ):
        fw, _ = self._service(tmp_path, pick, kronos_first=kronos_first)
        # The file under ``kronos/`` is already where Kronos opens it, so the
        # batch skips it there; take it away to see what each pick fetches.
        (tmp_path / "bios" / "kronos" / "saturn_bios.bin").unlink()

        result = await fw.download_required_firmware("saturn")

        assert result["downloaded"] == 1
        assert self._linked(tmp_path, lands_at)

    @pytest.mark.asyncio
    async def test_download_required_fetches_for_a_pick_that_is_not_installed(self, tmp_path):
        """Nothing bakes: the first declared emulator is the pick, and its requirement is what is fetched."""
        options = [
            replace(libretro_option(_KRONOS, "Kronos"), status="needs_setup", reason="not_installed"),
            replace(libretro_option(_YABASANSHIRO, "YabaSanshiro"), status="needs_setup", reason="not_installed"),
        ]
        fw, _ = self._service(tmp_path, "YabaSanshiro", options=options)
        (tmp_path / "bios" / "kronos" / "saturn_bios.bin").unlink()

        result = await fw.download_required_firmware("saturn")

        assert result["downloaded"] == 1
        assert self._linked(tmp_path, "kronos/saturn_bios.bin")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rom_picks_kronos", [False, True])
    async def test_a_game_page_download_links_the_file_for_every_emulator_of_the_platform(
        self, tmp_path, rom_picks_kronos
    ):
        """Whichever emulator the game resolves to, the file is linked wherever an emulator of the platform opens it."""
        invocation = EmulatorInvocation.libretro(_KRONOS, "Kronos", f"{_KRONOS}.so") if rom_picks_kronos else None
        game = FakeActiveCoreResolver(per_rom_emulator={42: invocation})
        fw, _ = self._service(tmp_path, "YabaSanshiro", active_core=game)
        (tmp_path / "bios" / "kronos" / "saturn_bios.bin").unlink()

        result = await fw.download_all_firmware("saturn", rom_id=42)

        assert result["placed"] == 2
        assert self._linked(tmp_path, "saturn_bios.bin")
        assert self._linked(tmp_path, "kronos/saturn_bios.bin")

    @pytest.mark.asyncio
    async def test_the_per_row_download_links_every_free_place_and_leaves_the_rest(self, tmp_path):
        """The file under ``kronos/`` is not Tender's: it stays, and YabaSanshiro's place gets the link."""
        fw, _ = self._service(tmp_path, "YabaSanshiro")

        result = await fw.download_platform_firmware_file("saturn", "saturn_bios.bin")

        assert (result["placed"], result["left_in_place"]) == (1, 1)
        assert self._linked(tmp_path, "saturn_bios.bin")
        assert not (tmp_path / "bios" / "kronos" / "saturn_bios.bin").is_symlink()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("shown", "lands_at"), [(_YABASANSHIRO, "saturn_bios.bin"), (_KRONOS, "kronos/saturn_bios.bin")]
    )
    async def test_download_required_fetches_for_the_shown_emulator(self, tmp_path, shown, lands_at):
        """The button follows the emulator the page shows, whichever the platform launches."""
        pick = "Kronos" if shown == _YABASANSHIRO else "YabaSanshiro"
        fw, _ = self._service(tmp_path, pick)
        (tmp_path / "bios" / "kronos" / "saturn_bios.bin").unlink()

        result = await fw.download_required_firmware("saturn", _id(shown))

        assert result["downloaded"] == 1
        assert self._linked(tmp_path, lands_at)

    @pytest.mark.asyncio
    async def test_an_emulator_the_answer_does_not_hold_is_refused(self, tmp_path):
        """A shown emulator nobody answered for has no requirement to fetch; never the first declarer's."""
        fw, _ = self._service(tmp_path, "YabaSanshiro")
        (tmp_path / "bios" / "kronos" / "saturn_bios.bin").unlink()

        with patch.object(fw._store, "do_fetch") as fetch, pytest.raises(Refused) as required:
            await fw.download_required_firmware("saturn", "gone_libretro.so")

        assert required.value.reason == "emulator_unknown"
        fetch.assert_not_called()


class TestOneEntryPerEmulator:
    """One entry per emulator the resolver answered for, each exactly that emulator's own answer."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kronos_first", [True, False])
    async def test_the_launching_emulator_is_the_first_entry_and_the_others_follow(self, tmp_path, kronos_first):
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro", kronos_first=kronos_first)

        page = await fw.check_platform_bios("saturn")

        tabs = [(tab["emulator"], tab["label"], tab["launching"]) for tab in page["emulator_tabs"]]
        assert tabs == [(_id(_YABASANSHIRO), "YabaSanshiro", True), (_id(_KRONOS), "Kronos", False)]

    @pytest.mark.asyncio
    async def test_each_entry_carries_its_own_emulators_verdict(self, tmp_path):
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro")

        tabs = {tab["emulator"]: tab for tab in (await fw.check_platform_bios("saturn"))["emulator_tabs"]}

        assert tabs[_id(_YABASANSHIRO)]["requirements_met"] is False
        assert tabs[_id(_KRONOS)]["requirements_met"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pick", ["Kronos", "Kronos HLE"])
    async def test_an_identity_under_two_labels_is_one_entry_named_by_both(self, tmp_path, pick):
        """Every label the answer gives one identity, in its own order, whichever is the pick."""
        options = [
            libretro_option(_KRONOS, "Kronos"),
            libretro_option(_YABASANSHIRO, "YabaSanshiro"),
            libretro_option(_KRONOS, "Kronos HLE"),
        ]
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, pick, options=options)

        page = await fw.check_platform_bios("saturn")

        entries = [(entry["emulator"], entry["label"], entry["launching"]) for entry in page["emulator_tabs"]]
        assert entries == [(_id(_KRONOS), "Kronos / Kronos HLE", True), (_id(_YABASANSHIRO), "YabaSanshiro", False)]

    @pytest.mark.asyncio
    async def test_another_emulators_rows_carry_what_a_delete_there_would_remove(self, tmp_path):
        """A row of any emulator's entry offers a delete where Tender's own record names its file."""
        uow = FakeUnitOfWork()
        uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="saturn",
                file_name="saturn_bios.bin",
                file_path=str(tmp_path / "bios" / "kronos" / "saturn_bios.bin"),
                downloaded_at="2026-01-01T00:00:00+00:00",
                firmware_id=7,
            )
        )
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro", uow=uow)

        entry = (await fw.get_platform_firmware_status("saturn"))["platform"]
        kronos = next(tab for tab in entry["emulator_tabs"] if tab["emulator"] == _id(_KRONOS))

        assert [(r["file_name"], r["deletable_count"]) for r in kronos["rows"]] == [("saturn_bios.bin", 1)]

    @pytest.mark.asyncio
    async def test_another_emulators_entry_shows_its_own_place_and_verdict(self, tmp_path):
        """Kronos's entry carries ``kronos/saturn_bios.bin``, met, while YabaSanshiro launches and reads it missing."""
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro")

        page = await fw.check_platform_bios("saturn")
        kronos = next(tab for tab in page["emulator_tabs"] if tab["emulator"] == _id(_KRONOS))

        assert [(r["declared_path"], r["satisfied"], r["required_by_active"]) for r in kronos["rows"]] == [
            ("kronos/saturn_bios.bin", True, True)
        ]
        assert _rows(page)["saturn_bios.bin"]["declared_path"] == "saturn_bios.bin"

    @pytest.mark.asyncio
    async def test_the_launching_entry_carries_no_rows_of_its_own(self, tmp_path):
        """Its rows are the payload's ``files``, which the downloads and counts read."""
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro")

        launching = (await fw.check_platform_bios("saturn"))["emulator_tabs"][0]

        assert "rows" not in launching

    @pytest.mark.asyncio
    async def test_a_row_names_the_emulator_whose_requirement_it_is(self, tmp_path):
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "Kronos")

        row = _rows(await fw.check_platform_bios("saturn"))["saturn_bios.bin"]

        assert row["declared_by"] == _id(_KRONOS)

    @staticmethod
    def _not_installed(option: EmulatorOption) -> EmulatorOption:
        return replace(option, status="needs_setup", reason="not_installed")

    @pytest.mark.asyncio
    async def test_a_pick_that_is_not_installed_is_the_launching_entry_marked_so(self, tmp_path):
        """Nothing bakes: the plain launch starts RetroDECK's first emulator, so the answer is about it."""
        options = [
            self._not_installed(libretro_option(_YABASANSHIRO, "YabaSanshiro")),
            self._not_installed(libretro_option(_KRONOS, "Kronos")),
        ]
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "Kronos", options=options)

        page = await fw.check_platform_bios("saturn")

        entries = [(e["emulator"], e["launching"], e["installed"]) for e in page["emulator_tabs"]]
        assert entries == [(_id(_YABASANSHIRO), True, False), (_id(_KRONOS), False, False)]
        # The header and its counts are that emulator's: its file, where it opens it.
        assert page["active_core_label"] == "YabaSanshiro"
        assert _rows(page)["saturn_bios.bin"]["declared_path"] == "saturn_bios.bin"

    @pytest.mark.asyncio
    async def test_an_installed_emulators_entry_says_so(self, tmp_path):
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro")

        page = await fw.check_platform_bios("saturn")

        assert [e["installed"] for e in page["emulator_tabs"]] == [True, True]

    @pytest.mark.asyncio
    async def test_no_entry_stands_for_a_launching_emulator_nothing_identifies(self, tmp_path):
        """No emulator listed: no launching entry, and none under a made-up name."""
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro", options=[])

        page = await fw.check_platform_bios("saturn")

        assert [(e["emulator"], e["launching"]) for e in page["emulator_tabs"]] == [
            (_id(_KRONOS), False),
            (_id(_YABASANSHIRO), False),
        ]

    @pytest.mark.asyncio
    async def test_the_platform_pane_carries_the_same_entries(self, tmp_path):
        fw, _ = TestEachEmulatorsOwnPlace._service(tmp_path, "YabaSanshiro")

        entry = (await fw.get_platform_firmware_status("saturn"))["platform"]

        assert [tab["emulator"] for tab in entry["emulator_tabs"]] == [_id(_YABASANSHIRO), _id(_KRONOS)]


class TestTheRowsVerdictIsTheResolvers:
    """A file row reads the resolver's per-file verdict, never presence."""

    @staticmethod
    async def _row(
        tmp_path, *, present: bool | None = True, checked: str | None = None, **pinned: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        bios_dir = tmp_path / "bios"
        bios_dir.mkdir(exist_ok=True)
        resolver = FakeFirmwareResolver()
        resolver.declare("gba_bios.bin", required_by=[_id("gpsp_libretro")], present=present, checked=checked, **pinned)
        fw = _make_firmware_service(
            firmware_resolver=resolver,
            retrodeck_folders=FakeRetroDeckFolders(bios=str(bios_dir)),
            core_info=FakeCoreInfoProvider(options=[libretro_option("gpsp_libretro", "gpSP")]),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        page = await fw.check_platform_bios("gba")
        return page, _rows(page)["gba_bios.bin"]

    @pytest.mark.asyncio
    async def test_a_wrong_image_is_not_met_and_red(self, tmp_path):
        page, row = await self._row(tmp_path, checked="mismatch")

        assert (row["downloaded"], row["satisfied"], row["checked"]) == (True, False, "mismatch")
        assert page["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_refused_file_is_not_met_and_red(self, tmp_path):
        page, row = await self._row(tmp_path, checked="refused")

        assert (row["downloaded"], row["satisfied"]) == (True, False)
        assert page["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_file_it_could_not_compare_is_not_established_and_grey(self, tmp_path):
        page, row = await self._row(tmp_path, checked="not-comparable")

        assert (row["downloaded"], row["satisfied"]) == (True, None)
        assert page["bios_level"] == "unknown"
        assert page["required_downloaded"] == 0

    @pytest.mark.asyncio
    async def test_a_place_it_could_not_look_at_is_not_established_never_missing(self, tmp_path):
        # The resolver states the place inaccessible: nothing at it is known, either way.
        page, row = await self._row(tmp_path, present=None, satisfied=None)

        assert row["satisfied"] is None
        assert page["bios_level"] == "unknown"
        assert page["required_withheld"] == 1

    @pytest.mark.asyncio
    async def test_a_verified_file_is_met(self, tmp_path):
        page, row = await self._row(tmp_path, checked="verified")

        assert row["satisfied"] is True
        assert page["bios_level"] == "ok"


class TestTheRegionRuleOnThePlatform:
    """Amber only where the resolver's verdict is open because of the disc's region alone."""

    @staticmethod
    async def _page(*options: tuple[str, tuple[str, ...], bool | None], undetermined: tuple[str, ...] = ()):
        resolver = FakeFirmwareResolver()
        for name, _regions, satisfied in options:
            resolver.declare(name, optional_for=[_id(_BEETLE)], satisfied=satisfied)
        resolver.state_group(_id(_BEETLE), *options)
        resolver.describe_emulator(_id(_BEETLE), region_decided_by_disc=True, undetermined=undetermined)
        fw = _make_firmware_service(
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(options=[libretro_option(_BEETLE, "Beetle PSX")]),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        return await fw.check_platform_bios("psx")

    @pytest.mark.asyncio
    async def test_only_the_japanese_image_reads_amber_naming_the_regions(self):
        page = await self._page(
            ("scph5500.bin", ("ntsc-j",), True),
            ("scph5501.bin", ("ntsc-u",), False),
            ("scph5502.bin", ("pal",), False),
        )

        assert page["bios_level"] == "partial"
        group = page["one_of_groups"][0]
        assert (group["covered"], group["missing"]) == (["ntsc-j"], ["ntsc-u", "pal"])

    @pytest.mark.asyncio
    async def test_a_met_image_beside_one_not_established_is_grey(self):
        page = await self._page(("scph5500.bin", ("ntsc-j",), True), ("scph5501.bin", ("ntsc-u",), None))

        assert page["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_met_and_missing_images_beside_a_required_file_not_established_are_grey(self):
        page = await self._page(
            ("scph5500.bin", ("ntsc-j",), True),
            ("scph5501.bin", ("ntsc-u",), False),
            undetermined=("scph1001.bin",),
        )

        assert page["bios_level"] == "unknown"


class TestTheGamePageReadsItsOwnRegion:
    """Where the game's region is clear, the image serving it decides."""

    @staticmethod
    async def _page(
        rom_regions: tuple[str, ...],
        *options,
        undetermined: tuple[str, ...] = (),
        unmet=(),
        unstated: tuple[str, ...] = (),
        **verdict: Any,
    ) -> dict[str, Any]:
        resolver = FakeFirmwareResolver()
        for name, _regions, satisfied in options:
            resolver.declare(name, optional_for=[_id(_BEETLE)], satisfied=satisfied)
        resolver.state_group(_id(_BEETLE), *options, unstated_regions=unstated)
        resolver.describe_emulator(
            _id(_BEETLE), region_decided_by_disc=True, undetermined=undetermined, unmet=unmet, **verdict
        )
        fw = _make_firmware_service(
            firmware_resolver=resolver,
            core_info=FakeCoreInfoProvider(options=[libretro_option(_BEETLE, "Beetle PSX")]),
        )
        _inline_executor(fw)
        _stub_listing(fw, [])
        return await fw.check_platform_bios("psx", None, rom_regions=rom_regions)

    _JAPAN_ONLY = (
        ("scph5500.bin", ("ntsc-j",), True),
        ("scph5501.bin", ("ntsc-u",), False),
        ("scph5502.bin", ("pal",), False),
    )

    @pytest.mark.asyncio
    async def test_with_only_the_japanese_image_a_japanese_game_is_green(self):
        assert (await self._page(("Japan",), *self._JAPAN_ONLY))["bios_level"] == "ok"

    @pytest.mark.asyncio
    async def test_with_only_the_japanese_image_a_north_american_game_is_red(self):
        page = await self._page(("USA",), *self._JAPAN_ONLY)

        assert page["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_game_whose_region_has_no_option_is_grey(self):
        page = await self._page(("USA",), ("scph5500.bin", ("ntsc-j",), True))

        assert page["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_game_whose_image_could_not_be_checked_is_grey(self):
        page = await self._page(("USA",), ("scph5500.bin", ("ntsc-j",), True), ("scph5501.bin", ("ntsc-u",), None))

        assert page["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_plain_file_not_met_outside_the_group_is_red_whatever_the_region(self):
        page = await self._page(("Japan",), *self._JAPAN_ONLY, unmet=("scph1001.bin",))

        assert page["bios_level"] == "missing"

    @pytest.mark.asyncio
    async def test_a_verdict_not_met_that_nothing_stated_explains_is_grey(self):
        """The console's own image may be what is missing, but nothing is inferred.

        The verdict is not met, no plain file is unmet and the group has not
        failed. Until the resolver states the console's image as absent, the
        page declines rather than reading red off the remainder.
        """
        page = await self._page(("Japan",), *self._JAPAN_ONLY, requirements_met=False)

        assert page["bios_level"] == "unknown"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("system_firmware_met", "level"), [(False, "missing"), (None, "unknown")])
    async def test_with_the_consoles_image_shown_absent_a_game_no_image_serves_is_red(self, system_firmware_met, level):
        """The console's own image absent holds for every region — read off the resolver, never off the group."""
        page = await self._page(
            ("Europe",),
            ("scph5500.bin", ("ntsc-j",), False),
            ("scph5501.bin", ("ntsc-u",), False),
            requirements_met=False,
            system_firmware_met=system_firmware_met,
        )

        assert page["bios_level"] == level

    @pytest.mark.asyncio
    async def test_a_game_of_a_region_the_group_states_nothing_for_is_grey_and_its_region_named(self):
        page = await self._page(
            ("Europe",),
            ("scph5500.bin", ("ntsc-j",), True),
            ("scph5501.bin", ("ntsc-u",), False),
            unstated=("pal",),
        )

        assert page["bios_level"] == "unknown"
        assert page["one_of_groups"][0]["unstated"] == ["pal"]

    @pytest.mark.asyncio
    async def test_on_the_platform_a_region_the_group_states_nothing_for_is_grey_not_amber(self):
        options = (("scph5500.bin", ("ntsc-j",), True), ("scph5501.bin", ("ntsc-u",), False))

        amber = await self._page((), *options)
        page = await self._page((), *options, unstated=("pal",))

        assert amber["bios_level"] == "partial"
        assert page["bios_level"] == "unknown"
        assert page["one_of_groups"][0]["unstated"] == ["pal"]

    @pytest.mark.asyncio
    async def test_a_plain_file_not_established_outside_the_group_is_grey_whatever_the_region(self):
        page = await self._page(("Japan",), *self._JAPAN_ONLY, undetermined=("scph1001.bin",))

        assert page["bios_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_a_game_with_no_clear_region_keeps_the_platforms_amber(self):
        assert (await self._page(("World",), *self._JAPAN_ONLY))["bios_level"] == "partial"


class TestTheResolversNotesReachTheWire:
    """An emulator-level note crosses to the panel with every word a sentence is built from."""

    def test_a_search_that_found_nothing_carries_its_folder_and_candidates(self):
        note = EmulatorNote(kind="search-found-nothing", dir="/bios", candidates=2, regions=("ntsc-u",))
        verdict = CoreFirmwareVerdict(requirements_met=False, locating="by-name-then-content", notes=(note,))

        fields = emulator_fields(EmulatorAnswer(complete=True, verdict=verdict, region_decides=False, groups=()))

        assert fields["emulator_notes"] == [
            {
                "kind": "search-found-nothing",
                "setting": None,
                "tied": None,
                "dir": "/bios",
                "candidates": 2,
                "regions": ["ntsc-u"],
            }
        ]
