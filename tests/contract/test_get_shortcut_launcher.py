"""Contract test for the callable that tells the frontend where the launcher is.

Driven frontend-shaped per ``src/api/backend.ts``:
``getShortcutLauncher = callable<[], {exe: string; start_dir: string; installed: boolean}>``.

This is the tier that reaches the answer through the real ``bootstrap()``, which
is what makes it worth having: the path is derived from the data root the
start-up migration settled on, and ``installed`` is what the start-up install
actually managed. A unit test can only be told both.
"""

from __future__ import annotations

import os
import pathlib


async def test_the_launcher_is_reported_under_the_data_root(harness):
    result = await harness.plugin.get_shortcut_launcher()

    assert result == {
        "exe": os.path.join(harness.data_dir, "bin", "rom-launcher"),
        "start_dir": os.path.join(harness.data_dir, "bin"),
        "installed": True,
    }


async def test_the_reported_launcher_is_on_disk_and_runnable(harness):
    """The flag is the frontend's authority to rewrite shortcuts, so it is a real file."""
    result = await harness.plugin.get_shortcut_launcher()

    launcher = pathlib.Path(result["exe"])
    assert launcher.is_file()
    assert os.access(launcher, os.X_OK)


async def test_the_start_dir_is_the_directory_holding_the_exe(harness):
    result = await harness.plugin.get_shortcut_launcher()

    assert result["start_dir"] == os.path.dirname(result["exe"])


async def test_the_exe_ends_in_the_suffix_shortcut_ownership_is_read_off(harness):
    """``src/utils/steamShortcuts.ts`` decides a shortcut is ours by this suffix.

    A launcher reported at any other shape of path would leave every shortcut
    written before the move unrecognised — and this callable is what the rewrite
    writes into them.
    """
    result = await harness.plugin.get_shortcut_launcher()

    assert result["exe"].endswith("/bin/rom-launcher")


async def test_it_is_not_reported_in_deckys_own_directory(harness):
    """``runtime_dir`` answers Decky's layout question; the launcher follows the data."""
    result = await harness.plugin.get_shortcut_launcher()

    assert str(harness.tmp_path / "runtime") not in result["exe"]
    assert str(harness.tmp_path / "plugin") not in result["exe"]
