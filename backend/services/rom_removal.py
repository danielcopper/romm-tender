"""RomRemovalService — installed-ROM file deletion and ``rom_installs`` cleanup.

Physically deletes a ROM's files from disk and drops its ``rom_installs``
record. Per [ADR-0007](docs/adr/0007-rom-retention-identity-anchor.md) an
uninstall is *not* a purge: the ``roms`` identity row, playtime, saves, and
metadata all survive — only the on-disk files and the install record go.
Forgetting a download whose files are already gone is the same uninstall
without the deletion.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from models.prune import InstalledContentRemoval

from lib.errors import NotInstalled, Refused
from lib.partial_failure import PartialFailure
from lib.path_safety import is_safe_rom_path

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator, Callable

    from models.prune import MutationOutcome, SourceClaim

    from domain.rom_install import RomInstall
    from services.protocols import (
        Clock,
        ConflictRules,
        DownloadQueueCleanup,
        EventEmitter,
        RetroDeckPaths,
        RomFileStore,
        UnitOfWorkFactory,
    )

# Seconds between ``uninstall_progress`` frames while a multi-file removal runs.
# The terminal frame is never throttled.
_PROGRESS_INTERVAL_S = 0.5


@dataclass(frozen=True)
class UninstallIncomplete(PartialFailure):
    """A bulk uninstall that removed only part of the installed ROMs."""

    removed_count: int
    errors: list[dict[str, str]]
    app_ids: list[int]
    prune_lease_token: str | None


@dataclass(frozen=True)
class RomRemovalServiceConfig:
    """Frozen wiring bundle handed to ``RomRemovalService.__init__``.

    Holds the runtime infrastructure, the Protocol-typed filesystem
    adapter, the RetroDECK paths bundle, the ``DownloadQueueCleanup``
    eviction seam (``None`` when no download cleanup is wired), the
    SQLite Unit-of-Work factory (the transactional seam over the
    ``rom_installs`` repository), and the ``ConflictRules`` a use case an
    endpoint calls checks at its entry and takes its lease through.
    Decomposes the ctor so a new dependency does not push past the S107
    parameter-count limit.
    """

    logger: logging.Logger
    loop: asyncio.AbstractEventLoop
    clock: Clock
    emit: EventEmitter
    rom_file_store: RomFileStore
    retrodeck_paths: RetroDeckPaths
    download_queue_cleanup: DownloadQueueCleanup | None
    uow_factory: UnitOfWorkFactory
    conflict_rules: ConflictRules


class RomRemovalService:
    """Handles physical deletion of installed ROM files and ``rom_installs`` cleanup.

    A use case an endpoint calls checks its conflict rules at its entry, under
    that endpoint's name, and raises the rule's refusal when one holds; a
    peer service that calls one calls its ``<verb>_unchecked`` twin instead
    (GLOSSARY.md → Conflict rules).
    """

    def __init__(
        self,
        *,
        config: RomRemovalServiceConfig,
    ):
        self._logger = config.logger
        self._loop = config.loop
        self._clock = config.clock
        self._emit = config.emit
        self._rom_file_store = config.rom_file_store
        self._retrodeck_paths = config.retrodeck_paths
        self._download_queue_cleanup = config.download_queue_cleanup
        self._uow_factory = config.uow_factory
        self._rules = config.conflict_rules
        # Read and written only on the loop thread — every mutation brackets a
        # ``run_in_executor`` call, or runs in that call's completion callback,
        # rather than happening inside one — so both removal entry points share
        # it without a lock, which `services/` may not import anyway
        # (`.importlinter`, no-stdlib-io-in-services).
        self._removals_in_flight: set[int] = set()

    def _delete_rom_files(
        self,
        install: RomInstall,
        claims: dict[str, SourceClaim] | None = None,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> MutationOutcome:
        """Delete ROM files for an install record. Handles both single-file and multi-file ROMs.

        A multi-file ROM owns a dedicated per-ROM directory (``rom_dir`` is set)
        and is removed whole. A single-file ROM has no ``rom_dir`` (``None``) —
        it lives as a bare file in the shared ``<roms_base>/<system>/`` dir,
        which must **never** be removed — so only the launch file itself is
        deleted. ``is_safe_rom_path`` stays the path-containment guard before
        any removal.

        *claims* is the claim map a cleanup run that sealed a recovery bundle
        hands in; its absence marks a caller that has no bundle at all and
        therefore seals its own claim — see :meth:`_remove_under_claim`.
        """
        rom_dir = install.rom_dir
        file_path = install.file_path

        roms_base = self._retrodeck_paths.roms_path()
        if rom_dir:
            if not is_safe_rom_path(rom_dir, roms_base):
                raise ValueError(f"Refusing to delete path outside roms directory: {rom_dir}")
            if self._rom_file_store.exists(rom_dir) and not self._rom_file_store.is_dir(rom_dir):
                raise ValueError(f"Expected installed ROM directory, found another file type: {rom_dir}")
            return self._remove_under_claim(rom_dir, roms_base, claims, on_progress)
        if file_path:
            if not is_safe_rom_path(file_path, roms_base):
                raise ValueError(f"Refusing to delete path outside roms directory: {file_path}")
            if self._rom_file_store.is_dir(file_path):
                raise ValueError(f"Expected installed ROM file, found a directory: {file_path}")
            return self._remove_under_claim(file_path, roms_base, claims, on_progress)
        return {"success": True, "changed": False, "ambiguous": False, "message": "No installed path recorded"}

    def _remove_under_claim(
        self,
        path: str,
        roms_base: str,
        claims: dict[str, SourceClaim] | None,
        on_progress: Callable[[int, int], None] | None,
    ) -> MutationOutcome:
        """Remove one already-guarded path under the claim that authorizes it.

        A run that sealed a recovery bundle hands its claim map in, so a source
        it captured is removed under what the bundle proved, and one it did not
        capture takes a fresh content-bound claim here — the bundle exists, so
        the hashes still have a copy to bind to. A caller that hands in no map
        has no bundle anywhere: the claim it seals here is consumed a moment
        later by this same call, with nothing else holding the bytes, so it is
        sealed identity-only.

        That last case is also the only one allowed to adopt the debris of an
        attempt interrupted between the staging rename and the last unlink. It
        holds the install record proving the path is this ROM's, and its claim
        discipline is one it can simply re-seal, where a bundle-backed removal's
        authority came from a seal that a partially consumed source no longer
        matches.
        """
        self_sealed = claims is None
        claim = claims.get(path) if claims is not None else None
        if claim is None:
            claim = self._rom_file_store.claim_source(path, roms_base, digest=not self_sealed)
            if self_sealed and not claim["source_identity"]["exists"]:
                reclaimed = self._rom_file_store.reclaim_staged_source(path, roms_base)
                if reclaimed["changed"] or not reclaimed["success"]:
                    return reclaimed
        return self._rom_file_store.remove_claimed(path, roms_base, claim, on_progress)

    def _make_progress_callback(self, rom_id: int) -> Callable[[int, int], None]:
        """Build a throttled per-file removal callback for one uninstall.

        Single-file ROMs report nothing: a one-entry progress bar is noise. The
        callback runs on the ``run_in_executor`` worker, so the emit is marshaled
        to the loop thread.
        """
        last_emit = [0.0]

        def on_progress(removed: int, total: int) -> None:
            if total <= 1:
                return
            now = self._clock.monotonic()
            if now - last_emit[0] < _PROGRESS_INTERVAL_S and removed < total:
                return
            last_emit[0] = now
            self._loop.call_soon_threadsafe(self._publish_removal_progress, rom_id, removed, total)

        return on_progress

    def _publish_removal_progress(self, rom_id: int, removed: int, total: int) -> None:
        """Schedule one ``uninstall_progress`` emit. Runs on the loop thread."""
        self._loop.create_task(
            self._emit(
                "uninstall_progress",
                {"rom_id": rom_id, "files_removed": removed, "files_total": total},
            )
        )

    def _elapsed(self, started: float) -> str:
        """Render the time since *started* for a log line."""
        return f"{self._clock.monotonic() - started:.1f}s"

    def delete_rom_files(self, rom_id: int, claims: dict[str, SourceClaim] | None = None) -> InstalledContentRemoval:
        """Delete only installed content, leaving every database row untouched."""
        with self._uow_factory() as uow:
            install = uow.rom_installs.get(int(rom_id))
        if install is None:
            return InstalledContentRemoval(changed=False, ambiguous=False)
        try:
            outcome = self._delete_rom_files(install, claims)
        except Exception as exc:
            self._logger.error(f"Failed to delete ROM files: {exc}")
            return InstalledContentRemoval(changed=False, ambiguous=True, failure=str(exc))
        return InstalledContentRemoval(
            changed=outcome["changed"],
            ambiguous=outcome["ambiguous"],
            failure=None if outcome["success"] else outcome["message"],
        )

    def _drop_install_record(self, rom_id: int) -> None:
        """Delete the ``rom_installs`` row in a short write UoW, recording the uninstalled launch command.

        A bound ROM has its recorded ``applied_launch_options`` reset to the
        uninstalled placeholder (``""``) in the same UoW: the frontend resets the
        kept shortcut's launch command to ``""`` after an uninstall or a forget
        (#1146), so recording ``""`` keeps the next sync from re-touching an
        already-correct shortcut (delta apply, #1383). Fourth of the six
        recorded-state writer sites, shared by both.
        """
        with self._uow_factory() as uow:
            uow.rom_installs.delete(rom_id)
            rom = uow.roms.get(rom_id)
            if rom is not None and rom.shortcut_app_id is not None:
                rom.record_applied_launch_options("")
                uow.roms.set_applied_launch_options(rom_id, rom.applied_launch_options)

    def _remove_rom_io(self, rom_id: int, install: RomInstall) -> None:
        """Sync helper for remove_rom — file deletion (outside UoW), then the install record dropped.

        Files are deleted outside any transaction (ADR-0006); only the
        ``rom_installs`` row delete is wrapped. Per ADR-0007 the ``roms`` row,
        playtime, saves, and metadata are left untouched — an uninstall drops
        only the files and the install record.
        """
        outcome = self._delete_rom_files(install, on_progress=self._make_progress_callback(rom_id))
        if not outcome["success"]:
            raise RuntimeError(outcome["message"])
        self._drop_install_record(rom_id)

    def _forget_download_io(self, rom_id: int, install: RomInstall) -> str | None:
        """Sync helper for forget_download — the uninstall's record drop without its file deletion.

        Answers the recorded folder or file it finds on disk instead, dropping
        nothing: forgetting it would leave content on disk that no record
        accounts for, which only an uninstall may remove. ``None`` once the
        record is dropped.
        """
        for path in (install.rom_dir, install.file_path):
            if path and self._rom_file_store.exists(path):
                return path
        self._drop_install_record(rom_id)
        return None

    async def remove_rom(self, rom_id: int | str) -> dict[str, Any]:
        """Remove a single installed ROM for the ``remove_rom`` endpoint.

        :meth:`remove_rom_unchecked` under the endpoint's conflict rules. A
        removal that succeeded carries a ``rom_uninstall`` lease in
        ``prune_lease_token`` for the frontend's Steam writes.
        """
        async with self._rules.hold("remove_rom", update=True, migration=True, prune=True):
            result = await self.remove_rom_unchecked(rom_id)
            result["prune_lease_token"] = await self._rules.acquire_lease("rom_uninstall")
            return result

    async def remove_rom_unchecked(self, rom_id: int | str) -> dict[str, Any]:
        """Remove a single installed ROM: delete files and drop the install record.

        :meth:`remove_rom` without its conflict rules or its lease, for the
        download service's sibling supersede, which a download or an adoption
        runs from inside its own call once that call has answered for its own
        rules. Every caller calls it inside a ``hold(..., prune=True)`` block,
        which a cancelled call's operation outlives (:meth:`_removal_claim`).

        Raises :class:`NotInstalled` for a ROM with nothing installed, and
        ``uninstall_failed`` for a removal that failed the ways a removal can —
        an ``OSError`` from the file store, a path the guards refuse
        (``ValueError``), or a removal the store reported as failed
        (``RuntimeError``); anything else it raises is a bug.
        """
        rom_id_int = int(rom_id)
        install = self._admit_removal(rom_id_int, "This ROM is already being uninstalled")
        removal = self._loop.run_in_executor(None, self._remove_rom_io, rom_id_int, install)
        async with self._removal_claim(rom_id_int, "Uninstall", "remove_rom", removal) as started:
            try:
                await asyncio.shield(removal)
            except (OSError, ValueError, RuntimeError) as e:
                self._logger.error(f"Failed to delete ROM files after {self._elapsed(started)}: {e}")
                raise Refused("uninstall_failed", "Failed to delete ROM files") from e
        self._complete_removal(rom_id_int, "Uninstall", started)
        return {"success": True, "message": "ROM removed"}

    async def forget_download(self, rom_id: int | str) -> dict[str, Any]:
        """Forget a download whose files are gone, for the ``forget_download`` endpoint.

        An uninstall without the file deletion: the same conflict rules as
        ``remove_rom``, the same claim on the ROM, the install record dropped
        through the same writer, and on success the same ``rom_uninstall`` lease
        for the frontend's reset of the shortcut's launch command. Refused with
        ``file_present``, naming the ``path`` found, while the recorded folder
        or file exists.
        """
        async with self._rules.hold("forget_download", update=True, migration=True, prune=True):
            result = await self._forget_download(int(rom_id))
            if result.get("success"):
                result["prune_lease_token"] = await self._rules.acquire_lease("rom_uninstall")
            return result

    async def _forget_download(self, rom_id: int) -> dict[str, Any]:
        install = self._admit_removal(rom_id, "This ROM is already being uninstalled or forgotten")
        forget = self._loop.run_in_executor(None, self._forget_download_io, rom_id, install)
        async with self._removal_claim(rom_id, "Forget download", "forget_download", forget) as started:
            try:
                present = await asyncio.shield(forget)
            except Exception as e:
                self._logger.error(f"Failed to forget the download after {self._elapsed(started)}: {e}")
                return {"success": False, "reason": "unknown", "message": "Failed to forget the download"}
        if present is not None:
            self._logger.info(f"Forget download refused: rom_id={rom_id}: {present} exists")
            return {
                "success": False,
                "reason": "file_present",
                "message": f"The recorded download exists: {present}",
                "path": present,
            }
        self._complete_removal(rom_id, "Forget download", started)
        return {"success": True, "message": "Download forgotten"}

    def _admit_removal(self, rom_id: int, in_progress_message: str) -> RomInstall:
        """Read the ROM's install record, or refuse to start a removal on it.

        Raises :class:`NotInstalled` for a ROM with nothing installed. Refused
        with ``in_progress`` while any removal holds this ROM's claim — an
        uninstall, a forget, or a bulk uninstall, which claims every ROM it is
        about to remove. A running uninstall has renamed its source to a staging
        name, so a second uninstall would report the source as vanished, and a
        forget would find the file gone and drop the record the uninstall is
        still working under. A forget renames nothing; it takes the same claim
        so that one removal of a ROM at a time holds without an exception.
        """
        with self._uow_factory() as uow:
            install = uow.rom_installs.get(rom_id)
        if install is None:
            raise NotInstalled("ROM not installed")
        if rom_id in self._removals_in_flight:
            raise Refused("in_progress", in_progress_message)
        return install

    @contextlib.asynccontextmanager
    async def _removal_claim(
        self, rom_id: int, label: str, operation: str, work: asyncio.Future[Any]
    ) -> AsyncIterator[float]:
        """Hold *rom_id*'s removal claim while *work* runs, logging its start; yields the start time.

        The block awaits *work* through ``asyncio.shield``. The claim is given
        back once *work* has ended — a success, a refusal or a failure — so the
        next removal of this ROM is admitted. A call cancelled before *work*
        ended keeps the claim, and an operation named *operation* on the prune
        conflicts, until it does: the worker thread may still be deleting files
        or dropping the record.
        """
        self._removals_in_flight.add(rom_id)
        self._logger.info(f"{label} started: rom_id={rom_id}")
        try:
            yield self._clock.monotonic()
        except asyncio.CancelledError:
            if not work.done():
                await self._outlive_the_call(work, operation, partial(self._end_cancelled_removal, rom_id, label))
            raise
        finally:
            if work.done():
                self._removals_in_flight.discard(rom_id)

    def _complete_removal(self, rom_id: int, label: str, started: float) -> None:
        """Log a finished removal and drop the ROM's download-queue entry."""
        self._logger.info(f"{label} completed: rom_id={rom_id} in {self._elapsed(started)}")
        if self._download_queue_cleanup is not None:
            self._download_queue_cleanup.evict(rom_id)

    async def _outlive_the_call(
        self, work: asyncio.Future[Any], label: str, on_end: Callable[[asyncio.Future[Any]], None]
    ) -> None:
        """Keep a cancelled call's removal claimed until *work* ends, under an operation named *label*.

        *on_end* gives the claim back when the thread ends. The operation is
        retained from inside the call's ``hold(..., prune=True)`` block, so no
        cleanup starts between the call's end and the thread's.
        """
        work.add_done_callback(on_end)
        await self._rules.retain(self._loop.create_task(asyncio.wait([work])), label)

    def _end_cancelled_removal(self, rom_id: int, label: str, removal: asyncio.Future[Any]) -> None:
        """Give back the claim of a removal whose call was cancelled, and log how its thread ended."""
        self._removals_in_flight.discard(rom_id)
        failure = None if removal.cancelled() else removal.exception()
        if failure is not None:
            self._logger.error(f"{label} of rom_id={rom_id} ended after its call was cancelled: {failure}")
        else:
            self._logger.info(f"{label} of rom_id={rom_id} ended after its call was cancelled")

    def _end_cancelled_bulk_run(
        self, claimed: set[int], run: asyncio.Future[tuple[int, list[dict[str, str]], list[int]]]
    ) -> None:
        """Give back the claims of a bulk run whose call was cancelled, and log how its thread ended."""
        self._removals_in_flight -= claimed
        if run.cancelled():
            return
        failure = run.exception()
        if failure is not None:
            self._logger.error(f"Bulk uninstall ended after its call was cancelled: {failure}")
            return
        count, errors, _app_ids = run.result()
        self._logger.info(f"Bulk uninstall ended after its call was cancelled: {count} removed, {len(errors)} failed")

    def _uninstall_all_roms_io(self, installs: list[RomInstall]) -> tuple[int, list[dict[str, str]], list[int]]:
        """Sync helper for uninstall_all_roms — bulk file deletion (outside UoW) then row deletes in a write UoW.

        Deletes the files of every already-claimed install outside any
        transaction (collecting per-ROM errors), then drops the install rows for
        the ROMs whose files were deleted in one write UoW. Per ADR-0007 the
        ``roms`` rows, playtime, saves, and metadata survive.

        *installs* is read and claimed by the caller on the loop thread rather
        than here, so the set of removals in flight has a single writer.

        Also returns the bound ``shortcut_app_id`` of each ROM whose files were
        deleted (unbound rows contribute none), so the frontend can reset those
        kept shortcuts' now-stale ``launch_options`` to the uninstalled
        placeholder (#1146).
        """
        count = 0
        errors: list[dict[str, str]] = []
        successfully_deleted: list[int] = []
        for install in installs:
            try:
                outcome = self._delete_rom_files(install)
                if not outcome["success"]:
                    raise RuntimeError(outcome["message"])
                count += 1
                successfully_deleted.append(install.rom_id)
            except Exception as e:
                errors.append({"rom_id": str(install.rom_id), "error": str(e)})
                self._logger.error(f"Failed to delete ROM {install.rom_id}: {e}")

        app_ids: list[int] = []
        with self._uow_factory() as uow:
            for rom_id in successfully_deleted:
                uow.rom_installs.delete(rom_id)
                rom = uow.roms.get(rom_id)
                if rom is not None and rom.shortcut_app_id is not None:
                    app_ids.append(rom.shortcut_app_id)
                    # The frontend resets each kept shortcut's launch command to ""
                    # (#1146); record that so the next sync skips it (delta apply,
                    # #1383). The uninstall writer site, bulk: what
                    # ``_drop_install_record`` does for one ROM, in one UoW for all.
                    rom.record_applied_launch_options("")
                    uow.roms.set_applied_launch_options(rom_id, rom.applied_launch_options)
        return count, errors, app_ids

    async def uninstall_all_roms(self) -> dict[str, Any] | UninstallIncomplete:
        """Remove all installed ROMs: delete files and drop their install records.

        A run in which every per-ROM deletion succeeded answers ``success: True``,
        ``removed_count`` (number of ROMs whose files were deleted), ``errors``
        (empty) and ``app_ids`` (the bound Steam ``shortcut_app_id`` of each ROM
        whose files were deleted), so the frontend can reset those kept
        shortcuts' now-stale ``launch_options`` to the uninstalled placeholder.
        A run in which any deletion failed answers :class:`UninstallIncomplete`
        with the same ``removed_count``, ``errors`` and ``app_ids``, ``errors``
        holding one ``{"rom_id", "error"}`` entry per failed deletion. Install
        records for the failing entries are left intact so the user can retry.

        Claims every ROM it is about to remove before dispatching the worker, so
        a single uninstall of any of them is refused while this runs and this is
        refused while any of them is already being removed — one bulk run and
        one single removal must never work the same tree. A call cancelled
        while the files are being deleted keeps those claims, and an operation
        on the prune conflicts, until the deletion ends. The refusal carries no
        removal payload, which is how the frontend already tells a refusal from
        a partial failure.

        A removal that ran and reports bound ``app_ids`` carries a
        ``bulk_uninstall`` lease in ``prune_lease_token`` for the frontend's
        launch-options reset, a partial failure included: those ROMs' files are
        gone whether or not every other deletion succeeded. A refusal, this
        method's own included, carries neither ``app_ids`` nor a lease.
        """
        async with self._rules.hold("uninstall_all_roms", update=True, migration=True, sync=True, prune=True):
            count, errors, app_ids = await self._uninstall_all_roms()
            lease = await self._rules.acquire_lease("bulk_uninstall") if app_ids else None
            if errors:
                noun = "ROM" if len(errors) == 1 else "ROMs"
                return UninstallIncomplete(
                    reason="uninstall_incomplete",
                    message=f"{len(errors)} {noun} could not be uninstalled",
                    removed_count=count,
                    errors=errors,
                    app_ids=app_ids,
                    prune_lease_token=lease,
                )
            result: dict[str, Any] = {"success": True, "removed_count": count, "errors": errors, "app_ids": app_ids}
            if lease is not None:
                result["prune_lease_token"] = lease
            return result

    async def _uninstall_all_roms(self) -> tuple[int, list[dict[str, str]], list[int]]:
        with self._uow_factory() as uow:
            installs = list(uow.rom_installs.iter_all())
        claimed = {install.rom_id for install in installs}
        if claimed & self._removals_in_flight:
            raise Refused("in_progress", "A ROM is already being uninstalled")

        run = self._loop.run_in_executor(None, self._uninstall_all_roms_io, installs)
        self._removals_in_flight |= claimed
        started = self._clock.monotonic()
        self._logger.info("Bulk uninstall started")
        try:
            count, errors, app_ids = await asyncio.shield(run)
        except asyncio.CancelledError:
            if not run.done():
                await self._outlive_the_call(run, "uninstall_all_roms", partial(self._end_cancelled_bulk_run, claimed))
            raise
        finally:
            if run.done():
                self._removals_in_flight -= claimed
        self._logger.info(
            f"Bulk uninstall completed: {count} removed, {len(errors)} failed in {self._elapsed(started)}"
        )
        if self._download_queue_cleanup is not None:
            self._download_queue_cleanup.clear()
        return count, errors, app_ids
