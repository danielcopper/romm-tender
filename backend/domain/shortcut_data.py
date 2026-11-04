"""Pure functions for building shortcut data dicts.

No I/O, no imports from services, adapters, or lib.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from domain.rom_file import has_file_on_romm
from domain.sibling_group import compute_sibling_group_key
from domain.user_data_location import LAUNCHER_EXE_SUFFIX


def shortcut_launch_options(app_id: int) -> str:
    """The launch options every bound shortcut carries: its own appId.

    Steam passes them to the launcher as its first argument, and the launcher
    claims the start hand-over by it; the command itself is asked at each start
    (``services/game_start.py``). The panel composes the same value for a
    shortcut it has just created, before Tender knows the appId
    (``frontend/src/utils/steamShortcuts.ts``).
    """
    return str(app_id)


def extract_version_metadata(rom: dict[str, Any]) -> dict[str, Any]:
    """Extract a raw RomM ROM dict's sibling-group identity + version dimensions.

    The server-derived facts (ADR-0021) persisted on the ``Rom`` aggregate: the
    sibling-group key plus the ``regions`` / ``languages`` / ``revision`` /
    ``tags`` / ``is_main_sibling`` dimensions. Shared by the sync shortcut build
    (:func:`build_shortcuts_data`) and the version-switch persist path so both
    derive the group key and ``is_main_sibling`` identically — one source of
    truth for the extraction, no drift between the two call sites.

    Prefers a ``sibling_group_key`` already on the dict (the incremental-skip
    path carries the authoritative key) and recomputes only when absent.
    ``is_main_sibling`` sits under ``rom_user``; a missing or ``null``
    ``rom_user`` degrades to ``False``.
    """
    return {
        "sibling_group_key": rom.get("sibling_group_key") or compute_sibling_group_key(rom),
        "regions": list(rom.get("regions") or []),
        "languages": list(rom.get("languages") or []),
        "revision": rom.get("revision") or "",
        "tags": list(rom.get("tags") or []),
        "is_main_sibling": bool((rom.get("rom_user") or {}).get("is_main_sibling", False)),
    }


def select_shortcuts_to_relocate(exes: Mapping[int, str], launcher_exe: str) -> list[int]:
    """Pick the app IDs whose ``exe`` is ours but is not *launcher_exe* yet.

    *exes* maps a live shortcut's app ID to the ``exe`` it currently carries —
    every non-Steam shortcut, ours and foreign. Ownership is the
    :data:`~domain.user_data_location.LAUNCHER_EXE_SUFFIX` ending and nothing
    else, which is what lets a shortcut of ours be recognised and repointed
    wherever it was written, without anything having recorded where it came
    from. A shortcut naming a launcher that does not end that way is foreign,
    whoever wrote it, and is never selected.

    Surrounding quotes are stripped before both tests. ``AddShortcut`` stores
    the path unquoted, so ours are bare; a hand-added shortcut can be quoted,
    and reading one as foreign would leave it behind while counting the run
    complete.

    Sorted, so two readings of one unchanged library produce the same plan in
    the same order — the app IDs cross to the frontend as a list it writes in
    order, and neither log line prints them, so nothing else would show a
    difference.
    """
    selected = []
    for app_id, exe in exes.items():
        bare = exe.strip('"')
        if bare.endswith(LAUNCHER_EXE_SUFFIX) and bare != launcher_exe:
            selected.append(app_id)
    return sorted(selected)


def build_shortcuts_data(roms: list[dict[str, Any]], launcher_exe: str) -> list[dict[str, Any]]:
    """Transform ROM list into shortcut data dicts for frontend AddShortcut calls.

    *launcher_exe* is the launcher every built shortcut's ``exe`` names, and the
    directory holding it is the ``start_dir`` written beside it. It is handed in
    rather than composed here because its home is outside every directory this
    program owns, which one start's install settles and only the composition
    root knows.

    No launch command is built: a shortcut's launch options are its own appId,
    which Steam assigns when the shortcut is created, so the panel writes them
    (``frontend/src/utils/steamShortcuts.ts``), and what starts the game is asked
    at each start (``services/game_start.py``).

    The sibling-group key (ADR-0021) and RomM's version dimensions (``regions`` /
    ``languages`` / ``revision`` / ``tags`` / ``is_main_sibling``) are derived
    from each raw ROM dict here and carried through so the commit persists them
    on the ``Rom`` aggregate. ``is_main_sibling`` sits under ``rom_user``; the
    lookup is guarded so a missing or ``null`` ``rom_user`` degrades to ``False``.
    """
    start_dir = os.path.dirname(launcher_exe)
    return [
        {
            "rom_id": rom["id"],
            "name": rom["name"],
            "fs_name": rom.get("fs_name", ""),
            # Alphabetical tie-break key for sibling-group representative
            # resolution (ADR-0021): RomM ships it on the list endpoint; fall
            # back to the filename stem when absent. No DB column — carried only
            # through the sync pipeline, never persisted.
            "fs_name_no_ext": rom.get("fs_name_no_ext") or os.path.splitext(rom.get("fs_name", ""))[0],
            "exe": launcher_exe,
            "start_dir": start_dir,
            "platform_name": rom.get("platform_name", "Unknown"),
            "platform_slug": rom.get("platform_slug", ""),
            "igdb_id": rom.get("igdb_id"),
            "sgdb_id": rom.get("sgdb_id"),
            "ra_id": rom.get("ra_id"),
            # Server-reported ROM size in bytes (#1395), carried through so the
            # commit persists it on the Rom aggregate (it rides the sync UPSERT
            # like the version dimensions). Absent/None → "size unknown".
            "fs_size_bytes": rom.get("fs_size_bytes"),
            # Persisted like the size; the sibling collapse reads it to keep a
            # ROM RomM holds no file for from becoming the bound version.
            "has_file_on_disk": has_file_on_romm(rom),
            "cover_path": "",
            # The sibling-group key + version dimensions (ADR-0021), extracted by
            # the shared helper so the sync build and the version-switch persist
            # path derive them identically. The incremental-skip path reconstructs
            # ROM dicts from persisted rows carrying the authoritative key (real
            # platform_id baked in) but no ``platform_id`` field, so the helper
            # prefers that key over recomputing (which would yield ``…:None`` and
            # split the group's bucket, #1296).
            **extract_version_metadata(rom),
        }
        for rom in roms
    ]
