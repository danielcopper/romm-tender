"""Contract test for ``fix_retroarch_input_driver`` over the real ``Endpoints``.

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``fixRetroarchInputDriver = endpoint<[], { success: boolean; message: string }>`` —
no arguments. The panel shows ``message`` whatever the outcome, so each of the
three answers is pinned verbatim.

The real ``SteamConfigAdapter`` reads and rewrites a real ``retroarch.cfg``: it
looks under the home, which is the test's own (``tests/conftest.py``,
``_isolated_environment``), so the config is seeded there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _seed_config(home: Path, value: str) -> Path:
    config = home / ".config" / "retroarch" / "retroarch.cfg"
    config.parent.mkdir(parents=True)
    config.write_text(f'video_driver = "vulkan"\ninput_driver = "{value}"\n')
    return config


async def test_a_config_using_x_is_fixed(harness, home):
    config = _seed_config(home, "x")

    result = harness.endpoints.fix_retroarch_input_driver()

    assert result == {"success": True, "message": "Changed input_driver to sdl2"}
    assert config.read_text() == 'video_driver = "vulkan"\ninput_driver = "sdl2"\n'


async def test_a_config_not_using_x_has_nothing_to_fix(harness, home):
    config = _seed_config(home, "sdl2")

    result = harness.endpoints.fix_retroarch_input_driver()

    assert result == {"success": False, "reason": "nothing_to_fix", "message": "No fix needed"}
    assert config.read_text() == 'video_driver = "vulkan"\ninput_driver = "sdl2"\n'


async def test_no_config_at_all_has_nothing_to_fix(harness):
    result = harness.endpoints.fix_retroarch_input_driver()

    assert result == {"success": False, "reason": "nothing_to_fix", "message": "No fix needed"}


async def test_a_write_that_fails_answers_unknown_and_leaves_the_config(harness, home):
    config = _seed_config(home, "x")
    # The replacement is written beside the config first, so a directory that
    # takes no new file fails the write before the config is touched.
    config.parent.chmod(0o500)
    try:
        result = harness.endpoints.fix_retroarch_input_driver()
    finally:
        config.parent.chmod(0o700)

    assert result == {"success": False, "reason": "unknown", "message": "Operation failed"}
    assert config.read_text() == 'video_driver = "vulkan"\ninput_driver = "x"\n'
