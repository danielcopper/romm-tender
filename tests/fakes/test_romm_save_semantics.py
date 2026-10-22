"""Both save fakes answer ``device_syncs`` the way RomM 5.3.0 does.

Every case runs against each fake, because each fake has to route its own
answers through the shared model in ``_romm_save_semantics``.
"""

from __future__ import annotations

from typing import Any

import pytest

from fakes.fake_romm_api import FakeRommApi
from fakes.fake_save_api import FakeSaveApi
from lib.errors import RommConflictError

ROM_ID = 42
UPDATED_AT = "2026-03-01T00:00:00Z"


@pytest.fixture(params=[FakeRommApi, FakeSaveApi], ids=["FakeRommApi", "FakeSaveApi"])
def fake(request) -> Any:
    return request.param()


def _register(fake: Any, name: str = "steamdeck") -> str:
    return fake.register_device(name, "linux", "romm-tender", "1.0.0")["id"]


def _seed_unsynced_save(fake: Any, save_id: int = 100, **fields: Any) -> None:
    fake.saves[save_id] = {
        "id": save_id,
        "rom_id": ROM_ID,
        "file_name": "game.srm",
        "slot": "default",
        "updated_at": UPDATED_AT,
        "emulator": "retroarch",
        "download_path": "/saves/game.srm",
        **fields,
    }


def _syncs(fake: Any, device_id: str | None) -> list[dict[str, Any]]:
    (save,) = fake.list_saves(ROM_ID, device_id=device_id)
    return save["device_syncs"]


class TestTheCallersPlaceholder:
    def test_a_device_without_a_sync_row_gets_romms_placeholder(self, fake) -> None:
        device_id = _register(fake)
        _seed_unsynced_save(fake)

        assert _syncs(fake, device_id) == [
            {
                "device_id": device_id,
                "device_name": "steamdeck",
                "last_synced_at": UPDATED_AT,
                "is_untracked": False,
                "is_current": False,
            }
        ]

    def test_the_placeholder_comes_first_and_the_others_follow_newest_sync_first(self, fake) -> None:
        device_id = _register(fake)
        _seed_unsynced_save(fake)
        fake.stage_device_sync(100, "device-B", "2026-02-01T00:00:00Z")
        fake.stage_device_sync(100, "device-D", "2026-02-15T00:00:00Z")
        fake.stage_device_sync(100, "device-C", "2026-02-15T00:00:00Z")

        syncs = _syncs(fake, device_id)

        assert [ds["device_id"] for ds in syncs] == [device_id, "device-C", "device-D", "device-B"]
        assert syncs[0]["is_current"] is False
        assert syncs[0]["last_synced_at"] == UPDATED_AT

    def test_a_device_with_a_sync_row_gets_that_row_first_and_no_placeholder(self, fake) -> None:
        device_id = _register(fake)
        fake.seed_foreign_save(ROM_ID, save_id=100, uploaded_by="device-B", updated_at=UPDATED_AT)
        fake.stage_device_sync(100, device_id, UPDATED_AT)

        assert _syncs(fake, device_id) == [
            {
                "device_id": device_id,
                "device_name": "steamdeck",
                "last_synced_at": UPDATED_AT,
                "is_untracked": False,
                "is_current": True,
            },
            {
                "device_id": "device-B",
                "device_name": None,
                "last_synced_at": UPDATED_AT,
                "is_untracked": False,
                "is_current": True,
            },
        ]

    def test_seeded_device_syncs_get_the_placeholder_too(self, fake) -> None:
        device_id = _register(fake)
        seeded = {"device_id": "device-B", "is_current": True, "last_synced_at": UPDATED_AT}
        _seed_unsynced_save(fake, device_syncs=[seeded])

        syncs = _syncs(fake, device_id)

        assert [ds["device_id"] for ds in syncs] == [device_id, "device-B"]
        assert syncs[0]["is_current"] is False
        assert syncs[1] == seeded

    def test_listing_writes_no_sync_row(self, fake) -> None:
        device_id = _register(fake)
        fake.seed_foreign_save(ROM_ID, save_id=100, uploaded_by="device-B", updated_at=UPDATED_AT)

        _syncs(fake, device_id)

        assert _syncs(fake, device_id)[0]["is_current"] is False
        with pytest.raises(RommConflictError):
            fake.upload_save(ROM_ID, "/saves/game.srm", "retroarch", device_id=device_id, slot="default")


class TestWithoutADeviceId:
    def test_device_syncs_is_empty_whatever_rows_are_stored(self, fake) -> None:
        fake.seed_foreign_save(ROM_ID, save_id=100, uploaded_by="device-B", updated_at=UPDATED_AT)
        _seed_unsynced_save(
            fake,
            save_id=101,
            device_syncs=[{"device_id": "device-B", "is_current": True, "last_synced_at": UPDATED_AT}],
        )

        saves = fake.list_saves(ROM_ID)

        assert [save["device_syncs"] for save in saves] == [[], []]


class TestTheDedupAnswer:
    def test_carries_the_placeholder_with_the_saves_updated_at_first(self, fake) -> None:
        device_id = _register(fake)
        fake.seed_foreign_save(ROM_ID, save_id=100, uploaded_by="device-B", updated_at=UPDATED_AT)
        # A newer head this device is current on lets the POST past RomM's slot gate.
        _seed_unsynced_save(fake, save_id=101, updated_at="2026-03-02T00:00:00Z")
        fake.stage_device_sync(101, device_id, "2026-03-02T00:00:00Z")
        fake.arm_add_save_dedup(100)

        response = fake.upload_save(ROM_ID, "/saves/game.srm", "retroarch", device_id=device_id, slot="default")

        assert response["id"] == 100
        assert [ds["device_id"] for ds in response["device_syncs"]] == [device_id, "device-B"]
        assert response["device_syncs"][0]["last_synced_at"] == UPDATED_AT
        assert response["device_syncs"][0]["is_current"] is False

    def test_the_slot_gate_refuses_before_the_dedup_answers(self, fake) -> None:
        device_id = _register(fake)
        fake.seed_foreign_save(ROM_ID, save_id=100, uploaded_by="device-B", updated_at=UPDATED_AT)
        fake.arm_add_save_dedup(100)

        with pytest.raises(RommConflictError):
            fake.upload_save(ROM_ID, "/saves/game.srm", "retroarch", device_id=device_id, slot="default")
