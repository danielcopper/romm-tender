"""Contract tests for keeping automatic artwork current (#2381 D5, D22).

Drives ``keep_artwork_current`` and the per-slot fetch the frontend follows it
with, positionally and typed as ``frontend/src/api/backend.ts`` declares them,
over the real wired backend, a real grid directory and the real ``roms`` row the
check's time is recorded on.
"""

from __future__ import annotations

import base64
import hashlib

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import ArtworkSlotOutcome
from lib.errors import SgdbRateLimitedError

from ._seed import seed_rom

_ROM_ID = 41


def _grid(harness):
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    seed_rom(harness, _ROM_ID)
    with harness.uow_factory() as uow:
        rom = uow.roms.get(_ROM_ID)
        rom.assign_sgdb_id(9999)
        uow.roms.save(rom)
    path = harness.tmp_path / "home" / ".local" / "share" / "Steam" / "userdata" / "123" / "config" / "grid"
    path.mkdir(parents=True)
    return path


def _show_tenders_logo(harness, grid, image_id: int) -> None:
    data = f"logo {image_id}".encode()
    (grid / f"{_ROM_ID}_logo.png").write_bytes(data)
    with harness.uow_factory() as uow:
        uow.artwork_slot_outcomes.save(
            ArtworkSlotOutcome.record_set(
                rom_id=_ROM_ID,
                slot=ArtworkSlot.LOGO,
                sgdb_id=9999,
                image_id=image_id,
                image_sha256=hashlib.sha256(data).hexdigest(),
                recorded_at=1.0,
            )
        )


def _seed_logos(harness, *image_ids: int) -> None:
    data = []
    for image_id in image_ids:
        url = f"https://cdn.example/{image_id}.png"
        data.append({"id": image_id, "url": url, "width": 800, "height": 300})
        harness.sgdb.seed_image_bytes(url, f"logo {image_id}".encode())
    harness.sgdb.seed_raw_response("/logos/game/9999", {"success": True, "data": data})


def _checked_at(harness) -> float | None:
    with harness.uow_factory() as uow:
        return uow.roms.get(_ROM_ID).artwork_checked_at


async def test_a_new_pick_is_named_served_and_read_back_once_steam_took_it(harness):
    grid = _grid(harness)
    _show_tenders_logo(harness, grid, 5)
    _seed_logos(harness, 6, 5)

    checked = await harness.endpoints.keep_artwork_current(_ROM_ID)
    served = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "logo", True)
    await harness.endpoints.release_prune_conflict_lease(served.pop("prune_lease_token"))
    (grid / f"{_ROM_ID}_logo.png").write_bytes(base64.b64decode(served["base64"]))
    report = await harness.endpoints.report_sgdb_slot_artwork(
        _ROM_ID, "logo", "set", served["sgdb_id"], served["image_id"], served["sha256"]
    )
    slots = await harness.endpoints.get_artwork_slots(_ROM_ID, _ROM_ID)

    assert checked == {"status": "checked", "replaced": ["logo"]}
    assert (served["status"], served["image_id"], base64.b64decode(served["base64"])) == ("image", 6, b"logo 6")
    assert report == {"success": True}
    logo = next(row for row in slots["slots"] if row["slot"] == "logo")
    assert (logo["state"], logo["source"], logo["image_id"]) == ("automatic", "tender", 6)
    assert _checked_at(harness) == harness.clock.time()


async def test_the_visit_after_the_write_checks_nothing(harness):
    grid = _grid(harness)
    _show_tenders_logo(harness, grid, 5)
    _seed_logos(harness, 6)
    await harness.endpoints.keep_artwork_current(_ROM_ID)
    asked = len(harness.sgdb.requested_paths)
    # The write that makes Steam rebuild the page: the new logo set and reported.
    (grid / f"{_ROM_ID}_logo.png").write_bytes(b"logo 6")
    await harness.endpoints.report_sgdb_slot_artwork(
        _ROM_ID, "logo", "set", 9999, 6, hashlib.sha256(b"logo 6").hexdigest()
    )

    again = await harness.endpoints.keep_artwork_current(_ROM_ID)

    assert again == {"status": "not_due", "replaced": []}
    assert len(harness.sgdb.requested_paths) == asked


async def test_a_check_steamgriddb_did_not_answer_is_asked_again_at_the_next_visit(harness):
    grid = _grid(harness)
    _show_tenders_logo(harness, grid, 5)
    _seed_logos(harness, 6)
    harness.sgdb.request_side_effect = SgdbRateLimitedError("slow down")

    unanswered = await harness.endpoints.keep_artwork_current(_ROM_ID)
    harness.sgdb.request_side_effect = None
    again = await harness.endpoints.keep_artwork_current(_ROM_ID)

    assert unanswered == {"status": "unanswered", "replaced": []}
    assert again == {"status": "checked", "replaced": ["logo"]}


async def test_without_a_key_nothing_is_checked(harness):
    _grid(harness)
    harness.settings["steamgriddb_api_key"] = ""

    answer = await harness.endpoints.keep_artwork_current(_ROM_ID)

    assert answer == {"status": "no_api_key", "replaced": []}
    assert _checked_at(harness) is None


async def test_a_key_no_header_can_carry_answers_the_failure_shape(harness):
    _grid(harness)
    harness.settings["steamgriddb_api_key"] = "bad\nkey"

    answer = await harness.endpoints.keep_artwork_current(_ROM_ID)

    assert (answer["success"], answer["reason"]) == (False, "auth_failed")
    assert set(answer) == {"success", "reason", "message"}


async def test_a_replacement_the_page_was_rebuilt_before_handing_over_is_named_at_the_next_visit(harness):
    grid = _grid(harness)
    _show_tenders_logo(harness, grid, 5)
    _seed_logos(harness, 6)
    await harness.endpoints.keep_artwork_current(_ROM_ID)
    asked = len(harness.sgdb.requested_paths)

    again = await harness.endpoints.keep_artwork_current(_ROM_ID)

    assert again == {"status": "not_due", "replaced": ["logo"]}
    assert len(harness.sgdb.requested_paths) == asked
