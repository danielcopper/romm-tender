"""Which of Tender's Steam collections a shortcut belongs to, as the sync files it.

Two answers come from here. The platform collection's name for one ROM is the
rule the end-of-run complete set and the per-shortcut answer both apply, so the
two agree. The per-shortcut answer is what each entry of a ``sync_apply_unit``
frame carries, so the frontend can put a game into its collections the moment its
shortcut exists, reading no setting and applying no membership rule itself. Why
the answer is per shortcut, and what it does not cover until the run ends:
``docs/architecture/steam-non-steam-shortcuts.md`` § Collection management.
No I/O, no state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.collection_label import steam_collection_key
from domain.collection_name import fold_collection_name
from domain.sync_diff import BIND_ROM_ID_KEY, should_include_in_platform_collection

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from domain.work_unit import WorkUnit


def platform_collection_name(
    rom_id: int,
    platform_slug: str,
    platform_rom_ids: set[int] | None,
    platform_names: Mapping[str, str],
    create_platform_groups: bool,
) -> str | None:
    """The platform collection *rom_id* is filed in, or ``None`` where it is filed in none.

    ``None`` where :func:`should_include_in_platform_collection` leaves the ROM
    out. Otherwise the run's display name for *platform_slug*, falling back to
    the slug for a platform with no unit in the run.
    """
    if not should_include_in_platform_collection(rom_id, platform_rom_ids, create_platform_groups):
        return None
    return platform_names.get(platform_slug, platform_slug)


@dataclass(frozen=True)
class RunCollections:
    """What a run knows at its start about the Steam collections its shortcuts belong to.

    ``romm_keys_by_rom`` maps each ROM a queued collection lists to the keys of
    those collections — the names ``romm_collection_app_ids`` is keyed by. A
    foreign smart collection is not in it: its listing is its owner's view, not
    the caller's walk.
    """

    romm_keys_by_rom: Mapping[int, tuple[str, ...]]
    platform_names: Mapping[str, str]
    create_platform_groups: bool

    @classmethod
    def at_run_start(
        cls,
        work_queue: Iterable[WorkUnit],
        *,
        platform_names: Mapping[str, str],
        naming_mode: str,
        create_platform_groups: bool,
    ) -> RunCollections:
        """Index the queued collection units' listed members under their Steam keys.

        Keys that fold to one Steam collection are spelled the way the first of
        them in queue order spells it, as the end-of-run map spells them.
        """
        spelling_by_fold: dict[str, str] = {}
        keys_by_rom: dict[int, list[str]] = {}
        for unit in work_queue:
            if unit.type != "collection" or unit.collection_kind is None:
                continue
            if unit.collection_kind == "smart" and not unit.is_own:
                continue
            key = steam_collection_key(unit.name, unit.collection_kind, unit.virtual_type, naming_mode)
            key = spelling_by_fold.setdefault(fold_collection_name(key), key)
            for rom_id in unit.member_rom_ids:
                keys = keys_by_rom.setdefault(rom_id, [])
                if key not in keys:
                    keys.append(key)
        return cls(
            romm_keys_by_rom={rom_id: tuple(keys) for rom_id, keys in keys_by_rom.items()},
            platform_names=dict(platform_names),
            create_platform_groups=create_platform_groups,
        )

    def file_entries(
        self,
        emitted: list[dict[str, Any]],
        shortcuts_data: list[dict[str, Any]],
        platform_rom_ids: set[int],
    ) -> list[dict[str, Any]]:
        """Return *emitted* as fresh copies carrying ``platform_collection`` and ``romm_collections``.

        *shortcuts_data* is the unit's whole built set, which names the members of
        each entry's sibling group; *platform_rom_ids* the ROMs the run's platform
        units have fetched so far. An entry is judged by its binding target
        (``bind_rom_id`` where it rebinds, else its own ``rom_id``). Its RomM keys
        are those of every collection listing a member of its sibling group, or
        only the target where the entry has no group key — a game is in a
        collection when any of its versions is.
        """
        group_rom_ids: dict[str, list[int]] = {}
        for built in shortcuts_data:
            group_key = built.get("sibling_group_key")
            if group_key is not None:
                group_rom_ids.setdefault(group_key, []).append(int(built["rom_id"]))

        filed: list[dict[str, Any]] = []
        for entry in emitted:
            target = int(entry.get(BIND_ROM_ID_KEY, entry["rom_id"]))
            group_key = entry.get("sibling_group_key")
            members = [target, *group_rom_ids.get(group_key, [])] if group_key is not None else [target]
            romm_keys: list[str] = []
            for rom_id in members:
                for key in self.romm_keys_by_rom.get(rom_id, ()):
                    if key not in romm_keys:
                        romm_keys.append(key)
            filed.append(
                {
                    **entry,
                    "platform_collection": platform_collection_name(
                        target,
                        entry.get("platform_slug", ""),
                        platform_rom_ids,
                        self.platform_names,
                        self.create_platform_groups,
                    ),
                    "romm_collections": romm_keys,
                }
            )
        return filed
