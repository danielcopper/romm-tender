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
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check() -> ModuleType:
    return _load()


# Asked of the check rather than listed: a file added to the scope has to reach
# the per-file cases below without anyone remembering to name it twice.
_SCOPE_NAMES = tuple(display for _path, display in _load().files_in_scope())


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

    def test_two_different_functions_on_one_line_are_two_findings(self, check, tmp_path):
        """The key is the line AND the function: collapsing on the line alone loses one."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
first() {
    exit 1
}

second() {
    exit 2
}

both="$(first) $(second)"
""",
        )

        assert len(findings) == 2
        assert {f.split("$(")[1].split(" ")[0] for f in findings} == {"first", "second"}


class TestWhatTheLexerHasToGetRight:
    def test_exit_in_a_comment_does_not_count(self, check, tmp_path):
        """A whole line that is only `# exit 1` — command position, commented out."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    # exit 1
    echo "value"
}

x="$(reader)"
""",
            )
            == []
        )

    def test_an_apostrophe_in_a_comment_does_not_swallow_the_code_after_it(self, check, tmp_path):
        """A comment ends at its newline; its quotes are text, not an open string.

        Read the other way, everything from the apostrophe to the next quote
        anywhere in the file becomes string, and the exit below it disappears.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
reader() {
    # it's a comment with an apostrophe
    exit 1
}

x="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> exit" in findings[0]

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

    def test_a_herestring_is_not_a_heredoc(self, check, tmp_path):
        """`<<<` takes a word, not a body; read as `<<` it blanks the rest of the file."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    read -r line <<< "one line"
    abort "after a herestring"
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> abort -> exit" in findings[0]

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
        """The string stands alone on its line, so `exit` is in command position inside it."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
reader() {
    printf '%s' \
'exit 1'
    echo ok
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

    def test_an_unquoted_expansion_does_not_end_the_function(self, check, tmp_path):
        """The `}` of an UNQUOTED `${x}` is followed by a space, like a block's.

        Judged by what follows it, that brace closed the function and every
        `exit` below it was invisible — a silent green over a real defect. What
        decides is what comes BEFORE: a block brace is a word in command
        position, and this one is the tail of a word.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    local x=1
    echo ${x}
    abort "after an unquoted expansion"
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> abort -> exit" in findings[0]

    def test_a_find_exec_brace_does_not_end_the_function(self, check, tmp_path):
        """`{}` in `find … -exec rm {} \\;` is the same shape, from a different direction."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    find . -name '*.tmp' -exec rm {} \\;
    abort "after find -exec"
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> abort -> exit" in findings[0]

    def test_a_quoted_parameter_expansion_does_not_break_the_brace_count(self, check, tmp_path):
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

    def test_arithmetic_is_not_a_heredoc(self, check, tmp_path):
        """`$(( 1 << 3 ))` carries a `<<`, and reading it as one blanks the rest of the file."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    local bits=$(( 1 << 3 ))
    echo "$bits"
    abort "after a shift"
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> abort -> exit" in findings[0]

    def test_a_case_pattern_is_not_a_call(self, check, tmp_path):
        """`abort)` names a pattern. Reading it as a call invents an edge that is not there."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    case "$1" in
        abort) echo "the word abort, not a call" ;;
        *) echo "$1" ;;
    esac
}

y="$(reader ok)"
""",
            )
            == []
        )

    def test_an_expansion_naming_a_function_is_not_a_call(self, check, tmp_path):
        """`${step}` reads a variable that happens to share a name with a function."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
abort() {
    exit 1
}

step() {
    abort "x"
}

reader() {
    local step=3
    echo ${step}
    printf 'done\\n'
}

y="$(reader)"
""",
            )
            == []
        )

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

    def test_a_case_pattern_does_not_end_the_substitution_it_is_in(self, check, tmp_path):
        """`$(case "$1" in a) … esac)` — the `a)` closes a pattern, not the substitution.

        Counting parentheses alone ends the substitution at the first arm, so
        every command in the arms below it is read as the text around a
        substitution rather than as code, and the call among them is unseen.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    abort "always"
}

y="$(case "$1" in a) echo a ;; *) reader ;; esac)"
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_a_backtick_inside_double_quotes_is_code(self, check, tmp_path):
        """`x="`f`"` runs f exactly as `x="$(f)"` does; only the spelling differs."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    abort "always"
}

y="`reader`"
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_arithmetic_inside_a_substitution_does_not_end_it(self, check, tmp_path):
        """The span must start at the FIRST of `$((`'s two parens.

        One later leaves the closing pair a paren short, and the stray `)` pops
        the substitution the arithmetic sits in — so the call after it is unseen.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    abort "boom"
}

y="$(printf '%d' $((1 + 1)); reader)"
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_arithmetic_does_not_open_a_command_position_beside_it(self, check, tmp_path):
        """The same paren, the other way round: a stray `)` makes the next word a command."""
        assert (
            _findings(
                check,
                tmp_path,
                """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    echo $((1 + 1)) abort
    printf 'done\\n'
}

y="$(reader)"
""",
            )
            == []
        )

    def test_a_subshell_inside_a_substitution_does_not_end_it(self, check, tmp_path):
        """`( … )` is a level of its own; without one its `)` pops the substitution."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    abort "boom"
    echo v
}

y="$( (echo pre) ; reader )"
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_a_pattern_with_a_leading_paren_does_not_end_the_substitution(self, check, tmp_path):
        """POSIX writes an arm as `(a)`. That paren groups nothing, so it must not be counted."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    abort "boom"
}

y="$(case "$1" in (a) echo a ;; (*) echo o ;; esac; reader)"
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_a_fallthrough_arm_still_ends_an_arm(self, check, tmp_path):
        """`;&` and `;;&` end an arm as `;;` does, so a pattern follows each of them."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    abort "boom"
}

y="$(case "$1" in a) echo a ;& b) echo b ;; esac; reader)"
""",
        )

        assert len(findings) == 1
        assert "$(reader …)" in findings[0]

    def test_a_brace_as_an_argument_does_not_end_the_function(self, check, tmp_path):
        """`echo }` is an argument. A block brace is a word in COMMAND position."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    echo }
    abort "boom"
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> abort -> exit" in findings[0]

    def test_a_brace_group_after_a_bang_is_still_a_block(self, check, tmp_path):
        """`!` stands in front of a command, so what follows it opens one.

        It is the one word of its kind that is punctuation, so a backwards read
        looking for an identifier cannot find it. Miss it and the `{` is not a
        block's while its `}` is, which ends the enclosing function at the inner
        brace and takes every call below it out of the graph.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

reader() {
    if ! { exec 3< /dev/tty; } 2> /dev/null; then
        abort "no terminal"
    fi
    echo v
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> abort -> exit" in findings[0]

    def test_a_nested_block_still_balances(self, check, tmp_path):
        """A `}` ends a command, so the next `}` is still a block's: `{ { …; } }`.

        The opening brace admits a neighbour the closing one may not — the `{`
        of `find … -exec rm {} \\;` would otherwise close whatever was open.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
reader() {
    { { echo one; } }
    exit 1
}

y="$(reader)"
""",
        )

        assert len(findings) == 1

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


class TestWhatKeepsAWordInCommandPosition:
    """Two small lists decide whether an edge is seen at all, and both fail silently."""

    def test_a_call_behind_a_keyword_is_still_a_call(self, check, tmp_path):
        """`if helper; then` is the only edge here: drop `if` from TRANSPARENT and it vanishes."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
helper() {
    exit 1
}

reader() {
    if helper; then
        echo yes
    fi
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> helper -> exit" in findings[0]

    def test_a_call_behind_an_assignment_prefix_is_still_a_call(self, check, tmp_path):
        """`LANG=C helper` runs helper. Reading the assignment as the command loses the edge."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
helper() {
    exit 1
}

reader() {
    LANG=C helper
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "reader -> helper -> exit" in findings[0]

    def test_a_valued_call_behind_local_is_seen(self, check, tmp_path):
        """`local x="$(f)"` is the shape this gate exists for, and `local` must be transparent."""
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
reader() {
    exit 1
}

caller() {
    local v
    local v="$(reader)"
    echo "$v"
}

caller
""",
        )

        assert len(findings) == 1


class TestACallInsideASubstitutionIsNotAnEdge:
    """The gate's own premise, read backwards.

    An ``exit`` inside ``$( )`` ends the subshell, so a function that calls
    another THROUGH one does not inherit its exit. Treating it as an edge
    reports the outer site too, with a chain that claims something untrue.
    """

    def test_a_nested_pair_is_reported_once_at_the_inner_site(self, check, tmp_path):
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
abort() {
    exit 1
}

inner() {
    abort "x"
}

outer() {
    local v
    v="$(inner)"
    echo "$v"
}

y="$(outer)"
""",
        )

        assert len(findings) == 1
        assert "$(inner …)" in findings[0]
        assert ":12:" in findings[0], "the inner substitution, which is the site to fix"

    def test_a_function_whose_only_exit_is_behind_a_substitution_is_not_one(self, check, tmp_path):
        """It really does carry on: the subshell ends, and the function does not.

        ``$(dies)`` is a site of its own and is reported. What must NOT follow
        is a second finding at ``$(reader)``: ``reader`` does not exit, so
        taking its value is not the shape this gate is about.
        """
        findings = _findings(
            check,
            tmp_path,
            """#!/usr/bin/env bash
dies() {
    exit 1
}

reader() {
    local ignored
    ignored="$(dies || true)"
    echo "still here"
}

y="$(reader)"
""",
        )

        assert len(findings) == 1
        assert "$(dies …)" in findings[0]
        assert not any("$(reader …)" in finding for finding in findings)


class TestTheBlindSpots:
    """Named in the script's docstring, their one home. They pass; that is the point."""

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

    def test_the_launcher_is_named_because_the_glob_cannot_see_it(self, check):
        """`bin/tender-rom-launcher` is bash under a name with no extension."""
        names = [display for _path, display in check.files_in_scope()]

        assert "bin/tender-rom-launcher" in names

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
            for _index, body_start, body_end in check._substitutions(masked.text)
            for word, _at in check._command_words(masked.text[body_start:body_end], body_start)
            if word in functions
        }

        assert {"value_of", "check_native_steam", "resolve_tag"} <= valued
        assert "abort" in functions

    def test_the_installers_acknowledgement_still_reaches_abort(self, check):
        """One real function's body, held whole by an edge that runs near its end.

        A span that ends early is silent: the function is still collected, the
        count is unchanged, and only the calls below the truncation disappear.
        `acknowledge` is the file's longest reader of `/dev/tty` and its `abort`
        sits past a brace group written `! { …; }`, so this edge is what says
        the body was read to its end rather than to the first brace that looked
        like the last one.
        """
        path = _REPO_ROOT / "install.sh"
        masked = check.Masked(path.read_text(encoding="utf-8"))
        functions = check._find_functions(masked)
        graph, exits = {}, set()
        for name, (_line, body_start, body_end) in functions.items():
            body = check._without_substitutions(masked.text[body_start:body_end])
            words = [word for word, _at in check._command_words(body, body_start)]
            graph[name] = {word for word in words if word in functions}
            if "exit" in words:
                exits.add(name)

        assert check._exit_chain("acknowledge", graph, exits) == ["acknowledge", "abort"]

    @pytest.mark.parametrize("display", _SCOPE_NAMES)
    def test_every_scripts_block_braces_balance(self, check, display):
        """The whole-file reading of a truncated span, asked directly.

        Every shape `_is_block_brace` gets wrong costs a `{` or a `}` and
        nothing else, so the depth over a real file either ends away from zero
        or dips below it. That is one number per file rather than a span per
        function, and it fails on the file the mistake is in — where a test over
        a hand-written fixture only fails on the shape someone thought to write.
        """
        path = next(p for p, name in check.files_in_scope() if name == display)
        masked = check.Masked(path.read_text(encoding="utf-8"))
        text = masked.text
        depth = 0
        for index, char in enumerate(text):
            if char not in "{}" or not check._is_block_brace(text, index):
                continue
            depth += 1 if char == "{" else -1
            assert depth >= 0, f"{display}:{masked.line_of(index)} closes a block that was never opened"

        assert depth == 0, f"{display} ends inside {depth} unclosed block(s)"
