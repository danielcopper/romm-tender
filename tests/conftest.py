import atexit
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from hypothesis import HealthCheck, settings

# CI-safe hypothesis profile: deadline=None avoids timing flakes on shared CI
# runners; 200 examples balances coverage against suite runtime. Loaded by
# default for every property test (#1028).
settings.register_profile(
    "ci",
    deadline=None,
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("ci")

# `backend/` is the import root the backend runs under (`python backend/main.py`),
# so `from lib.xxx import` resolves here the same way it does in production.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tests_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_project_root, "backend"))
# Add tests/ root so subdirectory tests can still import from fakes/ and conftest
sys.path.insert(0, _tests_root)


def _drop_directory_variables(delete: Callable[[str], object]) -> None:
    for name in [name for name in os.environ if name.startswith(("XDG_", "TENDER_"))]:
        delete(name)


# What runs before any test's own fixtures — collection, module- and
# session-scoped fixtures — runs under this home, for the reason
# `_isolated_environment` gives.
_suite_home = tempfile.mkdtemp(prefix="tests-home-")
atexit.register(shutil.rmtree, _suite_home, True)
os.environ["HOME"] = _suite_home
_drop_directory_variables(os.environ.pop)


@pytest.fixture
def home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """This test's home directory: fresh, empty, and what ``HOME`` names while the test runs.

    It is not ``tmp_path``: adapter tests assert the exact contents of their
    ``tmp_path`` (``listdir(...) == []``), and the contract harness lays its own
    directories out under it, a ``home`` among them.
    """
    path = tmp_path_factory.mktemp("home")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_environment(home: Path) -> Iterator[None]:
    """Point ``HOME`` at the test's own home and drop every ``XDG_*`` and ``TENDER_*`` variable.

    ``HOME`` is what ``os.path.expanduser`` and ``Path.home()`` answer from, so
    nothing a test runs can reach the developer's real Steam, RetroDECK or
    settings tree. ``TENDER_*`` and the XDG base directories are the rungs
    ``domain/app_directories.py`` reads before the home, so one inherited from
    the shell would point a test back at a real directory; the rest of the
    ``XDG_*`` family goes with them, because a test has no business reading the
    desktop session it was started from either. The process itself starts under
    a home of its own too (``_suite_home``), so the same holds for everything
    that runs before this fixture.

    **One named exception:** ``TestTheRealMachineAnswers`` in
    ``tests/adapters/test_atlas_saves.py`` reads the real home, because it pins
    what the real resolver answers over the RetroDECK installed on this machine
    and no fabricated tree can stand in. It takes that home from the password
    database, never from ``HOME``, and only reads.
    ``tests/test_conftest_isolation.py`` holds every other test to the rule.

    A ``MonkeyPatch`` of its own rather than the ``monkeypatch`` fixture: an
    autouse fixture that requested it would set it up before the test's
    ``tmp_path``, so a test's ``monkeypatch.setattr(os, "unlink", ...)`` would
    still be in force when ``tmp_path`` is removed.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(home))
        _drop_directory_variables(patch.delenv)
        yield


@pytest.fixture
def project_root() -> str:
    """The repository root — the code root the backend runs from in a checkout."""
    return _project_root


@pytest.fixture
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A per-test data directory that is not ``tmp_path``, for the same reason the home is not."""
    path = tmp_path_factory.mktemp("data")
    yield str(path)
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def emit() -> AsyncMock:
    """This test's own event sink — the ``emit`` seam a service is built with."""
    return AsyncMock()


@pytest.fixture
def logger(request: pytest.FixtureRequest) -> logging.Logger:
    """This test's own logger, named after the test so ``caplog`` can select it by ``logger.name``."""
    return logging.getLogger(f"tests.{request.node.nodeid}")


@pytest.fixture
def fake_romm_api():
    """Function-scoped ``FakeRommApi`` instance.

    Returns a fresh fake per test so seeded state never leaks across
    tests. Construct without args — tests seed ``platforms`` / ``roms``
    / ``firmware_files`` / etc. directly on the returned instance.
    """
    from fakes.fake_romm_api import FakeRommApi

    return FakeRommApi()


@pytest.fixture
def fake_steamgrid_db_api():
    """Function-scoped ``FakeSteamGridDbApi`` instance.

    Returns a fresh fake per test so seeded responses never leak
    across tests. Construct without args — tests seed responses via
    ``seed_igdb_lookup`` / ``seed_artwork`` / ``seed_raw_response`` /
    ``seed_image_bytes`` / ``seed_verify_response``.
    """
    from fakes.fake_steamgrid_db_api import FakeSteamGridDbApi

    return FakeSteamGridDbApi()


@pytest.fixture
def restore_root_logger():
    """Put the root logger back — the suite's own logging must survive this file."""
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in saved:
        root.addHandler(handler)
    root.setLevel(level)
