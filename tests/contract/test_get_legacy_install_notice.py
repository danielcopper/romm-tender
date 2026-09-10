"""Contract test for the legacy-install notice callable.

Driven frontend-shaped per ``src/api/backend.ts``:
``getLegacyInstallNotice = callable<[], {pending: boolean; legacy_data_present: boolean}>``.

The harness roots the real ``bootstrap()`` at ``tmp_path/plugin`` and
``tmp_path/runtime``, so both directories have the SAME parent and one staged
``tmp_path/decky-romm-sync`` answers the plugin-folder question and the
database question at once. Decky's own layout keeps them apart (``plugins/``
and ``data/``), and this tier therefore cannot tell which of the two the
service asked about — pointing the database probe at the plugin directory
passes all four cases here. ``tests/services/test_legacy_install.py`` is what
carries that distinction; what this tier carries is everything the unit tier
cannot see, above all a real ``bootstrap()``. The read is live: a folder
created after the plugin was wired is still seen.

This is the tier that keeps ``legacy_data_present`` honest, because it is the
only one that reaches this callable through the real ``bootstrap()`` — which
creates **our own** ``romm_sync.db`` before the user has seen a single game. A flag written as "and
ours does not exist" would be green in every unit test and dead on every device.
"""

from __future__ import annotations

import pathlib

from tests.contract._seed import seed_rom

_DB_FILENAME = "romm_sync.db"


def _stage_legacy_install(harness, *, with_database: bool = False):
    """Create the pre-rename install's folder beside ours, optionally with a db."""
    legacy = harness.tmp_path / "decky-romm-sync"
    legacy.mkdir(exist_ok=True)
    if with_database:
        (legacy / _DB_FILENAME).write_bytes(b"")
    return legacy


async def test_no_legacy_install_reports_nothing(harness):
    result = await harness.plugin.get_legacy_install_notice()
    assert result == {"pending": False, "legacy_data_present": False}


async def test_the_legacy_plugin_folder_beside_ours_fires_the_notice(harness):
    _stage_legacy_install(harness)

    result = await harness.plugin.get_legacy_install_notice()
    assert result == {"pending": True, "legacy_data_present": False}


async def test_a_legacy_database_is_reported_though_ours_exists_too(harness):
    _stage_legacy_install(harness, with_database=True)
    # bootstrap already made ours, which is exactly why "ours is absent" cannot
    # be the question.
    assert (pathlib.Path(harness.data_dir) / _DB_FILENAME).exists()

    result = await harness.plugin.get_legacy_install_notice()
    assert result == {"pending": True, "legacy_data_present": True}


async def test_the_warning_survives_a_library_this_install_already_holds(harness):
    """No database of ours is read, so nothing about our library moves either flag."""
    _stage_legacy_install(harness, with_database=True)
    seed_rom(harness, 1)

    result = await harness.plugin.get_legacy_install_notice()
    assert result == {"pending": True, "legacy_data_present": True}
