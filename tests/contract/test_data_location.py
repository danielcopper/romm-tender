"""Contract tests for the data-location callables.

Driven frontend-shaped per ``src/api/backend.ts``:
``getDataLocationNotice = callable<[], DataLocationNotice>``,
``getDataLocationCandidates = callable<[], DataLocationCandidates | CallableFailure>``,
``chooseDataLocation = callable<[string], {success: true} | CallableFailure>``.

This is the tier that reaches the callables through the REAL ``bootstrap()``,
which is the only thing that can answer where this run's data actually is — the
service reads what the start decided and never re-probes. The harness roots
``user_home`` under ``tmp_path`` with no older location beside it, so the honest
answer here is the settled one, and the assertion that matters is that the roots
bootstrap chose are the ones under that home.
"""

from __future__ import annotations

import json
import os


async def test_a_clean_start_raises_no_condition(harness):
    result = await harness.plugin.get_data_location_notice()
    assert result == {"pending": False, "kind": None, "message": None}


async def test_the_run_reads_and_writes_under_the_users_own_home(harness):
    home = str(harness.tmp_path / "home")
    assert harness.settings_dir == os.path.join(home, ".config", "romm-tender")
    assert harness.data_dir == os.path.join(home, ".local", "share", "romm-tender")
    assert os.path.isfile(os.path.join(harness.data_dir, "romm_sync.db"))


async def test_every_older_location_is_listed_even_when_none_is_on_disk(harness):
    """Dropping the absent ones would leave a choice showing a single option."""
    result = await harness.plugin.get_data_location_candidates()

    assert [entry["source"] for entry in result["candidates"]] == ["decky-romm-sync", "romm-tender"]
    assert all(entry["present"] is False for entry in result["candidates"])
    assert all(entry["size_bytes"] is None for entry in result["candidates"])


async def test_a_choice_is_recorded_where_the_next_start_looks_for_it(harness):
    result = await harness.plugin.choose_data_location("decky-romm-sync")

    assert result == {"success": True}
    recorded = harness.tmp_path / "runtime" / "data-location-choice.json"
    assert json.loads(recorded.read_text(encoding="utf-8")) == {"source": "decky-romm-sync"}


async def test_a_choice_naming_something_else_is_refused(harness):
    result = await harness.plugin.choose_data_location("some-other-plugin")

    assert result["success"] is False
    assert result["reason"] == "unknown_source"
    assert result["message"]
    assert not (harness.tmp_path / "runtime" / "data-location-choice.json").exists()
