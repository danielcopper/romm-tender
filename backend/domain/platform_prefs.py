"""Per-platform sync toggles: which listed platform a sync reads, and the move of the toggles kept by RomM id.

The toggles are kept by each platform's ``fs_slug`` (#2452 D8). Those an earlier
release kept by RomM's platform id wait under their own key until a read of
RomM's listing moves them (:func:`switches_by_folder`); until then they are read
by the rule they were saved under (:func:`resolve_sync_enabled`, D27).

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.platform_names import listed_fs_slug
from domain.state_migrations import SWITCHES_BY_ID_SETTING, SWITCHES_SETTING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def stored_switches(settings: Mapping[str, Any]) -> dict[str, Any]:
    """The toggles kept by ``fs_slug`` in *settings*; ``{}`` where the value is no map."""
    switches = settings.get(SWITCHES_SETTING)
    return switches if isinstance(switches, dict) else {}


def switches_kept_by_id(settings: Mapping[str, Any]) -> dict[str, Any] | None:
    """The toggles still kept by RomM's platform id in *settings*; ``None`` once they have moved, or were never kept."""
    by_id = settings.get(SWITCHES_BY_ID_SETTING)
    return by_id if isinstance(by_id, dict) else None


def platform_sync_enabled(switches: Mapping[str, Any], fs_slug: str | None) -> bool:
    """Whether the platform kept in folder *fs_slug* has its own toggle switched on.

    A platform without a toggle is not synced — none stored for its folder
    (#2452 D24), or no ``fs_slug`` to keep one under (D26).
    """
    return fs_slug is not None and switches.get(fs_slug) is True


def resolve_sync_enabled(stored: Mapping[str, Any], pid: str) -> bool:
    """Whether the toggles kept by RomM's platform id had platform ``pid`` synced.

    An absent id was synced **only while the map was empty** (every platform on
    by default); once any toggle was recorded, an absent id was off.
    """
    return stored.get(pid, len(stored) == 0) is True


def listed_platform_synced(
    switches: Mapping[str, Any], by_id: Mapping[str, Any] | None, platform: Mapping[str, Any]
) -> bool:
    """Whether the listed *platform* is synced: by its own toggle, else by the toggles still kept by RomM id.

    *by_id* is ``None`` once the toggles kept by id have moved. Until then a
    platform with no toggle of its own is read by the rule its id was saved
    under, so a sync reads exactly the platforms the move would switch on
    (#2452 D27). A platform without an ``fs_slug`` is never synced (D26).
    """
    folder = listed_fs_slug(platform)
    if folder is None:
        return False
    if folder in switches or by_id is None:
        return platform_sync_enabled(switches, folder)
    return resolve_sync_enabled(by_id, str(platform.get("id")))


def switches_by_folder(
    by_id: Mapping[str, Any], switches: Mapping[str, Any], platforms: Iterable[Mapping[str, Any]]
) -> dict[str, Any] | None:
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
        moved.setdefault(folder, resolve_sync_enabled(by_id, pid))
    return moved


def ids_the_listing_drops(by_id: Mapping[str, Any], platforms: Iterable[Mapping[str, Any]]) -> list[str]:
    """The ids in *by_id* no listed platform with an ``fs_slug`` carries, sorted: their toggles are dropped (D21)."""
    listed = {str(p.get("id")) for p in platforms if listed_fs_slug(p) is not None}
    return sorted(pid for pid in by_id if pid not in listed)
