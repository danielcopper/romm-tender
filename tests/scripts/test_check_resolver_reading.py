"""Tests for ``scripts/check_resolver_reading.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. Fixtures lay out a small ``backend/`` tree under ``tmp_path`` and
point the check's lists at it, so each scan runs with its real file discovery.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_resolver_reading.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_resolver_reading", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_CODES = frozenset(check.resolver_codes().values())
_VALUES = check.resolver_values()
_WATCHED = _CODES | _VALUES
_EXPORTS = check.resolver_exports()

# What the shrinking lists held when they were written. Each may lose entries,
# never gain one: a new reader is built into the reading module of its kind,
# never added to a list, and a new homonym is an edit of this test.
_READERS_WHEN_WRITTEN: dict[str, frozenset[str]] = {
    "adapters/es_find_rules.py": frozenset({"staticpath"}),
    "adapters/repositories/rom_install.py": frozenset({"launchable"}),
    "domain/bios_status.py": frozenset(
        {
            "caveats",
            "checked",
            "declaration",
            "declared_kind",
            "locating",
            "requirements_met",
            "satisfied",
            "supplied_by",
            "system_firmware_met",
            "undetermined",
            "unmet",
        }
    ),
    "domain/firmware_groups.py": frozenset({"ntsc-j", "ntsc-u", "pal", "satisfied"}),
    "domain/firmware_rows.py": frozenset({"present"}),
    "domain/firmware_wants.py": frozenset(
        {
            "archive",
            "by-name",
            "by-name-then-content",
            "cannot-run-without-firmware",
            "caveats",
            "core-supplies-an-alternative",
            "declared_kind",
            "firmware-image-identified",
            "firmware-image-unlisted",
            "firmware-path-inaccessible",
            "firmware-scan-incomplete",
            "refused",
            "runs-without-firmware",
            "system_firmware",
            "unestablished",
            "unrecognised",
            "verified",
        }
    ),
    "domain/platform_system.py": frozenset({"declared", "disabled", "libretro", "screenscraper", "thegamesdb"}),
    "domain/save_answer.py": frozenset(
        {
            "caveats",
            "companion-config-missing",
            "config-unreadable",
            "content_directory",
            "cwd",
            "emulator-config-missing",
            "emulator-config-unreadable",
            "file-names-unestablished",
            "granularity",
            "marker-invalid",
            "marker-missing",
            "marker-unreadable",
            "needs",
            "not-set-up",
            "notes",
            "root_kind",
            "save-inside-content",
            "save-inside-image",
            "saves-root-missing",
            "shared-card",
            "shared-file",
            "size_fixed",
            "sorted-dir-missing",
        }
    ),
    "domain/source_roots.py": frozenset(
        {
            "assumed",
            "bios-root-missing",
            "frontend-default",
            "installation",
            "marker-invalid",
            "marker-missing",
            "marker-unreadable",
            "not-set-up",
            "provenance",
            "root-missing",
            "saves-root-missing",
        }
    ),
    "services/downloads/service.py": frozenset({"launchable"}),
    "services/firmware/downloads.py": frozenset({"satisfied"}),
    "services/firmware/emulator_answers.py": frozenset(
        {
            "declaration",
            "declared",
            "locating",
            "refused",
            "requirements_met",
        }
    ),
    "services/firmware/replacement.py": frozenset({"present", "satisfied"}),
    "services/firmware/rows.py": frozenset({"caveats", "checked", "declared_kind", "present", "satisfied"}),
    "services/firmware/service.py": frozenset({"known_as"}),
    "services/firmware/status.py": frozenset({"declaration", "present", "requirements_met", "satisfied"}),
    "services/firmware/store.py": frozenset({"present", "unread"}),
    "services/saves/save_directory.py": frozenset({"caveats", "root_kind"}),
    "services/saves/status/service.py": frozenset({"caveats", "granularity", "needs"}),
    "services/saves/sync_engine/matrix.py": frozenset({"size_fixed"}),
}

_GENERIC_WHEN_WRITTEN = frozenset(
    {
        "absent",
        "answered",
        "bios",
        "directory",
        "empty",
        "file",
        "igdb",
        "list",
        "missing",
        "none",
        "ok",
        "open",
        "optional",
        "read",
        "refused",
        "region",
        "required",
        "roms",
        "saves",
        "settings",
        "slug",
        "title",
        "unchecked",
        "unknown",
        "unread",
        "unreadable",
        "unsupported",
    }
)

_INTERNALS_WHEN_WRITTEN = frozenset(
    {
        ("adapters/atlas_firmware.py", "_vendor.atlas.firmware.core_short_name"),
        ("adapters/atlas_firmware.py", "_vendor.atlas.firmware.declared_directory_of"),
        ("adapters/atlas_firmware.py", "_vendor.atlas.firmware.load_hashes"),
    }
)

_NOT_THE_RESOLVERS_WHEN_WRITTEN: dict[str, frozenset[str]] = {
    "adapters/flatpak_install.py": frozenset({"flatpak"}),
    "adapters/gavel_native.py": frozenset({"native", "size_fixed"}),
    "domain/rom_adoption.py": frozenset({"mismatch"}),
    "domain/save_answer.py": frozenset({"unestablished"}),
    "host/inject/bundles.py": frozenset({"standalone"}),
    "host/server.py": frozenset({"refused"}),
    "services/emulator_sources.py": frozenset({"catalogue"}),
    "services/firmware/emulator_answers.py": frozenset({"setting"}),
    "services/rom_adoption/service.py": frozenset({"mismatch", "unread"}),
    "services/saves/status/service.py": frozenset({"checked", "unestablished"}),
}

_READING = "adapters/atlas_firmware.py"
_SERVICE = "services/firmware/rows.py"


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    backend = tmp_path / "backend"
    for name, source in files.items():
        path = backend / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return backend


@pytest.fixture
def lists(monkeypatch: pytest.MonkeyPatch):
    """Empty every list the scans consult; a test sets what it needs."""
    monkeypatch.setattr(check, "READING_MODULES", frozenset({_READING}))
    monkeypatch.setattr(check, "IMPORT_GRANTS", {})
    monkeypatch.setattr(check, "INTERNALS_GRANTED", {})
    monkeypatch.setattr(check, "INTERNALS_TO_REMOVE", frozenset())
    monkeypatch.setattr(check, "READERS_TODAY", {})
    monkeypatch.setattr(check, "NOT_THE_RESOLVERS", {})
    return monkeypatch


class TestTheRealTree:
    def test_nothing_reads_the_resolver_outside_the_lists(self):
        assert check.import_findings(check.BACKEND, _EXPORTS) == []
        assert check.vocabulary_findings(check.BACKEND, _WATCHED) == []
        assert check.generic_findings(check.BACKEND, check.FRONTEND_SRC, check.resolver_vocabulary_values()) == []

    def test_the_panels_copy_is_what_the_check_writes_today(self):
        assert check.frontend_findings(_CODES, _VALUES) == []

    def test_the_readers_list_only_shrinks(self):
        added = {
            (module, token)
            for module, tokens in check.READERS_TODAY.items()
            for token in tokens
            if token not in _READERS_WHEN_WRITTEN.get(module, frozenset())
        }

        assert added == set()

    def test_the_internals_list_only_shrinks(self):
        assert check.INTERNALS_TO_REMOVE <= _INTERNALS_WHEN_WRITTEN

    def test_the_generic_list_only_shrinks(self):
        assert set(check.GENERIC_VALUES) <= _GENERIC_WHEN_WRITTEN

    def test_the_homonym_list_only_shrinks(self):
        added = {
            (module, name)
            for module, names in check.NOT_THE_RESOLVERS.items()
            for name in names
            if name not in _NOT_THE_RESOLVERS_WHEN_WRITTEN.get(module, frozenset())
        }

        assert added == set()

    def test_the_reading_modules_and_the_grants_are_the_decided_ones(self):
        assert set(check.READING_MODULES) == {
            "adapters/atlas_catalogue.py",
            "adapters/atlas_firmware.py",
            "adapters/atlas_saves.py",
            "adapters/emulator_sources.py",
            "adapters/retrodeck_folders.py",
        }
        assert set(check.IMPORT_GRANTS) == {"adapters/atlas_zstd.py"}
        assert set(check.INTERNALS_GRANTED) == {("adapters/emulator_sources.py", "_vendor.atlas.machine.RealMachine")}


class TestTheVocabulary:
    def test_the_codes_are_the_four_families_by_their_constants_name(self):
        codes = check.resolver_codes()

        assert {name.split("_")[0] for name in codes} == {"CAVEAT", "HEALTH", "UNRESOLVED", "REASON"}
        assert codes["CAVEAT_FIRMWARE_IMAGE_IDENTIFIED"] == "firmware-image-identified"
        assert codes["HEALTH_ISSUE_MARKER_MISSING"] == "marker-missing"
        # The other closed vocabularies are watched by value, not as codes.
        assert "ROLE_SETTINGS" not in codes
        assert "GRANULARITY_SHARED_CARD" not in codes

    def test_the_values_are_every_closed_vocabulary_less_the_generic_words(self):
        assert {
            "cannot-run-without-firmware",
            "shared-card",
            "per-game-files",
            "assumed",
            "by-name-then-content",
            "unestablished",
            "packaged",
            "mismatch",
            "not-installed",
            "startable",
            "memory-card",
            "staticpath",
            "game-id",
            "libretro",
            "standalone",
            # Stated only in a tuple the resolver exports.
            "second-document",
            # Generic in the panel alone, which the backend still watches.
            "background",
            "disabled",
            "tag",
        } <= _VALUES
        assert {"read", "unknown", "none", "open", "unsupported", "file", "directory", "required"}.isdisjoint(_VALUES)
        # A code is no value: the codes are watched as codes.
        assert "marker-missing" not in check.resolver_vocabulary_values()

    def test_a_value_stated_only_in_a_union_of_literals_is_watched(self, monkeypatch):
        planted = SimpleNamespace(
            __all__=["PlantedStatus", "PlantedCount"],
            PlantedStatus=Literal["answered"] | Literal["planted-only-here", "planted-too"] | None,
            PlantedCount=int | str,
        )
        monkeypatch.setattr(check, "_vendored_atlas", lambda: planted)

        assert check.resolver_vocabulary_values() == {"answered", "planted-only-here", "planted-too"}

    def test_a_generic_word_the_resolver_no_longer_states_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check, "GENERIC_VALUES", {"not-a-value": "a reason"})
        backend = _tree(tmp_path, {_SERVICE: "x = 'not-a-value'\n"})

        assert check.generic_findings(backend, tmp_path / "src", check.resolver_vocabulary_values()) == [
            "GENERIC_VALUES lists 'not-a-value', which the resolver no longer states: take it off."
        ]

    def test_a_generic_word_tenders_own_code_no_longer_speaks_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check, "READING_MODULES", frozenset({_READING}))
        monkeypatch.setattr(check, "GENERIC_VALUES", {"per-game-file": "a reason"})
        # A docstring and a reading module do not speak it for Tender's own code.
        backend = _tree(tmp_path, {_SERVICE: '"""per-game-file"""\n', _READING: "x = 'per-game-file'\n"})

        assert check.generic_findings(backend, tmp_path / "src", check.resolver_vocabulary_values()) == [
            "GENERIC_VALUES lists 'per-game-file', which Tender's own code no longer speaks: take it off."
        ]

    @pytest.mark.parametrize(
        ("files", "panel"),
        [
            ({_SERVICE: "x = 'per-game-file'\n"}, {}),
            ({}, {"utils/words.ts": 'export const w = "per-game-file";\n'}),
        ],
    )
    def test_a_generic_word_is_needed_where_the_backend_or_the_panel_speaks_it(
        self, tmp_path, monkeypatch, files, panel
    ):
        monkeypatch.setattr(check, "GENERIC_VALUES", {"per-game-file": "a reason"})
        backend = _tree(tmp_path, files)
        backend.mkdir(exist_ok=True)
        for name, source in panel.items():
            path = tmp_path / "src" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")

        assert check.generic_findings(backend, tmp_path / "src", check.resolver_vocabulary_values()) == []

    def test_a_generic_word_in_a_panel_test_or_comment_is_not_spoken(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check, "GENERIC_VALUES", {"per-game-file": "a reason"})
        (tmp_path / "backend").mkdir()
        (tmp_path / "src" / "utils").mkdir(parents=True)
        (tmp_path / "src" / "utils" / "words.test.ts").write_text('const w = "per-game-file";\n', encoding="utf-8")
        (tmp_path / "src" / "utils" / "words.ts").write_text('// "per-game-file"\n', encoding="utf-8")

        assert check.generic_findings(tmp_path / "backend", tmp_path / "src", check.resolver_vocabulary_values()) == [
            "GENERIC_VALUES lists 'per-game-file', which Tender's own code no longer speaks: take it off."
        ]

    def test_every_watched_name_is_a_field_of_some_answer(self):
        # A field, or a property the answer computes (``requirements_met``).
        atlas = check._vendored_atlas()
        classes = [cls for name in atlas.__all__ if inspect.isclass(cls := getattr(atlas, name))]
        fields = {field.name for cls in classes if dataclasses.is_dataclass(cls) for field in dataclasses.fields(cls)}
        properties = {
            name for cls in classes for name in dir(cls) if isinstance(inspect.getattr_static(cls, name), property)
        }

        assert check.WATCHED_NAMES - fields - properties == set()


class TestImports:
    @pytest.mark.parametrize(
        "source",
        [
            "from _vendor.atlas import CAVEAT_FIRMWARE_UNREADABLE\n",
            "from _vendor import atlas\n",
            "import _vendor.atlas\n",
            "import importlib\nimportlib.import_module('_vendor.atlas')\n",
        ],
    )
    def test_a_module_that_reads_no_answer_may_not_import_the_resolver(self, tmp_path, lists, source):
        backend = _tree(tmp_path, {_SERVICE: source, _READING: source})

        assert check.import_findings(backend, _EXPORTS) == [
            f"backend/{_SERVICE}:{source.count(chr(10))} imports the resolver, and is no reading module."
        ]

    @pytest.mark.parametrize(
        ("source", "internal"),
        [
            ("from _vendor.atlas.firmware import load_hashes\n", "_vendor.atlas.firmware.load_hashes"),
            ("from _vendor.atlas import load_hashes\n", "_vendor.atlas.load_hashes"),
            ("import _vendor.atlas.firmware\n", "_vendor.atlas.firmware"),
            ("from _vendor import atlas\nx = atlas.firmware\n", "_vendor.atlas.firmware"),
            ("import _vendor.atlas\nx = _vendor.atlas.machine\n", "_vendor.atlas.machine"),
            ("from _vendor import atlas as resolver\nx = resolver.firmware\n", "_vendor.atlas.firmware"),
            ("import _vendor.atlas as resolver\nx = resolver.machine\n", "_vendor.atlas.machine"),
            ("import importlib\nimportlib.import_module('_vendor.atlas.machine')\n", "_vendor.atlas.machine"),
        ],
    )
    def test_a_reading_module_imports_only_what_the_resolver_exports(self, tmp_path, lists, source, internal):
        backend = _tree(tmp_path, {_READING: source})

        assert check.import_findings(backend, _EXPORTS) == [
            f"backend/{_READING}:{source.count(chr(10))} imports {internal}, which atlas.__all__ does not export."
        ]

    @pytest.mark.parametrize(
        "source",
        [
            "from _vendor.atlas import CAVEAT_FIRMWARE_UNREADABLE, FirmwareAnswer\n",
            "from _vendor.atlas import *\n",
            "from _vendor import atlas\nx = atlas.CAVEAT_FIRMWARE_UNREADABLE\n",
        ],
    )
    def test_a_reading_module_imports_what_the_resolver_exports(self, tmp_path, lists, source):
        backend = _tree(tmp_path, {_READING: source})

        assert check.import_findings(backend, _EXPORTS) == []

    def test_a_granted_import_passes_and_stays_needed(self, tmp_path, lists):
        lists.setattr(check, "IMPORT_GRANTS", {"adapters/codec.py": "a reason"})
        lists.setattr(check, "INTERNALS_GRANTED", {(_READING, "_vendor.atlas.machine.RealMachine"): "a reason"})
        backend = _tree(
            tmp_path,
            {
                "adapters/codec.py": "from _vendor.atlas import zstd_provider\n",
                _READING: "from _vendor.atlas.machine import RealMachine\n",
            },
        )

        assert check.import_findings(backend, _EXPORTS) == []

        (backend / "adapters" / "codec.py").write_text("", encoding="utf-8")
        (backend / _READING).write_text("", encoding="utf-8")

        assert check.import_findings(backend, _EXPORTS) == [
            f"backend/{_READING} holds _vendor.atlas.machine.RealMachine no longer: take it off INTERNALS_GRANTED.",
            "backend/adapters/codec.py imports the resolver no longer: take it off IMPORT_GRANTS.",
        ]

    def test_an_internal_on_the_list_passes_until_it_is_gone(self, tmp_path, lists):
        lists.setattr(check, "INTERNALS_TO_REMOVE", frozenset({(_READING, "_vendor.atlas.firmware.load_hashes")}))
        backend = _tree(tmp_path, {_READING: "from _vendor.atlas.firmware import load_hashes\n"})

        assert check.import_findings(backend, _EXPORTS) == []

        (backend / _READING).write_text("", encoding="utf-8")

        assert check.import_findings(backend, _EXPORTS) == [
            f"backend/{_READING} holds _vendor.atlas.firmware.load_hashes no longer: take it off INTERNALS_TO_REMOVE."
        ]

    def test_vendored_and_native_trees_are_not_scanned(self, tmp_path, lists):
        backend = _tree(
            tmp_path,
            {
                "_vendor/other/x.py": "from _vendor.atlas.firmware import load_hashes\n",
                "native/x.py": "from _vendor.atlas.firmware import load_hashes\n",
            },
        )

        assert check.import_findings(backend, _EXPORTS) == []


class TestVocabulary:
    @pytest.mark.parametrize(
        ("source", "token"),
        [
            ("x = row.requirements_met\n", "requirements_met"),
            ("x = Row(satisfied=True)\n", "satisfied"),
            ("x = getattr(row, 'locating', None)\n", "locating"),
            ("x = row['granularity']\n", "granularity"),
            ("x = row.get('declared_kind')\n", "declared_kind"),
            ("x = {'caveats': []}\n", "caveats"),
            ("x = 'marker-missing'\n", "marker-missing"),
            ("x = {'firmware-image-identified': 'Identified'}\n", "firmware-image-identified"),
            ("x = state == 'cannot-run-without-firmware'\n", "cannot-run-without-firmware"),
            ("x = granularity in ('shared-card',)\n", "shared-card"),
            ("x = provenance == 'assumed'\n", "assumed"),
            ("x = locating == 'by-name-then-content'\n", "by-name-then-content"),
            ("x = locating == 'unestablished'\n", "unestablished"),
            ("x = availability != 'not-installed'\n", "not-installed"),
            ("x = role == 'memory-card'\n", "memory-card"),
            ("x = rule == 'staticpath'\n", "staticpath"),
            ("x = declaration == 'packaged'\n", "packaged"),
            ("x = checked == 'not-comparable'\n", "not-comparable"),
        ],
    )
    def test_a_read_outside_a_reading_module_is_found(self, tmp_path, lists, source, token):
        backend = _tree(tmp_path, {_SERVICE: source, _READING: source})

        [finding] = check.vocabulary_findings(backend, _WATCHED)

        assert finding.startswith(f"backend/{_SERVICE}:1 names the resolver's ")
        assert f"{token!r} outside a reading module" in finding

    @pytest.mark.parametrize(
        "source",
        [
            '"""marker-missing"""\n',
            "def f():\n    '''marker-missing'''\n",
            "x = row.path + row.label + row.regions\n",
            "x = 'marker'\n",
            "x = ('read', 'unknown', 'none', 'open', 'unsupported')\n",
            "row.satisfied = True\n",
        ],
    )
    def test_what_the_scan_does_not_see_is_not_found(self, tmp_path, lists, source):
        backend = _tree(tmp_path, {_SERVICE: source})

        assert check.vocabulary_findings(backend, _WATCHED) == []

    def test_a_listed_module_reads_only_what_it_is_listed_for(self, tmp_path, lists):
        lists.setattr(check, "READERS_TODAY", {_SERVICE: frozenset({"satisfied", "marker-missing"})})
        backend = _tree(tmp_path, {_SERVICE: "x = (row.satisfied, 'marker-missing', row.checked)\n"})

        [finding] = check.vocabulary_findings(backend, _WATCHED)

        assert "'checked'" in finding

    def test_a_listed_read_the_module_no_longer_makes_fails(self, tmp_path, lists):
        lists.setattr(check, "READERS_TODAY", {_SERVICE: frozenset({"satisfied", "marker-missing"})})
        backend = _tree(tmp_path, {_SERVICE: "x = row.satisfied\n"})

        assert check.vocabulary_findings(backend, _WATCHED) == [
            f"backend/{_SERVICE} reads 'marker-missing' no longer: take it off READERS_TODAY."
        ]

    def test_a_name_that_means_something_else_passes_until_it_is_gone(self, tmp_path, lists):
        lists.setattr(check, "NOT_THE_RESOLVERS", {"host/server.py": {"refused": "a reason"}})
        backend = _tree(tmp_path, {"host/server.py": "x = verdict.refused\n"})

        assert check.vocabulary_findings(backend, _WATCHED) == []

        (backend / "host" / "server.py").write_text("", encoding="utf-8")

        assert check.vocabulary_findings(backend, _WATCHED) == [
            "backend/host/server.py reads 'refused' no longer: take it off NOT_THE_RESOLVERS."
        ]


class TestThePanelsCopy:
    def test_a_copy_that_differs_fails(self, tmp_path, monkeypatch):
        copy = tmp_path / "frontend" / "resolver-vocabulary.json"
        copy.parent.mkdir()
        monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(check, "FRONTEND_VOCABULARY", copy)

        assert check.frontend_findings(_CODES, _VALUES) == [
            "frontend/resolver-vocabulary.json does not exist: run `python scripts/check_resolver_reading.py --write`."
        ]

        copy.write_text(check.frontend_vocabulary(_CODES, _VALUES - {"shared-card"}), encoding="utf-8")

        [finding] = check.frontend_findings(_CODES, _VALUES)
        assert finding.startswith("frontend/resolver-vocabulary.json is not what the vendored resolver")

    def test_the_write_is_what_the_check_compares_with(self, tmp_path, monkeypatch):
        copy = tmp_path / "frontend" / "resolver-vocabulary.json"
        copy.parent.mkdir()
        monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(check, "FRONTEND_VOCABULARY", copy)

        assert check.main(["--write"]) == 0
        assert check.frontend_findings(_CODES, _VALUES) == []
