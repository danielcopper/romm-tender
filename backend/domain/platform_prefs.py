"""Per-platform sync toggles: which listed platform a sync reads, and the move of the toggles kept by RomM id.

The toggles are kept by each platform's ``fs_slug`` (#2452 D8). Those an earlier
release kept by RomM's platform id wait under their own key until a read of
RomM's listing moves them (:func:`switches_by_folder`); until then the
Platforms tab and a sync go by the map that move would write
(:func:`switches_as_read`, D27).

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from domain.platform_listing import platform_entries
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


def resolve_sync_enabled(stored: Mapping[str, Any], pid: str | None) -> bool:
    """Whether the toggles kept by RomM's platform id had platform ``pid`` synced.

    An absent id was synced **only while the map was empty** (every platform on
    by default); once any toggle was recorded, an absent id was off. ``None``
    is a platform listed without a usable id, which no toggle is kept under.
    """
    if pid is None:
        return len(stored) == 0
    return stored.get(pid, len(stored) == 0) is True


class SwitchesMove(NamedTuple):
    """The toggles once those kept by RomM id have moved, and the ids no listed platform with an ``fs_slug`` carries.

    Those ids' toggles are dropped (#2452 D21, D26).
    """

    switches: dict[str, Any]
    dropped: tuple[str, ...]


def switches_by_folder(
    by_id: Mapping[str, Any], switches: Mapping[str, Any], platforms: Iterable[Mapping[str, Any]]
) -> SwitchesMove | None:
    """*switches* with the toggles kept by RomM id moved onto the listed *platforms*' ``fs_slug``.

    Every listed platform with an ``fs_slug`` that *switches* holds no toggle for
    is written the state *by_id* gave it (:func:`resolve_sync_enabled`), so the
    move switches no platform on or off (#2452 D22, D24). An id no listed
    platform with an ``fs_slug`` carries — one the listing no longer names, or
    names without an ``fs_slug`` — is dropped (D21, D26). ``None`` where the
    listing names no ``fs_slug``, since nothing can be moved yet.
    """
    listed = [
        (None if entry.platform_id is None else str(entry.platform_id), folder)
        for entry in platform_entries(platforms)
        if (folder := entry.fs_slug) is not None
    ]
    if not listed:
        return None
    moved = dict(switches)
    for pid, folder in listed:
        moved.setdefault(folder, resolve_sync_enabled(by_id, pid))
    listed_ids = {pid for pid, _folder in listed}
    return SwitchesMove(moved, tuple(sorted(pid for pid in by_id if pid not in listed_ids)))


def switches_as_read(
    switches: Mapping[str, Any], by_id: Mapping[str, Any] | None, platforms: Iterable[Mapping[str, Any]]
) -> Mapping[str, Any]:
    """The toggles a reader of the listed *platforms* goes by: the map the move would write, while one is pending.

    *by_id* is ``None`` once the toggles kept by RomM id have moved, and then
    *switches* are read as they stand (#2452 D27).
    """
    if by_id is None:
        return switches
    move = switches_by_folder(by_id, switches, platforms)
    return switches if move is None else move.switches
