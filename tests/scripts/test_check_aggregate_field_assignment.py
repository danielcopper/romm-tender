"""Tests for ``scripts/check_aggregate_field_assignment.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path`` (and is excluded from ruff/basedpyright). Fixtures use
``tmp_path`` to lay out a small ``backend/`` tree that the check walks,
monkeypatching the script's ``REPO_ROOT`` / ``BACKEND_DIR`` / ``DOMAIN_DIR``
constants for the duration of the test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_aggregate_field_assignment.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_aggregate_field_assignment", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_ROM = (
    "from domain._aggregate import cosmic_aggregate\n"
    "@cosmic_aggregate\n"
    "class Rom:\n"
    "    name: str\n"
    "    cover_path: str | None\n"
    "    fs_name: str\n"
    "    regions: tuple[str, ...]\n"
    "    def rename(self, name: str) -> None:\n"
    "        self.name = name\n"
)

_SAVE_STATE = (
    "from domain._aggregate import cosmic_aggregate\n"
    "@cosmic_aggregate\n"
    "class RomSaveSyncState:\n"
    "    rom_id: int\n"
    "    files: dict[str, int]\n"
    "    own_upload_ids: list[int] | None\n"
    "    def record(self, key: str, value: int) -> None:\n"
    "        self.files[key] = value\n"
    "        self.files.pop('stale', None)\n"
)

_PLAYTIME = (
    "from domain._aggregate import cosmic_aggregate\n"
    "@cosmic_aggregate\n"
    "class Playtime:\n"
    "    rom_id: int\n"
    "    total_seconds: int\n"
)

_DOMAIN = {"rom.py": _ROM, "rom_save_sync_state.py": _SAVE_STATE, "playtime.py": _PLAYTIME}


def _make_fake_backend(tmp_path: Path, files: dict[str, str]) -> Path:
    """Build a fake ``backend/`` tree under ``tmp_path``; keys are paths relative to it."""
    backend_dir = tmp_path / "backend"
    (backend_dir / "domain").mkdir(parents=True)
    for name, source in files.items():
        path = backend_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return backend_dir


@pytest.fixture
def patched_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Yield a helper that retargets the check at a tmp-path ``backend/`` + runs it."""

    def _run(*, domain_files: dict[str, str] | None = None, **dirs: dict[str, str]):
        files = {f"domain/{name}": source for name, source in (domain_files or {}).items()}
        for directory, sources in dirs.items():
            files.update({f"{directory.removesuffix('_files')}/{name}": source for name, source in sources.items()})
        backend_dir = _make_fake_backend(tmp_path, files)
        monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(check, "BACKEND_DIR", backend_dir)
        monkeypatch.setattr(check, "DOMAIN_DIR", backend_dir / "domain")
        return check.collect_aggregate_fields(backend_dir / "domain"), check.find_violations()

    return _run


class TestCollectAggregateFields:
    def test_collects_classes_decorated_with_bare_name(self, patched_check):
        fields, _ = patched_check(domain_files={"rom.py": _ROM})
        assert fields.classes == {"Rom"}

    def test_collects_classes_decorated_through_an_attribute(self, patched_check):
        fields, _ = patched_check(
            domain_files={
                "rom.py": "from domain import _aggregate\n@_aggregate.cosmic_aggregate\nclass Rom:\n    a: int\n"
            },
        )
        assert fields.classes == {"Rom"}

    def test_ignores_classes_without_decorator(self, patched_check):
        fields, _ = patched_check(domain_files={"save_state.py": "class SaveSyncState:\n    files: dict\n"})
        assert fields.classes == set()
        assert fields.owners == {}

    def test_mixed_decorated_and_undecorated(self, patched_check):
        fields, _ = patched_check(domain_files={"mixed.py": _ROM + "class LegacyContainer:\n    legacy: int\n"})
        assert fields.classes == {"Rom"}
        assert "legacy" not in fields.owners

    def test_handles_syntax_error_files(self, patched_check):
        fields, _ = patched_check(domain_files={"broken.py": "def(\n", "playtime.py": _PLAYTIME})
        assert fields.classes == {"Playtime"}

    def test_collects_each_annotated_field_with_the_aggregates_declaring_it(self, patched_check):
        fields, _ = patched_check(domain_files=_DOMAIN)
        assert fields.owners["cover_path"] == {"Rom"}
        assert fields.owners["rom_id"] == {"RomSaveSyncState", "Playtime"}
        assert "rename" not in fields.owners

    def test_a_list_dict_or_set_field_is_a_container_field_and_a_tuple_is_not(self, patched_check):
        fields, _ = patched_check(
            domain_files={
                "rom_save_sync_state.py": _SAVE_STATE,
                "rom.py": _ROM,
                "run.py": (
                    "from domain._aggregate import cosmic_aggregate\n"
                    "@cosmic_aggregate\n"
                    "class SyncRun:\n"
                    "    done: 'set[str]'\n"
                    "    bare: list\n"
                ),
            },
        )
        assert fields.containers == {"files", "own_upload_ids", "done", "bare"}

    def test_a_frozen_value_objects_fields_are_not_collected(self, patched_check):
        fields, _ = patched_check(
            domain_files={
                "value.py": (
                    "from dataclasses import dataclass\n"
                    "@dataclass(frozen=True, slots=True)\n"
                    "class FileSyncState:\n"
                    "    checksum: str\n"
                    "    parts: list[str]\n"
                ),
            },
        )
        assert fields.owners == {}
        assert fields.containers == set()


class TestFieldStores:
    def test_flags_direct_field_assignment(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"library.py": "def update(rom):\n    rom.cover_path = '/x'\n"},
        )
        assert len(findings) == 1
        assert findings[0].startswith("backend/services/library.py:2:")
        assert "'rom.cover_path' is assigned" in findings[0]
        assert "a field name of Rom" in findings[0]

    def test_names_every_aggregate_declaring_the_field(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def update(entry):\n    entry.rom_id = 1\n"},
        )
        assert len(findings) == 1
        assert "a field name of Playtime, RomSaveSyncState" in findings[0]

    def test_flags_a_store_in_adapters(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            adapters_files={"repo.py": "def load(row):\n    row.fs_name = 'b'\n"},
        )
        assert len(findings) == 1
        assert findings[0].startswith("backend/adapters/repo.py:2:")

    def test_flags_a_store_from_another_object_inside_the_domain(self, patched_check):
        _, findings = patched_check(
            domain_files={**_DOMAIN, "rules.py": "def retitle(other):\n    other.name = 'x'\n"},
        )
        assert len(findings) == 1
        assert findings[0].startswith("backend/domain/rules.py:2:")

    def test_an_aggregates_own_method_storing_on_bare_self_is_allowed(self, patched_check):
        _, findings = patched_check(domain_files=_DOMAIN)
        assert findings == []

    def test_a_bare_self_store_in_any_class_is_allowed(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "class Service:\n    def boot(self):\n        self.name = 'n'\n"},
        )
        assert findings == []

    def test_reads_are_not_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"clean.py": "def show(rom):\n    return rom.cover_path\n"},
        )
        assert findings == []

    def test_an_attribute_that_is_no_field_is_not_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"fetcher.py": "def setup(romm_api):\n    romm_api.timeout = 30\n"},
        )
        assert findings == []

    def test_multiple_violations_each_get_own_line(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={
                "library.py": "def update(rom):\n    rom.cover_path = '/x'\n    rom.name = 'a'\n    rom.fs_name = 'b'\n"
            },
        )
        assert len(findings) == 3
        assert any("rom.cover_path" in f for f in findings)
        assert any("rom.name" in f for f in findings)
        assert any("rom.fs_name" in f for f in findings)

    def test_any_variable_name_is_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={
                "fetcher.py": "def setup(rom_state, bound):\n    rom_state.files = {}\n    bound.name = 'a'\n"
            },
        )
        assert len(findings) == 2

    def test_subscript_receiver_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "def update(roms):\n    roms['key'].cover_path = '/x'\n"},
        )
        assert len(findings) == 1
        assert "roms['key'].cover_path" in findings[0]

    def test_nested_attribute_receiver_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "class S:\n    def update(self):\n        self.repo.rom.cover_path = '/x'\n"},
        )
        assert len(findings) == 1
        assert "self.repo.rom.cover_path" in findings[0]

    def test_aug_assign_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "def add(playtime):\n    playtime.total_seconds += 60\n"},
        )
        assert len(findings) == 1
        assert "playtime.total_seconds" in findings[0]

    def test_bare_ann_assign_without_value_not_flagged(self, patched_check):
        # ``rom.field: int`` is a type annotation, not a mutation.
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "def annotate(rom):\n    rom.cover_path: str\n"},
        )
        assert findings == []

    def test_ann_assign_with_value_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "def assign(rom):\n    rom.cover_path: str = '/x'\n"},
        )
        assert len(findings) == 1
        assert "rom.cover_path" in findings[0]

    def test_unpacking_loop_and_with_targets_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={
                "service.py": (
                    "def assign(rom, names, ctx):\n"
                    "    rom.name, other = 'a', 'b'\n"
                    "    for rom.fs_name in names:\n"
                    "        pass\n"
                    "    with ctx as rom.cover_path:\n"
                    "        pass\n"
                ),
            },
        )
        assert [f.split(":")[1] for f in findings] == ["2", "3", "5"]

    def test_del_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "def drop(rom):\n    del rom.cover_path\n"},
        )
        assert len(findings) == 1
        assert "'rom.cover_path' is deleted" in findings[0]


class TestContainerMutations:
    def test_item_store_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def put(save_state):\n    save_state.files['a'] = 1\n"},
        )
        assert len(findings) == 1
        assert "'save_state.files['a']' is assigned through 'files'" in findings[0]
        assert "a container field of RomSaveSyncState" in findings[0]

    def test_augmented_item_store_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def bump(save_state):\n    save_state.files['a'] += 1\n"},
        )
        assert len(findings) == 1

    def test_item_delete_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def drop(save_state):\n    del save_state.files['a']\n"},
        )
        assert len(findings) == 1
        assert "is deleted through 'files'" in findings[0]

    def test_store_into_a_value_held_in_a_container_field_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def mark(save_state):\n    save_state.files['a'].seen = True\n"},
        )
        assert len(findings) == 1
        assert "through 'files'" in findings[0]

    @pytest.mark.parametrize("method", sorted(check.MUTATING_METHODS))
    def test_mutating_call_flagged(self, patched_check, method: str):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": f"def change(state):\n    state.own_upload_ids.{method}(1)\n"},
        )
        assert len(findings) == 1
        assert f"'state.own_upload_ids.{method}(...)' mutates 'own_upload_ids'" in findings[0]

    def test_mutating_call_through_a_nested_receiver_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def change(ctx):\n    ctx.save_state.files.update({})\n"},
        )
        assert len(findings) == 1

    def test_mutating_call_on_an_item_of_a_container_field_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def change(state):\n    state.files['a'].append(1)\n"},
        )
        assert len(findings) == 1

    def test_non_mutating_call_not_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def read(state):\n    return state.files.get('a'), state.files['a']\n"},
        )
        assert findings == []

    def test_mutating_call_on_a_field_that_is_no_container_not_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "def change(rom, results):\n    results.name.append(rom.regions)\n"},
        )
        assert findings == []

    def test_bare_self_container_mutation_allowed(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"cache.py": "class Cache:\n    def put(self):\n        self.files.append(1)\n"},
        )
        assert findings == []

    def test_container_mutation_through_an_attribute_of_self_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"saves.py": "class S:\n    def put(self):\n        self.state.files['a'] = 1\n"},
        )
        assert len(findings) == 1


class TestSetattr:
    @pytest.mark.parametrize(
        "call",
        [
            "setattr(rom, 'name', 'a')",
            "delattr(rom, 'name')",
            "object.__setattr__(rom, 'name', 'a')",
            "object.__delattr__(rom, 'name')",
            "rom.__setattr__('name', 'a')",
        ],
    )
    def test_a_literal_field_name_flagged(self, patched_check, call: str):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": f"def change(rom):\n    {call}\n"},
        )
        assert len(findings) == 1
        assert "'name' is a field name of Rom" in findings[0]

    def test_a_computed_name_not_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "def change(rom, key):\n    setattr(rom, key, 'a')\n"},
        )
        assert findings == []

    def test_a_name_that_is_no_field_not_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "def change(func):\n    setattr(func, '_route', True)\n"},
        )
        assert findings == []

    def test_bare_self_allowed(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            services_files={"service.py": "class S:\n    def change(self):\n        setattr(self, 'name', 'a')\n"},
        )
        assert findings == []


class TestScope:
    def test_a_non_aggregate_value_sharing_a_field_name_is_flagged(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            adapters_files={"native.py": "def build(saves, index):\n    saves[index].name = 'a'\n"},
        )
        assert len(findings) == 1

    def test_escape_hatch_suppresses_finding(self, patched_check):
        _, findings = patched_check(
            domain_files=_DOMAIN,
            adapters_files={
                "native.py": "def build(saves, index):\n    saves[index].name = 'a'  # pragma: no aggregate-check\n"
            },
        )
        assert findings == []

    def test_vendored_and_native_trees_not_scanned(self, patched_check):
        source = "def update(rom):\n    rom.cover_path = '/x'\n"
        _, findings = patched_check(
            domain_files=_DOMAIN,
            _vendor_files={"lib.py": source},
            native_files={"lib.py": source},
            services_files={"library.py": source},
        )
        assert len(findings) == 1
        assert findings[0].startswith("backend/services/library.py:")

    def test_a_file_that_does_not_parse_is_skipped(self, patched_check):
        _, findings = patched_check(domain_files=_DOMAIN, services_files={"broken.py": "def(\n"})
        assert findings == []

    def test_no_aggregates_no_findings(self, patched_check):
        fields, findings = patched_check(services_files={"service.py": "def update(rom):\n    rom.cover_path = '/x'\n"})
        assert fields.classes == set()
        assert findings == []


class TestMainEntryPoint:
    def test_help_flag_returns_zero(self, capsys):
        rc = check.main(["--help"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Aggregate field-assignment ban" in captured.out

    def test_short_help_flag_returns_zero(self):
        rc = check.main(["-h"])
        assert rc == 0

    def test_real_repo_run_is_clean(self, capsys):
        rc = check.main([])
        assert rc == 0
        captured = capsys.readouterr()
        assert "OK:" in captured.out

    def test_main_reports_and_returns_one_on_violations(self, patched_check, capsys):
        patched_check(
            domain_files=_DOMAIN, services_files={"library.py": "def update(rom):\n    rom.cover_path = '/x'\n"}
        )
        capsys.readouterr()

        rc = check.main([])
        assert rc == 1
        captured = capsys.readouterr()
        assert "rom.cover_path" in captured.out
        assert "ERROR:" in captured.out
