"""The save-sort migration: RetroArch moved the save folder, so the saves follow.

RetroArch's ``sort_savefiles_enable`` / ``sort_savefiles_by_content_enable``
flags decide which subdirectory a core opens for a game's save. Flipping one
does not move anything, so every existing save is suddenly in a directory the
emulator no longer reads. This module detects the flip, works out where each
ROM's save files are and where they now belong, and moves them.

It shares nothing with the home migration but :class:`~services.migration._moves.FileMover`
— different trigger, different source of truth for the destination, different
conflict rule (newest-wins in place, never a strategy the user picks). Its
public methods are re-exposed on :class:`~services.migration.service.MigrationService`,
which is the surface every caller already reaches.

**Which files a save consists of is the emulator's answer**, read live per ROM
through the injected ``save_locations`` seam and never a per-system extension
table. Where that answer refuses, the move falls back to whatever the old
directory holds under the ROM's name: a move is not a sync, the files are
already on the user's disk, and leaving one behind where the emulator will not
look is worse than moving one this plugin would never upload.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

from domain.save_layout import ContentDir
from domain.save_path import resolve_save_dir

if TYPE_CHECKING:
    import logging

    from models.state import SaveSortSettings

    from domain.rom_install import RomInstall
    from domain.save_answer import SaveAnswer
    from domain.save_layout import InSaveDir, SaveLayout
    from services.migration._moves import FileMover
    from services.protocols import (
        ActiveCoreReader,
        CoreNameProviderFn,
        EventEmitter,
        MigrationFileStore,
        RetroArchSaveLayoutProvider,
        RetroDeckPaths,
        SaveLocationReader,
        UnitOfWorkFactory,
    )

# kv_config keys for the cross-run save-sort markers this module diffs
# (ADR-0003 Bucket 2): the last-seen observation and the ``_previous``
# companion that exists only while a migration awaits user confirmation.
_KV_SAVE_SORT = "save_sort_settings"
_KV_SAVE_SORT_PREVIOUS = "save_sort_settings_previous"


class SaveSortMigrator:
    """Detects a RetroArch save-sort change and relocates the affected saves."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        migration_file_store: MigrationFileStore,
        retrodeck_paths: RetroDeckPaths,
        get_save_layout: RetroArchSaveLayoutProvider,
        active_core: ActiveCoreReader,
        save_locations: SaveLocationReader,
        get_core_name: CoreNameProviderFn,
        mover: FileMover,
        emit: EventEmitter,
        loop: asyncio.AbstractEventLoop,
        logger: logging.Logger,
    ) -> None:
        self._uow_factory = uow_factory
        self._migration_file_store = migration_file_store
        self._retrodeck_paths = retrodeck_paths
        self._get_save_layout = get_save_layout
        self._active_core = active_core
        self._save_locations = save_locations
        self._get_core_name = get_core_name
        self._mover = mover
        self._emit = emit
        self._loop = loop
        self._logger = logger
        # One-shot guard so the ContentDir "save sync unsupported" warning is
        # logged at most once per process rather than on every detect pass
        # (which runs at the entry of every sync flow).
        self._content_dir_warned = False

    @staticmethod
    def _read_save_sort_settings(uow) -> SaveSortSettings | None:
        """Decode the last-seen save-sort observation from kv_config, ``None`` when absent."""
        raw = uow.kv_config.get(_KV_SAVE_SORT)
        return json.loads(raw) if raw is not None else None

    @staticmethod
    def _read_save_sort_settings_previous(uow) -> SaveSortSettings | None:
        """Decode the pending pre-change save-sort snapshot from kv_config, ``None`` when absent."""
        raw = uow.kv_config.get(_KV_SAVE_SORT_PREVIOUS)
        return json.loads(raw) if raw is not None else None

    def detect_save_sort_change(self) -> SaveLayout:
        """Refresh save-sort state from the live RetroArch config; return the layout.

        Reads the live ``SaveLayout`` and returns it so the SyncEngine can
        hard-gate save sync when it is ``ContentDir`` (#239). When the
        layout is ``ContentDir`` the kv_config save-sort change-detection
        markers are never touched — content-dir saves live next to the ROM,
        outside the saves tree the plugin syncs, so there is no sort layout
        to migrate. A single per-process warning is logged so the unsupported
        state is visible without spamming every sync.

        For the supported ``InSaveDir`` case, runs the cross-run change
        detection against the stored observation, writing the
        ``_KV_SAVE_SORT`` / ``_KV_SAVE_SORT_PREVIOUS`` markers and emitting
        ``save_sort_changed`` when the layout flips (#238).

        May be called from a worker thread (via
        ``SyncEngine._refresh_save_sort_state`` → ``run_in_executor``) or
        from the loop thread. Use ``asyncio.run_coroutine_threadsafe`` to
        schedule the emit coroutine: it is explicitly thread-safe and
        also works correctly when invoked from the loop thread itself.
        ``loop.create_task`` is NOT thread-safe and races with loop
        internals on CPython (#238 review).
        """
        layout = self._get_save_layout()
        if isinstance(layout, ContentDir):
            if not self._content_dir_warned:
                self._logger.warning(
                    "RetroArch savefiles_in_content_dir is enabled — saves are written "
                    "next to the ROM, so plugin save sync is unsupported and is disabled."
                )
                self._content_dir_warned = True
        else:
            self._detect_in_save_dir_change(layout)
        return layout

    def _detect_in_save_dir_change(self, layout: InSaveDir) -> None:
        """Run the cross-run save-sort change detection for a supported ``InSaveDir`` layout.

        Records the current sort settings as the ``_KV_SAVE_SORT`` observation; when they
        differ from the stored one, sets the ``_KV_SAVE_SORT_PREVIOUS`` pending-migration
        marker and emits ``save_sort_changed`` so the frontend can offer the migration (#238).
        """
        current: SaveSortSettings = {"sort_by_content": layout.sort_by_content, "sort_by_core": layout.sort_by_core}
        with self._uow_factory() as uow:
            stored = self._read_save_sort_settings(uow)
        if stored is None:
            with self._uow_factory() as uow:
                uow.kv_config.set(_KV_SAVE_SORT, json.dumps(current))
            return
        if stored == current:
            return
        with self._uow_factory() as uow:
            uow.kv_config.set(_KV_SAVE_SORT_PREVIOUS, json.dumps(stored))
            uow.kv_config.set(_KV_SAVE_SORT, json.dumps(current))
        self._logger.warning(f"RetroArch save sorting changed: {stored} -> {current}")
        # Fire-and-forget: thread-safe schedule of the emit coroutine on
        # the plugin event loop. We deliberately do not await or .result()
        # the future — this mirrors the previous create_task semantics.
        asyncio.run_coroutine_threadsafe(
            self._emit(
                "save_sort_changed",
                {"old_settings": stored, "new_settings": current},
            ),
            self._loop,
        )

    def _resolve_retroarch_corename(self, rom_id: int) -> tuple[str | None, str | None]:
        """Resolve the RetroArch save subdirectory name for a ROM by ``rom_id``.

        Asks the per-ROM ``ActiveCoreReader`` **which** core is active (the
        per-game ``emulator_override`` pin folded over the system default),
        then asks the RetroArch ``.info`` parser (via ``get_core_name``)
        **what** RetroArch calls that core in its own subsystem — which
        is what ``sort_savefiles_enable`` uses when naming save
        subdirectories.

        Returns a ``(corename, core_so)`` tuple. ``corename`` is ``None``
        (fail loud, no ES-DE label fallback) when the resolver cannot
        resolve a core for this ROM. ``core_so`` is the underlying
        ES-DE core ``.so`` basename when known (useful for diagnostics
        when ``corename`` is ``None``), otherwise ``None``.
        """
        core_so, _label = self._active_core.active_core_for_rom(rom_id)
        if not core_so:
            return (None, None)
        corename = self._get_core_name(core_so)
        return (corename or None, core_so)

    def _collect_save_sorting_items(
        self,
        old_settings: SaveSortSettings,
        new_settings: SaveSortSettings,
        installs: list[RomInstall],
    ) -> list[tuple[str, str, str, object, str]]:
        """Collect save files that need migration due to sort setting change.

        ``installs`` is the pre-snapshotted ``RomInstall`` list, and the caller
        closes its read UoW before calling: the walk below reads the machine —
        the core's ``.info`` for a sort-by-core name, the resolver for each
        ROM's file set, and the old directory for what is actually there — and a
        Unit of Work never spans file I/O.
        """
        saves_base = self._retrodeck_paths.saves_path()
        roms_base = self._retrodeck_paths.roms_path()
        need_core = bool(old_settings.get("sort_by_core") or new_settings.get("sort_by_core"))
        items: list[tuple[str, str, str, object, str]] = []
        for install in installs:
            self._collect_rom_sort_items(
                install,
                saves_base,
                roms_base,
                old_settings,
                new_settings,
                need_core,
                items,
            )
        return items

    def _collect_rom_sort_items(
        self,
        install: RomInstall,
        saves_base: str,
        roms_base: str,
        old_settings: SaveSortSettings,
        new_settings: SaveSortSettings,
        need_core: bool,
        items: list[tuple[str, str, str, object, str]],
    ) -> None:
        """Collect migration items for a single ROM's save files."""
        system = install.system
        file_path = install.file_path
        if not system or not file_path:
            return
        core_name: str | None = None
        if need_core:
            core_name, core_so = self._resolve_retroarch_corename(install.rom_id)
            if core_name is None:
                # Fail loud — cannot resolve the RetroArch corename for this ROM's
                # active core, so we can't build the correct sort-by-core path.
                # Skip this item and warn the user rather than silently corrupting
                # the migration with the wrong destination directory.
                self._logger.warning(
                    "Skipping save sort migration for %s/%s: unable to resolve "
                    "RetroArch corename from .info (core_so=%s)",
                    system,
                    os.path.basename(file_path),
                    core_so,
                )
                return
        old_dir = resolve_save_dir(
            file_path,
            saves_base,
            system,
            roms_base=roms_base,
            sort_by_content=old_settings["sort_by_content"],
            sort_by_core=old_settings["sort_by_core"],
            core_name=core_name,
        )
        new_dir = resolve_save_dir(
            file_path,
            saves_base,
            system,
            roms_base=roms_base,
            sort_by_content=new_settings["sort_by_content"],
            sort_by_core=new_settings["sort_by_core"],
            core_name=core_name,
        )
        if old_dir == new_dir:
            return
        emulator = self._active_core.active_emulator_for_rom(install.rom_id)
        answer = self._save_locations.resolve_save_answer(
            system=system,
            content_path=file_path,
            emulator_label=emulator.label if emulator is not None else None,
            # Every ROM this walk sees carries an install record.
            content_installed=True,
        )
        rom_name = os.path.splitext(os.path.basename(file_path))[0]
        names = self._sort_migration_names(answer, old_dir, rom_name)
        for name in names:
            old_file = os.path.join(old_dir, name)
            new_file = os.path.join(new_dir, name)
            if self._migration_file_store.exists(old_file):
                items.append((name, old_file, new_file, lambda: None, "save"))

    def _sort_migration_names(self, answer: SaveAnswer, old_dir: str, rom_name: str) -> list[str]:
        """Which files in *old_dir* this ROM's save-sort move has to carry.

        **A move is not a sync.** Discovery may refuse to say what a save
        consists of, but the files are already on the user's disk and the sort
        change is only relocating them — leaving one behind where the emulator
        will not look is worse than moving one this plugin would never upload.
        So the two paths differ:

        - The answer names files: carry exactly those, configuration included.
          Moving a Saturn ``.bkr`` and leaving its ``.smpc`` behind would split
          one save across two directories.
        - The answer refuses: carry whatever the old directory holds under this
          ROM's name. Judging what counts as a save is not the migration's job.

        The refusing path anchors on ``<stem>.`` rather than the bare stem, so a
        library holding both ``Sonic`` and ``Sonic 2`` does not drag the second
        game's saves along with the first's. Every real save name measured on
        this machine — ``<stem>.srm``, ``<stem>.0.srm``, ``<stem>.dsk.sav`` —
        matches either way.
        """
        if answer.syncable:
            return [component.name for component in answer.owned_files]
        prefix = f"{rom_name}."
        walked = self._migration_file_store.walk_files(old_dir)
        found = sorted(name for name in (walked[0][2] if walked else []) if name.startswith(prefix))
        if found:
            self._logger.info(
                "Save-sort migration: %s answers %s, so moving the %d file(s) named after it in %s: %s",
                rom_name,
                answer.state,
                len(found),
                old_dir,
                found,
            )
        return found

    def _get_save_sort_migration_status_io(
        self, old_settings: SaveSortSettings, new_settings: SaveSortSettings
    ) -> dict[str, Any]:
        with self._uow_factory() as uow:
            installs = list(uow.rom_installs.iter_all())
        items = self._collect_save_sorting_items(old_settings, new_settings, installs)
        return {
            "pending": True,
            "old_settings": old_settings,
            "new_settings": new_settings,
            "saves_count": len(items),
        }

    def dismiss_save_sort_migration(self) -> dict[str, Any]:
        """Dismiss the save sort migration warning without migrating files."""
        with self._uow_factory() as uow:
            uow.kv_config.delete(_KV_SAVE_SORT_PREVIOUS)
        return {"success": True}

    async def get_save_sort_migration_status(self) -> dict[str, Any]:
        with self._uow_factory() as uow:
            old = self._read_save_sort_settings_previous(uow)
            new = self._read_save_sort_settings(uow)
        if not old or not new or old == new:
            return {"pending": False}
        return await self._loop.run_in_executor(None, self._get_save_sort_migration_status_io, old, new)

    def _resolve_save_sort_conflict(
        self,
        label: str,
        old_path: str,
        new_path: str,
        state_updater,
        counts: dict[str, int],
        count_key: str,
        errors: list[str],
    ) -> None:
        """Newest-wins resolution for a save-sort conflict.

        RetroArch does not migrate saves when its sort setting changes. If a
        user flips ``sort_savefiles_enable`` mid-game via the Quick Menu and
        then saves in-game, the new progress is written to the new layout
        while the old location still holds pre-change content. The file at
        the newer mtime contains actual user progress; the older one is
        stale and must be cleaned up. Save-sync has already uploaded the
        newest version to RomM before this runs, so even if local migration
        fails the server still holds the authoritative copy.
        """
        try:
            old_mtime = self._migration_file_store.get_mtime(old_path)
            new_mtime = self._migration_file_store.get_mtime(new_path)
        except OSError as e:
            errors.append(f"{label}: {e}")
            self._logger.error(f"Save-sort conflict mtime read failed: {old_path}: {e}")
            return

        if new_mtime >= old_mtime:
            # Destination is newer — keep it, delete the stale orphan at old_path.
            try:
                self._migration_file_store.remove_file(old_path)
                state_updater()
                counts[count_key] = counts.get(count_key, 0) + 1
                self._logger.info(f"Save-sort conflict: kept newer {new_path}, removed stale {old_path}")
            except OSError as e:
                errors.append(f"{label}: {e}")
                self._logger.error(f"Save-sort orphan cleanup failed: {old_path}: {e}")
            return

        # Source is newer — atomically overwrite destination.
        try:
            self._migration_file_store.make_dirs(os.path.dirname(new_path))
            self._migration_file_store.rename(old_path, new_path)
            state_updater()
            counts[count_key] = counts.get(count_key, 0) + 1
            self._logger.info(f"Save-sort conflict: moved newer {old_path} -> {new_path}")
        except OSError as e:
            errors.append(f"{label}: {e}")
            self._logger.error(f"Save-sort overwrite failed: {old_path}: {e}")

    def _migrate_save_sort_files_io(
        self, old_settings: SaveSortSettings, new_settings: SaveSortSettings, conflict_strategy: str | None
    ) -> dict[str, Any]:
        # conflict_strategy is retained for backwards-compatibility with the
        # callable signature but is unused for save-sort migration — conflicts
        # are resolved in place via newest-wins (see _resolve_save_sort_conflict).
        del conflict_strategy
        with self._uow_factory() as uow:
            installs = list(uow.rom_installs.iter_all())
        items = self._collect_save_sorting_items(old_settings, new_settings, installs)
        if not items:
            with self._uow_factory() as uow:
                uow.kv_config.delete(_KV_SAVE_SORT_PREVIOUS)
            return {"success": True, "message": "No save files to migrate", "saves_moved": 0}
        counts: dict[str, int] = {"rom": 0, "bios": 0, "save": 0}
        errors: list[str] = []
        for label, old_path, new_path, updater, _kind in items:
            if self._migration_file_store.exists(old_path) and self._migration_file_store.exists(new_path):
                self._resolve_save_sort_conflict(label, old_path, new_path, updater, counts, "save", errors)
            else:
                self._mover.migrate_single_item(label, old_path, new_path, updater, "save", None, counts, errors)
        if not errors:
            with self._uow_factory() as uow:
                uow.kv_config.delete(_KV_SAVE_SORT_PREVIOUS)
        return self._mover.build_migration_result(counts, errors)

    async def migrate_save_sort_files(self, conflict_strategy: str | None = None) -> dict[str, Any]:
        with self._uow_factory() as uow:
            old = self._read_save_sort_settings_previous(uow)
            new = self._read_save_sort_settings(uow)
        if not old or not new or old == new:
            return {"success": False, "reason": "no_migration_needed", "message": "No save sorting migration needed"}
        return await self._loop.run_in_executor(None, self._migrate_save_sort_files_io, old, new, conflict_strategy)
