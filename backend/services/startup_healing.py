"""StartupHealingService — startup-time state reconciliation.

Owns the reconciliation steps that run after state is loaded and
adapters are wired: reports the downloads whose files are not where their
``rom_installs`` row says, transitions any ``running`` ``SyncRun`` left
behind by a crash into ``interrupted``, and lists the bound shortcuts whose
launch options in Steam's shortcut file are not their appId. The report deletes
nothing; why such a record stays is ``docs/architecture/database-design.md``'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.installed_roms import is_pending_migration_path
from domain.migration_paths import pending_homes_from_kv
from domain.shortcut_data import shortcut_launch_options

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Sequence

    from services.protocols import (
        Clock,
        ConflictRules,
        PathExistsReader,
        ResolvedPathFn,
        SteamConfigStore,
        UnitOfWorkFactory,
    )


# The most paths one report line names; its count covers every one.
_PATHS_LOGGED = 3


def _first_paths(paths: Sequence[str]) -> str:
    shown = paths[:_PATHS_LOGGED]
    lead = "" if len(shown) == len(paths) else f"; the first {len(shown)}"
    return f"{lead}: {', '.join(shown)}"


@dataclass(frozen=True)
class StartupHealingServiceConfig:
    """Frozen wiring bundle handed to ``StartupHealingService.__init__``.

    Carries the runtime logger, the clock, the generic path-exists probe, the
    path resolver that turns a stored home marker into the directory it names,
    and the SQLite Unit-of-Work factory (the transactional seam over the
    ``rom_installs``, ``sync_runs``, and ``kv_config`` repositories — the last
    holding the pending-migration previous home marker). ``steam_config``
    reads the launch options Steam's shortcut file holds, which the
    launch-options reconcile compares with each bound shortcut's appId;
    ``loop`` runs that read off the loop thread, and ``conflict_rules`` are what
    its use case checks and leases through. Bundled
    here so the ctor stays within the S107 parameter budget and the service
    stays free of raw filesystem I/O.
    """

    logger: logging.Logger
    clock: Clock
    path_probe: PathExistsReader
    resolve_path: ResolvedPathFn
    uow_factory: UnitOfWorkFactory
    steam_config: SteamConfigStore
    loop: asyncio.AbstractEventLoop
    conflict_rules: ConflictRules


class StartupHealingService:
    """Reports ``rom_installs`` rows missing on disk and heals orphaned ``SyncRun``s."""

    def __init__(self, *, config: StartupHealingServiceConfig) -> None:
        self._logger = config.logger
        self._clock = config.clock
        self._path_probe = config.path_probe
        self._resolve_path = config.resolve_path
        self._uow_factory = config.uow_factory
        self._steam_config = config.steam_config
        self._loop = config.loop
        self._rules = config.conflict_rules

    def report_missing_installs(self) -> None:
        """Log the ``rom_installs`` rows whose recorded file and folder are both missing.

        Deletes nothing. One line for the missing downloads and one for those
        waiting for a pending migration home (the previous home plus any
        additional hops, #1042) — RetroDECK has moved away from those paths and
        the migration relocates the record — each with the count and the first
        :data:`_PATHS_LOGGED` paths, so a drive holding hundreds of downloads
        costs two lines rather than hundreds.
        """
        with self._uow_factory() as uow:
            installs = list(uow.rom_installs.iter_all())
            stored_homes = pending_homes_from_kv(
                uow.kv_config.get("retrodeck_home_path_previous") or "",
                uow.kv_config.get("retrodeck_home_path_hops"),
            )
        pending_homes = [self._resolve_path(home) for home in stored_homes]
        missing: list[str] = []
        waiting: list[str] = []
        for install in installs:
            file_path = install.file_path
            rom_dir = install.rom_dir
            if (file_path and self._path_probe.exists(file_path)) or (rom_dir and self._path_probe.exists(rom_dir)):
                continue
            if self._under_pending_home(file_path, rom_dir, pending_homes):
                waiting.append(rom_dir or file_path)
            else:
                missing.append(rom_dir or file_path)
        if missing:
            self._logger.warning(f"{len(missing)} download(s) missing on disk{_first_paths(missing)}")
        if waiting:
            self._logger.info(f"{len(waiting)} download(s) wait for the pending RetroDECK move{_first_paths(waiting)}")

    def _under_pending_home(self, file_path: str, rom_dir: str | None, pending_homes: Sequence[str]) -> bool:
        """Answer whether one install's recorded paths live under a pending home.

        Both sides are resolved before the prefix match, because either can be
        spelled two ways for one directory: a path recorded through
        ``lib.path_safety.safe_join`` is resolved, one an older migration
        relocated carries whatever spelling the home had when it ran
        (``remap_under_current`` joins that home verbatim), and a marker written
        before the roots were resolved carries the other spelling again (#1838).
        A match that misses reports an install the move will relocate as
        missing, so the question has to be about directories rather than
        strings.

        Resolving the recorded path is safe here in a way it is not in the
        deletion guards: this decides what a log line says and authorizes
        nothing. The loop already probes each path's existence, so it is no new
        class of cost.
        """
        return is_pending_migration_path(
            self._resolve_path(file_path) if file_path else file_path,
            self._resolve_path(rom_dir) if rom_dir else rom_dir,
            pending_homes,
        )

    def reconcile_orphaned_sync_runs(self) -> None:
        """Transition a ``running`` ``SyncRun`` left by a crash into ``interrupted``.

        A hard crash (process kill, true ``asyncio.CancelledError``) mid-sync
        leaves the run record stuck in ``running`` because no terminal
        transition fired. A backend restart mid-run is an external death, not a
        user Cancel, so on the next startup that orphaned run is marked
        ``interrupted`` in a short write UoW — the sync-run history reflects what
        actually happened rather than an eternally-in-flight sync.
        """
        with self._uow_factory() as uow:
            run = uow.sync_runs.get_running()
            if run is None:
                return
            self._logger.info(f"Healing orphaned sync run {run.id}: marking interrupted (backend restarted mid-run)")
            run.mark_interrupted(at=self._clock.now().isoformat(), reason="interrupted by restart")
            uow.sync_runs.save(run)

    async def get_bound_launch_options(self) -> dict[str, Any]:
        """List every bound shortcut whose launch options are not its appId, for the panel to set.

        Checks the ``get_bound_launch_options`` endpoint's conflict rules first.
        Answers ``{"success": True, "items", "prune_lease_token"}``: each item is
        ``{"app_id", "launch_options"}`` with the launch options the shortcut
        must carry, and the token an ``installed_reconcile`` lease for the
        panel's Steam writes when there are items, ``None`` when there are none.

        A shortcut is listed when Steam's shortcut file holds anything else for
        it, holds nothing for it, or cannot be read at all — then every bound
        shortcut is listed, and the panel's confirm decides. Installed or not
        makes no difference: every bound shortcut carries its appId.
        """
        async with self._rules.hold("get_bound_launch_options", prune=True):
            items = await self._loop.run_in_executor(None, self._bound_launch_options_io)
            token = await self._rules.acquire_lease("installed_reconcile") if items else None
            return {"success": True, "items": items, "prune_lease_token": token}

    def _bound_launch_options_io(self) -> list[dict[str, Any]]:
        """The bound shortcuts whose launch options in Steam's file differ from their appId.

        The bound appIds are read in one short read UoW that is closed before the
        file is read, so the file read holds no transaction.
        """
        with self._uow_factory() as uow:
            app_ids = sorted({rom.shortcut_app_id for rom in uow.roms.iter_all() if rom.shortcut_app_id is not None})
        held = self._steam_config.read_shortcut_launch_options()
        items: list[dict[str, Any]] = []
        for app_id in app_ids:
            wanted = shortcut_launch_options(app_id)
            if held is None or held.get(app_id) != wanted:
                items.append({"app_id": app_id, "launch_options": wanted})
        return items
