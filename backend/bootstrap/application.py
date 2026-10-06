"""The built backend — every wired service, and what the process does with them as a whole.

Contract: :func:`build_application` composes :func:`bootstrap` and
:func:`wire_services` into an :class:`Application`, and runs nothing. What runs,
and when, is the entry point's to decide: the start-up repairs before the port
is bound, :meth:`Application.shutdown` at the end. The endpoints are not here — they are ``main.py``'s, and reach the
services through :attr:`Application.services`.

The failure recorder is handed to :meth:`Application.run_startup_repairs` as a
callable rather than as the host's status record, because ``bootstrap/`` may not
import ``host/``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from domain.identity import MIN_ROMM_VERSION

from .adapters import RuntimeBundle, bootstrap
from .services import WiringConfig, wire_services
from .startup import StartupSteps

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from domain.app_directories import AppDirectories
    from domain.update_release import UpdateSource
    from services.protocols import EventEmitter, SteamInterfaceReader

    from .services import ServicesBundle


class Application:
    """Every wired service, the start-up repairs, and the process's shutdown."""

    def __init__(
        self,
        services: ServicesBundle,
        *,
        logger: logging.Logger,
        loop: asyncio.AbstractEventLoop,
        user_agent: str,
    ) -> None:
        self.services = services
        self.user_agent = user_agent
        self._logger = logger
        self._loop = loop
        # The one-time save-directory backfill, held so the loop cannot collect it.
        self._save_directory_backfill: asyncio.Task[None] | None = None
        # The release check asked for as long as the backend runs, held likewise.
        self._due_update_checks: asyncio.Task[None] | None = None

    def run_startup_repairs(self, report_failure: Callable[[str], None]) -> None:
        """Run the start-up repairs, each one reporting a failure rather than raising it.

        Everything here must be through before the port is bound, which is what
        makes the port file mean "ready".
        """
        steps = StartupSteps(self._logger, report_failure)
        services = self.services
        steps.run("note_update_outcome", services.update_outcome_service.note_start)
        # The prune may run only after a SUCCESSFUL detection: it reads the
        # pending homes the detection writes, and without them it takes every
        # install under the home RetroDECK just left for orphaned.
        if steps.run("detect_retrodeck_path_change", services.migration_service.detect_retrodeck_path_change):
            steps.run("prune_stale_installed_roms", services.startup_healing_service.prune_stale_installed_roms)
        steps.run("reconcile_orphaned_sync_runs", services.startup_healing_service.reconcile_orphaned_sync_runs)
        # No save-sync orphan prune: roms rows are permanent identity anchors
        # and saves/playtime survive a ROM leaving RomM (ADR-0007).
        steps.run("prune_orphaned_artwork_cache", services.sgdb_service.prune_orphaned_artwork_cache)
        steps.run("prune_orphaned_staging_artwork", services.artwork_service.prune_orphaned_staging_artwork)
        steps.run("prune_orphaned_cover_cache", services.artwork_service.prune_orphaned_cover_cache)
        steps.run("cleanup_leftover_tmp_files", services.leftover_tmp_cleanup_service.cleanup_leftover_tmp_files)
        steps.run("remove_update_leftovers", services.update_install_service.remove_leftovers)
        steps.run("note_update_attempt", services.update_install_service.note_start)
        steps.run("record_save_directories", self._start_save_directory_backfill)
        steps.run("run_due_update_checks", self._start_due_update_checks)

    async def shutdown(self) -> None:
        """Stop the background tasks that are still running, then shut the services down."""
        for task in (self._save_directory_backfill, self._due_update_checks):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        services = self.services
        await services.update_install_service.shutdown()
        services.sync_service.shutdown()
        await services.prune_service.shutdown()
        await services.download_service.shutdown()
        await services.migration_service.shutdown()
        await services.session_lifecycle_service.shutdown()
        await services.playtime_service.shutdown()

    def _start_save_directory_backfill(self) -> None:
        """Start the one-time save-directory backfill without holding up start-up.

        It asks the resolver once per installed ROM, which on a large library
        takes a while; the task is kept here so the loop cannot collect it
        mid-run and :meth:`shutdown` can cancel it.
        """
        self._save_directory_backfill = self._loop.create_task(
            self.services.save_sync_service.record_save_directories_once()
        )

    def _start_due_update_checks(self) -> None:
        """Start asking the release check whenever it may be due, for as long as the backend runs."""
        self._due_update_checks = self._loop.create_task(self.services.update_check_service.run_due_checks())


def build_application(
    *,
    directories: AppDirectories,
    update_source: UpdateSource,
    installer_environment: tuple[tuple[str, str], ...],
    user_home: str,
    logger: logging.Logger,
    loop: asyncio.AbstractEventLoop,
    emit: EventEmitter,
    steam: SteamInterfaceReader,
) -> Application:
    """Build every adapter and wire every service into an :class:`Application`.

    Runs none of the start-up repairs; the caller does, once it has somewhere
    to record a failure. Settings are loaded and migrated inside
    :func:`bootstrap`, so every adapter that binds the live settings dict binds
    the migrated one.
    """
    result = bootstrap(
        directories=directories,
        update_source=update_source,
        user_home=user_home,
        logger=logger,
    )
    services = wire_services(
        WiringConfig(
            adapters=result.adapters,
            stores=result.stores,
            runtime=RuntimeBundle(
                loop=loop,
                logger=logger,
                emit=emit,
                clock=result.runtime_adapters.clock,
                uuid_gen=result.runtime_adapters.uuid_gen,
                sleeper=result.runtime_adapters.sleeper,
                hostname_provider=result.runtime_adapters.hostname_provider,
                machine_id_provider=result.runtime_adapters.machine_id_provider,
                steam=steam,
            ),
            callbacks=result.callbacks,
            min_required_version=MIN_ROMM_VERSION,
            directories=directories,
            launcher=result.launcher,
            update_source=update_source,
            installer_environment=installer_environment,
        )
    )
    return Application(services, logger=logger, loop=loop, user_agent=result.user_agent)
