"""Local candidate discovery (platform stamps plus the gone-from-RomM marks) and paged preview projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from domain.fetch_generation import gone_from_romm_ids
from domain.retrodeck_folders import FolderRefused, folder_of
from domain.sibling_resolution import fs_name_stem, group_rows
from lib.url_host import romm_namespace
from services.prune._models import PrunePreview

if TYPE_CHECKING:
    from domain.rom import Rom
    from domain.rom_install import RomInstall
    from services.protocols import RecoveryBundleStore, RetroDeckFolders, UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True)
class PreviewBuilderConfig:
    """Dependencies for local-only prune candidate projection."""

    uow_factory: UnitOfWorkFactory
    recovery_store: RecoveryBundleStore
    retrodeck_folders: RetroDeckFolders
    settings: dict[str, Any]


def _explicit_candidates(rows: list[Rom], gone_ids: set[int], explicit_rom_id: int | None) -> set[int]:
    """The row an explicit per-ROM cleanup names, or every version of its game where each one is gone.

    A run never removes a version its preview did not offer, so a game whose
    every version is gone is offered whole here, as the bulk cleanup offers it.
    """
    for group in group_rows(rows):
        group_ids = {row.rom_id for row in group}
        if explicit_rom_id in group_ids:
            return group_ids if group_ids <= gone_ids else {explicit_rom_id}
    return set()


def _gone_from_romm_ids(uow: UnitOfWork, rows: list[Rom]) -> set[int]:
    """Every row absent from its own platform's last completed fetch, and every row carrying a gone-from-RomM mark."""
    platform_rows: dict[str, list[Rom]] = {}
    for row in rows:
        platform_rows.setdefault(row.platform_slug, []).append(row)
    gone_ids: set[int] = set()
    for slug, platform in platform_rows.items():
        gone_ids.update(gone_from_romm_ids(platform, uow.platform_sync_state.get(slug)))
    return gone_ids


def _preview_entry(
    row: Rom,
    *,
    group_id: str,
    group_size: int,
    bound_count: int,
    candidate: bool,
    gone: bool,
    install: RomInstall | None,
    size: int | None,
    warning: str | None,
) -> dict[str, Any]:
    """One disclosure row."""
    return {
        "rom_id": row.rom_id,
        "name": row.name,
        "fs_name": row.fs_name,
        "fs_name_no_ext": fs_name_stem(row.fs_name),
        "platform_slug": row.platform_slug,
        "group_id": group_id,
        "group_size": group_size,
        "bound_count": bound_count,
        "bound": row.shortcut_app_id is not None,
        "is_main_sibling": row.is_main_sibling,
        "candidate": candidate,
        "gone_from_romm": gone,
        "installed": install is not None,
        "installed_bytes": size,
        "warning": warning,
    }


class PreviewBuilder:
    """Build immutable candidate snapshots without contacting RomM."""

    def __init__(self, *, config: PreviewBuilderConfig) -> None:
        self._uow_factory = config.uow_factory
        self._recovery_store = config.recovery_store
        self._retrodeck_folders = config.retrodeck_folders
        self._settings = config.settings

    def _installed_size(
        self, install: RomInstall | None, folders: dict[str, str | FolderRefused] | FolderRefused
    ) -> tuple[int | None, str | None]:
        """Measured bytes of a row's installed content, or why they could not be read.

        Measured inside the install's system's own ROM folder; nothing is
        measured where that folder is refused. A refusal that stands for every
        folder — a ROM root only assumed, a RetroDECK not set up, folders that
        could not be established — also refuses a start; RetroDECK naming no
        ROM folder does not.
        """
        if install is None:
            return None, None
        folder = folder_of(folders, install.system)
        if isinstance(folder, FolderRefused):
            return None, None
        try:
            return self._recovery_store.measure_path(install.rom_dir or install.file_path, folder), None
        except (OSError, ValueError) as exc:
            return None, str(exc)

    def build(self, preview_id: str, scope: Literal["bulk", "rom"], explicit_rom_id: int | None) -> PrunePreview:
        with self._uow_factory() as uow:
            rows = list(uow.roms.iter_all())
            installs = {install.rom_id: install for install in uow.rom_installs.iter_all()}
            gone_ids = _gone_from_romm_ids(uow, rows)
            if scope == "rom" and explicit_rom_id is not None:
                # The version picker offers this cleanup only after RomM answered
                # the row with a 404, which no stamp or mark has to record yet.
                gone_ids.add(explicit_rom_id)
            candidate_ids = _explicit_candidates(rows, gone_ids, explicit_rom_id) if scope == "rom" else gone_ids

            relevant_groups = [
                group for group in group_rows(rows) if candidate_ids.intersection(r.rom_id for r in group)
            ]
            fingerprint = self._fingerprint(relevant_groups, installs)

        folders = self._retrodeck_folders.rom_folders(install.system for install in installs.values())
        entries: list[dict[str, Any]] = []
        for group in relevant_groups:
            group_id = str(group[0].sibling_group_key or f"rom:{group[0].rom_id}")
            bound_count = sum(row.shortcut_app_id is not None for row in group)
            for row in group:
                install = installs.get(row.rom_id)
                size, warning = self._installed_size(install, folders)
                entries.append(
                    _preview_entry(
                        row,
                        group_id=group_id,
                        group_size=len(group),
                        bound_count=bound_count,
                        candidate=row.rom_id in candidate_ids,
                        gone=row.rom_id in gone_ids,
                        install=install,
                        size=size,
                        warning=warning,
                    )
                )
        # Candidates first, so the paged list opens on the rows a run can actually
        # remove and the retained siblings read as the subordinate disclosure they
        # are. Within each block the old platform/name/id order still applies.
        entries.sort(
            key=lambda entry: (
                not entry["candidate"],
                str(entry["platform_slug"]),
                str(entry["name"]),
                int(entry["rom_id"]),
            )
        )
        return PrunePreview(
            preview_id=preview_id,
            scope=scope,
            explicit_rom_id=explicit_rom_id,
            candidate_ids=frozenset(candidate_ids),
            fingerprint=fingerprint,
            entries=tuple(entries),
            free_bytes=self._recovery_store.free_bytes(),
            server_namespace=romm_namespace(self._settings),
        )

    def page(self, preview: PrunePreview, offset: int, limit: int) -> dict[str, Any]:
        """Project one window of a snapshot onto the wire.

        ``total`` counts every listed row, the candidates and the other versions
        of their games, while ``candidate_total`` counts only the rows offered
        for removal (docs/architecture/removed-game-cleanup.md, "Discovery"). The
        frontend needs both before it has paged the whole list: the headline
        count must not inflate itself with rows that are only listed.
        """
        return {
            "success": True,
            "preview_id": preview.preview_id,
            "scope": preview.scope,
            "items": list(preview.entries[offset : offset + limit]),
            "offset": offset,
            "limit": limit,
            "total": len(preview.entries),
            "candidate_total": sum(1 for entry in preview.entries if entry["candidate"]),
            "free_bytes": self._recovery_store.free_bytes(),
            "recovery_root": self._recovery_store.root(),
        }

    @staticmethod
    def _fingerprint(groups: list[list[Any]], installs: dict[int, Any]) -> tuple[tuple[object, ...], ...]:
        values: list[tuple[object, ...]] = []
        for group in groups:
            for row in group:
                install = installs.get(row.rom_id)
                values.append(
                    (
                        row.rom_id,
                        row.last_fetch_id,
                        row.shortcut_app_id,
                        row.sibling_group_key,
                        install.file_path if install is not None else None,
                        install.rom_dir if install is not None else None,
                    )
                )
        return tuple(sorted(values))
