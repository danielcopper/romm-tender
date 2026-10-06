"""Tests for adapters/emulator_sources.py — detection per reading, one machine, the settings list."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

import pytest
from _vendor.atlas import (
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
    HEALTH_ISSUE_CATALOGUE_INVALID,
    HEALTH_ISSUE_MARKER_INVALID,
    HEALTH_ISSUE_ROOT_MISSING,
)
from _vendor.atlas import machine as atlas_machine
from _vendor.atlas.installations import Health, SystemsAnswer
from _vendor.atlas.machine import CORE_READ_TIMED_OUT, CoreReading, RealMachine
from _vendor.atlas.placement import Caveat

from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.emulator_sources import CATALOGUE_READ, CATALOGUE_SEALED, CATALOGUE_UNAVAILABLE, SourceFinding


class _Installation:
    """A detected installation answering prepared health, root and systems."""

    def __init__(
        self,
        kind: str,
        *,
        issues: tuple[Caveat, ...] = (),
        root: str = "/run/media/deck/Emulation/retrodeck",
        systems: SystemsAnswer | Exception | None = None,
        health_raises: Exception | None = None,
    ) -> None:
        self.kind = kind
        self._issues = issues
        self._root = root
        self._systems = systems if systems is not None else SystemsAnswer(systems=("gba",))
        self._health_raises = health_raises

    def health(self) -> Health:
        if self._health_raises is not None:
            raise self._health_raises
        return Health(self._issues)

    def root(self) -> str:
        return self._root

    def systems(self) -> SystemsAnswer:
        if isinstance(self._systems, Exception):
            raise self._systems
        return self._systems


class _Detect:
    """A detection that answers what a test names and records every machine it was handed."""

    def __init__(self, *installations: _Installation) -> None:
        self.installations = list(installations)
        self.machines: list[Any] = []

    def __call__(self, home: str, machine: Any) -> list[Any]:
        self.machines.append(machine)
        return list(self.installations)


@pytest.fixture
def traces() -> list[str]:
    return []


def _holder(detect: Any, traces: list[str], settings: dict[str, Any] | None = None, **kwargs: Any):
    return EmulatorSourcesAdapter(
        user_home="/home/deck",
        settings=settings if settings is not None else {},
        log_debug=traces.append,
        detect_installations=detect,
        **kwargs,
    )


class TestDetection:
    def test_nothing_is_detected_at_construction(self, traces):
        detect = _Detect(_Installation("retrodeck"))
        _holder(detect, traces)
        assert detect.machines == []

    def test_every_reading_detects_afresh_through_one_machine(self, traces):
        detect = _Detect(_Installation("retrodeck"))
        holder = _holder(detect, traces)

        holder.read()
        holder.read()

        assert len(detect.machines) == 2
        assert detect.machines[0] is detect.machines[1]

    def test_the_held_machine_is_the_resolvers_real_one(self, traces):
        detect = _Detect()
        _holder(detect, traces).read()
        assert isinstance(detect.machines[0], RealMachine)

    def test_a_source_installed_later_appears_on_the_next_reading(self, traces):
        detect = _Detect()
        holder = _holder(detect, traces)
        assert holder.read().sources == ()

        detect.installations.append(_Installation("emudeck"))

        assert [source.kind for source in holder.read().sources] == ["emudeck"]

    def test_a_raising_detection_reads_as_no_source(self, traces):
        def explode(home: str, machine: Any) -> list[Any]:
            raise OSError("no such home")

        reading = _holder(explode, traces).read()

        assert reading.sources == ()
        assert reading.answering is None
        assert reading.no_answer_reason() == "no_source"
        assert any("detection failed" in line for line in traces)


class TestTheSettingsAreReadLive:
    def test_a_switch_written_after_a_reading_holds_from_the_next_one(self, traces):
        settings: dict[str, Any] = {"emulator_sources_off": []}
        holder = _holder(_Detect(_Installation("retrodeck"), _Installation("emudeck")), traces, settings)
        first = holder.read()

        settings["emulator_sources_off"] = ["retrodeck"]
        second = holder.read()

        assert first.answering_kind == "retrodeck"
        assert second.answering_kind == "emudeck"

    def test_the_stored_order_arranges_the_sources(self, traces):
        settings = {"emulator_source_order": ["emudeck", "retrodeck"]}
        reading = _holder(_Detect(_Installation("retrodeck"), _Installation("emudeck")), traces, settings).read()

        assert [source.kind for source in reading.sources] == ["emudeck", "retrodeck"]
        assert reading.answering_kind == "retrodeck"

    @pytest.mark.parametrize("stored", [None, "emudeck", {"emudeck": True}, [1, None]])
    def test_a_stored_value_that_is_not_a_list_of_kinds_is_read_as_none(self, traces, stored):
        settings = {"emulator_source_order": stored, "emulator_sources_off": stored}
        reading = _holder(_Detect(_Installation("retrodeck"), _Installation("emudeck")), traces, settings).read()

        assert [(source.kind, source.enabled) for source in reading.sources] == [
            ("retrodeck", True),
            ("emudeck", True),
        ]


class TestAReadingRemembers:
    def test_a_question_is_asked_once_per_reading(self, traces):
        holder = _holder(_Detect(_Installation("retrodeck")), traces)
        asked: list[int] = []
        reading = holder.read()

        for _ in range(3):
            reading.remember(("emulators_for", "gba"), lambda: asked.append(1) or len(asked))

        assert asked == [1]

    def test_two_readings_ask_twice(self, traces):
        holder = _holder(_Detect(_Installation("retrodeck")), traces)
        asked: list[int] = []

        for _ in range(2):
            holder.read().remember(("emulators_for", "gba"), lambda: asked.append(1))

        assert asked == [1, 1]

    def test_the_answering_installation_is_the_detected_handle(self, traces):
        retrodeck = _Installation("retrodeck")
        reading = _holder(_Detect(_Installation("emudeck"), retrodeck), traces).read()

        assert reading.answering_installation() is retrodeck
        assert reading.installation("missing") is None


class TestDescribe:
    def test_both_sources_are_listed_with_their_health_and_switch(self, traces):
        retrodeck = _Installation("retrodeck", issues=(Caveat(HEALTH_ISSUE_ROOT_MISSING, "prose", {"path": "/sd"}),))
        emudeck = _Installation(
            "emudeck",
            root="/run/media/deck/Emulation/Emulation",
            systems=SystemsAnswer(systems=("psx",), caveats=(Caveat(CAVEAT_EMULATOR_CATALOGUE_SEALED, "prose"),)),
        )
        holder = _holder(_Detect(retrodeck, emudeck), traces, {"emulator_sources_off": ["emudeck"]})

        reports = holder.describe()

        assert [(r.kind, r.enabled, r.starts_games) for r in reports] == [
            ("retrodeck", True, True),
            ("emudeck", False, False),
        ]
        assert reports[0].findings == (SourceFinding(code=HEALTH_ISSUE_ROOT_MISSING, data={"path": "/sd"}),)
        assert reports[0].root == "/run/media/deck/Emulation/retrodeck"
        assert reports[0].catalogue == CATALOGUE_READ
        assert reports[1].findings == ()
        assert reports[1].root == "/run/media/deck/Emulation/Emulation"
        assert reports[1].catalogue == CATALOGUE_SEALED

    def test_a_broken_systems_file_costs_only_its_own_source(self, traces):
        broken = _Installation(
            "retrodeck",
            issues=(
                Caveat(HEALTH_ISSUE_CATALOGUE_INVALID, "prose", {"path": "/rd/custom.xml", "problem": "parse-error"}),
            ),
            systems=SystemsAnswer(caveats=(Caveat(HEALTH_ISSUE_CATALOGUE_INVALID, "prose"),)),
        )
        healthy = _Installation("bare_retroarch_native", root="/home/deck/.config/retroarch")

        broken_report, healthy_report = _holder(_Detect(broken, healthy), traces).describe()

        assert broken_report.catalogue == CATALOGUE_UNAVAILABLE
        assert [finding.code for finding in broken_report.findings] == [HEALTH_ISSUE_CATALOGUE_INVALID]
        assert healthy_report.findings == ()
        assert healthy_report.catalogue == CATALOGUE_READ
        assert healthy_report.root == "/home/deck/.config/retroarch"

    def test_a_given_reading_is_described_as_the_settings_stand_now_without_detecting_again(self, traces):
        settings: dict[str, Any] = {"emulator_sources_off": []}
        detect = _Detect(_Installation("retrodeck"), _Installation("emudeck"))
        holder = _holder(detect, traces, settings)
        reading = holder.read()
        settings["emulator_sources_off"] = ["emudeck"]

        reports = holder.describe(reading)

        assert len(detect.machines) == 1
        assert [(r.kind, r.enabled) for r in reports] == [("retrodeck", True), ("emudeck", False)]

    def test_the_root_is_left_out_while_the_settings_file_is_broken(self, traces):
        broken = _Installation(
            "retrodeck", issues=(Caveat(HEALTH_ISSUE_MARKER_INVALID, "prose", {"path": "/rd.json"}),)
        )
        (report,) = _holder(_Detect(broken), traces).describe()

        assert report.root is None
        assert report.findings == (SourceFinding(code=HEALTH_ISSUE_MARKER_INVALID, data={"path": "/rd.json"}),)

    def test_a_refused_catalogue_reads_unavailable(self, traces):
        refused = _Installation(
            "bare_retroarch_native",
            systems=SystemsAnswer(caveats=(Caveat(CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE, "prose"),)),
        )
        (report,) = _holder(_Detect(refused), traces).describe()

        assert report.catalogue == CATALOGUE_UNAVAILABLE
        assert report.starts_games is False

    def test_a_resolver_that_raises_costs_only_what_it_answered(self, traces):
        failing = _Installation("retrodeck", health_raises=ValueError("invariant"), systems=ValueError("loader"))
        (report,) = _holder(_Detect(failing), traces).describe()

        assert report.findings == ()
        assert report.catalogue == CATALOGUE_UNAVAILABLE
        assert any("resolver failed on health" in line for line in traces)


class TestTheCoreProbeRunsOncePerCoreFile:
    """One machine for the process: an unchanged core is probed once, a changed one again."""

    @pytest.fixture
    def probes(self, monkeypatch) -> list[str]:
        probed: list[str] = []

        def probe(so_path: str) -> tuple[CoreReading, bool]:
            # A probe killed at its timeout is remembered like an answer, and
            # it needs no core that loads.
            probed.append(so_path)
            return CoreReading(CORE_READ_TIMED_OUT), True

        monkeypatch.setattr(atlas_machine.RealMachine, "_probe", staticmethod(probe))
        return probed

    def test_detections_through_the_holder_share_the_probe_memory(self, traces, probes, tmp_path):
        core = tmp_path / "mgba_libretro.so"
        core.write_bytes(b"\x7fELF")
        detect = _Detect(_Installation("retrodeck"))
        holder = _holder(detect, traces)

        for _ in range(3):
            holder.read()
            detect.machines[-1].read_core(str(core))

        assert probes == [str(core)]

        os.utime(core, ns=(1, 1))
        holder.read()
        detect.machines[-1].read_core(str(core))

        assert probes == [str(core), str(core)]


def _seed_retrodeck(tmp_path, *, custom: str | None) -> str:
    """A RetroDECK marker, a valid bundled catalogue, and optionally a custom systems overlay."""
    home = tmp_path / "home"
    rd_home = home / "retrodeck"
    marker = home / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck" / "retrodeck.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"paths": {"rd_home_path": str(rd_home)}}), encoding="utf-8")
    (rd_home / "saves").mkdir(parents=True)
    bundled = (
        home
        / ".local"
        / "share"
        / "flatpak"
        / "app"
        / "net.retrodeck.retrodeck"
        / "current"
        / "active"
        / "files"
        / "retrodeck"
        / "components"
        / "es-de"
        / "share"
        / "es-de"
        / "resources"
        / "systems"
        / "linux"
        / "es_systems.xml"
    )
    bundled.parent.mkdir(parents=True)
    bundled.write_text(
        '<?xml version="1.0"?>\n<systemList><system><name>gba</name><extension>.gba</extension>'
        '<command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>'
        "</system></systemList>\n",
        encoding="utf-8",
    )
    if custom is not None:
        overlay = rd_home / "ES-DE" / "custom_systems" / "es_systems.xml"
        overlay.parent.mkdir(parents=True)
        overlay.write_text(custom, encoding="utf-8")
    return str(home)


@pytest.fixture
def _no_host_deploy(tmp_path, monkeypatch):
    """Never resolve ``/app`` out of the dev box's own RetroDECK deploy (as ``test_atlas_catalogue.py``)."""
    monkeypatch.setattr(
        "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
    )


@pytest.mark.usefixtures("_no_host_deploy")
class TestTheRealResolver:
    def test_a_broken_custom_systems_file_is_a_finding_with_its_file_and_reason(self, tmp_path, traces):
        home = _seed_retrodeck(tmp_path, custom="<systemList><system>")
        holder = EmulatorSourcesAdapter(user_home=home, settings={}, log_debug=traces.append)

        (report,) = holder.describe()

        invalid = [finding for finding in report.findings if finding.code == HEALTH_ISSUE_CATALOGUE_INVALID]
        assert len(invalid) == 1
        assert invalid[0].data["path"].endswith(os.path.join("ES-DE", "custom_systems", "es_systems.xml"))
        assert invalid[0].data["problem"] == "parse-error"

    def test_a_malformed_settings_file_is_listed_with_its_finding_and_no_root(self, tmp_path, traces):
        home = _seed_retrodeck(tmp_path, custom=None)
        marker = os.path.join(home, ".var", "app", "net.retrodeck.retrodeck", "config", "retrodeck", "retrodeck.json")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        holder = EmulatorSourcesAdapter(user_home=home, settings={}, log_debug=traces.append)

        (report,) = holder.describe()

        assert report.kind == "retrodeck"
        assert HEALTH_ISSUE_MARKER_INVALID in {finding.code for finding in report.findings}
        assert report.root is None


_BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _modules_importing_detect(root: Path) -> list[str]:
    """Every module under *root* (outside ``_vendor``) that imports the resolver's ``detect``."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "_vendor" in path.relative_to(root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("_vendor.atlas"):
                continue
            if node.module == "_vendor.atlas.detect" or any(alias.name == "detect" for alias in node.names):
                found.append(path.relative_to(root).as_posix())
    return found


class TestOnlyTheHolderDetects:
    """The sources are detected in one place; every other adapter asks the holder."""

    def test_no_module_but_the_holder_imports_detect(self):
        assert _modules_importing_detect(_BACKEND) == ["adapters/emulator_sources.py"]

    def test_the_scan_sees_an_import_elsewhere(self, tmp_path):
        (tmp_path / "adapters").mkdir()
        (tmp_path / "adapters" / "rogue.py").write_text("from _vendor.atlas import detect\n", encoding="utf-8")
        (tmp_path / "adapters" / "deep.py").write_text("from _vendor.atlas.detect import detect\n", encoding="utf-8")
        (tmp_path / "adapters" / "fine.py").write_text("from _vendor.atlas import KIND_LIBRETRO\n", encoding="utf-8")

        assert _modules_importing_detect(tmp_path) == ["adapters/deep.py", "adapters/rogue.py"]
