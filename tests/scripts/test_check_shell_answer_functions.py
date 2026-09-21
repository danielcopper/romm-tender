"""Tests for ``scripts/check_shell_answer_functions.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. Fixtures write whole shell scripts under ``tmp_path`` and the real
scan runs over them, so what is exercised is the lexer as well as the rule — a
gate for shell that cannot tell a heredoc from a function body would report
nonsense about every file it reads.

Coverage centres on the rule (a ``$(...)``-valued function must not reach
``exit``), on the transitive half that makes the abort helper derived rather
than listed, on the lexing the rule rests on (heredocs, comments, quotes,
parameter expansions, backticks), and on the documented blind spots — a gate
whose advertised reach outruns its real one is worse than none.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_shell_answer_functions.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_shell_answer_functions", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check() -> ModuleType:
    return _load()


def _findings(check: ModuleType, tmp_path: Path, body: str) -> list[str]:
    """Write *body* as a script and answer what the real scan says about it."""
    path = tmp_path / "subject.sh"
    path.write_text(body, encoding="utf-8")
    return check.scan(path, "subject.sh")


class TestTheRule:
    def test_a_script_with_no_answer_function_passes(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
set -euo pipefail

abort() {
    echo "$1" >&2
    exit 1
}

main() {
    [ $# -ge 1 ] || abort "need an argument"
    echo "$1"
}

main "$@"
""",
            )
            == []
        )

    def test_a_valued_function_that_exits_is_a_finding(self, check, tmp_path):
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
value_of() {
    [ $# -ge 2 ] || exit 1
    echo "$2"
}

x="$(value_of "$@")"
""",
        )

        assert len(findings) == 1
        assert "subject.sh:7:" in findings[0]
        assert "$(value_of …)" in findings[0]
        assert "value_of -> exit" in findings[0]

    def test_it_follows_the_chain_to_whatever_helper_exits(self, check, tmp_path):
        """The abort helper is derived, not listed: a second one is covered on the day it is written."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
die() {
    exit 1
}

complain() {
    echo "$1" >&2
    die
}

value_of() {
    [ $# -ge 2 ] || complain "needs a value"
    echo "$2"
}

x="$(value_of "$@")"
""",
        )

        assert len(findings) == 1
        assert "value_of -> complain -> die -> exit" in findings[0]

    def test_a_backtick_substitution_counts_too(self, check, tmp_path):
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    abort "no"
}

x=`reader`
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_the_helper_that_holds_the_status_codes_is_a_finding_too(self, check, tmp_path):
        """The shape a first attempt at applying this rule reaches for.

        A helper written to turn an answering function's status codes into
        messages, and then called for its value — which puts the aborts back
        inside a subshell one level up.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    echo "$1" >&2
    exit 1
}

read_version() {
    [ -f "$1" ] || return 1
    cat "$1"
}

resolved_version() {
    local version status=0
    version="$(read_version "$1")" || status=$?
    [ "$status" -eq 0 ] || abort "no version"
    echo "$version"
}

main() {
    local v
    v="$(resolved_version "$1")"
    echo "$v"
}

main "$@"
""",
        )

        assert len(findings) == 1
        assert "$(resolved_version …)" in findings[0]
        assert "resolved_version -> abort -> exit" in findings[0]

    def test_a_valued_function_that_only_returns_passes(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
abort() {
    echo "$1" >&2
    exit 1
}

value_of() {
    [ $# -ge 2 ] || return 1
    printf '%s\\n' "$2"
}

x="$(value_of "$@")" || abort "needs a value"
""",
            )
            == []
        )

    def test_a_function_that_exits_but_is_never_valued_passes(self, check, tmp_path):
        """The rule is about the substitution, not about exiting."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
abort() {
    exit 1
}

main() {
    abort "always"
}

main "$@"
""",
            )
            == []
        )

    def test_a_substitution_naming_an_external_command_is_not_ours_to_judge(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
abort() {
    exit 1
}

work="$(mktemp -d)"
here="$(cd "$(dirname "$0")" && pwd)"
""",
            )
            == []
        )

    def test_every_substitution_of_the_same_function_is_reported(self, check, tmp_path):
        """One line per site, because each is a separate place to fix."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
value_of() {
    exit 1
}

a="$(value_of x)"
b="$(value_of y)"
""",
        )

        assert len(findings) == 2
        assert [f.split(":")[1] for f in findings] == ["6", "7"]


class TestWhatTheLexerHasToGetRight:
    def test_exit_in_a_comment_does_not_count(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    # exit 1 would be wrong here
    echo "value"
}

x="$(reader)"
""",
            )
            == []
        )

    def test_exit_inside_a_heredoc_does_not_count(self, check, tmp_path):
        """A heredoc is text the script EMITS; the exit in it is not this script's."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    cat <<'EOF'
if [ -z "$1" ]; then
    exit 1
fi
EOF
    echo "value"
}

x="$(reader)"
""",
            )
            == []
        )

    def test_a_function_defined_inside_a_heredoc_is_not_a_function_here(self, check, tmp_path):
        """The real `scripts/dev_place_window.sh` embeds JavaScript with `function` in it."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
arm() {
    cat > "$1" <<'EOF'
function reader() {
  return null;
}
EOF
}

x="$(arm /tmp/x)"
""",
            )
            == []
        )

    def test_exit_in_a_single_quoted_string_does_not_count(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    echo 'exit 1'
}

x="$(reader)"
""",
            )
            == []
        )

    def test_exit_in_a_double_quoted_string_does_not_count(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    echo "then exit 1 happens"
}

x="$(reader)"
""",
            )
            == []
        )

    def test_a_parameter_expansion_does_not_break_the_brace_count(self, check, tmp_path):
        """``${x}`` braces are not block braces, so a body must not end at one."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
reader() {
    local tag="${1#tender-v}"
    local rest="${2:-${3}}"
    echo "$tag$rest"
    exit 1
}

x="$(reader a b c)"
""",
        )

        assert len(findings) == 1
        assert "reader -> exit" in findings[0]

    def test_the_function_keyword_form_is_collected(self, check, tmp_path):
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
function reader {
    exit 1
}

x="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_a_substitution_inside_double_quotes_is_still_seen(self, check, tmp_path):
        """The shape every one of these actually takes: ``x="$(f)"``."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
reader() {
    exit 1
}

echo "the answer is $(reader) today"
""",
        )

        assert len(findings) == 1

    def test_a_nested_substitution_is_seen(self, check, tmp_path):
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
reader() {
    exit 1
}

x="$(dirname "$(reader)")"
""",
        )

        # Once, not twice: the inner substitution lies inside the outer one's
        # body as well as being one itself, and it is a single site to fix.
        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_a_function_after_a_pipe_inside_a_substitution_is_seen(self, check, tmp_path):
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
reader() {
    exit 1
}

x="$(printf 'x' | reader)"
""",
        )

        assert len(findings) == 1


class TestTheBlindSpots:
    """Named in the script's docstring and in the register. They pass; that is the point."""

    def test_a_function_reached_through_a_variable_is_missed(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    exit 1
}

cmd=reader
x="$($cmd)"
""",
            )
            == []
        )

    def test_a_subshell_and_a_pipeline_are_out_of_scope(self, check, tmp_path):
        """Both swallow an exit the same way; neither is this gate's rule."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    exit 1
}

( reader )
reader | cat
""",
            )
            == []
        )

    def test_a_function_defined_inside_another_is_not_collected(self, check, tmp_path):
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
outer() {
    inner() {
        exit 1
    }
    inner
}

x="$(inner)"
""",
            )
            == []
        )


class TestTheRealScripts:
    def test_every_script_in_scope_passes_today(self, check):
        """The gate over this repository, exactly as CI runs it."""
        findings = [line for path, display in check.files_in_scope() for line in check.scan(path, display)]

        assert findings == []

    def test_the_scope_names_the_two_scripts_a_user_runs(self, check):
        names = [display for _path, display in check.files_in_scope()]

        assert "install.sh" in names
        assert "scripts/package.sh" in names

    def test_the_scope_picks_up_the_developer_scripts_beside_them(self, check):
        """A glob rather than a list, so a new script is covered on the day it is written."""
        names = [display for _path, display in check.files_in_scope()]

        assert "scripts/dev_steam.sh" in names
        assert len(names) == len(set(names))

    def test_the_installers_own_answer_functions_are_seen_as_answer_functions(self, check):
        """Non-vacuity over the real file: the three the rule was applied to ARE in the graph.

        A lexer that failed on ``install.sh`` would report no findings for the
        happiest of reasons — it found no functions at all — and this test is
        what tells that apart from the file being clean.
        """
        path = _REPO_ROOT / "install.sh"
        masked = check.Masked(path.read_text(encoding="utf-8"))
        functions = check._find_functions(masked)
        valued = {
            word
            for _index, body_start, body_end in check._substitutions(masked)
            for word, _at in check._command_words(masked.text[body_start:body_end], body_start)
            if word in functions
        }

        assert {"value_of", "check_native_steam", "resolve_tag"} <= valued
        assert "abort" in functions
