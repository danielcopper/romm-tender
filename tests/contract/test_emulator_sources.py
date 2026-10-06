"""Contract: the emulator sources endpoints over the real bootstrap and the real resolver.

The harness's home holds a seeded RetroDECK and nothing else, so what is
listed, switched and answered here is what the real holder detects under it.
"""

from __future__ import annotations

from ._seed import _retrodeck_marker_path, seed_es_systems


async def test_the_listing_names_a_detected_retrodeck(harness):
    seed_es_systems(harness)

    result = await harness.endpoints.get_emulator_sources()

    assert set(result) == {"sources"}
    (source,) = result["sources"]
    assert set(source) == {"kind", "enabled", "starts_games", "root", "findings", "catalogue"}
    assert (source["kind"], source["enabled"], source["starts_games"], source["catalogue"]) == (
        "retrodeck",
        True,
        True,
        "read",
    )


async def test_nothing_detected_lists_nothing(harness):
    assert await harness.endpoints.get_emulator_sources() == {"sources": []}


async def test_switching_the_source_off_takes_its_emulators_away_and_on_brings_them_back(harness):
    seed_es_systems(harness)
    assert (await harness.endpoints.get_system_core_info("gba"))["emulator_data_available"] is True

    await harness.endpoints.set_emulator_source_enabled("retrodeck", False)
    off = await harness.endpoints.get_system_core_info("gba")
    await harness.endpoints.set_emulator_source_enabled("retrodeck", True)
    on = await harness.endpoints.get_system_core_info("gba")

    assert (off["emulator_data_available"], off["emulators"], off["emulator_data_reason"]) == (
        False,
        [],
        "switched_off",
    )
    assert harness.settings["emulator_sources_off"] == []
    assert on["emulator_data_available"] is True
    assert on["emulators"] != []


async def test_a_source_that_is_not_detected_is_refused_in_the_failure_shape(harness):
    seed_es_systems(harness)

    result = await harness.endpoints.move_emulator_source("emudeck", "up")

    assert result["success"] is False
    assert result["reason"] == "unknown_source"
    assert isinstance(result["message"], str)


async def test_a_malformed_retrodeck_json_is_listed_with_its_finding(harness):
    seed_es_systems(harness)
    with open(_retrodeck_marker_path(harness), "w") as handle:
        handle.write("{not json")

    (source,) = (await harness.endpoints.get_emulator_sources())["sources"]

    assert source["kind"] == "retrodeck"
    assert "marker-invalid" in {finding["code"] for finding in source["findings"]}
    assert source["root"] is None
