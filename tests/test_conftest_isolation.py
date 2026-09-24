"""The root conftest's isolation, pinned.

A test sees a fresh, empty home instead of the developer's real one, no
``XDG_*`` or ``TENDER_*`` variable reaches it, and the password database is
read only by the named exception and by this file.
"""

import os
import pwd
import re
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).parent
# The named exception the root conftest states, and this file, which has to name
# the real home to show it is not the one a test sees.
_ALLOWED_REAL_HOME_READERS = {"adapters/test_atlas_saves.py", "test_conftest_isolation.py"}


def _real_home() -> str:
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.fixture(scope="module")
def home_seen_before_any_test() -> str:
    return os.path.expanduser("~")


def test_the_home_a_test_sees_is_not_the_real_one_and_is_empty(home: Path) -> None:
    seen = os.path.expanduser("~")

    assert seen == str(home)
    assert os.path.realpath(seen) != _real_home()
    assert Path.home() == home
    assert os.listdir(seen) == []


def test_a_fixture_set_up_before_the_test_does_not_see_the_real_home_either(home_seen_before_any_test: str) -> None:
    assert os.path.realpath(home_seen_before_any_test) != _real_home()


def test_no_directory_variable_leaks_in() -> None:
    leaked = sorted(name for name in os.environ if name.startswith(("TENDER_", "XDG_")))

    assert leaked == []


def test_only_the_named_exception_reads_the_home_from_the_password_database() -> None:
    readers = {
        path.relative_to(_TESTS_ROOT).as_posix()
        for path in _TESTS_ROOT.rglob("*.py")
        if re.search(r"\bgetpw(uid|nam|all)\b|\bpw_dir\b", path.read_text(encoding="utf-8"))
    }

    assert readers == _ALLOWED_REAL_HOME_READERS
