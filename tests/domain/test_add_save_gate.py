"""Tests for ``domain.add_save_gate`` — RomM's ``add_save`` refusal rule read off a listing."""

from __future__ import annotations

from typing import Any

import pytest

from domain.add_save_gate import slot_refuses_new_save

ME = "device-1"
SLOT = "promoted"


def _save(
    save_id: Any,
    updated_at: Any,
    *,
    slot: str | None = SLOT,
    file_name: str = "pokemon.srm",
    current: bool | None = True,
) -> dict[str, Any]:
    """A listed save; *current* None leaves out this device's entry, as RomM never does."""
    device_syncs = [] if current is None else [{"device_id": ME, "is_current": current}]
    return {
        "id": save_id,
        "slot": slot,
        "file_name": file_name,
        "updated_at": updated_at,
        "device_syncs": device_syncs,
    }


class TestAnEmptySlot:
    def test_a_slot_with_no_save_passes(self):
        assert slot_refuses_new_save([], SLOT, ME) is False

    def test_saves_in_other_slots_do_not_count(self):
        saves = [
            _save(1, "2026-03-05T10:00:00Z", slot="autosave", current=False),
            _save(2, "2026-03-05T10:00:00Z", slot=None, current=False),
        ]

        assert slot_refuses_new_save(saves, SLOT, ME) is False


class TestTheNewestSaveDecides:
    def test_current_on_the_newest_passes(self):
        saves = [_save(1, "2026-03-01T10:00:00Z", current=False), _save(2, "2026-03-05T10:00:00Z", current=True)]

        assert slot_refuses_new_save(saves, SLOT, ME) is False

    def test_not_current_on_the_newest_refuses(self):
        saves = [_save(1, "2026-03-01T10:00:00Z", current=True), _save(2, "2026-03-05T10:00:00Z", current=False)]

        assert slot_refuses_new_save(saves, SLOT, ME) is True

    def test_the_newest_is_taken_by_time_not_by_listing_order_or_id(self):
        saves = [_save(9, "2026-03-01T10:00:00Z", current=False), _save(2, "2026-03-05T10:00:00Z", current=True)]

        assert slot_refuses_new_save(saves, SLOT, ME) is False
        assert slot_refuses_new_save(list(reversed(saves)), SLOT, ME) is False

    def test_times_compare_as_instants_across_offsets(self):
        saves = [
            _save(1, "2026-03-05T11:00:00+02:00", current=True),
            _save(2, "2026-03-05T10:00:00Z", current=False),
        ]

        assert slot_refuses_new_save(saves, SLOT, ME) is True

    def test_a_tie_on_time_is_broken_by_the_higher_id(self):
        saves = [_save(7, "2026-03-05T10:00:00Z", current=False), _save(3, "2026-03-05T10:00:00Z", current=True)]

        assert slot_refuses_new_save(saves, SLOT, ME) is True
        assert slot_refuses_new_save(list(reversed(saves)), SLOT, ME) is True

    def test_the_newest_is_taken_over_every_file_name(self):
        saves = [
            _save(1, "2026-03-01T10:00:00Z", file_name="pokemon.srm", current=True),
            _save(2, "2026-03-05T10:00:00Z", file_name="pokemon.rtc", current=False),
        ]

        assert slot_refuses_new_save(saves, SLOT, ME) is True


class TestThisDevicesEntry:
    def test_no_entry_for_this_device_refuses(self):
        assert slot_refuses_new_save([_save(1, "2026-03-05T10:00:00Z", current=None)], SLOT, ME) is True

    def test_another_device_current_on_it_refuses(self):
        save = _save(1, "2026-03-05T10:00:00Z", current=None)
        save["device_syncs"] = [{"device_id": "device-B", "is_current": True}]

        assert slot_refuses_new_save([save], SLOT, ME) is True

    @pytest.mark.parametrize("device_syncs", [None, "device-1", {"device_id": ME, "is_current": True}])
    def test_a_device_syncs_of_another_shape_refuses(self, device_syncs):
        save = _save(1, "2026-03-05T10:00:00Z")
        save["device_syncs"] = device_syncs

        assert slot_refuses_new_save([save], SLOT, ME) is True

    @pytest.mark.parametrize("is_current", [1, "true", None])
    def test_only_a_literal_true_is_current(self, is_current):
        save = _save(1, "2026-03-05T10:00:00Z")
        save["device_syncs"] = [{"device_id": ME, "is_current": is_current}]

        assert slot_refuses_new_save([save], SLOT, ME) is True


class TestWhatCannotBeEstablished:
    @pytest.mark.parametrize("updated_at", [None, "", "yesterday"])
    def test_a_save_in_the_slot_whose_time_does_not_parse_is_left_to_romm(self, updated_at):
        saves = [_save(1, "2026-03-05T10:00:00Z", current=False), _save(2, updated_at)]

        assert slot_refuses_new_save(saves, SLOT, ME) is None

    def test_an_unparsable_time_in_another_slot_does_not_count(self):
        saves = [_save(1, "2026-03-05T10:00:00Z", current=False), _save(2, "yesterday", slot="autosave")]

        assert slot_refuses_new_save(saves, SLOT, ME) is True

    @pytest.mark.parametrize("tied_id", [None, "7"])
    def test_a_tie_with_a_save_without_an_integer_id_is_left_to_romm(self, tied_id):
        saves = [_save(3, "2026-03-05T10:00:00Z", current=True), _save(tied_id, "2026-03-05T10:00:00Z")]

        assert slot_refuses_new_save(saves, SLOT, ME) is None

    def test_a_save_without_an_id_that_is_not_tied_still_decides(self):
        saves = [_save(3, "2026-03-01T10:00:00Z", current=True), _save(None, "2026-03-05T10:00:00Z", current=False)]

        assert slot_refuses_new_save(saves, SLOT, ME) is True
