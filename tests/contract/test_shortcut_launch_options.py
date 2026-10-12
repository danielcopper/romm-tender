"""Contract test for reading a shortcut's launch options off Steam's shortcut file.

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``getShortcutLaunchOptions = endpoint<[number[]], ShortcutLaunchOptions>``.

Reaches the answer through the real ``bootstrap()`` and a real ``shortcuts.vdf``
parse under the harness home, so the wiring from the endpoint to the adapter is
what is under test here.
"""

from __future__ import annotations

from _vendor import vdf


def _write_shortcuts(harness, entries: dict[int, str]) -> None:
    config_dir = harness.tmp_path / "home" / ".local" / "share" / "Steam" / "userdata" / "123" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shortcuts = {
        str(index): {"appid": app_id, "AppName": f"Game {app_id}", "LaunchOptions": value}
        for index, (app_id, value) in enumerate(entries.items())
    }
    with open(str(config_dir / "shortcuts.vdf"), "wb") as handle:
        handle.write(vdf.binary_dumps({"shortcuts": shortcuts}))


async def test_answers_what_the_file_holds_for_the_app_ids_asked_about(harness):
    _write_shortcuts(harness, {-949288395: "written", 7: "another game", 8: ""})

    result = await harness.endpoints.get_shortcut_launch_options([3345678901, 8, 99])

    assert result["status"] == "read"
    assert sorted(result["launch_options"], key=lambda entry: entry["app_id"]) == [
        {"app_id": 8, "launch_options": ""},
        {"app_id": 3345678901, "launch_options": "written"},
    ]


async def test_no_steam_directory_at_all_is_unreadable(harness):
    result = await harness.endpoints.get_shortcut_launch_options([1])

    assert result["status"] == "unreadable"
    assert result["message"]
