"""DownloadTransfer — one admitted download's task, from its turn in the queue to its end.

The task waits for a transfer slot, streams the ROM into its partial file, has
the unpacker turn the finished transfer into an install, and reports how it
ended: completed, paused, cancelled or failed. Whatever way it ends, it gives
back what the download's admission registered in the state box, unless a newer
attempt for the same ROM registered its own since.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from domain.disk_space import NotEnoughSpaceToUnpack
from domain.download_frames import failed_frame
from domain.rom_files import TMP_EXT, ZIP_TMP_EXT, lands_as_folder
from services.downloads._fetch import DownloadFetch, plan_fetch
from services.downloads._names import extract_dir_name_for

if TYPE_CHECKING:
    import logging

    from services.downloads._state import DownloadsStateBox
    from services.downloads.queue import DownloadQueue
    from services.downloads.unpack import DownloadUnpacker
    from services.protocols import EventEmitter, RommRomReader


@dataclass(frozen=True)
class DownloadTransferConfig:
    """Frozen wiring bundle handed to ``DownloadTransfer.__init__``.

    ``state`` is the state box the façade shares, ``queue`` holds and reports the
    download's entry and ``unpacker`` turns the finished transfer into an
    install.
    """

    state: DownloadsStateBox
    queue: DownloadQueue
    unpacker: DownloadUnpacker
    romm_api: RommRomReader
    loop: asyncio.AbstractEventLoop
    emit: EventEmitter
    logger: logging.Logger


class DownloadTransfer:
    """An admitted download's task: the transfer, what the finished transfer becomes, and how the download ended."""

    def __init__(self, *, config: DownloadTransferConfig) -> None:
        self._state = config.state
        self._queue = config.queue
        self._unpacker = config.unpacker
        self._romm_api = config.romm_api
        self._loop = config.loop
        self._emit = config.emit
        self._logger = config.logger

    def _post_download_multi_io(
        self, rom_id, rom_detail, target_path, extract_progress, system, extract_dir_name, reserved_bytes=0
    ):
        """Sync helper for :meth:`run` multi-file — extraction + renames in executor.

        *extract_dir_name* is the ROM-identity-derived, sanitized base name for
        the extract directory (``_names.safe_extract_dir_name``) — never a
        name derived from ``files[0]``. Returns ``(launch_file, error)`` as
        ``DownloadUnpacker.unpack_and_record`` does.
        """
        return self._unpacker.finish_multi(
            rom_id,
            rom_detail,
            target_path,
            os.path.join(os.path.dirname(target_path), extract_dir_name),
            system,
            progress_callback=extract_progress,
            reserved_bytes=reserved_bytes,
        )

    def _post_download_single_io(
        self,
        rom_id,
        rom_detail,
        target_path,
        extract_progress,
        system,
        extract_dir_name="",
        reserved_bytes=0,
        rel_path=None,
    ):
        """Sync helper for :meth:`run` single-file — rename or unpack, then DB persist, in executor.

        Returns ``(path, error)`` as ``DownloadUnpacker.finish_single`` does; a
        zip holding a disc image is unpacked into *extract_dir_name* beside
        *target_path*, and a folder game's one file is placed at *rel_path*
        inside it.
        """
        return self._unpacker.finish_single(
            rom_id,
            rom_detail,
            target_path,
            os.path.join(os.path.dirname(target_path), extract_dir_name) if extract_dir_name else None,
            system,
            progress_callback=extract_progress,
            reserved_bytes=reserved_bytes,
            rel_path=rel_path,
        )

    async def _finalize_download_complete(self, rom_id, final_path, rom_name, platform_name):
        """Mark the queue entry completed and emit ``download_complete``.

        The shortcut is not touched: its launch options are its appId whether or
        not the game is installed, and the next start asks with the new install.
        Called from the normal success path and from the cancel handler when the
        install committed before the cancel landed (#1049).
        """
        entry = self._queue.mark_completed(rom_id)
        await self._emit(
            "download_complete",
            {
                "rom_id": rom_id,
                "rom_name": rom_name,
                "platform_name": platform_name,
                "file_path": final_path,
                "resumable": entry.get("resumable", False),
            },
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

    async def run(
        self, rom_id, rom_detail, target_path, system, file_name, control, *, platform_name: str, resume=False
    ):
        """Run one admitted download to its end, and give back what its admission registered.

        *control* is the token the admission registered for this attempt; the
        progress callback polls it, and the release at the end is gated on it
        (:meth:`DownloadsStateBox.release`). *platform_name* is the name the
        admission's platform answer carries; every frame and event of this
        download names the platform by it, because RomM's ROM answer
        (``RomSchema``) carries no ``platform_name``, and its
        ``platform_display_name`` is the custom name a user set wherever one is
        set. On *resume* the transfer appends to the partial file instead of
        restarting it.
        """
        rom_name = rom_detail.get("name", file_name)
        fetch = plan_fetch(rom_detail, file_name, self._romm_api)
        has_multiple = fetch.zipped
        # What a failure tears down: the folder this download creates for the
        # game, whether it unpacks a zip into it or places one file there.
        creates_folder = has_multiple or lands_as_folder(rom_detail)
        # Name the extract dir from the ROM's identity, not files[0] (which may be
        # an arbitrary inner asset for a nested-single folder game). Computed once
        # here and threaded to both the extraction and the cleanup so the dir a
        # failure tears down matches the dir extraction created.
        extract_dir_name = extract_dir_name_for(rom_detail, target_path, self._logger)
        progress_callback = self._queue.make_progress_callback(rom_id, rom_name, platform_name, file_name, control)
        on_meta = self._queue.make_on_meta(rom_id, rom_name, platform_name, file_name)
        extract_progress = self._queue.make_progress_callback(
            rom_id, rom_name, platform_name, file_name, phase="extracting"
        )
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
            if self._state.semaphore.locked():
                await self._queue.report_queued(
                    rom_id, rom_name, platform_name, file_name, total_bytes=fetch.size_bytes
                )

            async with self._state.semaphore:
                self._queue.mark_downloading(rom_id)
                transfer = partial(self._fetch_unless_whole, rom_id, fetch, file_name, target_path, progress_callback)

                if has_multiple:
                    # Multi-file ROM: API returns ZIP, download to temp then extract
                    await transfer(target_path + ZIP_TMP_EXT, resume=resume, on_meta=on_meta)
                    post_io_future = self._loop.run_in_executor(
                        None,
                        self._post_download_multi_io,
                        rom_id,
                        rom_detail,
                        target_path,
                        extract_progress,
                        system,
                        extract_dir_name,
                        self._state.bytes_reserved_by_others(rom_id, self._queue.entries),
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
                    await transfer(target_path + TMP_EXT, resume=resume, on_meta=on_meta)
                    post_io_future = self._loop.run_in_executor(
                        None,
                        self._post_download_single_io,
                        rom_id,
                        rom_detail,
                        target_path,
                        extract_progress,
                        system,
                        extract_dir_name,
                        self._state.bytes_reserved_by_others(rom_id, self._queue.entries),
                        _place_in_folder_at(fetch, file_name) if lands_as_folder(rom_detail) else None,
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

                await self._finalize_download_complete(rom_id, final_path, rom_name, platform_name)

        except asyncio.CancelledError:
            # The cancel/pause may have raced a committing install. Executor
            # threads run to completion, so wait for the post-IO future before
            # deciding (#1049).
            committed_path, committed = await self._reconcile_post_io(post_io_future)
            if committed:
                # The ROM IS installed — surface completed, don't tear it down.
                await self._finalize_download_complete(rom_id, committed_path, rom_name, platform_name)
                self._logger.info(f"Download cancelled after install committed; surfaced as complete: {rom_name}")
            elif control.paused:
                # PAUSE: keep the partial ``.tmp`` so the transfer can resume from
                # where it stopped. NOTHING is cleaned up.
                await self._queue.report_paused(rom_id, rom_name, platform_name, file_name, target_path)
                self._logger.info(f"Download paused: {rom_name}")
            else:
                self._queue.mark_cancelled(rom_id)
                self._unpacker.remove_partial_download(target_path, creates_folder, extract_dir_name, final_path)
                # #1017: emit a terminal frame so the frontend resets the button
                # out of its downloading state (the global cancel path was silent).
                await self._queue.report_cancelled(rom_id, rom_name, platform_name, file_name)
                self._logger.info(f"Download cancelled: {rom_name}")
            raise

        except Exception as e:
            self._queue.mark_failed(rom_id, str(e))
            self._cleanup_failed_download(e, target_path, creates_folder, extract_dir_name, final_path)
            self._logger.error(f"Download failed for {rom_name}: {e}")
            await self._emit("download_failed", failed_frame(rom_id, rom_name, platform_name, str(e)))

        finally:
            self._state.release(rom_id, control)
            self._queue.prune()

    def _cleanup_failed_download(self, error, target_path, has_multiple, extract_dir_name, final_path) -> None:
        """Remove what a failed download left behind (``DownloadUnpacker.remove_partial_download``)."""
        # Too little space to unpack keeps the download whole for a retry; nothing
        # was extracted yet, so there is nothing else to remove.
        if not isinstance(error, NotEnoughSpaceToUnpack):
            self._unpacker.remove_partial_download(target_path, has_multiple, extract_dir_name, final_path)

    async def _fetch_unless_whole(
        self, rom_id, fetch: DownloadFetch, file_name, target_path, progress_callback, partial_path, *, resume, on_meta
    ) -> None:
        """Transfer this download into *partial_path*, unless it already sits whole there.

        A zip already whole on disk — kept after a failed unpack, or paused after
        its last byte — is unpacked without a transfer (``kept_download_complete``).
        The request names the files *fetch* asks for, where it names any.
        """
        if await self._loop.run_in_executor(None, self._unpacker.kept_download_complete, target_path, fetch):
            self._logger.info(f"Unpacking the download already on disk for {file_name}")
            return
        named = {"file_ids": fetch.file_ids} if fetch.file_ids is not None else {}
        await self._loop.run_in_executor(
            None,
            partial(
                self._romm_api.download_rom_content,
                rom_id,
                file_name,
                partial_path,
                progress_callback,
                resume=resume,
                on_meta=on_meta,
                **named,
            ),
        )


def _place_in_folder_at(fetch: DownloadFetch, file_name: str) -> str:
    """Where inside a folder game's folder the one file its download fetched goes.

    Its place inside the game's folder on RomM, else its name; a payload that
    names no file leaves the download's own file name.
    """
    single = fetch.single_file
    if single is None:
        return file_name
    return single.rel_path or single.file_name or file_name
