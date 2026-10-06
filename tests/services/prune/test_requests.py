from __future__ import annotations

from services.prune._models import PruneOptions
from services.prune.requests import (
    _MAX_STEAM_SNAPSHOT_BYTES,
    parse_options,
    parse_preview_request,
    parse_selection_page,
    snapshot_bytes,
    valid_snapshot,
)


def _snapshot(app_id: int = 9001) -> dict[str, object]:
    return {
        "app_id": app_id,
        "name": "Game",
        "exe": "/tender/bin/tender-rom-launcher",
        "start_dir": "/tender",
        "launch_options": "launch",
        "minutes_playtime_forever": 10,
        "minutes_playtime_last_two_weeks": None,
        "last_played": 123,
        "collections": [{"id": "favorites", "name": "Favorites"}],
    }


def test_preview_request_defaults_and_rejects_bad_pages() -> None:
    assert parse_preview_request({"scope": "bulk"}) == ("bulk", None, None, 0, 50)
    assert parse_preview_request({"scope": "rom", "rom_id": 7}) == ("rom", 7, None, 0, 50)
    invalid = parse_preview_request({"scope": "bulk", "offset": -1, "limit": 101})
    assert invalid == {
        "success": False,
        "reason": "invalid_page",
        "message": "Offset must be non-negative and limit 0-100.",
    }


def test_options_require_explicit_booleans_and_recovery_for_content_selection() -> None:
    parsed = parse_options(
        {
            "repoint_shortcuts": True,
            "remove_rows": True,
            "remove_fully_vanished": False,
            "create_recovery_bundle": True,
        },
        frozenset({7}),
    )
    assert parsed == PruneOptions(True, True, False, True, frozenset({7}))
    invalid = parse_options(
        {
            "repoint_shortcuts": True,
            "remove_rows": True,
            "remove_fully_vanished": False,
            "create_recovery_bundle": False,
        },
        frozenset({7}),
    )
    assert isinstance(invalid, dict)
    assert invalid["reason"] == "invalid_options"


def test_installed_selection_pages_are_wire_bounded_without_total_selection_cap() -> None:
    page = parse_selection_page(
        {"preview_id": "preview", "selection_id": None, "rom_ids": list(range(1, 101)), "final": False}
    )
    assert page == ("preview", None, list(range(1, 101)), False)
    too_large = parse_selection_page(
        {"preview_id": "preview", "selection_id": None, "rom_ids": list(range(1, 102)), "final": True}
    )
    assert isinstance(too_large, dict)
    assert too_large["reason"] == "invalid_selection"


def test_snapshot_requires_exact_app_complete_shape_and_no_base64() -> None:
    assert valid_snapshot(_snapshot(), 9001) is True
    assert valid_snapshot(_snapshot(9002), 9001) is False
    missing = _snapshot()
    missing.pop("launch_options")
    assert valid_snapshot(missing, 9001) is False
    encoded = _snapshot()
    encoded["cover_base64"] = "AAAA"
    assert valid_snapshot(encoded, 9001) is False
    foreign = _snapshot()
    foreign["exe"] = "/usr/bin/foreign-game"
    assert valid_snapshot(foreign, 9001) is False


def test_snapshot_rejects_oversized_payload() -> None:
    snapshot = _snapshot()
    snapshot["collections"] = [{"id": str(index), "name": "x" * 4096} for index in range(100)]
    assert valid_snapshot(snapshot, 9001) is False


# The same snapshot and the same count stand in `frontend/src/utils/pruneActions.test.ts`:
# the panel refuses to send what this side would refuse to accept, and no more.
_SHARED_SNAPSHOT_VECTOR: dict[str, object] = {
    "app_id": 9001,
    "name": "Pok\u00e9mon \u904a\u622f \U0001f3ae",
    "exe": "/tender/bin/tender-rom-launcher",
    "start_dir": "/tender",
    "launch_options": 'a\tb\nc\u0001d\u007fe"f\\g',
    "minutes_playtime_forever": 120,
    "minutes_playtime_last_two_weeks": None,
    "last_played": 1234,
    "collections": [{"id": "favorites", "name": "Favoris \u2605 \U0001f579\ufe0f"}],
}


def test_a_snapshot_measures_the_same_bytes_as_in_the_panel() -> None:
    assert snapshot_bytes(_SHARED_SNAPSHOT_VECTOR) == 339


def test_snapshot_cap_is_judged_on_compact_json() -> None:
    snapshot = _snapshot()
    snapshot["launch_options"] = ""
    snapshot["launch_options"] = "x" * (_MAX_STEAM_SNAPSHOT_BYTES - snapshot_bytes(snapshot))

    assert snapshot_bytes(snapshot) == _MAX_STEAM_SNAPSHOT_BYTES
    assert valid_snapshot(snapshot, 9001) is True
    snapshot["launch_options"] += "x"
    assert valid_snapshot(snapshot, 9001) is False
