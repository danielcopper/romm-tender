"""What a download does once its bytes are on disk: keep the file, or unpack it into a folder.

A multi-file ROM arrives as a zip and is always unpacked. A single file is kept
as it is, unless it is a zip that holds a disc image (``holds_disc_image``,
``domain/served_shape.py``); that zip is unpacked by the same steps. An
unpacked folder gets its playlist, its launch file and its ES-DE name, and is
recorded as the install. Removing what a download that recorded nothing left
behind — its partial files and the folders it created — is here too.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.disc_formats import DISC_IMAGE_EXTENSIONS
from domain.disc_selection import one_descriptor_per_disc
from domain.disk_space import NotEnoughSpaceToUnpack, unpack_space_verdict
from domain.retrodeck_folders import EveryFolderRefused, FolderRefused, folder_of
from domain.rom_candidates import DIR
from domain.rom_files import (
    TMP_EXT,
    ZIP_TMP_EXT,
    build_m3u_content,
    detect_launch_file,
    es_de_collapse_rename,
    folder_boot_layout_root,
    is_multi_file_download,
    needs_m3u,
    ps3_folder_rename,
)
from domain.served_shape import holds_disc_image, is_zip_name, served_shapes, wrapper_prefix

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from models.adoption import ArchiveMemberInfo
    from services.protocols import (
        DownloadFileStore,
        RetroDeckFolders,
        RomInstallRecorder,
        SystemM3uSupportFn,
        SystemSupportedExtensionsFn,
    )

    ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class DownloadUnpackerConfig:
    """Frozen wiring bundle handed to ``DownloadUnpacker.__init__``."""

    download_file_store: DownloadFileStore
    retrodeck_folders: RetroDeckFolders
    install_recorder: RomInstallRecorder
    m3u_support: SystemM3uSupportFn
    system_extensions: SystemSupportedExtensionsFn
    logger: logging.Logger


class DownloadUnpacker:
    """Turns a finished transfer into a recorded install. Every method blocks.

    ``kept_download_complete`` and the unpack run off the loop; the removal of a
    partial download runs on it, in the transfer and in the cancel of a paused
    download.
    """

    def __init__(self, *, config: DownloadUnpackerConfig) -> None:
        self._download_file_store = config.download_file_store
        self._retrodeck_folders = config.retrodeck_folders
        self._install_recorder = config.install_recorder
        self._m3u_support = config.m3u_support
        self._system_extensions = config.system_extensions
        self._logger = config.logger

    def kept_download_complete(self, target_path: str, rom_detail: dict[str, Any]) -> bool:
        """Whether a zip this ROM downloads already sits whole in its partial file.

        The admission weighs a whole one without what is already on disk, and the
        transfer unpacks it without fetching it: a failed unpack keeps the downloaded zip for a
        retry, and a download paused after its last byte has nothing left to
        fetch — a resume would ask for a range past its end, which a server
        refuses. Whole means it opens as a zip: the central directory sits at its
        end, so a transfer cut short has none. A single zip must also be exactly
        the size RomM states, so that a whole zip of something else under this
        name is fetched again rather than unpacked as this game. Only a zip
        qualifies — any other single file is no archive to open.
        """
        if is_multi_file_download(rom_detail):
            path = target_path + ZIP_TMP_EXT
        elif is_zip_name(target_path):
            path = target_path + TMP_EXT
            stated = rom_detail.get("fs_size_bytes", 0)
            if not stated or self._download_file_store.file_size(path) != stated:
                return False
        else:
            return False
        return self._download_file_store.list_archive_members(path) is not None

    def finish_multi(
        self,
        rom_id: int,
        rom_detail: dict[str, Any],
        target_path: str,
        extract_dir: str,
        system: str,
        *,
        progress_callback: ProgressCallback,
        reserved_bytes: int,
    ) -> tuple[str | None, str | None]:
        """Unpack a multi-file ROM's ``.zip.tmp`` and record it; see :meth:`unpack_and_record`."""
        archive = target_path + ZIP_TMP_EXT
        return self.unpack_and_record(
            rom_id,
            rom_detail,
            archive,
            extract_dir,
            system,
            members=self._download_file_store.list_archive_members(archive),
            progress_callback=progress_callback,
            reserved_bytes=reserved_bytes,
            decode_names=True,
            strip_prefix="",
            single_zip=False,
        )

    def finish_single(
        self,
        rom_id: int,
        rom_detail: dict[str, Any],
        target_path: str,
        extract_dir: str | None,
        system: str,
        *,
        progress_callback: ProgressCallback,
        reserved_bytes: int,
    ) -> tuple[str | None, str | None]:
        """Move a single file's ``.tmp`` into place and record it, or unpack it.

        Returns ``(path, error)``. ``error`` is a string when the RomM data
        fails the ``RomInstall`` invariant — what was placed is removed and
        nothing is persisted — otherwise ``None``.

        A zip holding a disc image is unpacked instead, into *extract_dir*,
        by :meth:`unpack_and_record`, and the answer is that install's launch
        file. What sat at *target_path* — the file a replace was answered for,
        or this ROM's own earlier copy — goes only once that install is recorded,
        the moment ``os.replace`` would have swapped it out.
        """
        tmp_path = target_path + TMP_EXT
        members = None if extract_dir is None else self._members_to_unpack(rom_detail, tmp_path, extract_dir)
        if extract_dir is not None and members is not None:
            launch_file, error = self.unpack_and_record(
                rom_id,
                rom_detail,
                tmp_path,
                extract_dir,
                system,
                members=members,
                progress_callback=progress_callback,
                reserved_bytes=reserved_bytes,
                decode_names=False,
                strip_prefix=wrapper_prefix(member["name"] for member in members),
                single_zip=True,
            )
            if error is None:
                self._remove_replaced_file(target_path)
            return (launch_file, error)
        self._download_file_store.rename(tmp_path, target_path)
        return self._install_recorder.do_record_install(
            rom_id=rom_id,
            rom_detail=rom_detail,
            file_path=target_path,
            rom_dir=None,
            system=system,
            cleanup=lambda: self._download_file_store.remove_file(target_path),
        )

    def _members_to_unpack(
        self, rom_detail: dict[str, Any], archive: str, extract_dir: str
    ) -> tuple[ArchiveMemberInfo, ...] | None:
        """The members of a downloaded single zip that is to be unpacked, or ``None`` to keep it as it is.

        The zip's own central directory decides (:func:`holds_disc_image`); a
        file that does not open as a zip is kept as it is. One more case keeps
        it: RomM listed members that named no disc image, so the target gate
        never asked about the folder this would unpack into, and something
        already sits there. Nothing is unpacked over content nobody was asked
        about.
        """
        members = self._download_file_store.list_archive_members(archive)
        if members is None or not holds_disc_image(member["name"] for member in members):
            return None
        if DIR not in served_shapes(rom_detail) and self._download_file_store.exists(extract_dir):
            self._logger.warning(f"Keeping {archive} as it is: {extract_dir} is already taken")
            return None
        return members

    def _remove_replaced_file(self, target_path: str) -> None:
        """Remove what sat at a single zip's own path once its unpacked install is recorded.

        Logged, never raised: the install is already recorded, and a file that
        could not be removed is a leftover beside it rather than a failed
        download.
        """
        try:
            self._download_file_store.remove_file(target_path)
        except OSError as e:
            self._logger.warning(f"Could not remove {target_path} after unpacking it: {e}")

    def unpack_and_record(
        self,
        rom_id: int,
        rom_detail: dict[str, Any],
        archive: str,
        extract_dir: str,
        system: str,
        *,
        members: tuple[ArchiveMemberInfo, ...] | None,
        progress_callback: ProgressCallback,
        reserved_bytes: int,
        decode_names: bool,
        strip_prefix: str,
        single_zip: bool,
    ) -> tuple[str | None, str | None]:
        """Unpack the downloaded *archive* into *extract_dir* and record the folder as the install.

        The one post-processing every unpacked download goes through, a
        multi-file ROM's and a single zip's alike. Returns
        ``(launch_file, error)``; ``error`` is a string when the RomM data fails
        the ``RomInstall`` invariant — the extracted directory is removed and
        nothing is persisted — otherwise ``None``.

        *members* is what the archive's central directory lists, and decides
        whether its contents fit (*reserved_bytes* being what other downloads
        have claimed and not yet written). *decode_names* URL-decodes the
        unpacked names, which only RomM's own zip of a multi-file ROM needs: it
        writes them encoded, while a user's zip holds the names its maker chose,
        a literal ``%20`` included. *strip_prefix* is the folder a user's zip
        wraps all of its content in (``wrapper_prefix``), which is left out so
        the content lands as an unwrapped zip's would, and the ES-DE collapse
        finds its launch file at the top; *single_zip* leaves out what a user's
        zip carries beside its content (``unpacked_name``). A multi-file ROM's
        zip passes neither. Too little space raises
        :class:`NotEnoughSpaceToUnpack` before anything is written, with the
        archive left where it is for a retry. ``None`` — a file that would not
        open as a zip — skips the check, and the extraction then raises. Any
        failure from the first directory it creates onward removes that
        directory again, under whichever name it has by then; a member whose
        CRC-32 does not match is one, raised by ``zipfile`` when the member is
        read to its end.
        """
        # The removal's bound, the system's own folder, which no switch changes:
        # a download that started still extracts after RetroDECK is switched off.
        folders = self._retrodeck_folders.rom_folders([system])
        if isinstance(folders, EveryFolderRefused):
            raise folders
        folder = folder_of(folders, system)
        if isinstance(folder, FolderRefused):
            raise ValueError(f"No ROM folder to extract {extract_dir} inside: {folder.message}")
        if members is not None:
            verdict = unpack_space_verdict(
                unpacked_size=sum(member["size_bytes"] for member in members),
                free_space=self._download_file_store.disk_free(os.path.dirname(extract_dir)),
                reserved_bytes=reserved_bytes,
            )
            if not verdict.fits:
                raise NotEnoughSpaceToUnpack(verdict)
        self._download_file_store.make_dirs(extract_dir)
        try:
            # ZIP-slip protection: adapter validates members resolve within extract_dir
            # AND that extract_dir itself resolves within the system's folder.
            self._download_file_store.extract_zip(
                archive,
                extract_dir,
                folder,
                progress_callback=progress_callback,
                strip_prefix=strip_prefix,
                single_zip=single_zip,
            )
            self._download_file_store.remove_file(archive)
            if decode_names:
                self._download_file_store.decode_url_encoded_names(extract_dir)
            # Heal a folder-boot disc dump whose PS3_DISC.SFB ships .txt-suffixed,
            # before launch detection reads the layout (ADR-0019 / #1212).
            self.heal_ps3_sfb(extract_dir)
            # Whether ES-DE lists .m3u for this system (gates both M3U generation and
            # launch-file selection so a RomM-bundled .m3u is ignored on Switch/Xbox).
            m3u_supported = self._m3u_support(system)
            self.generate_m3u(extract_dir, rom_detail, m3u_supported)
            launch_file = self.detect_launch_file(extract_dir, m3u_supported)
            # ES-DE collapses a multi-file dir into one game entry only when the
            # dir is named after the launch file *including* the extension. The
            # launch file is only known after extraction (the M3U may be
            # auto-generated above), so the rename happens here, last of all the
            # filesystem work, so a later failure cleans up the renamed dir.
            extract_dir, launch_file = self._es_de_collapse(extract_dir, launch_file)
            extract_dir, launch_file = self._ps3_folder_name(extract_dir, launch_file, system)
        except BaseException:
            self._remove_extract_dir(extract_dir)
            raise

        return self._install_recorder.do_record_install(
            rom_id=rom_id,
            rom_detail=rom_detail,
            file_path=launch_file,
            rom_dir=extract_dir,
            system=system,
            cleanup=lambda: self._download_file_store.remove_tree(extract_dir),
        )

    def remove_partial_download(self, target_path, has_multiple, extract_dir_name, final_path=None):
        """Clean up partial download files. Each step is independent so one failure doesn't block others.

        Only ever called for a download that did NOT commit an install (the
        failure path and the cancel-without-commit path); a cancel that loses the
        race to a committed install routes to the transfer's
        ``_finalize_download_complete`` instead and never reaches here.

        Removes ONLY the transient transfer artifacts (``.zip.tmp`` / ``.tmp``)
        and, for a multi-file ROM, the extract dir(s) this download created. The
        bare ``target_path`` is NEVER removed: a single-file transfer writes to
        ``target_path + .tmp`` and only renames to ``target_path`` on success, so
        deleting the bare path would destroy a PRE-EXISTING install (a re-download
        that fails mid-stream) or a just-committed one (a cancel race).

        For a multi-file ROM the extract dir may have been renamed for ES-DE
        collapse after extraction. *final_path* (the resolved launch file,
        ``None`` until extraction returns it) lets cleanup tear down whichever
        of the two dir names exists — the staging name *and* the renamed dir
        (``os.path.dirname(final_path)``) — so no failure path orphans a dir.

        *extract_dir_name* is the same ROM-identity-derived base name extraction
        created the staging dir under (``_names.safe_extract_dir_name``), so
        cleanup targets exactly that dir rather than re-deriving it from a stale
        source. A single zip's is not
        removed here: whether it was unpacked at all is known only inside the
        unpack, which removes its own directory on failure, and a folder of that
        name it never created is not this download's to delete.
        """
        self.remove_partial_tmp_files(target_path)
        if has_multiple:
            staging_dir = os.path.join(os.path.dirname(target_path), extract_dir_name)
            dirs_to_remove = {staging_dir}
            if final_path:
                dirs_to_remove.add(os.path.dirname(final_path))
            for extract_dir in dirs_to_remove:
                self._remove_extract_dir(extract_dir)

    def remove_partial_tmp_files(self, target_path: str) -> None:
        """Remove the ``.zip.tmp`` and ``.tmp`` partials for *target_path*.

        Each removal is independent so one failure doesn't block the other, and a
        missing partial is not an error. The bare ``target_path`` is NEVER touched
        — only the transient transfer artifacts. Shared by
        :meth:`remove_partial_download` (running-cancel / failure) and the
        download service's paused-cancel path, which has no live task in scope
        to name the extension.
        """
        for path in (target_path + ZIP_TMP_EXT, target_path + TMP_EXT):
            try:
                self._download_file_store.remove_file(path)
            except Exception as e:
                self._logger.warning(f"Cleanup failed for {path}: {e}")

    def _remove_extract_dir(self, extract_dir: str) -> None:
        """Remove an extract directory a failed unpack left, logging rather than masking the failure."""
        try:
            self._download_file_store.remove_tree(extract_dir)
        except Exception as e:
            self._logger.warning(f"Cleanup failed for directory {extract_dir}: {e}")

    def _es_de_collapse(self, extract_dir: str, launch_file: str) -> tuple[str, str]:
        """Rename *extract_dir* after the launch file so ES-DE collapses it to one entry.

        Returns ``(rom_dir, launch_file)`` — the renamed pair when the move
        applied, or the originals unchanged. Moves the *whole* directory
        (never just the launch file — ADR-0008). Skips the move when
        ``es_de_collapse_rename`` reports no rename is needed, and on
        collision: if the target already exists the staging dir is kept and a
        warning is logged rather than clobbering or merging an existing dir.
        """
        rename = es_de_collapse_rename(extract_dir, launch_file)
        if rename is None:
            return (extract_dir, launch_file)
        new_rom_dir, new_launch_file = rename
        if self._download_file_store.exists(new_rom_dir):
            self._logger.warning(
                "ES-DE collapse rename skipped: target '%s' already exists; keeping staging dir '%s'",
                new_rom_dir,
                extract_dir,
            )
            return (extract_dir, launch_file)
        self._download_file_store.move_dir(extract_dir, new_rom_dir)
        return (new_rom_dir, new_launch_file)

    def _ps3_folder_name(self, extract_dir: str, launch_file: str, system: str) -> tuple[str, str]:
        """Name a folder-boot game's folder ``<game>.ps3``, as ES-DE scans it (:func:`ps3_folder_rename`).

        Returns ``(rom_dir, launch_file)`` — the renamed pair when the move
        applied, or the originals unchanged: for any other layout, and on
        collision, where the folder keeps its name and a warning is logged
        rather than an existing folder being clobbered or merged.
        """
        rename = ps3_folder_rename(extract_dir, launch_file, self._system_extensions(system))
        if rename is None:
            return (extract_dir, launch_file)
        new_rom_dir, new_launch_file = rename
        if self._download_file_store.exists(new_rom_dir):
            self._logger.warning(
                "PS3 folder rename skipped: target '%s' already exists; keeping '%s'", new_rom_dir, extract_dir
            )
            return (extract_dir, launch_file)
        self._download_file_store.move_dir(extract_dir, new_rom_dir)
        return (new_rom_dir, new_launch_file)

    def heal_ps3_sfb(self, extract_dir: str) -> None:
        """Copy a folder-boot dump's ``PS3_DISC.SFB.txt`` back to ``PS3_DISC.SFB``.

        RPCS3 identifies a disc-dump folder by a ``PS3_DISC.SFB`` at the game
        root; some RomM dumps ship it renamed ``PS3_DISC.SFB.txt`` and never
        boot until it is restored (verified on-device, #1212). Only for a
        folder-boot layout (:func:`folder_boot_layout_root` locates the game
        root) and only the exact ``.txt`` → real-name case: when the ``.txt``
        exists and the real name does not, a copy is written (the original is
        kept). Any other shape — real SFB already present, no ``.txt``, no
        folder-boot layout — is left untouched. Scoped to this one known dump
        quirk, not a general renamer.
        """
        all_files = self._download_file_store.scan_files_with_sizes(extract_dir)
        root = folder_boot_layout_root([path for path, _size in all_files])
        if root is None:
            return
        sfb = os.path.join(root, "PS3_DISC.SFB")
        sfb_txt = os.path.join(root, "PS3_DISC.SFB.txt")
        if self._download_file_store.exists(sfb_txt) and not self._download_file_store.exists(sfb):
            self._download_file_store.copy_file(sfb_txt, sfb)
            self._logger.info("healed PS3_DISC.SFB from .txt-suffixed dump: %s", sfb)

    def generate_m3u(self, extract_dir: str, rom_detail: dict[str, Any], m3u_supported: bool) -> None:
        """Auto-generate a game-named M3U playlist when one is warranted (see ``needs_m3u``).

        Writes ``<fs_name_no_ext>.m3u`` when the platform supports ``.m3u``, no
        M3U already exists, and the disc images warrant one: multi-disc ROMs
        (any format in ``DISC_IMAGE_EXTENSIONS``) for disc switching, or a
        single-disc bin/cue ROM so the extract dir collapses to a game-named
        entry in ES-DE. When the platform does not support ``.m3u`` (per ES-DE's
        ``es_systems.xml``), no playlist is generated.
        """
        if not m3u_supported:
            return

        all_files = self._download_file_store.scan_files_with_sizes(extract_dir)
        # A folder-boot dump (PS3 — …/PS3_GAME/USRDIR/EBOOT.BIN) launches the game
        # directory directly (ADR-0019), never a playlist — and its many payload
        # files must never be misread as "discs". Suppress the M3U outright even
        # though ES-DE lists .m3u for the platform. (#1212)
        if folder_boot_layout_root([path for path, _size in all_files]) is not None:
            return
        # Check if an M3U already exists (search recursively)
        if any(path.lower().endswith(".m3u") for path, _size in all_files):
            return

        # Collect disc files (the DISC_IMAGE_EXTENSIONS set; search recursively), one per disc.
        disc_suffixes = tuple(DISC_IMAGE_EXTENSIONS)
        disc_files = one_descriptor_per_disc(
            [os.path.relpath(path, extract_dir) for path, _size in all_files if path.lower().endswith(disc_suffixes)]
        )

        if not needs_m3u(disc_files, m3u_supported):
            return

        rom_name = rom_detail.get("fs_name_no_ext", rom_detail.get("name", "playlist"))
        m3u_path = os.path.join(extract_dir, f"{rom_name}.m3u")
        self._download_file_store.write_text_atomic(m3u_path, build_m3u_content(disc_files))
        self._logger.info(f"Auto-generated M3U playlist: {m3u_path}")

    def detect_launch_file(self, extract_dir: str, m3u_supported: bool) -> str:
        """Find the best launch file in an extracted ROM directory.

        *m3u_supported* gates whether a ``.m3u`` (including a RomM-bundled one)
        may be chosen as the launch file — see ``detect_launch_file``.
        """
        all_files = self._download_file_store.scan_files_with_sizes(extract_dir)
        result = detect_launch_file(all_files, m3u_supported)
        return result if result is not None else extract_dir
