"""Tests for ``scripts/check_reason_parity.py``.

Loaded via ``importlib`` because ``scripts/`` is not on ``sys.path``. Each case
writes a small ``backend/`` tree and a ``reasons.ts`` under ``tmp_path`` and
points the gate's roots at them; the last class runs the gate over the real
repository.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_reason_parity.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_reason_parity", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_ERRORS = """\
class RommApiError(Exception):
    pass


class RommNotFoundError(RommApiError):
    pass


def classify_error(exc):
    if isinstance(exc, RommNotFoundError):
        return "not_found", "gone"
    return "unknown", str(exc)


class Refused(Exception):
    reason: str

    def __init__(self, reason, message, **details):
        super().__init__(message)
        self.reason = reason


class NamedRefused(Refused):
    def __init__(self, message, **details):
        super().__init__(type(self).reason, message, **details)
"""

_DOMAIN_REFUSAL = """\
class DomainRefused(Exception):
    reason: str

    def __init__(self, reason, message, **details):
        super().__init__(message)
        self.reason = reason


class NamedDomainRefused(DomainRefused):
    def __init__(self, message, **details):
        super().__init__(type(self).reason, message, **details)
"""

_PARTIAL_FAILURE = """\
from dataclasses import dataclass


@dataclass(frozen=True)
class PartialFailure:
    reason: str
    message: str
"""


def _union(members: list[str], classifier: list[str] | None = None) -> str:
    classifier = ["not_found", "unknown"] if classifier is None else classifier
    romm = "\n".join(f'  | "{r}"' for r in classifier)
    wire = "\n".join(f'  | "{r}"' for r in members)
    return f"export type RommErrorReason =\n{romm};\n\nexport type WireReason =\n  | RommErrorReason\n{wire};\n"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A minimal backend with the refusal bases, the gate pointed at it and an empty pass-through list."""
    backend = tmp_path / "backend"
    for rel, body in {
        "lib/errors.py": _ERRORS,
        "lib/partial_failure.py": _PARTIAL_FAILURE,
        "domain/refusal.py": _DOMAIN_REFUSAL,
    }.items():
        path = backend / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    union_file = tmp_path / "reasons.ts"
    union_file.write_text(_union([]), encoding="utf-8")
    monkeypatch.setattr(check, "BACKEND_DIR", backend)
    monkeypatch.setattr(check, "UNION_FILE", union_file)
    monkeypatch.setattr(check, "PASS_THROUGHS", {})
    return tmp_path


def _write(root: Path, rel: str, body: str) -> None:
    path = root / "backend" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _union_file(root: Path, members: list[str], classifier: list[str] | None = None) -> None:
    (root / "reasons.ts").write_text(_union(members, classifier), encoding="utf-8")


def _findings() -> list[str]:
    return check.find_discrepancies(check.parse_backend_reasons(), check.parse_frontend_reasons(), check.EXEMPT)


class TestEitherSideAlone:
    def test_the_two_sides_agreeing_pass(self, tree: Path):
        _write(tree, "services/a.py", 'from lib.errors import Refused\nraise Refused("sync_busy", "busy")\n')
        _union_file(tree, ["sync_busy"])
        assert _findings() == []

    def test_a_reason_only_the_backend_answers_with_fails_and_is_named(self, tree: Path):
        _write(tree, "services/a.py", 'from lib.errors import Refused\nraise Refused("sync_busy", "busy")\n')
        findings = _findings()
        assert len(findings) == 1
        assert findings[0].startswith('"sync_busy": the backend answers with it')

    def test_a_reason_only_the_union_holds_fails_and_is_named(self, tree: Path):
        _union_file(tree, ["sync_busy"])
        findings = _findings()
        assert len(findings) == 1
        assert findings[0].startswith('"sync_busy": WireReason holds it')

    def test_an_exempt_reason_is_skipped(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        _union_file(tree, ["sync_busy"])
        monkeypatch.setattr(check, "EXEMPT", frozenset({"sync_busy"}))
        assert _findings() == []


class TestWhatTheBackendSideReads:
    def _reasons(self) -> set[str]:
        found = check.parse_backend_reasons()
        assert found.problems == []
        return found.reasons

    def test_the_reason_keyword_of_a_refusal(self, tree: Path):
        _write(tree, "services/a.py", 'from lib.errors import Refused\nRefused(reason="kw_reason", message="m")\n')
        assert "kw_reason" in self._reasons()

    def test_a_domain_refusal_and_a_subclass_taking_the_reason_positionally(self, tree: Path):
        _write(
            tree,
            "domain/folders.py",
            """\
            from domain.refusal import DomainRefused


            class FolderRefused(DomainRefused):
                pass


            class EveryFolderRefused(FolderRefused):
                pass


            DomainRefused("domain_reason", "m")
            EveryFolderRefused("folder_reason", "m")
            """,
        )
        assert {"domain_reason", "folder_reason"} <= self._reasons()

    def test_a_named_refusal_s_class_attribute_and_not_its_message(self, tree: Path):
        _write(
            tree,
            "services/a.py",
            """\
            from lib.errors import NamedRefused


            class Busy(NamedRefused):
                reason = "named_reason"


            raise Busy("not a reason")
            """,
        )
        reasons = self._reasons()
        assert "named_reason" in reasons
        assert "not a reason" not in reasons

    def test_a_named_domain_refusal_inherits_its_reason(self, tree: Path):
        _write(
            tree,
            "domain/a.py",
            """\
            from domain.refusal import NamedDomainRefused


            class Parent(NamedDomainRefused):
                reason = "parent_reason"


            class Child(Parent):
                pass
            """,
        )
        assert "parent_reason" in self._reasons()

    def test_a_partial_failure_subclass_s_reason(self, tree: Path):
        _write(
            tree,
            "services/a.py",
            """\
            from lib.partial_failure import PartialFailure


            class Incomplete(PartialFailure):
                done: int


            Incomplete(reason="partial_reason", message="m", done=1)
            PartialFailure("positional_partial", "m")
            """,
        )
        assert {"partial_reason", "positional_partial"} <= self._reasons()

    def test_classify_error_s_returned_reasons(self, tree: Path):
        found = check.parse_backend_reasons()
        assert found.classifier == {"not_found", "unknown"}
        assert found.classifier <= found.reasons

    def test_a_module_constant_in_the_module_and_imported_from_another(self, tree: Path):
        _write(tree, "domain/names.py", 'IMPORTED = "imported_reason"\nCHAINED = IMPORTED\n')
        _write(
            tree,
            "services/a.py",
            """\
            from lib.errors import Refused
            from domain.names import CHAINED as ALIASED

            LOCAL = "local_reason"
            Refused(LOCAL, "m")
            Refused(ALIASED, "m")
            """,
        )
        assert {"local_reason", "imported_reason"} <= self._reasons()

    def test_a_relative_import_of_a_constant(self, tree: Path):
        _write(tree, "services/pkg/__init__.py", "")
        _write(tree, "services/pkg/names.py", 'NAME = "relative_reason"\n')
        _write(
            tree, "services/pkg/a.py", 'from lib.errors import Refused\nfrom .names import NAME\nRefused(NAME, "m")\n'
        )
        assert "relative_reason" in self._reasons()

    def test_a_str_enum_member_with_and_without_value(self, tree: Path):
        _write(
            tree,
            "lib/codes.py",
            """\
            from enum import StrEnum


            class Code(StrEnum):
                FIRST = "enum_first"
                SECOND = "enum_second"
                UNUSED = "enum_unused"
            """,
        )
        _write(
            tree,
            "services/a.py",
            """\
            from lib.codes import Code
            from lib.errors import Refused

            Refused(Code.FIRST.value, "m")
            Refused(Code.SECOND, "m")
            """,
        )
        reasons = self._reasons()
        assert {"enum_first", "enum_second"} <= reasons
        assert "enum_unused" not in reasons

    def test_a_str_enum_field_s_value_stands_for_every_member(self, tree: Path):
        _write(
            tree,
            "domain/headers.py",
            """\
            from dataclasses import dataclass
            from enum import StrEnum


            class Problem(StrEnum):
                ONE = "problem_one"
                TWO = "problem_two"


            @dataclass
            class Refusal:
                problem: Problem
            """,
        )
        _write(tree, "services/a.py", 'from lib.errors import Refused\nRefused(found.problem.value, "m")\n')
        assert {"problem_one", "problem_two"} <= self._reasons()


class TestWhatFails:
    def _problems(self) -> list[str]:
        return check.parse_backend_reasons().problems

    @pytest.mark.parametrize(
        "argument",
        ['f"built_{x}"', "reason", "make_reason()", "*args", "kind.value"],
        ids=["f-string", "parameter", "call", "starred", "unknown-value"],
    )
    def test_a_reason_it_cannot_resolve(self, tree: Path, argument: str):
        _write(tree, "services/a.py", f'from lib.errors import Refused\nRefused({argument}, "m")\n')
        problems = self._problems()
        assert len(problems) == 1
        assert problems[0].startswith("services/a.py:2: Refused(...)")

    def test_a_pass_through_not_on_the_list(self, tree: Path):
        _write(
            tree,
            "services/a.py",
            """\
            from lib.errors import Refused

            try:
                pass
            except Refused as refused:
                Refused(refused.reason, "m")
            """,
        )
        problems = self._problems()
        assert len(problems) == 1
        assert "`refused.reason`" in problems[0]
        assert "PASS_THROUGHS" in problems[0]

    def test_a_pass_through_on_the_list_passes(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        _write(tree, "services/a.py", 'from lib.errors import Refused\nRefused(refused.reason, "m")\n')
        monkeypatch.setattr(check, "PASS_THROUGHS", {("services/a.py", "refused.reason"): "why"})
        assert self._problems() == []

    def test_a_pass_through_on_the_list_in_another_file_does_not_cover_it(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write(tree, "services/a.py", 'from lib.errors import Refused\nRefused(refused.reason, "m")\n')
        monkeypatch.setattr(check, "PASS_THROUGHS", {("services/b.py", "refused.reason"): "why"})
        problems = self._problems()
        assert any(p.startswith("services/a.py:2:") for p in problems)
        assert any("no refusal or partial failure there passes on any more" in p for p in problems)

    def test_a_list_entry_no_site_matches_any_more(self, tree: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(check, "PASS_THROUGHS", {("services/a.py", "refused.reason"): "why"})
        problems = self._problems()
        assert len(problems) == 1
        assert "PASS_THROUGHS names `refused.reason` in services/a.py" in problems[0]

    def test_a_str_enum_field_declared_with_two_types(self, tree: Path):
        _write(
            tree,
            "domain/headers.py",
            """\
            from dataclasses import dataclass
            from enum import StrEnum


            class Problem(StrEnum):
                ONE = "problem_one"


            @dataclass
            class Refusal:
                problem: Problem


            @dataclass
            class Other:
                problem: str
            """,
        )
        _write(tree, "services/a.py", 'from lib.errors import Refused\nRefused(found.problem.value, "m")\n')
        assert len(self._problems()) == 1

    def test_a_named_refusal_with_no_reason_in_its_chain(self, tree: Path):
        _write(tree, "services/a.py", "from lib.errors import NamedRefused\n\n\nclass Bare(NamedRefused):\n    pass\n")
        problems = self._problems()
        assert len(problems) == 1
        assert "Bare is a named refusal that declares no reason" in problems[0]

    def test_a_positional_refusal_subclass_with_its_own_init(self, tree: Path):
        _write(
            tree,
            "services/a.py",
            """\
            from lib.errors import Refused


            class Odd(Refused):
                def __init__(self, message, reason):
                    super().__init__(reason, message)
            """,
        )
        problems = self._problems()
        assert len(problems) == 1
        assert "Odd takes its reason as an argument but defines its own __init__" in problems[0]

    def test_a_classify_error_return_that_is_not_a_tuple(self, tree: Path):
        errors = tree / "backend" / "lib" / "errors.py"
        errors.write_text(
            errors.read_text(encoding="utf-8").replace('return "unknown", str(exc)', "return lookup(exc)"),
            encoding="utf-8",
        )
        problems = self._problems()
        assert len(problems) == 1
        assert "classify_error returns something other than a tuple" in problems[0]

    def test_the_classifier_missing(self, tree: Path):
        errors = tree / "backend" / "lib" / "errors.py"
        errors.write_text(
            errors.read_text(encoding="utf-8").replace("def classify_error", "def renamed"), encoding="utf-8"
        )
        assert self._problems() == ["lib.errors.classify_error not found"]

    def test_vendored_and_native_code_is_not_read(self, tree: Path):
        for skipped in ("_vendor/pkg/a.py", "native/a.py"):
            _write(tree, skipped, 'from lib.errors import Refused\nRefused(f"x{y}", "m")\n')
        assert self._problems() == []


class TestTheClassifierSubUnion:
    def test_a_classifier_reason_the_sub_union_lacks(self, tree: Path):
        _union_file(tree, ["unknown"], classifier=["not_found"])
        findings = _findings()
        assert '"unknown": classify_error answers with it but RommErrorReason does not hold it' in findings

    def test_a_sub_union_member_the_classifier_never_answers(self, tree: Path):
        _union_file(tree, [], classifier=["not_found", "unknown", "auth_failed"])
        findings = _findings()
        assert '"auth_failed": RommErrorReason holds it but classify_error never answers with it' in findings


class TestWhatTheFrontendSideReads:
    def _read(self, tree: Path, body: str):
        (tree / "reasons.ts").write_text(body, encoding="utf-8")
        return check.parse_frontend_reasons()

    def test_comments_are_not_members(self, tree: Path):
        found = self._read(
            tree,
            'export type RommErrorReason = "unknown";\n'
            '/** A "doc_reason" | note. */\n'
            'export type WireReason =\n  // | "line_comment"\n  | RommErrorReason\n  | "real";\n',
        )
        assert found.problems == []
        assert found.reasons == {"unknown", "real"}

    def test_a_member_that_is_not_a_literal_fails(self, tree: Path):
        found = self._read(
            tree, 'export type RommErrorReason = "unknown";\nexport type WireReason = RommErrorReason | string;\n'
        )
        assert found.problems == [
            "WireReason: member `string` is neither a string literal nor a union declared in this file"
        ]

    def test_a_missing_declaration_fails(self, tree: Path):
        found = self._read(tree, 'export type RommErrorReason = "unknown";\n')
        assert found.problems == ["reasons.ts: no `export type WireReason = …;`"]

    def test_a_missing_file_fails(self, tree: Path):
        (tree / "reasons.ts").unlink()
        found = check.parse_frontend_reasons()
        assert len(found.problems) == 1
        assert found.problems[0].endswith("reasons.ts not found")


class TestTheRepository:
    def test_the_backend_s_reasons_and_the_panel_s_union_match(self):
        assert _findings() == []

    def test_every_pass_through_on_the_list_is_matched(self):
        found = check.parse_backend_reasons()
        assert not any("PASS_THROUGHS names" in p for p in found.problems)

    def test_main_exits_zero(self, capsys: pytest.CaptureFixture[str]):
        assert check.main([]) == 0
        assert capsys.readouterr().out.startswith("OK: backend↔frontend wire-reason parity matches")
