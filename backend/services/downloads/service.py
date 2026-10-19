"""DownloadService — ROM download orchestration.

Owns every step between a frontend download request and a ROM
landing on disk: disk-space pre-flight, single-file and multi-file
downloads, ZIP extraction, and partial-download cleanup.
Raw filesystem I/O flows through the ``DownloadFileStore`` Protocol;
HTTP traffic flows through ``RommRomReader``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from domain.disk_space import NotEnoughSpaceToUnpack, disk_space_verdict
from domain.download_frames import cancelled_frame, failed_frame
from domain.emulator_sources import RETRODECK
from domain.retrodeck_folders import GAME_DOWNLOAD, FolderRefused
from domain.rom_candidates import DIR, FILE
from domain.rom_file import NO_FILE_ON_ROMM_MESSAGE, NoFileOnRomm, has_file_on_romm
from domain.rom_files import (
    TMP_EXT,
    ZIP_TMP_EXT,
    is_multi_file_download,
    resolve_extract_dir_name,
    resolve_local_file_name,
    synthetic_rom_name,
)
from domain.served_shape import is_zip_name, listed_unpacked_bytes, served_shapes
from domain.sibling_supersede import supersede_siblings
from lib.background_tasks import BackgroundTasks
from lib.errors import NotInstalled, Refused
from lib.path_safety import coerce_safe_component
from services.downloads.unpack import DownloadUnpacker, DownloadUnpackerConfig

if TYPE_CHECKING:
    import logging

    from domain.platform_system import PlatformSystem
    from lib.partial_failure import PartialFailure
    from models.state import InstalledRomEntry
    from services.protocols import (
        Clock,
        ConflictRules,
        DownloadFileStore,
        DownloadTargetGateFn,
        EventEmitter,
        PlatformSystems,
        RetroDeckFolders,
        RomInstallRecorder,
        RommRomReader,
        RomRemoverProvider,
        Sleeper,
        SystemM3uSupportFn,
        UnitOfWorkFactory,
    )

_DOWNLOAD_QUEUE_MAX_TERMINAL = 50
# One wording for both blocks that refuse a disk that cannot be read or prepared:
# the user reads the same sentence whichever step raised.
_START_FAILED_MESSAGE = "Failed to start download"

# A download in one of these statuses has run to a terminal end — it is no
# longer active/queued/paused/extracting. The queue prune trims the oldest of
# these over the cap, and "Clear Completed" evicts all of them (#149). In normal
# flow only completed/failed actually LINGER: a cancel is an explicit discard and
# evicts its entry on the spot (#149 downloads-round), so "cancelled" stays in
# this classification defensively (a stray cancelled row is still prune/clearable)
# and as the transient status the terminal cancel FRAME still carries.
_TERMINAL_DOWNLOAD_STATUSES = ("completed", "failed", "cancelled")

_ProgressPhase = Literal["downloading", "extracting"]


class _DownloadControl:
    """Per-download cooperative-control flags. Set on the event-loop thread by
    ``cancel_download`` / ``pause_download``; polled on the executor worker
    thread by the progress callback, which raises ``CancelledError`` to abort
    the in-flight HTTP transfer when EITHER flag is set (#144).

    ``cancelled`` and ``paused`` differ only in the terminal handling: a cancel
    deletes the partial ``.tmp``; a pause keeps it so the transfer can resume
    from where it stopped. The abort mechanism (raise to unwind the executor
    transfer) is identical.

    Plain bools — not ``threading.Event`` — because the import-linter
    ``no-stdlib-io-in-services`` contract forbids ``threading`` in services, and
    under the GIL a one-way set-once bool flip needs no synchronisation.
    """

    __slots__ = ("cancelled", "paused")

    def __init__(self) -> None:
        self.cancelled = False
        self.paused = False


@dataclass(frozen=True)
class DownloadServiceConfig:
    """Frozen wiring bundle handed to ``DownloadService.__init__``.

    Holds the Protocol-typed adapters, runtime infrastructure, time/sleep
    seams, the SQLite Unit-of-Work factory, and RetroDECK's folders
    DownloadService needs at construction time. ``install_recorder`` is the
    shared writer of the ``rom_installs`` row and the shortcut bake behind it;
    ``target_gate`` is the pre-flight that refuses to write over content
    Tender did not put there (ADR-0028). ``conflict_rules`` are what a start or
    resume checks at its entry, and what a download's task and its
    ``download_complete`` lease are held through.
    """

    romm_api: RommRomReader
    download_file_store: DownloadFileStore
    platform_systems: PlatformSystems
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    emit: EventEmitter
    clock: Clock
    sleeper: Sleeper
    retrodeck_folders: RetroDeckFolders
    install_recorder: RomInstallRecorder
    target_gate: DownloadTargetGateFn
    m3u_support: SystemM3uSupportFn
    uow_factory: UnitOfWorkFactory
    # Deferred access to RomRemovalService.remove_rom_unchecked for the sibling
    # supersede — the two services form a construction cycle, so the composition
    # root binds it after both exist.
    rom_remover: RomRemoverProvider
    conflict_rules: ConflictRules


class DownloadService:
    """ROM download engine: downloads and queue management.

    A start and a resume check their endpoint's conflict rules at their entry,
    under that endpoint's name, and raise the rule's refusal when one holds
    (GLOSSARY.md → Conflict rules).
    """

    def __init__(self, *, config: DownloadServiceConfig) -> None:
        self._romm_api = config.romm_api
        self._download_file_store = config.download_file_store
        self._platform_systems = config.platform_systems
        self._loop = config.loop
        self._logger = config.logger
        self._emit = config.emit
        self._clock = config.clock
        self._sleeper = config.sleeper
        self._retrodeck_folders = config.retrodeck_folders
        self._install_recorder = config.install_recorder
        self._target_gate = config.target_gate
        self._m3u_support = config.m3u_support
        self._uow_factory = config.uow_factory
        self._rom_remover = config.rom_remover
        self._rules = config.conflict_rules
        self._tasks = BackgroundTasks(logger=config.logger)

        # Owned state
        self._download_in_progress: set[int] = set()
        self._download_queue: dict[int, dict[str, Any]] = {}
        self._download_tasks: dict[int, asyncio.Task[None]] = {}
        # Bounded concurrency: at most two ROMs transfer at once. Excess
        # downloads enter the queue with status "queued" and acquire the
        # semaphore in FIFO order inside ``_do_download``.
        self._download_semaphore = asyncio.Semaphore(2)
        # Reserved bytes per in-flight ROM, so the disk pre-flight accounts for
        # siblings already committed to download but not yet written to disk.
        self._reserved_bytes: dict[int, int] = {}
        # Per-download cooperative-control tokens. The progress callback polls its
        # captured token on the executor thread; ``cancel_download`` /
        # ``pause_download`` flip it on the loop thread to abort the in-flight
        # transfer (#144).
        self._control_tokens: dict[int, _DownloadControl] = {}

    async def shutdown(self) -> None:
        """Cancel the per-ROM download tasks and the progress frames still running, and wait until they have ended.

        A download cancelled while its install is being committed waits for the
        commit's thread before it ends, so this waits for it too.
        """
        await self._tasks.shutdown()
        self._download_tasks.clear()

    def _prune_download_queue(self):
        """Remove oldest terminal items when over the limit.

        Keeps all non-terminal (queued/downloading/paused/extracting) items.
        Retains up to _DOWNLOAD_QUEUE_MAX_TERMINAL terminal items, removing the
        oldest (by insertion order) when the count exceeds the limit. In practice
        the retained terminals are completed/failed — a cancel evicts its own
        entry immediately (#149 downloads-round), so it rarely reaches the cap.
        """
        terminal_ids = [
            rid for rid, item in self._download_queue.items() if item.get("status") in _TERMINAL_DOWNLOAD_STATUSES
        ]
        excess = len(terminal_ids) - _DOWNLOAD_QUEUE_MAX_TERMINAL
        if excess <= 0:
            return
        # Dict preserves insertion order (Python 3.7+), so the first
        # entries in terminal_ids are the oldest.
        for rid in terminal_ids[:excess]:
            del self._download_queue[rid]

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
            if rom_id in self._download_in_progress:
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
            self._evict_if_paused(sibling_id)

    def _evict_if_paused(self, rom_id: int) -> None:
        """Drop a superseded sibling's queue entry when it is paused (#1298 S1).

        A paused download whose install this supersede just removed is stale — its
        "paused" row (and partial ``.tmp``) would otherwise linger and could be
        resumed into a second installed version. Only a paused row is evicted; a
        live or terminal row for the sibling is left to its own lifecycle.
        """
        entry = self._download_queue.get(rom_id)
        if entry is not None and entry.get("status") == "paused":
            self.evict(rom_id)

    def _conflicting_sibling_install_ids(self, rom_id: int, adopted_path: str | None = None) -> list[int]:
        """Sibling members with a ``rom_installs`` row that this install still has to remove.

        Read in one short UoW (closed before the removal seam runs); which member
        is displaced, and how, is ``supersede_siblings``'s answer. A member it
        hands over — one recorded at *adopted_path* — loses its ``rom_installs``
        row in that same UoW and is not listed, so its files are never removed;
        once the UoW has closed, each hand-over is logged with both rom ids and
        the path, and the member's paused queue entry is evicted
        (``_evict_if_paused``). A ROM with no sibling group key (solo /
        unbackfilled) has no siblings, so the list is empty.
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
            self._evict_if_paused(sibling_id)
        return list(displaced.removed)

    async def _begin_download(self, rom_id, *, resume: bool, replace_existing=False, **answer):
        """Shared core of ``start_download`` and ``resume_download``.

        *answer* passes the adopt dialog's answers, and what the game page reported, through to the gate unread.
        Fetches ROM detail and refuses one RomM holds no file for (``no_file_on_romm``), resolves the platform path,
        then runs the three pre-flights **in this order**: the occupancy gate, the #1298 sibling supersede, the
        disk-space check. Only then are the queue entry, task, byte reservation and control token registered. On
        ``resume=True`` the disk pre-flight discounts the bytes already on the existing ``.tmp`` and ``_do_download``
        appends rather than restarts. The ``already_downloading`` guard stays with ``start_download``;
        ``resume_download`` validates the paused entry before calling here.

        Holds the ROM's in-progress claim from the first step and gives it back on every way out but a started
        download, a cancelled start included, so the ROM is never stuck "Already downloading". A partial failure the
        gate returns is answered as it came, with the claim given back. Of what the steps raise, an ``OSError`` (a
        disk that cannot be read or prepared) is refused here with ``download_start_failed``; a RomM error and a
        refusal pass on unchanged (which: ``docs/architecture/backend-architecture.md``), and anything else is a bug.
        """
        self._download_in_progress.add(rom_id)
        try:
            return await self._start_claimed_download(
                rom_id, resume=resume, replace_existing=replace_existing, **answer
            )
        except BaseException:
            self._download_in_progress.discard(rom_id)
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
            file_name = self._safe_local_file_name(rom_detail)
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
                folder_path = os.path.join(roms_dir, self._resolve_safe_extract_dir_name(rom_detail))
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
            self._download_in_progress.discard(rom_id)
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
            # A download kept whole after a failed unpack is not fetched again, so
            # only what the unpack still needs is weighed — as for a resume.
            kept = resume or self._unpacker().kept_download_complete(target_path, rom_detail)
            verdict = disk_space_verdict(
                file_size=file_size,
                free_space=self._download_file_store.disk_free(roms_dir),
                reserved_bytes=sum(self._reserved_bytes.values()),
                multi_file=is_multi_file_download(rom_detail),
                already_on_disk=self._partial_tmp_size(target_path, rom_detail) if kept else 0,
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
        resumable = bool(self._download_queue.get(rom_id, {}).get("resumable", False))

        # Create the control token BEFORE the task so the closure captured inside
        # ``_do_download``'s progress callback polls this exact object. A later
        # re-download installs a fresh token, leaving the zombie's callback bound
        # to the cancelled one (#144).
        control = _DownloadControl()
        task = self._tasks.spawn(
            self._do_download(rom_id, rom_detail, target_path, system, file_name, control, resume=resume),
            f"download of rom {rom_id}",
        )

        self._download_queue[rom_id] = {
            "rom_id": rom_id,
            "rom_name": rom_name,
            "platform_name": platform_name,
            "file_name": file_name,
            # Honest initial status: the task hasn't acquired the concurrency
            # semaphore yet. ``_do_download`` flips this to "downloading" once it
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
            # Kept only where the answer is still LIVE: a replace of a file
            # deliberately deletes nothing (``os.replace`` is atomic, and an
            # unpacked zip removes it only once installed), so the original is
            # still there and still the content the user was shown. A replace of
            # a directory already removed it, so the answer is spent — anything at
            # that path now is content the user has never seen, and the gate must
            # ask about it rather than delete it.
            "_replace_existing": replace_existing and FILE in served_shapes(rom_detail),
        }
        self._download_tasks[rom_id] = task
        # Reserve this download's required bytes so a concurrent sibling's
        # pre-flight sees the outstanding claim (released in ``_do_download``'s
        # ``finally``).
        self._reserved_bytes[rom_id] = verdict.needed_bytes
        self._control_tokens[rom_id] = control
        return {"success": True, "message": "Download started"}

    def task_for_rom(self, rom_id: int) -> asyncio.Task[None] | None:
        """Return the detached task whose lifetime owns this ROM's install write."""
        return self._download_tasks.get(int(rom_id))

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

    def _safe_local_file_name(self, rom_detail: dict[str, Any]) -> str:
        """The on-disk name for this ROM: the server's, coerced to one safe component.

        Strips any directory portion and rejects a degenerate result — ``..``
        would escape the platform directory and ``.`` or ``""`` would resolve to
        it — falling back to the synthetic ``rom_<id>`` identity. Both repairs
        are logged, because the name the user sees on disk then differs from the
        one the server states.
        """
        file_name, files_missing = resolve_local_file_name(rom_detail)
        if files_missing:
            self._logger.warning(
                f"has_nested_single_file=true but files list is empty; falling back to fs_name='{file_name}'"
            )
        safe_name, name_changed = coerce_safe_component(file_name, synthetic_rom_name(rom_detail))
        if name_changed:
            self._logger.warning(f"Sanitized fs_name from '{file_name}' to '{safe_name}'")
        return safe_name

    def _partial_tmp_size(self, target_path, rom_detail) -> int:
        """Bytes already on disk in the partial ``.tmp`` for *target_path*.

        Single-file ROMs stream to ``target_path + .tmp``; multi-file ROMs to
        ``target_path + .zip.tmp``. Returns 0 when no partial exists (the file
        store reports a missing path as size 0).
        """
        tmp_ext = ZIP_TMP_EXT if is_multi_file_download(rom_detail) else TMP_EXT
        return self._download_file_store.file_size(target_path + tmp_ext)

    def _resolve_safe_extract_dir_name(self, rom_detail: dict[str, Any]) -> str:
        """Resolve the sanitized base name for the extract dir an unpacked ROM lands in.

        Names the directory after the ROM's identity via
        ``resolve_extract_dir_name`` (never ``files[0]``), then coerces the
        server-supplied value to a single safe path component
        (``coerce_safe_component``): a directory portion (``../evil``, an
        absolute path) is stripped to its basename, AND a degenerate result
        (``..``/``.``/empty/whitespace — which would resolve to the roms root
        or the platform dir and turn a later ``remove_tree`` into a
        library-wide delete) falls back to the synthetic ``rom_<id>`` identity.
        Mirrors the ``file_name`` guard in ``_start_claimed_download``.
        """
        raw = resolve_extract_dir_name(rom_detail)
        safe, changed = coerce_safe_component(raw, synthetic_rom_name(rom_detail))
        if changed:
            self._logger.warning(f"Sanitized extract dir name from '{raw}' to '{safe}'")
        return safe

    def _unpacker(self) -> DownloadUnpacker:
        """The post-processing over this service's seams as they stand now."""
        return DownloadUnpacker(
            config=DownloadUnpackerConfig(
                download_file_store=self._download_file_store,
                retrodeck_folders=self._retrodeck_folders,
                install_recorder=self._install_recorder,
                m3u_support=self._m3u_support,
                logger=self._logger,
            )
        )

    def _extract_progress(self, rom_id, rom_detail, file_name):
        """The ``extracting`` phase's progress callback for this download."""
        rom_name = rom_detail.get("name", file_name)
        platform_name = rom_detail.get("platform_name", rom_detail.get("platform_slug", ""))
        return self._make_progress_callback(rom_id, rom_name, platform_name, file_name, phase="extracting")

    def _post_download_multi_io(
        self, rom_id, rom_detail, target_path, file_name, system, extract_dir_name, reserved_bytes=0
    ):
        """Sync helper for _do_download multi-file — extraction + renames in executor.

        *extract_dir_name* is the ROM-identity-derived, sanitized base name for
        the extract directory (``_resolve_safe_extract_dir_name``) — never a
        name derived from ``files[0]``. Returns ``(launch_file, error)`` as
        ``DownloadUnpacker.unpack_and_record`` does.
        """
        return self._unpacker().finish_multi(
            rom_id,
            rom_detail,
            target_path,
            os.path.join(os.path.dirname(target_path), extract_dir_name),
            system,
            progress_callback=self._extract_progress(rom_id, rom_detail, file_name),
            reserved_bytes=reserved_bytes,
        )

    def _post_download_single_io(self, rom_id, rom_detail, target_path, system, extract_dir_name="", reserved_bytes=0):
        """Sync helper for _do_download single-file — rename + DB persist in executor.

        Returns ``(path, error)`` as ``DownloadUnpacker.finish_single`` does; a
        zip holding a disc image is unpacked into *extract_dir_name* beside
        *target_path*.
        """
        file_name = os.path.basename(target_path)
        return self._unpacker().finish_single(
            rom_id,
            rom_detail,
            target_path,
            os.path.join(os.path.dirname(target_path), extract_dir_name) if extract_dir_name else None,
            system,
            progress_callback=self._extract_progress(rom_id, rom_detail, file_name),
            reserved_bytes=reserved_bytes,
        )

    def _make_progress_callback(
        self, rom_id, rom_name, platform_name, file_name, control=None, *, phase: _ProgressPhase = "downloading"
    ):
        """Build a throttled progress callback for one *phase* of a download.

        The callback logs a one-line summary at most every 30s and emits at
        most every 0.5s, except that the final tick (``done >= total``) always
        emits. ``last_emit`` starts at 0.0 so the first extracting tick emits
        immediately and the UI switches to the "extracting" phase promptly once
        the byte transfer finishes.

        *control*, when given, is polled on every tick. The extracting phase is
        handed none: extraction is not cancellable.
        """
        last_emit = [0.0]  # mutable container for closure
        last_log = [0.0]

        def progress_callback(done, total):
            if control is not None and (control.cancelled or control.paused):
                # Abort the in-flight transfer thread (#144). CancelledError is a
                # BaseException, so it propagates untouched through the adapter's
                # Exception-only retry/translate — no retry, no error translation.
                # Both cancel and pause unwind through here; the terminal handling
                # in ``_do_download`` branches on which flag was set.
                raise asyncio.CancelledError()
            now = self._clock.monotonic()
            if now - last_log[0] >= 30.0:
                last_log[0] = now
                self._log_progress(phase, rom_name, done, total)
            if now - last_emit[0] < 0.5 and done < total:
                return
            last_emit[0] = now
            progress = done / total if total else 0

            # This callback runs on a ``run_in_executor`` worker thread. Both the
            # queue-dict mutation and the emit-scheduling must happen on the loop
            # thread, so marshal them across via ``call_soon_threadsafe`` (#973).
            self._loop.call_soon_threadsafe(
                self._apply_progress,
                phase,
                rom_id,
                rom_name,
                platform_name,
                file_name,
                progress,
                done,
                total,
            )

        return progress_callback

    def _log_progress(self, phase: _ProgressPhase, rom_name, done, total):
        """Log a throttled one-line human-readable progress summary (MB + %)."""
        label = "Extract" if phase == "extracting" else "Download"
        mb_done = done / (1024 * 1024)
        mb_total = total / (1024 * 1024) if total else 0
        pct = (done / total * 100) if total else 0
        self._logger.info(f"{label} progress: {rom_name} — {mb_done:.1f}/{mb_total:.1f} MB ({pct:.0f}%)")

    def _live_entry(self, rom_id) -> dict[str, Any] | None:
        # Every applier the worker queues with ``call_soon_threadsafe`` reads the entry
        # here. None for an evicted entry (never resurrected) and for an ended one: a
        # callback queued before the end can run after the terminal frame, because from
        # 3.13 asyncio settles an executor future that finished before the loop chained
        # it without yielding (``asyncio.futures._chain_future``).
        entry = self._download_queue.get(rom_id)
        return None if entry is None or entry.get("status") in _TERMINAL_DOWNLOAD_STATUSES else entry

    def _apply_progress(self, phase: _ProgressPhase, rom_id, rom_name, platform_name, file_name, progress, done, total):
        """Update the live queue entry and schedule a ``download_progress`` emit for *phase*.

        Runs on the loop thread, marshaled from the worker via ``call_soon_threadsafe``;
        an evicted or ended entry is left alone and no frame is sent (``_live_entry``).
        Extracting flips the entry to "extracting" and reports ``resumable: False``,
        as extraction never resumes; downloading keeps the status and its ``resumable``.
        """
        entry = self._live_entry(rom_id)
        if entry is None:
            return
        extracting = phase == "extracting"
        if extracting:
            entry["status"] = "extracting"
        entry.update(
            {
                "progress": progress,
                "bytes_downloaded": done,
                "total_bytes": total,
            }
        )
        self._tasks.spawn(
            self._emit(
                "download_progress",
                {
                    "rom_id": rom_id,
                    "rom_name": rom_name,
                    "platform_name": platform_name,
                    "file_name": file_name,
                    "status": phase,
                    "progress": progress,
                    "bytes_downloaded": done,
                    "total_bytes": total,
                    "resumable": False if extracting else entry.get("resumable", False),
                },
            ),
            "download_progress",
        )

    async def _finalize_download_complete(self, rom_id, rom_detail, final_path, rom_name, platform_name):
        """Mark the queue entry completed and emit ``download_complete``.

        Resolves the bound Steam ``shortcut_app_id`` for this rom_id (or ``None``
        when the ROM hasn't been synced yet) plus the ROM's full active core
        (resolved ``.so`` or ``None``) and the multi-disc launch path so the
        frontend confirm-sets launch options on the exact shortcut without a
        full-library scan to re-resolve rom_id→app_id, and the re-bake keeps the
        per-game/per-platform core AND the selected disc across uninstall →
        reinstall. Called from the normal success path and from the cancel handler
        when the install committed before the cancel landed (#1049).
        """
        entry = self._download_queue[rom_id]
        entry["status"] = "completed"
        entry["progress"] = 1.0
        app_id, launch_options = await self._loop.run_in_executor(
            None, self._install_recorder.do_resolve_launch_bake, rom_id, rom_detail, final_path
        )
        payload = {
            "rom_id": rom_id,
            "rom_name": rom_name,
            "platform_name": platform_name,
            "file_path": final_path,
            "app_id": app_id,
            "launch_options": launch_options,
            "resumable": entry.get("resumable", False),
        }
        # A bound ROM's event carries a lease for the frontend's write of the
        # launch command onto its shortcut; an unbound ROM has no shortcut to
        # write to, so its lease would hold off every cleanup until it expired.
        if app_id is None:
            await self._emit("download_complete", payload)
        else:
            await self._rules.emit_under_lease(
                "download_complete",
                lambda token: self._emit("download_complete", {**payload, "prune_lease_token": token}),
            )
        # Record the freshly baked launch command as this ROM's applied state (the
        # value the frontend confirm-sets onto the shortcut), so the next sync
        # skips the now-correct shortcut instead of re-touching it (delta apply,
        # #1383). Only when the ROM has a bound shortcut — an unbound ROM has none
        # to record, and the next sync creates + records it. Second of the six
        # writer sites.
        if app_id is not None:
            await self._loop.run_in_executor(
                None, self._install_recorder.do_record_applied_launch_options, rom_id, launch_options
            )
        self._logger.info(f"Download complete: {rom_name} -> {final_path}")

    async def _reconcile_post_io(self, post_io_future):
        """After a cancel, settle an in-flight post-IO commit and report whether
        the install committed. Executor threads run to completion regardless of
        cancellation, so letting the future settle here lets a race-committed
        install be honored instead of torn down. Returns (final_path, committed).
        """
        if post_io_future is None:
            return (None, False)  # cancel landed before the post-IO phase started
        # Let the (shielded) executor future settle WITHOUT awaiting it directly:
        # ``asyncio.wait`` reports completion through the future's own state, so a
        # cancelled or failed commit is inspected here, never swallowed — the
        # caller's ``except asyncio.CancelledError`` keeps ownership of the re-raise.
        await asyncio.wait({post_io_future})
        if post_io_future.cancelled() or post_io_future.exception() is not None:
            # Executor work was cancelled before it ran, or the commit raised.
            return (None, False)
        final_path, post_io_error = post_io_future.result()
        if post_io_error is None and final_path is not None:
            return (final_path, True)
        return (None, False)

    def _make_on_meta(self, rom_id, rom_name, platform_name, file_name):
        """Build the one-shot resumability callback the adapter fires when the
        download's response headers arrive (before the body streams).

        It records the server's ``range_supported`` verdict on the queue entry
        and emits a ``download_progress`` frame carrying it, so the frontend can
        flip Pause/Cancel live DURING the transfer instead of only learning the
        verdict at the end. Runs on the executor transfer thread, so it hops back
        to the loop via ``call_soon_threadsafe`` like the progress callback (#973).
        """

        def on_meta(range_supported: bool) -> None:
            def _apply() -> None:
                entry = self._live_entry(rom_id)
                if entry is None:
                    return
                entry["resumable"] = range_supported
                self._tasks.spawn(
                    self._emit(
                        "download_progress",
                        {
                            "rom_id": rom_id,
                            "rom_name": rom_name,
                            "platform_name": platform_name,
                            "file_name": file_name,
                            "status": entry.get("status", "downloading"),
                            "progress": entry.get("progress", 0),
                            "bytes_downloaded": entry.get("bytes_downloaded", 0),
                            "total_bytes": entry.get("total_bytes", 0),
                            "resumable": range_supported,
                        },
                    ),
                    "download_progress",
                )

            self._loop.call_soon_threadsafe(_apply)

        return on_meta

    async def _do_download(self, rom_id, rom_detail, target_path, system, file_name, control=None, *, resume=False):
        if control is None:
            # Direct invocation (no ``_start_claimed_download``): own + register a control
            # so the ``finally``'s identity-gated cleanup releases this task's
            # registrations like the real path does.
            control = _DownloadControl()
            self._control_tokens[rom_id] = control
        rom_name = rom_detail.get("name", file_name)
        platform_name = rom_detail.get("platform_name", rom_detail.get("platform_slug", ""))
        has_multiple = is_multi_file_download(rom_detail)
        # Name the extract dir from the ROM's identity, not files[0] (which may be
        # an arbitrary inner asset for a nested-single folder game). Computed once
        # here and threaded to both the extraction and the cleanup so the dir a
        # failure tears down matches the dir extraction created. Only a download
        # that may be unpacked — a multi-file ROM or a single zip — gets one.
        may_unpack = has_multiple or is_zip_name(target_path)
        extract_dir_name = self._resolve_safe_extract_dir_name(rom_detail) if may_unpack else ""
        progress_callback = self._make_progress_callback(rom_id, rom_name, platform_name, file_name, control)
        on_meta = self._make_on_meta(rom_id, rom_name, platform_name, file_name)
        # Tracks the resolved launch path once extraction returns it, so a
        # failure AFTER the ES-DE collapse rename cleans up the *renamed* dir
        # (``os.path.dirname(final_path)``) — not just the staging name.
        final_path: str | None = None
        # The post-IO commit future, declared before the try so the cancel
        # handler can reconcile a race-committed install (#1049). Stays ``None``
        # while waiting on the concurrency semaphore or during the transfer.
        post_io_future: asyncio.Future[tuple[str | None, str | None]] | None = None

        try:
            self._logger.info(f"Download starting: {rom_name} (rom_id={rom_id}, multi={has_multiple}) -> {target_path}")

            # Bounded concurrency (#1053): only two ROMs transfer at once. If the
            # semaphore is already held, surface an honest "queued" frame so the
            # UI shows the wait instead of a stalled "downloading" bar.
            if self._download_semaphore.locked():
                entry = self._download_queue[rom_id]
                entry["status"] = "queued"
                await self._emit(
                    "download_progress",
                    {
                        "rom_id": rom_id,
                        "rom_name": rom_name,
                        "platform_name": platform_name,
                        "file_name": file_name,
                        "status": "queued",
                        "progress": 0,
                        "bytes_downloaded": 0,
                        "total_bytes": rom_detail.get("fs_size_bytes", 0),
                        "resumable": entry.get("resumable", False),
                    },
                )

            async with self._download_semaphore:
                self._download_queue[rom_id]["status"] = "downloading"
                # A zip kept whole after a failed unpack is unpacked as it is.
                kept = self._unpacker().kept_download_complete(target_path, rom_detail)
                if kept:
                    self._logger.info(f"Unpacking the download already on disk for {rom_name}")

                if has_multiple:
                    # Multi-file ROM: API returns ZIP, download to temp then extract
                    tmp_zip = target_path + ZIP_TMP_EXT
                    if not kept:
                        await self._loop.run_in_executor(
                            None,
                            partial(
                                self._romm_api.download_rom_content,
                                rom_id,
                                file_name,
                                tmp_zip,
                                progress_callback,
                                resume=resume,
                                on_meta=on_meta,
                            ),
                        )
                    post_io_future = self._loop.run_in_executor(
                        None,
                        self._post_download_multi_io,
                        rom_id,
                        rom_detail,
                        target_path,
                        file_name,
                        system,
                        extract_dir_name,
                        self._bytes_reserved_by_others(rom_id),
                    )
                    # Shield the commit await: a cancel here must propagate to this
                    # coroutine WITHOUT cancelling the underlying future, so
                    # ``_reconcile_post_io`` can re-await it for the real result. A
                    # bare ``await`` cancels the asyncio future (the executor thread
                    # still commits), so the re-await would raise CancelledError and
                    # the committed install would be mis-reported as not committed
                    # → torn down (#1049).
                    final_path, post_io_error = await asyncio.shield(post_io_future)
                else:
                    tmp_path = target_path + TMP_EXT
                    if not kept:
                        await self._loop.run_in_executor(
                            None,
                            partial(
                                self._romm_api.download_rom_content,
                                rom_id,
                                file_name,
                                tmp_path,
                                progress_callback,
                                resume=resume,
                                on_meta=on_meta,
                            ),
                        )
                    post_io_future = self._loop.run_in_executor(
                        None,
                        self._post_download_single_io,
                        rom_id,
                        rom_detail,
                        target_path,
                        system,
                        extract_dir_name,
                        self._bytes_reserved_by_others(rom_id),
                    )
                    # Shielded so a racing cancel leaves the future intact for
                    # ``_reconcile_post_io`` to re-await (see the multi-file branch).
                    final_path, post_io_error = await asyncio.shield(post_io_future)

                if post_io_error is not None or final_path is None:
                    # The download succeeded but the install record failed its
                    # invariant; the artifact was already cleaned up by the worker.
                    # ``final_path is None`` always coincides with a non-None error
                    # — the guard narrows the type for the launch-command build below.
                    raise ValueError(post_io_error or "install record produced no launch path")

                await self._finalize_download_complete(rom_id, rom_detail, final_path, rom_name, platform_name)

        except asyncio.CancelledError:
            # The cancel/pause may have raced a committing install. Executor
            # threads run to completion, so wait for the post-IO future before
            # deciding (#1049).
            committed_path, committed = await self._reconcile_post_io(post_io_future)
            if committed:
                # The ROM IS installed — surface completed, don't tear it down.
                # This also bakes launch_options for the just-committed install.
                await self._finalize_download_complete(rom_id, rom_detail, committed_path, rom_name, platform_name)
                self._logger.info(f"Download cancelled after install committed; surfaced as complete: {rom_name}")
            elif control.paused:
                # PAUSE: keep the partial ``.tmp`` so the transfer can resume from
                # where it stopped. NOTHING is cleaned up. The entry stays "paused"
                # in the queue (``_prune_download_queue`` only prunes terminal
                # completed/failed/cancelled, so "paused" is retained). Record the
                # target path (internal ``_``-prefixed key, stripped from the wire
                # by ``get_download_queue``) so a later cancel of this now-task-less
                # download can delete the partial without re-fetching rom detail.
                entry = self._download_queue[rom_id]
                entry["status"] = "paused"
                entry["_target_path"] = target_path
                await self._emit(
                    "download_progress",
                    {
                        "rom_id": rom_id,
                        "rom_name": rom_name,
                        "platform_name": platform_name,
                        "file_name": file_name,
                        "status": "paused",
                        "progress": entry.get("progress", 0),
                        "bytes_downloaded": entry.get("bytes_downloaded", 0),
                        "total_bytes": entry.get("total_bytes", 0),
                        "resumable": entry.get("resumable", False),
                    },
                )
                self._logger.info(f"Download paused: {rom_name}")
            else:
                entry = self._download_queue[rom_id]
                entry["status"] = "cancelled"
                self._cleanup_partial_download(target_path, has_multiple, extract_dir_name, final_path)
                # #1017: emit a terminal frame so the frontend resets the button
                # out of its downloading state (the global cancel path was silent).
                await self._emit(
                    "download_progress",
                    {
                        "rom_id": rom_id,
                        "rom_name": rom_name,
                        "platform_name": platform_name,
                        "file_name": file_name,
                        "status": "cancelled",
                        "progress": entry.get("progress", 0),
                        "bytes_downloaded": entry.get("bytes_downloaded", 0),
                        "total_bytes": entry.get("total_bytes", 0),
                        "resumable": entry.get("resumable", False),
                    },
                )
                # A cancel is an explicit discard — drop the entry so no
                # "cancelled" row lingers in the queue view or QAM summary (#149
                # downloads-round). The terminal frame above already told the
                # frontend; the store listener drops it there. Emitted BEFORE the
                # evict so the frame carries the entry's final progress values.
                self.evict(rom_id)
                self._logger.info(f"Download cancelled: {rom_name}")
            raise

        except Exception as e:
            self._download_queue[rom_id]["status"] = "failed"
            self._download_queue[rom_id]["error"] = str(e)
            # Too little space to unpack keeps the download whole for a retry;
            # nothing was extracted yet, so there is nothing else to remove.
            if not isinstance(e, NotEnoughSpaceToUnpack):
                self._cleanup_partial_download(target_path, has_multiple, extract_dir_name, final_path)
            self._logger.error(f"Download failed for {rom_name}: {e}")
            await self._emit("download_failed", failed_frame(rom_id, rom_name, platform_name, str(e)))

        finally:
            # A re-download (or resume) can overwrite these per-download
            # registrations with a fresh attempt's BEFORE this (older/superseded)
            # task's finally runs. Gate ALL of them on the control-token identity
            # so a zombie/superseded task never evicts the newer attempt's task,
            # in-progress flag, reservation, or token (#144). The control is
            # registered by ``_start_claimed_download``; a direct-call test that never
            # registered it simply skips these no-op pops.
            if self._control_tokens.get(rom_id) is control:
                self._download_tasks.pop(rom_id, None)
                self._download_in_progress.discard(rom_id)
                self._reserved_bytes.pop(rom_id, None)
                del self._control_tokens[rom_id]
            self._prune_download_queue()

    def _bytes_reserved_by_others(self, rom_id: int) -> int:
        """What every other in-flight download has claimed — read on the loop, where the claims change."""
        return sum(reserved for other, reserved in self._reserved_bytes.items() if other != rom_id)

    def _maybe_heal_ps3_sfb_io(self, extract_dir: str) -> None:
        """``DownloadUnpacker.heal_ps3_sfb`` over this service's seams."""
        self._unpacker().heal_ps3_sfb(extract_dir)

    def _maybe_generate_m3u_io(self, extract_dir: str, rom_detail: dict[str, Any], m3u_supported: bool) -> None:
        """``DownloadUnpacker.generate_m3u`` over this service's seams."""
        self._unpacker().generate_m3u(extract_dir, rom_detail, m3u_supported)

    def _collect_and_detect_launch_file(self, extract_dir: str, m3u_supported: bool) -> str:
        """``DownloadUnpacker.detect_launch_file`` over this service's seams."""
        return self._unpacker().detect_launch_file(extract_dir, m3u_supported)

    def _cleanup_partial_download(self, target_path, has_multiple, extract_dir_name, final_path=None):
        """``DownloadUnpacker.remove_partial_download`` over this service's seams."""
        self._unpacker().remove_partial_download(target_path, has_multiple, extract_dir_name, final_path)

    def _remove_partial_tmp_files(self, target_path: str) -> None:
        """``DownloadUnpacker.remove_partial_tmp_files`` over this service's seams."""
        self._unpacker().remove_partial_tmp_files(target_path)

    def cancel_download(self, rom_id):
        """Cancel a download, whether it is running or paused.

        A running download has a live task: flip its cooperative-cancel token
        (stops the executor transfer thread, not just the asyncio wrapper — #144)
        and cancel the task; ``_do_download``'s terminal handler deletes the
        partial, emits the terminal ``cancelled`` frame, and evicts the entry.

        A **paused** download has NO task — the pause already cancelled it,
        leaving the entry "paused" and the partial ``.tmp`` kept for resume.
        ``_cancel_paused_download`` deletes the partial, emits the same terminal
        ``cancelled`` frame, and evicts the entry.

        A cancel that can act on neither (no task AND no paused entry) is refused
        with ``no_active_download`` so the frontend can surface it.
        """
        rom_id = int(rom_id)
        task = self._download_tasks.get(rom_id)
        if task:
            token = self._control_tokens.get(rom_id)
            if token is not None:
                token.cancelled = True  # stop the executor transfer thread, not just the asyncio wrapper (#144)
            task.cancel()
            return {"success": True, "message": "Download cancelled"}
        entry = self._download_queue.get(rom_id)
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
            self._remove_partial_tmp_files(target_path)
        # cancel_download runs on the loop thread; schedule the terminal frame the
        # same way the progress callbacks do.
        self._tasks.spawn(self._emit("download_progress", cancelled_frame(rom_id, entry)), "download_progress")
        self.evict(rom_id)

    def pause_download(self, rom_id):
        """Pause an in-flight download, keeping the partial ``.tmp`` for resume.

        Mirrors ``cancel_download`` but flips ``control.paused`` instead of
        ``cancelled`` before cancelling the task, so ``_do_download``'s terminal
        handler routes to the pause branch (status "paused", no cleanup) rather
        than the cancel branch (status "cancelled", ``.tmp`` deleted). Kept
        defensive — the frontend only offers Pause when a download is resumable.
        """
        rom_id = int(rom_id)
        task = self._download_tasks.get(rom_id)
        if not task:
            raise Refused("no_active_download", "No active download for this ROM")
        token = self._control_tokens.get(rom_id)
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
            entry = self._download_queue.get(rom_id)
            if entry is None or entry.get("status") != "paused":
                raise Refused("not_paused", "No paused download for this ROM")
            if self._resume_target_superseded(rom_id):
                self.evict(rom_id)
                raise Refused("superseded", "Another version is now active")
            replace_existing = bool(entry.get("_replace_existing"))
            result = await self._begin_download(rom_id, resume=True, replace_existing=replace_existing)
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

    def get_download_queue(self):
        # Drop internal ``_``-prefixed keys (e.g. ``_target_path``, kept on a
        # paused entry only for the paused-cancel cleanup) so they never cross
        # the wire to the frontend ``DownloadItem`` shape.
        return {
            "downloads": [
                {k: v for k, v in entry.items() if not k.startswith("_")} for entry in self._download_queue.values()
            ]
        }

    def clear_completed_downloads(self) -> dict[str, Any]:
        """Evict every terminal (completed/failed/cancelled) entry from the queue.

        The frontend "Clear Completed" action calls this so a dismissed download
        stays cleared across a QAM reopen or menu switch (#149): the backend queue
        is the source ``get_download_queue`` re-seeds the frontend from on every
        mount, so a purely-local hide reappeared on the next reopen. Active,
        queued, paused, and extracting entries are left untouched — only terminal
        ones are removed. In normal flow those are completed/failed only, since a
        cancel already evicted its own entry (#149 downloads-round); a stray
        cancelled row would still clear here. Idempotent: an empty queue or one
        with no terminal entries clears nothing and reports ``cleared: 0``. A ROM
        re-downloaded after being cleared re-enters the queue via ``start_download``.
        """
        terminal_ids = [
            rid for rid, item in self._download_queue.items() if item.get("status") in _TERMINAL_DOWNLOAD_STATUSES
        ]
        for rid in terminal_ids:
            del self._download_queue[rid]
        return {"success": True, "cleared": len(terminal_ids)}

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
        return set(self._download_in_progress)

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
        """Remove the queue entry for *rom_id* if present. Idempotent."""
        self._download_queue.pop(int(rom_id), None)

    def clear(self) -> None:
        """Remove all entries from the download queue."""
        self._download_queue.clear()
