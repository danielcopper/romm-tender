"""Tests for LegacyInstallService."""

from __future__ import annotations

import logging

import pytest
from fakes.fake_path_exists_reader import FakePathExistsReader
from fakes.fake_resolved_path import FakeResolvedPath

from services.legacy_install import LegacyInstallService, LegacyInstallServiceConfig

_PLUGINS = "/home/deck/homebrew/plugins"
_DATA = "/home/deck/homebrew/data"
_DB = "romm_sync.db"

_LEGACY_PLUGIN_DIR = f"{_PLUGINS}/decky-romm-sync"
_LEGACY_RUNTIME_DIR = f"{_DATA}/decky-romm-sync"
_OUR_PLUGIN_DIR = f"{_PLUGINS}/romm-tender"
_OUR_RUNTIME_DIR = f"{_DATA}/romm-tender"


class _RaisingResolvedPath:
    """``ResolvedPathFn`` that raises for the paths under one prefix.

    Stands in for the real ``os.path.realpath`` failing at the ``os.readlink``
    that ``posixpath._joinrealpath`` leaves unguarded. Two routes reach it, so
    the error is a parameter: a symlink the process may lstat but may not read
    (``/proc/<pid>/root`` — ``PermissionError``, the default here), and a link
    that changes or vanishes between the guarded lstat and that readlink
    (``FileNotFoundError``).
    """

    def __init__(self, raise_under: str, error: Exception | None = None) -> None:
        self._raise_under = raise_under
        self._error = error if error is not None else PermissionError(13, "Permission denied", raise_under)

    def __call__(self, path: str) -> str:
        if path.startswith(self._raise_under):
            raise self._error
        return path


class _RaisingProbe:
    """``PathExistsReader`` that raises for the paths under one prefix."""

    def __init__(self, raise_under: str, present: set[str]) -> None:
        self._raise_under = raise_under
        self._present = present

    def exists(self, path: str) -> bool:
        if path.startswith(self._raise_under):
            raise OSError(5, "Input/output error", path)
        return path in self._present


class _RecordingProbe:
    """``PathExistsReader`` that records every path it is asked about."""

    def __init__(self, probed: list[str], present: set[str]) -> None:
        self._probed = probed
        self._present = present

    def exists(self, path: str) -> bool:
        self._probed.append(path)
        return path in self._present


def _make_service(
    *,
    plugin_dir: str = _OUR_PLUGIN_DIR,
    runtime_dir: str = _OUR_RUNTIME_DIR,
    present: set[str] | None = None,
    links: dict[str, str] | None = None,
    path_exists=None,
    resolve_path=None,
    logger: logging.Logger | None = None,
) -> LegacyInstallService:
    return LegacyInstallService(
        config=LegacyInstallServiceConfig(
            plugin_dir=plugin_dir,
            runtime_dir=runtime_dir,
            db_filename=_DB,
            path_exists=path_exists if path_exists is not None else FakePathExistsReader(paths=present or set()),
            resolve_path=resolve_path if resolve_path is not None else FakeResolvedPath(links=links),
            logger=logger if logger is not None else logging.getLogger("test_legacy_install"),
        ),
    )


class TestPending:
    def test_silent_when_the_legacy_folder_is_absent(self):
        service = _make_service(present=set())
        assert service.get_legacy_install_notice() == {"pending": False, "legacy_data_present": False}

    def test_fires_when_the_legacy_folder_stands_beside_ours(self):
        service = _make_service(present={_LEGACY_PLUGIN_DIR})
        assert service.get_legacy_install_notice() == {"pending": True, "legacy_data_present": False}

    def test_silent_when_we_are_the_legacy_folder(self):
        """The dev deploy targets the legacy folder itself — nothing to warn about."""
        service = _make_service(
            plugin_dir=_LEGACY_PLUGIN_DIR,
            runtime_dir=_LEGACY_RUNTIME_DIR,
            present={_LEGACY_PLUGIN_DIR, _LEGACY_RUNTIME_DIR, f"{_LEGACY_RUNTIME_DIR}/{_DB}"},
        )
        assert service.get_legacy_install_notice() == {"pending": False, "legacy_data_present": False}

    def test_silent_when_the_legacy_folder_resolves_onto_our_own(self):
        """One directory under two names is one install (#1838).

        The discriminating case, and the only shape that is: the two paths
        DIFFER as written and name the same directory once resolved. Swap the
        comparison for the raw strings and this is the test that fails — a card
        telling the user to preserve the folder they are running from.
        """
        service = _make_service(
            present={_LEGACY_PLUGIN_DIR},
            links={_LEGACY_PLUGIN_DIR: _OUR_PLUGIN_DIR},
        )
        assert service.get_legacy_install_notice() == {"pending": False, "legacy_data_present": False}

    def test_fires_when_the_two_folders_resolve_apart(self):
        """Both spellings sit under a symlinked root and still name two installs."""
        service = _make_service(
            plugin_dir="/var/home/deck/homebrew/plugins/romm-tender",
            present={"/var/home/deck/homebrew/plugins/decky-romm-sync"},
            links={"/home": "/var/home"},
        )
        assert service.get_legacy_install_notice()["pending"] is True


class TestLegacyDataPresent:
    def test_set_when_only_the_legacy_runtime_dir_holds_a_database(self):
        service = _make_service(present={_LEGACY_PLUGIN_DIR, _LEGACY_RUNTIME_DIR, f"{_LEGACY_RUNTIME_DIR}/{_DB}"})
        assert service.get_legacy_install_notice() == {"pending": True, "legacy_data_present": True}

    def test_set_even_though_our_own_database_file_exists_too(self):
        """Ours is never absent — bootstrap creates it on the first boot."""
        service = _make_service(
            present={
                _LEGACY_PLUGIN_DIR,
                _LEGACY_RUNTIME_DIR,
                f"{_LEGACY_RUNTIME_DIR}/{_DB}",
                f"{_OUR_RUNTIME_DIR}/{_DB}",
            }
        )
        assert service.get_legacy_install_notice() == {"pending": True, "legacy_data_present": True}

    def test_clear_when_the_legacy_runtime_dir_holds_nothing(self):
        service = _make_service(present={_LEGACY_PLUGIN_DIR})
        assert service.get_legacy_install_notice() == {"pending": True, "legacy_data_present": False}

    def test_never_reported_without_a_pending_notice(self):
        """The sentence qualifies the warning, so it cannot stand on its own."""
        service = _make_service(present={_LEGACY_RUNTIME_DIR, f"{_LEGACY_RUNTIME_DIR}/{_DB}"})
        assert service.get_legacy_install_notice() == {"pending": False, "legacy_data_present": False}


class TestTheDatabaseProbeAsksTheSameQuestionAsTheCard:
    """The runtime dirs are held apart on their own, not on the plugin dirs' verdict.

    Two spellings of one data directory hold one database — ours. Drop the
    guard in ``_probe_legacy_database`` and that database answers as the older
    install's, so a fresh install reads "your library is still over there" over
    the library it is looking at.
    """

    def test_silent_when_the_runtime_dirs_resolve_onto_one_directory(self):
        service = _make_service(
            present={
                _LEGACY_PLUGIN_DIR,
                _LEGACY_RUNTIME_DIR,
                f"{_LEGACY_RUNTIME_DIR}/{_DB}",
            },
            links={_LEGACY_RUNTIME_DIR: _OUR_RUNTIME_DIR},
        )
        assert service.get_legacy_install_notice() == {"pending": True, "legacy_data_present": False}


class TestProbeDiscipline:
    @pytest.mark.parametrize("legacy_present", [True, False])
    def test_asks_only_about_the_sibling_of_its_own_directories(self, legacy_present):
        probed: list[str] = []
        present = {_LEGACY_PLUGIN_DIR} if legacy_present else set()
        service = LegacyInstallService(
            config=LegacyInstallServiceConfig(
                plugin_dir=_OUR_PLUGIN_DIR,
                runtime_dir=_OUR_RUNTIME_DIR,
                db_filename=_DB,
                path_exists=_RecordingProbe(probed, present),
                resolve_path=FakeResolvedPath(),
                logger=logging.getLogger("test_legacy_install"),
            ),
        )
        service.get_legacy_install_notice()
        assert probed[0] == _LEGACY_PLUGIN_DIR
        assert all(path.startswith((_PLUGINS, _DATA)) for path in probed)


class TestAFailingResolverStillAnswers:
    """A resolver that raises falls back to the paths as written, never to nothing.

    ``exists`` has already said the folder is there, so only the same-directory
    question is open, and the raw strings settle it in both directions.
    """

    def test_a_distinct_folder_still_shows_the_card(self, caplog):
        service = _make_service(
            present={_LEGACY_PLUGIN_DIR},
            resolve_path=_RaisingResolvedPath(raise_under=_PLUGINS),
        )
        with caplog.at_level(logging.WARNING):
            assert service.get_legacy_install_notice()["pending"] is True
        assert "comparing paths as written" in caplog.text

    def test_our_own_folder_stays_silent(self, caplog):
        """The dev deploy: the two paths are equal as written, so no card."""
        service = _make_service(
            plugin_dir=_LEGACY_PLUGIN_DIR,
            runtime_dir=_LEGACY_RUNTIME_DIR,
            present={_LEGACY_PLUGIN_DIR},
            resolve_path=_RaisingResolvedPath(raise_under=_PLUGINS),
        )
        with caplog.at_level(logging.WARNING):
            assert service.get_legacy_install_notice() == {"pending": False, "legacy_data_present": False}

    def test_a_non_oserror_still_propagates(self):
        """Pins the guard's TYPE scope — widening to ``except Exception`` fails here.

        A blanket catch swallows the ``OSError`` cases above just as happily, so
        nothing else in this file would notice the widening.
        """
        service = _make_service(
            present={_LEGACY_PLUGIN_DIR},
            resolve_path=_RaisingResolvedPath(raise_under=_PLUGINS, error=TypeError("not a path")),
        )
        with pytest.raises(TypeError):
            service.get_legacy_install_notice()

    def test_a_non_oserror_from_the_narrow_half_still_propagates(self):
        """Pins the OTHER guard's type scope — the one in ``_legacy_data_present``.

        The case above raises while the broad half is being answered and never
        reaches that guard. This one raises under the data root, so it travels
        the narrow half's path, and widening ``except OSError`` there to
        ``except Exception`` swallows it into a missing sentence.
        """
        service = _make_service(
            present={_LEGACY_PLUGIN_DIR, _LEGACY_RUNTIME_DIR},
            resolve_path=_RaisingResolvedPath(raise_under=_DATA, error=TypeError("not a path")),
        )
        with pytest.raises(TypeError):
            service.get_legacy_install_notice()


class TestTheWarningSurvivesAFailingProbe:
    """A probe error costs the sentence, never the launcher warning.

    Both flags are computed into one dict, so an exception raised while
    answering the narrow half would take the whole notice down — and the card it
    would take down is the one standing between the user and a removal nothing
    can undo.
    """

    def test_a_probe_that_raises_leaves_the_notice_up(self, caplog):
        """The probe cannot raise as ``os.path.exists``, but it is a Protocol."""
        service = _make_service(path_exists=_RaisingProbe(raise_under=_DATA, present={_LEGACY_PLUGIN_DIR}))
        with caplog.at_level(logging.WARNING):
            assert service.get_legacy_install_notice() == {"pending": True, "legacy_data_present": False}
        assert "Input/output error" in caplog.text

    def test_a_probe_that_raises_on_the_broad_half_still_propagates(self):
        """An unanswerable existence question is not a missing sentence."""
        service = _make_service(path_exists=_RaisingProbe(raise_under=_PLUGINS, present=set()))
        with pytest.raises(OSError):
            service.get_legacy_install_notice()
