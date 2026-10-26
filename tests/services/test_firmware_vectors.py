"""A conformance sweep of Tender's BIOS level against the resolver's verdict, over the resolver's recorded machines.

Every firmware vector in ``tests/adapters/atlas_vectors/`` is a whole machine.
On each, the installation the vector names — else the first one detected — is
asked Tender's own question, ``firmware_for_system(<system>, verify=True)``,
for every system it lists and for the vector's own system (#2585 D2). Every
emulator that answer names is then taken as the launching one in turn (D3),
the platform page's read, :meth:`FirmwareService.get_platform_firmware_status`,
is run for it (D6, D7), and its ``bios_level`` is held against that emulator's
``requirements_met``: ``ok`` for met, ``missing`` for not met, ``unknown`` for
not established. Any other pairing is a mismatch — a yellow ``partial`` against
a verdict not established (D4), and a verdict Tender withholds because the
catalogue is sealed (D5), among them.

Only the level is compared. The rows, the counts, the one-of groups' payload
and whether a file is present are not, so a level that matches says nothing
about any of those.

Today's mismatches are :data:`KNOWN_MISMATCHES`, a list that only shrinks: a new
mismatch fails, and so does a listed one that now matches or now reads another
pair. Where a vector asks exactly Tender's question, the live answer is also
held equal to the one the vector recorded, so a sweep over a resolver that no
longer answers as recorded cannot pass on the strength of agreeing with itself.

The service is the real one over the real resolver adapter, which reads the
vector's machine through the emulator sources adapter; only the edges are
fakes — RomM's listing (empty), the unit of work, Tender's firmware file store,
the platform's system (its slug) and the emulator pick, which states the one
emulator under comparison rather than running Tender's own pick, since that
goes through derivations #2195 removes (D3). That is a service-layer test with
fakes at the edges, as Cosmic Python writes one, with one departure: the
resolver adapter is not faked, because the resolver's answer is what the level
is compared with.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from _factories import _make_conflict_rules
from _vendor import atlas
from _vendor.atlas.contract import firmware_contract
from _vendor.atlas.machine import FixtureMachine
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_core_info_provider import FakeCoreInfoProvider, standalone_option
from fakes.fake_firmware_file_store import FakeFirmwareFileStore
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_platform_systems import FakePlatformSystems
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock

from adapters.atlas_firmware import AtlasPlatformFirmwareAdapter
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.bios_status import BIOS_LEVEL_MISSING, BIOS_LEVEL_OK, BIOS_LEVEL_UNKNOWN
from domain.emulator_sources import SWITCHED_OFF_SETTING
from services.firmware import FirmwareService, FirmwareServiceConfig
from services.firmware.store import FirmwareStore, FirmwareStoreConfig

if TYPE_CHECKING:
    from domain.firmware_places import PlaceLook

_COPY = Path(__file__).resolve().parents[1] / "adapters" / "atlas_vectors"
_MACHINES = _COPY / "vectors" / "machines"
_VENDORED_MANIFEST = Path(__file__).resolve().parents[2] / "backend" / "_vendor" / "atlas.SHA256SUMS"

_LEVEL_OF_VERDICT: dict[bool | None, str] = {
    True: BIOS_LEVEL_OK,
    False: BIOS_LEVEL_MISSING,
    None: BIOS_LEVEL_UNKNOWN,
}

# (file stem, vector name, system, emulator) -> (Tender's level, the resolver's verdict)
KNOWN_MISMATCHES: dict[tuple[str, str, str, str], tuple[str, bool | None]] = {
    # D4: yellow where the resolver could not establish a verdict — #2580 removes these.
    (
        "disc-region",
        "beetle-psx-a-chd-extracted-into-a-cache-directory-atlas-cannot-place",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "disc-region",
        "beetle-psx-a-chd-whose-codec-this-runtime-lacks-decides-nothing",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "disc-region",
        "beetle-psx-a-core-that-cannot-be-queried-leaves-the-archive-s-entry-unknown",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "disc-region",
        "beetle-psx-a-cue-extracted-into-a-cache-directory-atlas-cannot-place",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "disc-region",
        "beetle-psx-a-cue-extracted-without-its-bin-is-content-it-cannot-open",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    ("disc-region", "beetle-psx-a-first-entry-it-cannot-open-decides-nothing", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-a-sector-that-fails-its-checksum-decides-nothing", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-a-system-cnf-without-a-boot-line-decides-nothing", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    (
        "disc-region",
        "beetle-psx-an-archive-entry-in-a-folder-is-extracted-into-the-cache-directory",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "disc-region",
        "beetle-psx-an-archive-entry-in-a-folder-is-extracted-nowhere",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    ("disc-region", "beetle-psx-an-encrypted-pbp-is-a-format-atlas-does-not-read", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-does-not-open-a-bare-bin", "psx", "mednafen_psx_libretro.so"): ("partial", None),
    ("disc-region", "beetle-psx-does-not-open-a-chd-that-names-a-parent", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-keeps-a-playlist-line-s-leading-spaces", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-loads-the-toc-beside-a-chd", "psx", "mednafen_psx_libretro.so"): ("partial", None),
    ("disc-region", "beetle-psx-loads-the-toc-beside-a-cue-sheet", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-loads-the-toc-named-for-a-playlist", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-opens-a-cdzs-chd", "psx", "mednafen_psx_libretro.so"): ("partial", None),
    ("disc-region", "beetle-psx-reads-a-pbp-duckstation-and-swanstation-refuse", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-reads-only-a-boot-line-under-cdrom-backslash", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-reads-the-serial-before-the-licence", "psx", "mednafen_psx_libretro.so"): (
        "partial",
        None,
    ),
    ("disc-region", "beetle-psx-searches-the-licence-for-words", "psx", "mednafen_psx_libretro.so"): ("partial", None),
    (
        "firmware",
        "firmware-beetle-psx-a-spelling-linked-out-of-the-root-is-followed",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "firmware",
        "firmware-beetle-psx-boots-a-region-image-under-a-later-spelling",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "firmware",
        "firmware-beetle-psx-with-an-override-that-names-no-file-falls-back-to-the-region-names",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    (
        "firmware",
        "retrodeck-a-catalogue-listing-one-core-twice-states-an-unreadable-file-once",
        "psx",
        "mednafen_psx_libretro.so",
    ): ("partial", None),
    # D5: EmuDeck's sealed catalogue, whose verdict Tender withholds where the resolver states one.
    ("firmware", "emudeck-duckstation-searches-from-the-dataroot-its-launch-picks", "psx", "DUCKSTATION"): (
        "unknown",
        True,
    ),
    (
        "firmware",
        "emudeck-firmware-inventory-the-marker-cross-check-joins-the-sealed-statement",
        "gb",
        "gambatte_libretro.so",
    ): ("unknown", True),
    ("firmware", "emudeck-firmware-the-marker-cross-check-joins-the-sealed-statement", "gb", "gambatte_libretro.so"): (
        "unknown",
        True,
    ),
    ("firmware", "emudeck-firmware-the-sealed-catalogue-is-the-enumeration", "gb", "gambatte_libretro.so"): (
        "unknown",
        True,
    ),
    ("firmware", "emudeck-the-launcher-route-carries-the-keys-expectation", "wiiu", "cemu"): ("unknown", True),
    ("firmware", "emudeck-xemu-a-package-value-reads-through-the-deploy-its-launch-runs", "xbox", "XEMU"): (
        "unknown",
        True,
    ),
    ("firmware", "emudeck-xemu-reads-the-flatpaks-own-data-home", "xbox", "XEMU"): ("unknown", False),
    ("firmware", "emudeck-xemu-the-same-value-on-an-id-less-launch-is-refused", "xbox", "XEMU"): ("unknown", False),
}

# Firmware vectors whose installation lists no system, so there is no platform
# question to put to it: core and inventory questions on such a machine.
VECTORS_WITHOUT_A_SYSTEM = 33

# Firmware vectors that ask exactly Tender's question, so their recorded answer
# is the live one's to equal.
VECTORS_ASKING_TENDERS_QUESTION = 25


def _firmware_vectors() -> list[tuple[str, dict[str, Any]]]:
    vectors = []
    for path in sorted(_MACHINES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        vectors.extend((path.stem, vector) for vector in data["vectors"] if "firmware_query" in vector["input"])
    return vectors


_VECTORS = _firmware_vectors()


def fixture_machine(inp: dict[str, Any]) -> FixtureMachine:
    """The vector's machine, built as upstream's runner builds it.

    emu-atlas ``tests/test_machine_vectors.py`` at ``v0.28.0``, lines 68-83.
    """
    return FixtureMachine(
        inp["files"],
        symlinks=inp.get("symlinks"),
        cores=inp.get("cores"),
        dirs=inp.get("dirs"),
        inaccessible=inp.get("inaccessible"),
        unlistable=inp.get("unlistable"),
        appimages=inp.get("appimages"),
        appimage_stamps=inp.get("appimage_stamps"),
        ps2_bios_headers=inp.get("ps2_bios_headers"),
        disc_identities=inp.get("disc_identities"),
        archive_texts=inp.get("archive_texts"),
        archives=inp.get("archives"),
        whdload_slaves=inp.get("whdload_slaves"),
    )


def _target(inp: dict[str, Any]) -> tuple[Any, tuple[str, ...]]:
    """The installation the vector's question names, else the first detected; and every kind detected.

    The selection is upstream's ``_select``, the same file, lines 86-92.
    """
    installs = atlas.detect(inp["home"], fixture_machine(inp))
    selector = inp["firmware_query"].get("installation")
    kinds = tuple(install.kind for install in installs)
    if selector is None:
        assert installs, "no installation detected to answer the query"
        return installs[0], kinds
    return next(install for install in installs if install.kind == selector), kinds


def _systems(inp: dict[str, Any], installation: Any) -> list[str]:
    """Every system *installation* lists, and the vector's own where it asks about one."""
    systems = set(installation.systems().systems)
    query = inp["firmware_query"]
    if query["kind"] == "system":
        systems.add(query["system"])
    return sorted(systems)


def _asks_tenders_question(query: dict[str, Any]) -> bool:
    return (
        query["kind"] == "system" and query.get("verify") is True and "cwd" not in query and "content_path" not in query
    )


def _verdicts(answer: Any) -> dict[str, bool | None]:
    """Each emulator's ``requirements_met``; for an identity named twice the first entry's, as the adapter reads it."""
    verdicts: dict[str, bool | None] = {}
    for core in answer.cores:
        if core.emulator is not None:
            verdicts.setdefault(core.emulator, core.requirements_met)
    return verdicts


class _LookingFileStore(FakeFirmwareFileStore):
    """The in-memory file store, noting every place Tender looks at."""

    def __init__(self) -> None:
        super().__init__()
        self.looked_at: list[str] = []

    def look(self, path: str) -> PlaceLook:
        self.looked_at.append(path)
        return super().look(path)


def _service(
    inp: dict[str, Any], answering: str, detected: tuple[str, ...], emulator: str, files: _LookingFileStore
) -> FirmwareService:
    """The firmware service over the vector's machine, its answers from *answering*, judging by *emulator*."""
    log = logging.getLogger(__name__)
    sources = EmulatorSourcesAdapter(
        user_home=inp["home"],
        settings={SWITCHED_OFF_SETTING: [kind for kind in detected if kind != answering]},
        log_debug=log.debug,
        machine=fixture_machine(inp),
    )
    assert sources.read().answering_kind == answering
    resolver = AtlasPlatformFirmwareAdapter(sources=sources, log_debug=log.debug)
    romm_api = FakeRommApi()
    uows = FakeUnitOfWorkFactory()
    clock = FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC))
    platform_systems = FakePlatformSystems()

    def no_folder() -> str:
        raise AssertionError("a status read writes nothing into Tender's folder")

    store = FirmwareStore(
        config=FirmwareStoreConfig(
            romm_api=romm_api,
            firmware_file_store=files,
            platform_firmware_resolver=resolver,
            platform_systems=platform_systems,
            prepare_folder=no_folder,
            uow_factory=uows,
            clock=clock,
            data_dir="/nowhere/data",
            logger=log,
        )
    )
    return FirmwareService(
        config=FirmwareServiceConfig(
            romm_api=romm_api,
            loop=running_loop(),
            logger=log,
            clock=clock,
            firmware_file_store=files,
            platform_firmware_resolver=resolver,
            firmware_identifier=lambda md5: None,
            store=store,
            retrodeck_folders=FakeRetroDeckFolders(),
            core_info=FakeCoreInfoProvider(options=[standalone_option("emulator %ROM%", emulator, emulator=emulator)]),
            platform_systems=platform_systems,
            platform_core_reader=FakePlatformCoreReader(),
            active_core=FakeActiveCoreResolver(),
            uow_factory=uows,
            conflict_rules=_make_conflict_rules(),
            settings={},
            settings_persister=FakeSettingsPersister(),
        )
    )


@dataclass
class _Sweep:
    """What one vector's machine answered: the mismatches, how many pairs were compared, every place looked at."""

    mismatches: dict[tuple[str, str, str, str], tuple[str, bool | None]] = field(default_factory=dict)
    compared: int = 0
    looked_at: list[str] = field(default_factory=list)


async def _sweep(stem: str, vector: dict[str, Any]) -> _Sweep:
    inp = vector["input"]
    installation, detected = _target(inp)
    sweep = _Sweep()
    for system in _systems(inp, installation):
        answer = installation.firmware_for_system(system, verify=True)
        for emulator, verdict in _verdicts(answer).items():
            files = _LookingFileStore()
            service = _service(inp, installation.kind, detected, emulator, files)
            platform = (await service.get_platform_firmware_status(system))["platform"]
            assert platform["active_core"] == emulator
            sweep.compared += 1
            sweep.looked_at.extend(files.looked_at)
            if platform["bios_level"] != _LEVEL_OF_VERDICT[verdict]:
                sweep.mismatches[(stem, vector["name"], system, emulator)] = (platform["bios_level"], verdict)
    return sweep


@pytest.mark.parametrize(("stem", "vector"), [pytest.param(s, v, id=f"{s}:{v['name']}") for s, v in _VECTORS])
async def test_the_level_is_the_resolvers_verdict(stem: str, vector: dict[str, Any]) -> None:
    sweep = await _sweep(stem, vector)
    known = {key: pair for key, pair in KNOWN_MISMATCHES.items() if key[:2] == (stem, vector["name"])}
    assert sweep.mismatches == known


@pytest.mark.parametrize(
    ("stem", "vector"),
    [
        pytest.param(s, v, id=f"{s}:{v['name']}")
        for s, v in _VECTORS
        if _asks_tenders_question(v["input"]["firmware_query"])
    ],
)
def test_the_live_answer_is_the_recorded_one(stem: str, vector: dict[str, Any]) -> None:
    inp = vector["input"]
    installation, _ = _target(inp)
    answer = installation.firmware_for_system(inp["firmware_query"]["system"], verify=True)
    assert firmware_contract(answer) == vector["expected"]["firmware"]


def test_the_vectors_asking_tenders_question_are_counted() -> None:
    assert sum(1 for _, v in _VECTORS if _asks_tenders_question(v["input"]["firmware_query"])) == (
        VECTORS_ASKING_TENDERS_QUESTION
    )


def test_the_vectors_without_a_system_are_counted() -> None:
    """A machine whose installation lists no system has no platform to ask about, so it is not compared."""
    without = sum(1 for _, v in _VECTORS if not _systems(v["input"], _target(v["input"])[0]))
    assert without == VECTORS_WITHOUT_A_SYSTEM


def test_every_listed_mismatch_names_a_recorded_vector() -> None:
    """A listed mismatch whose vector is gone would never be checked again, and the list would hold it forever."""
    recorded = {(stem, v["name"]) for stem, v in _VECTORS}
    assert {key[:2] for key in KNOWN_MISMATCHES} <= recorded


async def test_tenders_look_at_a_place_reaches_the_in_memory_store() -> None:
    """Tender looks at a place in the store it was handed, never on this machine's disk.

    The places a vector names are real-looking paths — a home at
    ``/home/deck``, an SD card under ``/mnt/sd`` — so a look that reached the
    disk would read whatever this machine holds there. This vector's emulator
    declares one file, so the row reading looks at its place once.
    """
    stem, vector = next(
        (s, v)
        for s, v in _VECTORS
        if s == "firmware" and v["name"] == "retrodeck-firmware-a-catalogue-entry-states-the-declarations-nobody-reads"
    )
    sweep = await _sweep(stem, vector)
    assert sweep.compared == 1
    assert sweep.looked_at == ["/mnt/sd/retrodeck/bios/gb_bios.bin"]


def test_the_copy_is_the_vendored_resolvers_release() -> None:
    """The release the copy's README names is the resolver's, and upstream's own manifest lists its asset's digest."""
    readme = (_COPY / "README.md").read_text(encoding="utf-8")
    releases = re.findall(r"`(emu-atlas-vectors-v([0-9][^`]*)\.tar\.gz)`\s+\(`sha256:([0-9a-f]{64})`\)", readme)
    assert len(releases) == 1
    asset, version, digest = releases[0]
    assert version == atlas.__version__
    assert f"{digest}  {asset}" in _VENDORED_MANIFEST.read_text(encoding="utf-8").splitlines()
