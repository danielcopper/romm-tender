"""The backend's top-level names, guarded against shadowing.

``backend/`` is put first on ``sys.path`` by ``main.py``, ``check.py``,
``tests/conftest.py`` and ``scripts/dev_backend_lock.py``, ahead of the standard
library and site-packages, so a top-level name there and a module of the same
name from either cannot both be imported. Ours wins over a site-packages or
pure-Python standard-library module, a built-in or frozen one wins over ours,
and neither says so. The suite puts ``tests/`` ahead of ``backend/`` in turn, so
a test module or package of a backend name directly under ``tests/`` would win
over ours.
"""

import json
import subprocess
import sys
from importlib.machinery import PathFinder
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parent
_BACKEND_ROOT = _TESTS_ROOT.parent / "backend"

# Run under ``-P`` rather than ``-I``: ``-I`` also implies ``-E`` and ``-s``,
# ignoring ``PYTHONPATH`` and the user site, and the backend's interpreter honours
# both (``install.sh`` writes an ``ExecStart`` with no flag). ``-P`` only keeps the
# working directory off the path; ``backend/`` and ``tests/`` are taken off here,
# in case ``PYTHONPATH`` names either.
_FIND_WITHOUT_BACKEND = """
import importlib.util, json, os, sys
excluded = {os.path.realpath(path) for path in sys.argv[1:3]}
sys.path[:] = [path for path in sys.path if os.path.realpath(path) not in excluded]
found = {}
for name in sys.argv[3:]:
    spec = importlib.util.find_spec(name)
    if spec is not None:
        found[name] = spec.origin or ", ".join(spec.submodule_search_locations or [])
print(json.dumps(found))
"""


def _backend_top_level_names() -> set[str]:
    packages = {init.parent.name for init in _BACKEND_ROOT.glob("*/__init__.py")}
    modules = {module.stem for module in _BACKEND_ROOT.glob("*.py")}
    return packages | modules


def _found_without_backend(names: list[str]) -> dict[str, str]:
    """Where the suite's interpreter finds each name with neither ``backend/`` nor ``tests/`` on its path.

    The interpreter is the suite's own, in the environment the suite gives it.
    Its standard library and site-packages are asked, but no populated user
    site: the project's virtualenv reads none, and an interpreter that reads one
    reads the test's own empty ``HOME``. A site-packages only the device's
    interpreter reads is not asked either.
    """
    answer = subprocess.run(
        [sys.executable, "-P", "-c", _FIND_WITHOUT_BACKEND, str(_BACKEND_ROOT), str(_TESTS_ROOT), *names],
        capture_output=True,
        text=True,
        check=False,
    )
    assert answer.returncode == 0, answer.stderr
    return json.loads(answer.stdout)


def test_the_names_are_read_off_backend() -> None:
    names = _backend_top_level_names()

    assert {"services", "_vendor", "main"} <= names


def test_no_backend_name_is_a_standard_library_module() -> None:
    colliding = sorted(_backend_top_level_names() & sys.stdlib_module_names)

    assert colliding == [], f"these backend/ names are standard-library modules: {colliding}"


def test_the_question_reaches_site_packages() -> None:
    assert "pytest" in _found_without_backend(["pytest"])


def test_no_backend_name_is_importable_without_backend() -> None:
    found = _found_without_backend(sorted(_backend_top_level_names()))

    assert found == {}, "\n".join(
        f"{name} is importable without backend/, from {where}" for name, where in found.items()
    )


def test_no_name_directly_under_tests_answers_for_a_backend_name() -> None:
    misplaced = {}
    for name in sorted(_backend_top_level_names()):
        spec = PathFinder.find_spec(name, [str(_TESTS_ROOT), str(_BACKEND_ROOT)])
        origin = Path(spec.origin) if spec is not None and spec.origin is not None else None
        if origin is None or not origin.is_relative_to(_BACKEND_ROOT):
            misplaced[name] = str(origin) if origin is not None else "nowhere"

    assert misplaced == {}, f"with tests/ ahead of backend/, these names do not load from backend/: {misplaced}"


def _has_parent_package(directory: Path) -> bool:
    """Whether pytest's package walk goes on above ``directory`` (``_pytest/pathlib.py``, ``resolve_package_path``).

    It stops at a parent without an ``__init__.py`` and at one whose name is not an identifier.
    """
    parent = directory.parent
    return (parent / "__init__.py").exists() and parent.name.isidentifier()


def test_no_test_package_root_takes_a_backend_name() -> None:
    """No package root under ``tests/``, at any depth, carries a backend name.

    ``--import-mode=importlib`` registers the topmost directory of each chain of
    ``__init__.py`` under ``tests/`` as a top-level module of that directory's
    own name — ``tests/services/saves`` is ``saves`` — and whichever of it and
    the backend package of that name is imported first holds the name for the
    rest of the session.
    """
    roots = {init.parent for init in _TESTS_ROOT.rglob("__init__.py") if not _has_parent_package(init.parent)}
    colliding = sorted(str(root.relative_to(_TESTS_ROOT)) for root in roots if root.name in _backend_top_level_names())

    assert colliding == [], f"these test packages are registered under a backend name: {colliding}"
