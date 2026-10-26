"""Unit tests for the ``RomSaveSyncState`` aggregate and its ``FileSyncState`` VO."""

from __future__ import annotations

import pytest

from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from domain.save_measure import SaveFileMeasure


class TestDefaults:
    def test_default_construction_sets_expected_defaults(self):
        state = RomSaveSyncState()
        assert state.active_slot is None
        assert state.slot_confirmed is False
        assert state.emulator == "retroarch"
        assert state.system == ""
        assert state.last_synced_core is None
        assert state.own_upload_ids is None
        assert state.slots == {}
        assert state.files == {}
        assert state.last_sync_check_at is None

    def test_slots_and_files_are_independent_per_instance(self):
        a = RomSaveSyncState()
        b = RomSaveSyncState()
        a.slots["default"] = {"source": "local"}
        a.files["x.srm"] = FileSyncState()
        assert b.slots == {}
        assert b.files == {}


class TestFileSyncState:
    def test_defaults(self):
        fs = FileSyncState()
        assert fs.tracked_save_id is None
        assert fs.last_sync_hash is None
        assert fs.last_sync_server_hash is None
        assert fs.last_sync_at == ""
        assert fs.last_sync_server_updated_at == ""
        assert fs.last_sync_server_save_id is None
        assert fs.last_sync_server_size is None
        assert fs.last_sync_local_mtime is None
        assert fs.last_sync_local_size is None

    def test_is_frozen(self):
        fs = FileSyncState()
        with pytest.raises((AttributeError, Exception)):
            fs.last_sync_hash = "abc"  # type: ignore[misc]


class TestAdoptBaseline:
    def test_minimal_required_anchors_create_entry(self):
        state = RomSaveSyncState()
        state.adopt_baseline("game.srm", tracked_save_id=11, last_sync_hash="deadbeef")
        fs = state.files["game.srm"]
        assert fs.tracked_save_id == 11
        assert fs.last_sync_hash == "deadbeef"
        # Untouched optionals fall back to FileSyncState defaults.
        assert fs.last_sync_server_hash is None
        assert fs.last_sync_at == ""
        assert fs.last_sync_server_updated_at == ""
        assert fs.last_sync_server_save_id is None
        assert fs.last_sync_server_size is None
        assert fs.last_sync_local_mtime is None
        assert fs.last_sync_local_size is None

    def test_all_fields_are_recorded(self):
        state = RomSaveSyncState()
        state.adopt_baseline(
            "game.srm",
            tracked_save_id=11,
            last_sync_hash="deadbeef",
            last_sync_server_hash="srv-deadbeef",
            last_sync_at="2026-05-28T10:00:00",
            last_sync_server_updated_at="2026-05-28T09:00:00",
            last_sync_server_save_id=99,
            last_sync_server_size=2048,
            last_sync_local_mtime=1716890400.0,
            last_sync_local_size=2048,
        )
        fs = state.files["game.srm"]
        assert fs.tracked_save_id == 11
        assert fs.last_sync_hash == "deadbeef"
        assert fs.last_sync_server_hash == "srv-deadbeef"
        assert fs.last_sync_at == "2026-05-28T10:00:00"
        assert fs.last_sync_server_updated_at == "2026-05-28T09:00:00"
        assert fs.last_sync_server_save_id == 99
        assert fs.last_sync_server_size == 2048
        assert fs.last_sync_local_mtime == 1716890400.0
        assert fs.last_sync_local_size == 2048

    def test_re_adopt_replaces_existing_baseline(self):
        state = RomSaveSyncState()
        state.adopt_baseline("game.srm", tracked_save_id=11, last_sync_hash="old")
        state.adopt_baseline(
            "game.srm",
            tracked_save_id=11,
            last_sync_hash="new",
            last_sync_server_size=4096,
        )
        assert len(state.files) == 1
        fs = state.files["game.srm"]
        assert fs.last_sync_hash == "new"
        assert fs.last_sync_server_size == 4096

    @pytest.mark.parametrize("bad_id", [0, -1])
    def test_non_positive_tracked_save_id_raises(self, bad_id: int):
        state = RomSaveSyncState()
        with pytest.raises(ValueError, match="tracked_save_id must be positive"):
            state.adopt_baseline("game.srm", tracked_save_id=bad_id, last_sync_hash="x")
        assert state.files == {}

    def test_empty_hash_raises(self):
        state = RomSaveSyncState()
        with pytest.raises(ValueError, match="last_sync_hash is required"):
            state.adopt_baseline("game.srm", tracked_save_id=11, last_sync_hash="")
        assert state.files == {}


class TestTrackOwnUpload:
    def test_starts_list_when_unknown(self):
        state = RomSaveSyncState()
        assert state.own_upload_ids is None
        state.track_own_upload(5)
        assert state.own_upload_ids == [5]

    def test_appends_new_id(self):
        state = RomSaveSyncState()
        state.track_own_upload(5)
        state.track_own_upload(6)
        assert state.own_upload_ids == [5, 6]

    def test_idempotent_on_existing_id(self):
        state = RomSaveSyncState()
        state.track_own_upload(5)
        state.track_own_upload(5)
        assert state.own_upload_ids == [5]

    def test_appends_to_explicitly_empty_list(self):
        state = RomSaveSyncState()
        state.own_upload_ids = []
        state.track_own_upload(7)
        assert state.own_upload_ids == [7]


class TestConfirmSlot:
    def test_named_slot_sets_active_confirmed_and_key(self):
        state = RomSaveSyncState()
        state.confirm_slot("manual")
        assert state.active_slot == "manual"
        assert state.slot_confirmed is True
        assert state.slots["manual"] == {"source": "local", "count": 0, "latest_updated_at": None}

    def test_none_raises_legacy_retired(self):
        # Legacy slot:null confirmation is retired (#1276): confirm_slot rejects
        # None outright rather than confirming the no-slot mode.
        state = RomSaveSyncState()
        with pytest.raises(ValueError, match="non-empty slot name"):
            state.confirm_slot(None)  # type: ignore[arg-type]
        assert state.slot_confirmed is False
        assert state.active_slot is None

    def test_empty_string_raises_legacy_retired(self):
        state = RomSaveSyncState()
        with pytest.raises(ValueError, match="non-empty slot name"):
            state.confirm_slot("")
        assert state.slot_confirmed is False

    def test_does_not_overwrite_existing_slot_entry(self):
        state = RomSaveSyncState()
        state.slots["manual"] = {"source": "server", "count": 3, "latest_updated_at": "2026-05-28T10:00:00"}
        state.confirm_slot("manual")
        assert state.slots["manual"] == {
            "source": "server",
            "count": 3,
            "latest_updated_at": "2026-05-28T10:00:00",
        }


class TestSwitchActiveSlot:
    def test_named_slot_sets_active_and_key_without_confirming(self):
        state = RomSaveSyncState()
        state.switch_active_slot("manual")
        assert state.active_slot == "manual"
        assert state.slot_confirmed is False
        assert state.slots["manual"] == {"source": "local", "count": 0, "latest_updated_at": None}

    def test_does_not_clear_existing_confirmation(self):
        state = RomSaveSyncState()
        state.confirm_slot("a")
        assert state.slot_confirmed is True
        state.switch_active_slot("b")
        assert state.active_slot == "b"
        assert state.slot_confirmed is True

    def test_none_uses_empty_string_key(self):
        state = RomSaveSyncState()
        state.switch_active_slot(None)
        assert state.active_slot is None
        assert state.slots[""] == {"source": "local", "count": 0, "latest_updated_at": None}

    def test_empty_string_normalizes_to_none(self):
        state = RomSaveSyncState()
        state.switch_active_slot("")
        assert state.active_slot is None
        assert "" in state.slots

    def test_does_not_overwrite_existing_slot_entry(self):
        state = RomSaveSyncState()
        state.slots["manual"] = {"source": "server", "count": 3, "latest_updated_at": None}
        state.switch_active_slot("manual")
        assert state.slots["manual"]["source"] == "server"


class TestMarkSyncEvaluated:
    def test_sets_last_sync_check_at(self):
        state = RomSaveSyncState()
        state.mark_sync_evaluated("2026-05-28T12:00:00")
        assert state.last_sync_check_at == "2026-05-28T12:00:00"


class TestRecordSyncedCore:
    def test_sets_core_and_emulator(self):
        state = RomSaveSyncState()
        state.record_synced_core("snes9x", "retroarch")
        assert state.last_synced_core == "snes9x"
        assert state.emulator == "retroarch"

    def test_overwrites_default_emulator(self):
        state = RomSaveSyncState()
        state.record_synced_core("dolphin_core", "dolphin")
        assert state.emulator == "dolphin"

    def test_none_core_records_emulator_without_clobbering_core(self):
        state = RomSaveSyncState()
        state.record_synced_core("snes9x", "retroarch")
        state.record_synced_core(None, "dolphin")
        # Emulator updated; the previously-known core survives the None.
        assert state.emulator == "dolphin"
        assert state.last_synced_core == "snes9x"

    def test_none_core_on_fresh_state_leaves_core_none(self):
        state = RomSaveSyncState()
        state.record_synced_core(None, "retroarch")
        assert state.emulator == "retroarch"
        assert state.last_synced_core is None

    def test_empty_emulator_raises(self):
        state = RomSaveSyncState()
        with pytest.raises(ValueError, match="emulator is required"):
            state.record_synced_core("snes9x", "")
        # Nothing recorded on rejection.
        assert state.emulator == "retroarch"
        assert state.last_synced_core is None


class TestPromoteSlotToServer:
    def test_flips_local_slot_to_server_and_seeds_count(self):
        state = RomSaveSyncState()
        state.slots["manual"] = {"source": "local", "count": 0, "latest_updated_at": None}
        state.promote_slot_to_server("manual")
        assert state.slots["manual"]["source"] == "server"
        assert state.slots["manual"]["count"] == 1

    def test_noop_when_slot_already_server(self):
        state = RomSaveSyncState()
        state.slots["manual"] = {"source": "server", "count": 5, "latest_updated_at": "2026-05-28T10:00:00"}
        state.promote_slot_to_server("manual")
        # Untouched — no double-count on a re-run.
        assert state.slots["manual"] == {
            "source": "server",
            "count": 5,
            "latest_updated_at": "2026-05-28T10:00:00",
        }

    def test_noop_when_slot_untracked(self):
        state = RomSaveSyncState()
        state.promote_slot_to_server("ghost")
        assert "ghost" not in state.slots

    def test_empty_slot_raises(self):
        state = RomSaveSyncState()
        with pytest.raises(ValueError, match="slot is required"):
            state.promote_slot_to_server("")


class TestDeleteFileTracking:
    def test_removes_tracked_file(self):
        state = RomSaveSyncState()
        state.adopt_baseline("game.srm", tracked_save_id=1, last_sync_hash="abc")
        state.delete_file_tracking("game.srm")
        assert "game.srm" not in state.files

    def test_only_removes_named_file(self):
        state = RomSaveSyncState()
        state.adopt_baseline("a.srm", tracked_save_id=1, last_sync_hash="abc")
        state.adopt_baseline("b.srm", tracked_save_id=2, last_sync_hash="def")
        state.delete_file_tracking("a.srm")
        assert list(state.files) == ["b.srm"]

    def test_noop_when_file_untracked(self):
        state = RomSaveSyncState()
        state.adopt_baseline("a.srm", tracked_save_id=1, last_sync_hash="abc")
        state.delete_file_tracking("missing.srm")
        assert list(state.files) == ["a.srm"]


class TestDeleteSlotTracking:
    def test_removes_slot(self):
        state = RomSaveSyncState()
        state.slots["manual"] = {"source": "server", "count": 1, "latest_updated_at": None}
        state.delete_slot_tracking("manual")
        assert "manual" not in state.slots

    def test_only_removes_named_slot(self):
        state = RomSaveSyncState()
        state.slots["a"] = {"source": "local", "count": 0, "latest_updated_at": None}
        state.slots["b"] = {"source": "server", "count": 2, "latest_updated_at": None}
        state.delete_slot_tracking("a")
        assert list(state.slots) == ["b"]

    def test_noop_when_slot_untracked(self):
        state = RomSaveSyncState()
        state.slots["a"] = {"source": "local", "count": 0, "latest_updated_at": None}
        state.delete_slot_tracking("ghost")
        assert list(state.slots) == ["a"]


class TestRefreshSlotListing:
    def test_replaces_slots(self):
        state = RomSaveSyncState()
        state.slots = {"old": {"source": "local"}}
        merged = {"a": {"source": "server", "count": 1, "latest_updated_at": None}}
        state.refresh_slot_listing(merged)
        assert state.slots is merged
        assert "old" not in state.slots


class TestClearBaselines:
    def test_resets_files_to_empty(self):
        state = RomSaveSyncState()
        state.adopt_baseline("game.srm", tracked_save_id=1, last_sync_hash="abc")
        assert state.files
        state.clear_baselines()
        assert state.files == {}


class TestTheContentSize:
    """The content size a baseline records, and the one it gains from a file still holding it."""

    def test_adopt_baseline_records_the_content_size_beside_the_raw_size(self):
        state = RomSaveSyncState()
        state.adopt_baseline(
            "game.srm",
            tracked_save_id=11,
            last_sync_hash="h",
            last_sync_local_size=131072,
            last_sync_content_size=32768,
        )
        fs = state.files["game.srm"]
        assert (fs.last_sync_local_size, fs.last_sync_content_size) == (131072, 32768)

    def test_a_file_unchanged_since_its_baseline_backfills_its_content_size(self):
        state = RomSaveSyncState()
        state.adopt_baseline("game.srm", tracked_save_id=11, last_sync_hash="h", last_sync_local_size=131072)

        state.backfill_content_size("game.srm", local_hash="h", content_size=32768)

        fs = state.files["game.srm"]
        assert fs.last_sync_content_size == 32768
        assert (fs.tracked_save_id, fs.last_sync_hash, fs.last_sync_local_size) == (11, "h", 131072)

    def test_a_file_changed_since_its_baseline_backfills_nothing(self):
        state = RomSaveSyncState()
        state.adopt_baseline("game.srm", tracked_save_id=11, last_sync_hash="h")

        state.backfill_content_size("game.srm", local_hash="edited", content_size=32768)

        assert state.files["game.srm"].last_sync_content_size is None

    def test_a_recorded_content_size_is_never_replaced(self):
        state = RomSaveSyncState()
        state.adopt_baseline("game.srm", tracked_save_id=11, last_sync_hash="h", last_sync_content_size=100)

        state.backfill_content_size("game.srm", local_hash="h", content_size=32768)

        assert state.files["game.srm"].last_sync_content_size == 100

    @pytest.mark.parametrize("local_hash", ["", "h"])
    def test_an_untracked_file_or_an_empty_hash_backfills_nothing(self, local_hash):
        state = RomSaveSyncState()
        state.backfill_content_size("game.srm", local_hash=local_hash, content_size=1)
        assert state.files == {}

    def test_an_empty_hash_never_matches_an_empty_baseline(self):
        state = RomSaveSyncState(files={"game.srm": FileSyncState(last_sync_hash="")})
        state.backfill_content_size("game.srm", local_hash="", content_size=1)
        assert state.files["game.srm"].last_sync_content_size is None


_MEASURE = SaveFileMeasure(content_hash="local-hash", size=131072, content_size=32768)
_HEAD = {
    "id": 77,
    "content_hash": "server-hash",
    "updated_at": "2026-02-17T06:00:00Z",
    "file_size_bytes": 131072,
}


class TestReachSyncBoundary:
    def test_a_brand_new_aggregate_gets_its_system_the_default_slot_and_the_emulator(self):
        state = RomSaveSyncState(emulator="")
        state.reach_sync_boundary("gba", "quicksave")
        assert (state.system, state.active_slot, state.emulator, state.last_synced_core) == (
            "gba",
            "quicksave",
            "retroarch",
            None,
        )
        assert "quicksave" in state.slots

    def test_without_a_default_slot_the_first_slot_is_autosave(self):
        state = RomSaveSyncState()
        state.reach_sync_boundary("gba", None)
        assert state.active_slot == "autosave"

    def test_a_known_system_slot_and_core_stay_unless_a_core_is_given(self):
        state = RomSaveSyncState(system="snes", active_slot="mine", emulator="mgba", last_synced_core="old_libretro.so")
        state.slots["mine"] = {"source": "server"}

        state.reach_sync_boundary("gba", "quicksave")
        assert (state.system, state.active_slot, state.emulator, state.last_synced_core) == (
            "snes",
            "mine",
            "mgba",
            "old_libretro.so",
        )

        state.reach_sync_boundary("gba", "quicksave", emulator_tag="retroarch-snes9x", core="snes9x_libretro.so")
        assert (state.emulator, state.last_synced_core) == ("retroarch-snes9x", "snes9x_libretro.so")

    def test_a_legacy_aggregate_with_slots_keeps_its_legacy_slot(self):
        state = RomSaveSyncState(active_slot=None, slots={"": {"source": "server"}})
        state.reach_sync_boundary("gba", "quicksave")
        assert state.active_slot is None


class TestRecordTransfer:
    def test_the_baseline_is_composed_from_the_server_save_and_the_local_measure(self):
        state = RomSaveSyncState()

        recorded = state.record_transfer("game.srm", _HEAD, _MEASURE, 1700000000.5, synced_at="2026-03-01T00:00:00Z")

        assert recorded is True
        assert state.files["game.srm"] == FileSyncState(
            tracked_save_id=77,
            last_sync_hash="local-hash",
            last_sync_server_hash="server-hash",
            last_sync_at="2026-03-01T00:00:00Z",
            last_sync_server_updated_at="2026-02-17T06:00:00Z",
            last_sync_server_save_id=77,
            last_sync_server_size=131072,
            last_sync_local_mtime=1700000000.5,
            last_sync_local_size=131072,
            last_sync_content_size=32768,
        )

    def test_a_server_save_without_updated_at_records_the_sync_time(self):
        state = RomSaveSyncState()
        head = {k: v for k, v in _HEAD.items() if k != "updated_at"}
        state.record_transfer("game.srm", head, _MEASURE, None, synced_at="2026-03-01T00:00:00Z")
        assert state.files["game.srm"].last_sync_server_updated_at == "2026-03-01T00:00:00Z"

    def test_a_server_save_without_a_hash_records_none_for_it(self):
        state = RomSaveSyncState()
        head = {k: v for k, v in _HEAD.items() if k != "content_hash"}
        state.record_transfer("game.srm", head, _MEASURE, None, synced_at="t")
        assert state.files["game.srm"].last_sync_server_hash is None

    @pytest.mark.parametrize(
        ("server_save", "local"),
        [
            ({k: v for k, v in _HEAD.items() if k != "id"}, _MEASURE),
            (_HEAD, None),
            (_HEAD, SaveFileMeasure(content_hash="", size=1, content_size=1)),
        ],
        ids=["no-server-id", "no-local-file", "no-local-hash"],
    )
    def test_nothing_to_track_records_nothing(self, server_save, local):
        earlier = FileSyncState(tracked_save_id=5, last_sync_hash="earlier")
        state = RomSaveSyncState(files={"game.srm": earlier})

        assert state.record_transfer("game.srm", server_save, local, None, synced_at="t") is False
        assert state.files == {"game.srm": earlier}


class TestAdoptHead:
    def test_the_baseline_is_composed_from_the_head_and_the_local_measure(self):
        state = RomSaveSyncState()

        state.adopt_head("game.srm", _HEAD, _MEASURE, 1700000000.5, synced_at="2026-03-01T00:00:00Z")

        assert state.files["game.srm"] == FileSyncState(
            tracked_save_id=77,
            last_sync_hash="local-hash",
            last_sync_server_hash="server-hash",
            last_sync_at="2026-03-01T00:00:00Z",
            last_sync_server_updated_at="2026-02-17T06:00:00Z",
            last_sync_server_save_id=77,
            last_sync_server_size=131072,
            last_sync_local_mtime=1700000000.5,
            last_sync_local_size=131072,
            last_sync_content_size=32768,
        )

    def test_a_head_without_updated_at_records_none_known(self):
        state = RomSaveSyncState()
        head = {k: v for k, v in _HEAD.items() if k != "updated_at"}
        state.adopt_head("game.srm", head, _MEASURE, 1.0, synced_at="2026-03-01T00:00:00Z")
        assert state.files["game.srm"].last_sync_server_updated_at == ""


class TestAdoptHeadWithLocalChange:
    def test_the_baseline_is_the_head_s_and_keeps_the_previous_sync_time(self):
        previous = FileSyncState(
            tracked_save_id=5,
            last_sync_hash="copy-hash",
            last_sync_at="2026-03-01T00:00:00Z",
            last_sync_local_mtime=1700000000.5,
            last_sync_content_size=32768,
        )
        state = RomSaveSyncState(files={"game.srm": previous})

        adopted = state.adopt_head_with_local_change("game.srm", _HEAD)

        assert adopted is True
        assert state.files["game.srm"] == FileSyncState(
            tracked_save_id=77,
            last_sync_hash="server-hash",
            last_sync_server_hash="server-hash",
            last_sync_at="2026-03-01T00:00:00Z",
            last_sync_server_updated_at="2026-02-17T06:00:00Z",
            last_sync_server_save_id=77,
            last_sync_server_size=131072,
            last_sync_local_mtime=None,
            last_sync_local_size=131072,
            last_sync_content_size=None,
        )

    def test_a_file_without_a_previous_baseline_records_no_sync_time(self):
        state = RomSaveSyncState()
        assert state.adopt_head_with_local_change("game.srm", _HEAD) is True
        assert state.files["game.srm"].last_sync_at == ""

    def test_a_head_without_updated_at_records_none_known(self):
        state = RomSaveSyncState()
        head = {k: v for k, v in _HEAD.items() if k != "updated_at"}
        state.adopt_head_with_local_change("game.srm", head)
        assert state.files["game.srm"].last_sync_server_updated_at == ""

    @pytest.mark.parametrize(
        "head",
        [
            {k: v for k, v in _HEAD.items() if k != "content_hash"},
            {**_HEAD, "content_hash": ""},
            {k: v for k, v in _HEAD.items() if k != "id"},
            {**_HEAD, "id": None},
        ],
        ids=["no-content-hash", "empty-content-hash", "no-id", "id-none"],
    )
    def test_a_head_without_a_content_hash_or_an_id_drops_the_tracking(self, head):
        other = FileSyncState(tracked_save_id=6, last_sync_hash="other")
        state = RomSaveSyncState(
            files={"game.srm": FileSyncState(tracked_save_id=5, last_sync_hash="earlier"), "game.rtc": other}
        )

        assert state.adopt_head_with_local_change("game.srm", head) is False
        assert state.files == {"game.rtc": other}


class TestUploadSlot:
    def test_upload_slot_branches(self):
        """``upload_slot`` maps each (active_slot, slots) case correctly.

        Device presence is no branch here: ``SaveTransfer.upload_save_file`` takes a
        ``LiveDeviceId``, so by the time this resolver runs a registered device is
        guaranteed.
        """
        # Named active slot → that slot.
        assert RomSaveSyncState(active_slot="desktop").upload_slot("default") == "desktop"
        # Brand-new ROM (active None, no slots) → the configured default slot.
        assert RomSaveSyncState().upload_slot("main") == "main"
        # Explicit legacy (active None, slots populated) → None (slot:null), not default.
        legacy = RomSaveSyncState()
        legacy.switch_active_slot(None)  # active=None, adds the "" slots key
        assert legacy.upload_slot("default") is None
