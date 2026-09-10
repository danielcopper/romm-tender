"""Contract test for the one-time move of the shortcuts onto the launcher's home.

Driven frontend-shaped per ``src/api/backend.ts``:
``getShortcutRelocation = callable<[], ShortcutRelocation>``.

This tier reaches the answer through the real ``bootstrap()`` and a real SQLite
database, which is what makes it worth having: the launcher path is derived from
the data root the start-up migration settled, the completion stamp is a real
``kv_config`` row, and the reading is a real ``shortcuts.vdf`` parse. A unit test
can only be told all three.

It is also the only tier that can show the stamp's timing, because that timing
is a property of the file: a rewrite is stamped by the reading AFTER it, once the
file carries the new paths.
"""

from __future__ import annotations

import os

from _vendor import vdf

_HOME_SUFFIX = os.path.join("bin", "rom-launcher")


def _write_shortcuts(harness, entries: list[tuple[int, str]]) -> None:
    """Lay down a Steam shortcut file the way Steam does, under the harness home."""
    config_dir = harness.tmp_path / "home" / ".local" / "share" / "Steam" / "userdata" / "123" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shortcuts = {
        str(index): {"appid": app_id, "AppName": f"Game {app_id}", "Exe": exe, "StartDir": os.path.dirname(exe)}
        for index, (app_id, exe) in enumerate(entries)
    }
    with open(str(config_dir / "shortcuts.vdf"), "wb") as handle:
        handle.write(vdf.binary_dumps({"shortcuts": shortcuts}))


def _launcher_home(harness) -> str:
    return os.path.join(harness.data_dir, _HOME_SUFFIX)


async def test_no_steam_directory_at_all_blocks_rather_than_reporting_nothing_to_do(harness):
    """Not being able to look is not a finished reading, and must not be stamped."""
    result = await harness.plugin.get_shortcut_relocation()

    assert result["status"] == "blocked"
    assert result["message"]


async def test_a_machine_with_no_shortcut_file_is_already_done(harness):
    """Steam writes the file only once a non-Steam shortcut exists."""
    (harness.tmp_path / "home" / ".local" / "share" / "Steam" / "userdata" / "123").mkdir(parents=True)

    assert await harness.plugin.get_shortcut_relocation() == {"status": "done"}


async def test_a_shortcut_in_a_plugin_folder_is_named_for_rewriting(harness):
    _write_shortcuts(harness, [(1, "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher")])

    result = await harness.plugin.get_shortcut_relocation()

    assert result == {
        "status": "outstanding",
        "exe": _launcher_home(harness),
        "start_dir": os.path.dirname(_launcher_home(harness)),
        "app_ids": [1],
    }


async def test_a_foreign_shortcut_is_never_named(harness):
    _write_shortcuts(
        harness,
        [(1, "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher"), (2, "/usr/bin/some-other-game")],
    )

    result = await harness.plugin.get_shortcut_relocation()

    assert result["app_ids"] == [1]


async def test_a_library_already_on_the_home_is_done(harness):
    _write_shortcuts(harness, [(1, _launcher_home(harness))])

    assert await harness.plugin.get_shortcut_relocation() == {"status": "done"}


async def test_the_reported_exe_is_a_real_file_the_start_installed(harness):
    """It is the frontend's authority to repoint every shortcut, so it exists."""
    _write_shortcuts(harness, [(1, "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher")])

    result = await harness.plugin.get_shortcut_relocation()

    assert os.path.isfile(result["exe"])
    assert os.access(result["exe"], os.X_OK)
    assert result["exe"].endswith("/bin/rom-launcher")


async def test_a_completed_rewrite_is_stamped_on_the_following_reading(harness):
    """The file is the evidence, so the stamp lands one reading after the writes.

    Steam holds its shortcuts in memory and rewrites the file when it chooses,
    which is why nothing here reports the rewrite done — the next reading finds
    the new paths and closes the question itself.
    """
    _write_shortcuts(harness, [(1, "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher")])
    assert (await harness.plugin.get_shortcut_relocation())["status"] == "outstanding"

    # The frontend has written, and Steam has since flushed its memory to disk.
    _write_shortcuts(harness, [(1, _launcher_home(harness))])

    assert await harness.plugin.get_shortcut_relocation() == {"status": "done"}


async def test_a_reading_that_still_finds_them_stamps_nothing(harness):
    """A pass whose writes are not in the file yet leaves the question open."""
    _write_shortcuts(harness, [(1, "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher")])

    first = await harness.plugin.get_shortcut_relocation()
    second = await harness.plugin.get_shortcut_relocation()

    assert first == second
    assert second["status"] == "outstanding"
    assert second["app_ids"] == [1]


async def test_the_stamp_closes_the_question_for_every_later_start(harness):
    """Permanent by design: a shortcut that turns up later on the old path stays on it."""
    _write_shortcuts(harness, [(1, _launcher_home(harness))])
    assert await harness.plugin.get_shortcut_relocation() == {"status": "done"}

    _write_shortcuts(harness, [(1, "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher")])

    assert await harness.plugin.get_shortcut_relocation() == {"status": "done"}
