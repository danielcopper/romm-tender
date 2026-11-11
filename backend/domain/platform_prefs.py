"""Per-platform sync toggles: which listed platform a sync reads, and the move of the toggles kept by RomM id.

The toggles are kept by each platform's ``fs_slug`` (#2452 D8). Those an earlier
release kept by RomM's platform id wait under their own key until a read of
RomM's listing moves them (:func:`switches_by_folder`), and are read by the
rule they were written under (:func:`resolve_sync_enabled`).

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.platform_names import listed_fs_slug

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def platform_sync_enabled(switches: Mapping[str, Any], fs_slug: str | None) -> bool:
    """Whether the listed platform kept in folder *fs_slug* is synced: only one its own toggle switched on.

    A platform without a toggle is not synced — none stored for its folder
    (#2452 D24), or no ``fs_slug`` to keep one under (D26).
    """
    return fs_slug is not None and switches.get(fs_slug) is True


def resolve_sync_enabled(stored: Mapping[str, Any], pid: str) -> bool:
    """Whether the toggles kept by RomM's platform id had platform ``pid`` synced.

    An absent id was synced **only while the map was empty** (every platform on
    by default); once any toggle was recorded, an absent id was off.
    """
    return stored.get(pid, len(stored) == 0)


def switches_by_folder(
    by_id: Mapping[str, Any], switches: Mapping[str, bool], platforms: Iterable[Mapping[str, Any]]
) -> dict[str, bool] | None:
    """*switches* with the toggles kept by RomM id moved onto the listed *platforms*' ``fs_slug``.

    Every listed platform with an ``fs_slug`` that *switches* holds no toggle for
    is written the state *by_id* gave it (:func:`resolve_sync_enabled`), so the
    move switches no platform on or off (#2452 D22, D24); an id the listing does
    not name is dropped (D21). ``None`` where the listing names no ``fs_slug``,
    since nothing can be moved yet.
    """
    listed = [(str(p.get("id")), folder) for p in platforms if (folder := listed_fs_slug(p)) is not None]
    if not listed:
        return None
    moved = dict(switches)
    for pid, folder in listed:
        moved.setdefault(folder, bool(resolve_sync_enabled(by_id, pid)))
    return moved
