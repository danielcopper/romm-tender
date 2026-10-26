"""DownloadService — the façade the endpoints and the other services call for ROM downloads.

What happens before a download's task starts belongs here — its admission,
the occupancy gate, the sibling supersede and the disk-space pre-flight — and
so does the control of a running or paused download (cancel, pause, resume),
the installed-ROM read and the shutdown. The package's other parts are named
in its ``__init__``. Raw filesystem I/O flows through the ``DownloadFileStore``
Protocol; HTTP traffic flows through ``RommRomReader``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.disk_space import disk_space_verdict
from domain.emulator_sources import RETRODECK
from domain.retrodeck_folders import GAME_DOWNLOAD, FolderRefused
from domain.rom_candidates import DIR, FILE
from domain.rom_file import NO_FILE_ON_ROMM_MESSAGE, NoFileOnRomm, has_file_on_romm
from domain.rom_files import is_multi_file_download
from domain.served_shape import listed_unpacked_bytes, served_shapes
from domain.sibling_supersede import supersede_siblings
from lib.background_tasks import BackgroundTasks
from lib.errors import NotInstalled, Refused
from services.downloads._names import partial_tmp_size, safe_extract_dir_name, safe_local_file_name
from services.downloads._state import DownloadControl

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.platform_system import PlatformSystem
    from lib.partial_failure import PartialFailure
    from models.state import InstalledRomEntry
    from services.downloads._state import DownloadsStateBox
    from services.downloads.queue import DownloadQueue
    from services.downloads.transfer import DownloadTransfer
    from services.downloads.unpack import DownloadUnpacker
    from services.protocols import (
        ConflictRules,
        DownloadFileStore,
        DownloadTargetGateFn,
        PlatformSystems,
        RetroDeckFolders,
        RommRomReader,
        RomRemoverProvider,
        UnitOfWorkFactory,
    )

# One wording for both blocks that refuse a disk that cannot be read or prepared:
# the user reads the same sentence whichever step raised.
_START_FAILED_MESSAGE = "Failed to start download"


@dataclass(frozen=True)
class DownloadServiceConfig:
    """Frozen wiring bundle handed to ``DownloadService.__init__``.

    ``state``, ``queue``, ``transfer`` and ``unpacker`` are the package's
    parts, built by the composition root: an admitted download's task is
    ``transfer.run``, and the unpacker answers whether a
    whole download already sits on disk and removes a paused one's partial.
    ``target_gate`` is the pre-flight that refuses to write over content Tender
    did not put there (ADR-0028). ``conflict_rules`` are what a start or resume
    checks at its entry, and what a download's task is held through.
    """

    state: DownloadsStateBox
    queue: DownloadQueue
    transfer: DownloadTransfer
    unpacker: DownloadUnpacker
    romm_api: RommRomReader
    download_file_store: DownloadFileStore
    platform_systems: PlatformSystems
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    retrodeck_folders: RetroDeckFolders
    target_gate: DownloadTargetGateFn
    uow_factory: UnitOfWorkFactory
    # Deferred access to RomRemovalService.remove_rom_unchecked for the sibling
    # supersede — the two services form a construction cycle, so the composition
    # root binds it after both exist.
    rom_remover: RomRemoverProvider
    conflict_rules: ConflictRules


class DownloadService:
    """The download package's one surface: admission, control, the installed-ROM read and the shutdown.

    The queue's reads and clears pass through to :class:`DownloadQueue`. A start
    and a resume check their endpoint's conflict rules at their entry, under
    that endpoint's name, and raise the rule's refusal when one holds
    (GLOSSARY.md → Conflict rules).
    """

    def __init__(self, *, config: DownloadServiceConfig) -> None:
        self._state = config.state
        self._queue = config.queue
        self._transfer = config.transfer
        self._unpacker = config.unpacker
        self._romm_api = config.romm_api
        self._download_file_store = config.download_file_store
        self._platform_systems = config.platform_systems
        self._loop = config.loop
        self._logger = config.logger
        self._retrodeck_folders = config.retrodeck_folders
        self._target_gate = config.target_gate
        self._uow_factory = config.uow_factory
        self._rom_remover = config.rom_remover
        self._rules = config.conflict_rules
        self._tasks = BackgroundTasks(logger=config.logger)

    async def shutdown(self) -> None:
        """Cancel the per-ROM download tasks and the progress frames still running, and wait until they have ended.

        A download cancelled while its install is being committed waits for the
        commit's thread before it ends, so this waits for it too. The queue's
        frames go after the downloads: while a cancelled download waits for its
        unpack's thread, that thread's progress ticks still start frames.
        """
        await self._tasks.shutdown()
        await self._queue.shutdown()
        self._state.forget_tasks()

    async def start_download(
        self,
        rom_id,
        replace_existing=False,
        candidate_path=None,
        collision_choice=None,
        page_saw_candidate=False,
        confirmed_paths=None,
    ):
        """Start a download, refusing when this game is already on disk.

        The user's answers to that refusal and the game page's own report ride on
        this use case rather than a second one, so every download — first attempt
        or replace — passes the same prologue: the ``already_downloading`` guard,
        the path-safety coercion, the occupancy gate, the supersede, the disk
        pre-flight. ``DownloadTargetGateFn`` owns what each of them means.

        Checked against the ``start_download`` endpoint's conflict rules first.
        A started download's task holds an operation until it ends.
        """
        async with self._rules.hold("start_download", update=True, migration=True, prune=True):
            rom_id = int(rom_id)
            if rom_id in self._state.in_progress:
                raise Refused("already_downloading", "Already downloading")
            # The page's report rides with the user's answers because the gate reads
            # them together, but it is added apart from them to keep the difference
            # visible: those above are choices the user made, this one is not.
            answer = {
                "candidate_path": candidate_path,
                "collision_choice": collision_choice,
                "confirmed_paths": confirmed_paths,
            }
            answer["page_saw_candidate"] = page_saw_candidate
            result = await self._begin_download(rom_id, resume=False, replace_existing=bool(replace_existing), **answer)
            await self._hold_until_started_task_done(result, rom_id, "start_download")
            return result

    async def supersede_sibling_installs(self, rom_id: int, *, adopted_path: str | None = None) -> None:
        """Strip any other installed version of ``rom_id``'s sibling group (#1298 T7).

        The single home of the supersede — ``_start_claimed_download`` once its occupancy
        gate has passed (a refusal must not already have deleted another version),
        and adoption through ``SiblingSupersedeFn`` for the same reason: an adopted
        install is an install (ADR-0028). Neither caller may copy the selection
        rule (``domain/sibling_supersede.py``). An adoption passes its target as
        *adopted_path*, and a sibling recorded there hands its record over instead
        of being removed. The group's members are snapshotted in one short UoW,
        closed before the removal seam runs — the removal opens its own UoW, which
        must not nest (ADR-0006). Each superseded install is removed through the
        canonical ``RomRemovalService.remove_rom_unchecked`` (files + ``rom_installs``
        row; saves untouched per ADR-0007) rather than duplicating its deletion logic.
        Every attempt is logged with both rom ids so a failure is attributable. A
        removal refused with :class:`NotInstalled` raced clean and is skipped; any other
        refusal propagates so the caller aborts with it. A superseded sibling's *paused*
        queue entry is evicted so the queue stays coherent with disk.
        """
        sibling_ids = self._conflicting_sibling_install_ids(rom_id, adopted_path)
        if not sibling_ids:
            return
        remove_rom = self._rom_remover()
        for sibling_id in sibling_ids:
            try:
                await remove_rom(sibling_id)
            except NotInstalled:
                continue  # raced clean — nothing to supersede
            except (Refused, FolderRefused) as refused:
                self._logger.error(
                    f"Superseding install of rom {sibling_id} (group of rom {rom_id}) failed: {refused.message}"
                )
                raise
            self._logger.info(f"Superseding install of rom {sibling_id} (group of rom {rom_id})")
            self._queue.evict_if_paused(sibling_id)

    def _conflicting_sibling_install_ids(self, rom_id: int, adopted_path: str | None = None) -> list[int]:
        """Sibling members with a ``rom_installs`` row that this install still has to remove.

        Read in one short UoW (closed before the removal seam runs); which member
        is displaced, and how, is ``supersede_siblings``'s answer. A member it
        hands over — one recorded at *adopted_path* — loses its ``rom_installs``
        row in that same UoW and is not listed, so its files are never removed;
        once the UoW has closed, each hand-over is logged with both rom ids and
        the path, and the member's paused queue entry is evicted
        (``DownloadQueue.evict_if_paused``). A ROM with no sibling group key
        (solo / unbackfilled) has no siblings, so the list is empty.
        """
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None or rom.sibling_group_key is None:
                return []
            members = uow.roms.iter_by_group_key(rom.sibling_group_key)
            siblings = [(m, uow.rom_installs.get(m.rom_id)) for m in members if m.rom_id != rom_id]
            displaced = supersede_siblings(rom.shortcut_app_id, siblings, adopted_path)
            for sibling_id in displaced.handed_over:
                uow.rom_installs.delete(sibling_id)
        for sibling_id in displaced.handed_over:
            self._logger.info(f"Handing the install of rom {sibling_id} at {adopted_path} over to rom {rom_id}")
            self._queue.evict_if_paused(sibling_id)
        return list(displaced.removed)

    async def _begin_download(self, rom_id, *, resume: bool, replace_existing=False, **answer):
        """Shared core of ``start_download`` and ``resume_download``.

        *answer* passes the adopt dialog's answers, and what the game page reported, through to the gate unread.
        Fetches ROM detail and refuses one RomM holds no file for (``no_file_on_romm``), resolves the platform path,
        then runs the three pre-flights **in this order**: the occupancy gate, the #1298 sibling supersede, the
        disk-space check. Only then is the download admitted — its entry into the queue, and its task, byte reservation
        and control token into the state box. On ``resume=True`` the disk pre-flight discounts the bytes already on the
        existing ``.tmp`` and the transfer appends rather than restarts. The ``already_downloading`` guard stays with
        ``start_download``; ``resume_download`` validates the paused entry before calling here.

        Holds the ROM's in-progress claim from the first step and gives it back on every way out but a started
        download, a cancelled start included, so the ROM is never stuck "Already downloading". A partial failure the
        gate returns is answered as it came, with the claim given back. Of what the steps raise, an ``OSError`` (a
        disk that cannot be read or prepared) is refused here with ``download_start_failed``; a RomM error and a
        refusal pass on unchanged (which: ``docs/architecture/backend-architecture.md``), and anything else is a bug.
        """
        self._state.claim(rom_id)
        try:
            return await self._start_claimed_download(
                rom_id, resume=resume, replace_existing=replace_existing, **answer
            )
        except BaseException:
            self._state.drop_claim(rom_id)
            raise

    def _download_platform_io(self, platform_slug: str) -> PlatformSystem:
        """The platform's system in RetroDECK, which a download lands in. Blocking: it may read RomM's listing."""
        return self._platform_systems.platform_system(platform_slug, source=RETRODECK)

    async def _start_claimed_download(self, rom_id, *, resume: bool, replace_existing: bool, **answer):
        """:meth:`_begin_download` once the ROM's in-progress claim is held."""
        rom_detail = await self._loop.run_in_executor(None, self._romm_api.get_rom, rom_id)
        # The live answer decides, never the stored column: a rescan since the
        # last sync may have brought the file back or taken it away.
        if not has_file_on_romm(rom_detail):
            raise NoFileOnRomm(NO_FILE_ON_ROMM_MESSAGE)

        platform_slug = rom_detail.get("platform_slug", "")
        platform = await self._loop.run_in_executor(None, self._download_platform_io, platform_slug)
        system = platform.taken
        if system is None:
            raise platform.refusal(GAME_DOWNLOAD)

        # The folder is taken once, here: a download that started lands where it
        # started even if RetroDECK is switched off before it ends.
        folder = self._retrodeck_folders.download_folder(system)
        if isinstance(folder, FolderRefused):
            raise folder
        roms_dir = folder
        try:
            file_name = safe_local_file_name(rom_detail, self._logger)
            file_size = rom_detail.get("fs_size_bytes", 0)
            target_path = os.path.join(roms_dir, file_name)

            # Refuse before a single byte moves when this game is already on disk
            # — at the path this download would claim, or beside it under another
            # name — and let the user decide what happens to it: adopt, replace,
            # or (where nothing can be adopted) accept a second copy (ADR-0028).
            # A multi-file ROM claims its extract directory, not the archive name,
            # and a single zip that may be unpacked claims both.
            shapes = served_shapes(rom_detail)
            folder_path = None
            if DIR in shapes:
                folder_path = os.path.join(roms_dir, safe_extract_dir_name(rom_detail, self._logger))
            checked_path = target_path if FILE in shapes or folder_path is None else folder_path
            gated = await self._target_gate(
                rom_detail,
                checked_path,
                replace=replace_existing,
                resume=resume,
                folder_path=folder_path if FILE in shapes else None,
                **answer,
            )
        except OSError as e:
            self._logger.error(f"Failed to prepare download for ROM {rom_id}: {e}")
            raise Refused("download_start_failed", _START_FAILED_MESSAGE) from e
        if gated is not None:
            self._state.drop_claim(rom_id)
            return gated

        # At most one downloaded version per shortcut binding (#1298): strip a
        # sibling install bound to this shortcut (or unbound) before this one
        # lands — a grandfathered sibling with its own shortcut is exempt. A
        # removal failure aborts the download so the invariant stays honest.
        #
        # Ordering is load-bearing at both ends. AFTER the gate, because the
        # supersede deletes another version's files: refusing afterwards would
        # leave a user who then presses Cancel with one version uninstalled and
        # nothing in its place. BEFORE the disk pre-flight, because the removal
        # frees the space the replacement needs — checking first would reject a
        # same-size swap on a nearly full card. The in-progress claim is already
        # held (B1), so a second start_download during this await is rejected by
        # ``start_download``'s guard rather than racing past it.
        await self.supersede_sibling_installs(rom_id)

        try:
            self._download_file_store.make_dirs(roms_dir)
            # A zip already whole on disk is not fetched again (see
            # ``kept_download_complete``), so only what the unpack still needs is
            # weighed — as for a resume.
            kept = resume or await self._loop.run_in_executor(
                None, self._unpacker.kept_download_complete, target_path, rom_detail
            )
            already_on_disk = partial_tmp_size(self._download_file_store, target_path, rom_detail) if kept else 0
            verdict = disk_space_verdict(
                file_size=file_size,
                free_space=self._download_file_store.disk_free(roms_dir),
                reserved_bytes=self._state.bytes_reserved_by_others(rom_id, self._queue.entries),
                multi_file=is_multi_file_download(rom_detail),
                already_on_disk=already_on_disk,
                unpacked_size=listed_unpacked_bytes(rom_detail),
            )
        except OSError as e:
            self._logger.error(f"Failed to prepare download for ROM {rom_id}: {e}")
            raise Refused("download_start_failed", _START_FAILED_MESSAGE) from e
        if not verdict.fits:
            raise Refused(
                "insufficient_space",
                f"Not enough disk space ({verdict.free_mb}MB free, need {verdict.needed_mb}MB)",
            )

        rom_name = rom_detail.get("name", file_name)
        platform_name = rom_detail.get("platform_name", platform_slug)
        # Carry the prior resumability verdict across a resume so the UI keeps
        # showing Pause before the resumed transfer's headers re-confirm it.
        resumable = bool(self._queue.entries.get(rom_id, {}).get("resumable", False))

        # Create the control token BEFORE the task so the closure captured inside
        # the transfer's progress callback polls this exact object. A later
        # re-download installs a fresh token, leaving the zombie's callback bound
        # to the cancelled one (#144).
        control = DownloadControl()
        task = self._tasks.spawn(
            self._transfer.run(rom_id, rom_detail, target_path, system, file_name, control, resume=resume),
            f"download of rom {rom_id}",
        )

        entry = {
            "rom_id": rom_id,
            "rom_name": rom_name,
            "platform_name": platform_name,
            "file_name": file_name,
            # Honest initial status: the task hasn't acquired the concurrency
            # semaphore yet. The transfer flips this to "downloading" once it
            # enters the critical section (#1053).
            "status": "queued",
            "progress": 0,
            "bytes_downloaded": 0,
            "total_bytes": file_size,
            # Whether the server proved byte-range resume support for this ROM.
            # Re-confirmed live by the ``on_meta`` callback once headers arrive.
            "resumable": resumable,
            # The user's replace answer, carried across a pause so a resumed
            # replace-download is not refused by the very file it is replacing.
            # Kept only where the download writes a file of its own, because only
            # there is the answer still LIVE: a replaced file goes only once its
            # replacement is installed (``os.replace``, or an unpacked zip's own
            # removal), so it is still there and still what the user was shown. A
            # ROM served only as a folder had its directory removed at the start,
            # so the answer is spent — anything at that path now is content the
            # user has never seen, and the gate must ask about it rather than
            # delete it. The paths the user confirmed go with it; which of them a
            # resume still covers is ``replace_covers``'s.
            "_replace_existing": replace_existing and FILE in served_shapes(rom_detail),
            "_confirmed_paths": answer.get("confirmed_paths"),
            # What this download found on disk when it was admitted, which its
            # reservation already left out (``bytes_reserved_by_others``).
            "_on_disk_at_start": already_on_disk,
        }
        # The entry and the registrations go in one synchronous step, the entry
        # first, so nothing sees one without the other. The reservation lets
        # another download's pre-flight see the outstanding claim; the transfer
        # releases it when the task ends.
        self._queue.enqueue(rom_id, entry)
        self._state.admit(rom_id, task=task, reserved=verdict.needed_bytes, control=control)
        return {"success": True, "message": "Download started"}

    def task_for_rom(self, rom_id: int) -> asyncio.Task[None] | None:
        """Return the detached task whose lifetime owns this ROM's install write."""
        return self._state.tasks.get(int(rom_id))

    async def _hold_until_started_task_done(
        self, result: dict[str, Any] | PartialFailure, rom_id: int, label: str
    ) -> None:
        """Hold an operation named *label* for the task a successful start or resume left running.

        Called inside that call's own ``hold``, so no cleanup can start between
        the two operations; the task's ``download_complete`` lease is taken
        while this one still holds.
        """
        task = self.task_for_rom(rom_id) if isinstance(result, dict) else None
        if task is not None:
            await self._rules.hold_until_done(task, label)

    def cancel_download(self, rom_id):
        """Cancel a download, whether it is running or paused.

        A running download has a live task: flip its cooperative-cancel token
        (stops the executor transfer thread, not just the asyncio wrapper — #144)
        and cancel the task; the transfer's terminal handler deletes the
        partial, emits the terminal ``cancelled`` frame, and evicts the entry.

        A **paused** download has NO task — the pause already cancelled it,
        leaving the entry "paused" and the partial ``.tmp`` kept for resume.
        ``_cancel_paused_download`` deletes the partial, emits the same terminal
        ``cancelled`` frame, and evicts the entry.

        A cancel that can act on neither (no task AND no paused entry) is refused
        with ``no_active_download`` so the frontend can surface it.
        """
        rom_id = int(rom_id)
        task = self._state.tasks.get(rom_id)
        if task:
            token = self._state.control_tokens.get(rom_id)
            if token is not None:
                token.cancelled = True  # stop the executor transfer thread, not just the asyncio wrapper (#144)
            task.cancel()
            return {"success": True, "message": "Download cancelled"}
        entry = self._queue.entries.get(rom_id)
        if entry is not None and entry.get("status") == "paused":
            self._cancel_paused_download(rom_id, entry)
            return {"success": True, "message": "Download cancelled"}
        raise Refused("no_active_download", "No active download for this ROM")

    def _cancel_paused_download(self, rom_id: int, entry: dict[str, Any]) -> None:
        """Cancel a paused (task-less) download: delete its partial, notify, evict.

        Does the cleanup the running-cancel terminal branch would have, minus the
        task: removes the partial ``.tmp``/``.zip.tmp`` (via the ``_target_path``
        recorded when the entry paused — skipped if absent, the startup sweep
        still reaps it; a paused transfer is mid-download so it owns no extract
        dir), emits the same terminal ``download_progress`` ``cancelled`` frame
        the running-cancel emits (so the button resets and the store drops the
        row), then evicts the entry — a cancel is an explicit discard that leaves
        no residue.
        """
        target_path = entry.get("_target_path")
        if target_path:
            self._unpacker.remove_partial_tmp_files(target_path)
        self._queue.cancel_paused(rom_id, entry)

    def pause_download(self, rom_id):
        """Pause an in-flight download, keeping the partial ``.tmp`` for resume.

        Mirrors ``cancel_download`` but flips ``control.paused`` instead of
        ``cancelled`` before cancelling the task, so the transfer's terminal
        handler routes to the pause branch (status "paused", no cleanup) rather
        than the cancel branch (status "cancelled", ``.tmp`` deleted). Kept
        defensive — the frontend only offers Pause when a download is resumable.
        """
        rom_id = int(rom_id)
        task = self._state.tasks.get(rom_id)
        if not task:
            raise Refused("no_active_download", "No active download for this ROM")
        token = self._state.control_tokens.get(rom_id)
        if token is not None:
            token.paused = True  # stop the executor transfer thread, keeping the .tmp (#144)
        task.cancel()
        return {"success": True, "message": "Download paused"}

    async def resume_download(self, rom_id):
        """Resume a previously paused download from its partial ``.tmp``.

        Requires a queue entry in status "paused"; otherwise refuses with
        ``not_paused``. A switch may have moved the group's shortcut
        binding to a sibling while this download was paused (#1298 S1): if another
        member now owns the shortcut, this target is stale — the resume is refused
        (``superseded``) and its queue entry dropped rather than re-downloading a
        version the picker has already moved away from. Otherwise the transfer
        appends onto the existing bytes instead of restarting (when the server
        honoured the original ``Range`` probe).

        The occupancy gate runs again, against the **final** path rather than the
        partial ``.tmp``. A single-file replace's final path still holds the file
        the user chose to replace, so the entry's stored answer goes back to the
        gate — without it the resume is refused by the very file it is replacing,
        every time. Anything else faces a free path, or a refusal if content
        appeared there while it sat paused — but never the candidate search,
        which is skipped on a resume so a file the user already declined cannot
        refuse the transfer they started.

        Checked against the ``resume_download`` endpoint's conflict rules first.
        A resumed download's task holds an operation until it ends.
        """
        async with self._rules.hold("resume_download", update=True, migration=True, prune=True):
            rom_id = int(rom_id)
            entry = self._queue.entries.get(rom_id)
            if entry is None or entry.get("status") != "paused":
                raise Refused("not_paused", "No paused download for this ROM")
            if self._resume_target_superseded(rom_id):
                self._queue.evict(rom_id)
                raise Refused("superseded", "Another version is now active")
            replace_existing = bool(entry.get("_replace_existing"))
            result = await self._begin_download(
                rom_id, resume=True, replace_existing=replace_existing, confirmed_paths=entry.get("_confirmed_paths")
            )
            await self._hold_until_started_task_done(result, rom_id, "resume_download")
            return result

    def _resume_target_superseded(self, rom_id: int) -> bool:
        """True when a sibling now owns the group's shortcut, stranding this resume.

        Reads the target's sibling group in one short UoW. A solo ROM (no group
        key), a target that is itself a bound member, or a group with no bound
        member at all is never superseded — those resume normally. Only a group
        whose shortcut binding has moved to a DIFFERENT member (a version switch
        landed while this download was paused) strands this target: resuming it
        would re-create a second installed version the supersede predicate can't
        reach (it protects installs bound to a different app_id — #1298 S1).
        """
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None or rom.sibling_group_key is None:
                return False
            if rom.shortcut_app_id is not None:
                return False  # target is (a) bound member — resume normally
            return any(
                member.rom_id != rom_id and member.shortcut_app_id is not None
                for member in uow.roms.iter_by_group_key(rom.sibling_group_key)
            )

    def get_download_queue(self) -> dict[str, Any]:
        """``DownloadQueue.get_download_queue``."""
        return self._queue.get_download_queue()

    def clear_completed_downloads(self) -> dict[str, Any]:
        """Evict every terminal entry from the queue (``DownloadQueue.clear_completed_downloads``)."""
        return self._queue.clear_completed_downloads()

    def active_download_rom_ids(self) -> set[int]:
        """Snapshot the rom ids with an in-flight or queued download (#1298 F1).

        VersionSwitchService consults this to refuse a switch while any member of
        the target's sibling group is actively downloading (the user cancels
        first). A *paused* download is not in this set — its task's ``finally``
        already released the in-progress claim — so a switch is allowed while a
        sibling is paused, and a later resume of a superseded target is refused by
        ``resume_download`` instead. Returns a copy so the caller cannot mutate the
        live control set.
        """
        return set(self._state.in_progress)

    def get_installed_rom(self, rom_id: int) -> InstalledRomEntry | None:
        """Return the install record for *rom_id* as a frontend-shaped dict, or ``None``.

        Reads the ``RomInstall`` aggregate via the Unit of Work and projects it
        onto the ``InstalledRom`` shape the game page and the launch gate consume.
        ``file_name`` is derived from the launch ``file_path`` (the aggregate
        stores the launch file, not the original archive name).
        """
        with self._uow_factory() as uow:
            install = uow.rom_installs.get(int(rom_id))
        if install is None:
            return None
        entry: InstalledRomEntry = {
            "rom_id": install.rom_id,
            "file_name": os.path.basename(install.file_path),
            "file_path": install.file_path,
            "system": install.system,
            "platform_slug": install.platform_slug,
            "installed_at": install.installed_at,
            "launchable": install.launchable,
        }
        return entry

    # ── DownloadQueueCleanup Protocol ──────────────────────────────

    def evict(self, rom_id: int) -> None:
        """``DownloadQueue.evict``."""
        self._queue.evict(rom_id)

    def clear(self) -> None:
        """``DownloadQueue.clear``."""
        self._queue.clear()
