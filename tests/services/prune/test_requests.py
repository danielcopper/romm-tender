from __future__ import annotations

import pytest

from lib.errors import Refused
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
    bad_page = {"scope": "bulk", "offset": -1, "limit": 101}
    with pytest.raises(Refused) as caught:
        parse_preview_request(bad_page)
    assert (caught.value.reason, caught.value.message) == (
        "invalid_page",
        "Offset must be non-negative and limit 0-100.",
    )


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
    without_recovery = {
        "repoint_shortcuts": True,
        "remove_rows": True,
        "remove_fully_vanished": False,
        "create_recovery_bundle": False,
    }
    selected = frozenset({7})
    with pytest.raises(Refused) as caught:
        parse_options(without_recovery, selected)
    assert caught.value.reason == "invalid_options"


def test_installed_selection_pages_are_wire_bounded_without_total_selection_cap() -> None:
    page = parse_selection_page(
        {"preview_id": "preview", "selection_id": None, "rom_ids": list(range(1, 101)), "final": False}
    )
    assert page == ("preview", None, list(range(1, 101)), False)
    too_large = {"preview_id": "preview", "selection_id": None, "rom_ids": list(range(1, 102)), "final": True}
    with pytest.raises(Refused) as caught:
        parse_selection_page(too_large)
    assert caught.value.reason == "invalid_selection"


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


@pytest.mark.parametrize(
    ("payload", "reason", "message"),
    [
        (None, "invalid_request", "Preview request must be an object."),
        (["bulk"], "invalid_request", "Preview request must be an object."),
        ({}, "invalid_scope", "Preview scope must be bulk or rom."),
        ({"scope": "all"}, "invalid_scope", "Preview scope must be bulk or rom."),
        ({"scope": "rom"}, "invalid_rom_id", "A positive ROM id is required."),
        ({"scope": "rom", "rom_id": 0}, "invalid_rom_id", "A positive ROM id is required."),
        ({"scope": "rom", "rom_id": -3}, "invalid_rom_id", "A positive ROM id is required."),
        ({"scope": "rom", "rom_id": True}, "invalid_rom_id", "A positive ROM id is required."),
        ({"scope": "rom", "rom_id": "7"}, "invalid_rom_id", "A positive ROM id is required."),
        ({"scope": "bulk", "preview_id": 7}, "invalid_preview_id", "Preview id must be a string or null."),
        ({"scope": "bulk", "limit": 101}, "invalid_page", "Offset must be non-negative and limit 0-100."),
    ],
)
def test_a_preview_request_is_refused_with_the_reason_of_its_first_fault(payload, reason, message) -> None:
    with pytest.raises(Refused) as caught:
        parse_preview_request(payload)

    assert (caught.value.reason, caught.value.message) == (reason, message)


_OPTION_KEYS = ("repoint_shortcuts", "remove_rows", "remove_fully_vanished", "create_recovery_bundle")
_MISSING = object()


@pytest.mark.parametrize("key", _OPTION_KEYS)
@pytest.mark.parametrize("value", [_MISSING, None, 1, "true"])
def test_every_cleanup_option_must_be_an_explicit_boolean(key, value) -> None:
    request: dict[str, object] = dict.fromkeys(_OPTION_KEYS, True)
    if value is _MISSING:
        del request[key]
    else:
        request[key] = value
    none_selected = frozenset[int]()

    with pytest.raises(Refused) as caught:
        parse_options(request, none_selected)

    assert (caught.value.reason, caught.value.message) == (
        "invalid_options",
        "Every cleanup option must be explicitly true or false.",
    )


def _selection_page(**overrides: object) -> dict[str, object]:
    return {"preview_id": "preview", "selection_id": None, "rom_ids": [1], "final": False, **overrides}


_PAGE_MESSAGE = "Each selection page may contain 0-100 positive ROM ids."


@pytest.mark.parametrize(
    ("payload", "reason", "message"),
    [
        (None, "invalid_request", "Installed-content selection must be an object."),
        ([1], "invalid_request", "Installed-content selection must be an object."),
        (_selection_page(preview_id=None), "invalid_preview_id", "Preview id must be a non-empty string."),
        (_selection_page(preview_id=""), "invalid_preview_id", "Preview id must be a non-empty string."),
        (_selection_page(preview_id=7), "invalid_preview_id", "Preview id must be a non-empty string."),
        (_selection_page(selection_id=""), "invalid_selection_id", "Selection id must be a non-empty string or null."),
        (_selection_page(selection_id=7), "invalid_selection_id", "Selection id must be a non-empty string or null."),
        (_selection_page(rom_ids=None), "invalid_selection", _PAGE_MESSAGE),
        (_selection_page(rom_ids=[0]), "invalid_selection", _PAGE_MESSAGE),
        (_selection_page(rom_ids=[True]), "invalid_selection", _PAGE_MESSAGE),
        (_selection_page(final=None), "invalid_selection", "Selection final must be explicitly true or false."),
        (_selection_page(final=1), "invalid_selection", "Selection final must be explicitly true or false."),
        (_selection_page(final="true"), "invalid_selection", "Selection final must be explicitly true or false."),
    ],
)
def test_a_selection_page_is_refused_with_the_reason_of_its_first_fault(payload, reason, message) -> None:
    with pytest.raises(Refused) as caught:
        parse_selection_page(payload)

    assert (caught.value.reason, caught.value.message) == (reason, message)
