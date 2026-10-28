"""Tests for domain/save_status_builders — pure file-status DTO builders."""

from typing import Any

from domain.save_status_builders import resolve_chosen_server, shown_device_syncs, status_from_action


class TestBuildersDefensiveBranches:
    """Direct coverage of the defensive fallbacks in save_status_builders."""

    def test_status_from_action_unknown_type_defaults_to_synced(self):
        """An action that matches none of Skip/Upload/Download/Conflict falls back to "synced"."""
        assert status_from_action(object()) == "synced"

    def test_resolve_chosen_server_empty_candidates_returns_none(self):
        """Skip-branch with no server candidates yields no chosen server save."""
        # A bare object is not Download/Conflict/Upload, so the function falls
        # through to the candidates check; an empty list returns None.
        assert resolve_chosen_server(object(), []) is None


_UPDATED_AT = "2026-03-10T10:00:00Z"


def _entry(device_id: str, *, is_current: bool, last_synced_at: str | None = _UPDATED_AT) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "device_name": device_id,
        "is_current": is_current,
        "last_synced_at": last_synced_at,
    }


def _save(*device_syncs: dict[str, Any], updated_at: str | None = _UPDATED_AT) -> dict[str, Any]:
    return {"id": 1, "updated_at": updated_at, "device_syncs": list(device_syncs)}


class TestShownDeviceSyncs:
    """Only RomM's placeholder for the device asking is left out of what the panel is shown."""

    def test_the_placeholder_for_the_device_asking_is_dropped(self):
        other = _entry("other", is_current=True)
        save = _save(_entry("me", is_current=False), other)
        assert shown_device_syncs(save, "me") == [other]

    def test_a_placeholder_later_than_the_save_is_dropped_too(self):
        save = _save(_entry("me", is_current=False, last_synced_at="2026-03-10T11:00:00Z"))
        assert shown_device_syncs(save, "me") == []

    def test_a_row_of_the_device_asking_that_is_current_is_kept(self):
        mine = _entry("me", is_current=True)
        assert shown_device_syncs(_save(mine), "me") == [mine]

    def test_a_row_of_the_device_asking_synced_before_the_save_is_kept(self):
        mine = _entry("me", is_current=False, last_synced_at="2026-03-09T10:00:00Z")
        assert shown_device_syncs(_save(mine), "me") == [mine]

    def test_another_devices_entry_of_the_same_shape_is_kept(self):
        theirs = _entry("other", is_current=False)
        assert shown_device_syncs(_save(theirs), "me") == [theirs]

    def test_without_a_device_asking_every_entry_is_kept(self):
        entry = _entry("me", is_current=False)
        assert shown_device_syncs(_save(entry), None) == [entry]

    def test_an_entry_whose_time_cannot_be_read_is_kept(self):
        entry = _entry("me", is_current=False, last_synced_at="not a time")
        assert shown_device_syncs(_save(entry), "me") == [entry]

    def test_an_entry_of_a_save_whose_time_cannot_be_read_is_kept(self):
        entry = _entry("me", is_current=False)
        assert shown_device_syncs(_save(entry, updated_at=None), "me") == [entry]

    def test_a_save_without_device_syncs_shows_none(self):
        assert shown_device_syncs({"id": 1, "updated_at": _UPDATED_AT}, "me") == []
