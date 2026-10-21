"""Tender's folder over the real wire: the place, the drives, a choice and its refusals.

Drives the real ``Endpoints`` through the real ``bootstrap()``, with the real
resolver over a seeded RetroDECK where a ROM folder matters. A drive is a
folder under ``harness.drives_root`` that a test connects through
``harness.mounts``.
"""

from __future__ import annotations

import os

from ._seed import seed_retrodeck_marker


def _drive(harness, name: str, *, connected: bool = True) -> str:
    path = os.path.join(harness.drives_root, name)
    os.makedirs(path, exist_ok=True)
    if connected:
        harness.mounts.add(path)
    return path


async def test_the_folder_answers_its_default_place_and_the_connected_drives(harness):
    home = str(harness.tmp_path / "home")
    sd = _drive(harness, "SD")
    _drive(harness, "gone", connected=False)

    result = await harness.endpoints.get_tender_folder()

    assert result == {
        "place": home,
        "folder": os.path.join(home, "Tender"),
        "default": True,
        "condition": "folder_missing",
        "unreachable": None,
        "elsewhere": [],
        "drive": None,
        "home_free_bytes": result["home_free_bytes"],
        "drives_root": harness.drives_root,
        "drives": [
            {
                "name": "SD",
                "mount_point": sd,
                "kind": "drive",
                "folder": os.path.join(sd, "Tender"),
                "free_bytes": result["drives"][0]["free_bytes"],
            }
        ],
    }


async def test_choosing_a_drive_creates_the_folder_there_and_stores_the_choice(harness):
    sd = _drive(harness, "SD")

    result = await harness.endpoints.choose_tender_place(sd)

    assert result["folder"] == os.path.join(sd, "Tender")
    assert result["condition"] is None
    assert os.path.isfile(os.path.join(sd, "Tender", ".tender-folder"))
    assert harness.settings["tender_folder"] == {
        "place": sd,
        "mount_point": sd,
        "drive_kind": "drive",
        "drive_name": "SD",
    }
    assert result["drive"] == {"kind": "drive", "name": "SD", "mount_point": sd, "connected": True}


async def test_a_place_on_a_drive_that_is_not_connected_is_refused_in_the_canonical_shape(harness):
    result = await harness.endpoints.choose_tender_place(os.path.join(harness.drives_root, "SD", "Games"))

    assert result == {
        "success": False,
        "reason": "place_drive_not_mounted",
        "message": "The drive SD is not connected.",
        "drive": "SD",
    }
    assert "tender_folder" not in harness.settings


async def test_a_place_inside_retrodecks_rom_folder_is_refused_naming_the_source(harness):
    seed_retrodeck_marker(harness)
    os.makedirs(harness.roms_root, exist_ok=True)
    inside = os.path.join(harness.roms_root, "mine")

    result = await harness.endpoints.check_tender_place(inside)

    roms = os.path.realpath(harness.roms_root)
    assert result == {
        "success": False,
        "reason": "place_in_rom_folder",
        "message": f"{inside}/Tender lies inside retrodeck's ROM folder ({roms}). Choose a place outside it.",
        "path": f"{inside}/Tender",
        "source": "retrodeck",
        "folder": roms,
        "system": None,
    }


async def test_a_typed_place_is_asked_about_before_it_is_chosen(harness):
    result = await harness.endpoints.check_tender_place("~/Games/tender")

    home = str(harness.tmp_path / "home")
    assert result == {
        "place": os.path.join(home, "Games", "tender"),
        "folder": os.path.join(home, "Games", "tender", "Tender"),
        "tender_parent": os.path.join(home, "Games"),
        "existing": home,
        "missing": os.path.join("Games", "tender"),
    }


async def test_forgetting_a_folder_nothing_is_recorded_under_is_refused(harness):
    result = await harness.endpoints.forget_tender_folder_records("/run/media/deck/SD/Tender")

    assert result == {
        "success": False,
        "reason": "invalid_value",
        "message": "Tender has nothing recorded under /run/media/deck/SD/Tender any more.",
    }
