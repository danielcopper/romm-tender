"""Filesystem adapter for firmware/BIOS file operations.

Owns the raw POSIX calls used by FirmwareService to manage Tender's firmware
store and the links into emulator sources. Path construction, which link goes
where, and download orchestration remain a service concern; this adapter
exposes only the I/O seams declared by ``services.protocols.FirmwareFileStore``.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import pathlib
import shutil
import stat

from domain.firmware_places import PlaceLook

_MD5_CHUNK_SIZE = 8192

# A hardlink these refuse is one the filesystem cannot make here — another
# filesystem, or one without hardlinks — and the move copies instead.
_LINK_UNSUPPORTED = frozenset({errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK})


class FirmwareFileAdapter:
    """Synchronous filesystem operations for firmware/BIOS files.

    Implements the ``FirmwareFileStore`` Protocol. Methods are
    synchronous — services that call from an async context offload via
    ``loop.run_in_executor``.
    """

    def exists(self, path: str) -> bool:
        """Return True when *path* refers to an existing file or directory."""
        return os.path.exists(path)

    def remove_file(self, path: str) -> None:
        """Delete *path*. Idempotent: a missing file is not an error."""
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)

    def rename(self, src: str, dst: str) -> None:
        """Atomically rename *src* to *dst*, replacing any existing file at *dst*."""
        os.replace(src, dst)

    def make_dirs(self, path: str) -> None:
        """Create *path* and any missing parents. Idempotent."""
        os.makedirs(path, exist_ok=True)

    def checksum_md5(self, path: str) -> str:
        """Return the hex-encoded MD5 digest of *path*'s contents.

        Streams the file in fixed-size chunks so memory use stays
        bounded for large firmware blobs. Non-security use: MD5 here
        matches the digest published alongside the firmware download
        (RomM server / BIOS registry) to verify the file arrived intact.
        """
        h = hashlib.md5(usedforsecurity=False)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_MD5_CHUNK_SIZE), b""):
                h.update(chunk)
        return h.hexdigest()

    def read_bytes(self, path: str) -> bytes:
        """Return the contents of *path* as raw bytes."""
        return pathlib.Path(path).read_bytes()

    def look(self, path: str) -> PlaceLook:
        """What lies at *path* itself, a link not followed, and whether the folder it lies in is there."""
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            return PlaceLook(lexists=False, folder_exists=os.path.isdir(os.path.dirname(path)))
        target = None
        if stat.S_ISLNK(mode):
            named = os.readlink(path)
            target = named if os.path.isabs(named) else os.path.normpath(os.path.join(os.path.dirname(path), named))
        return PlaceLook(lexists=True, link_target=target, is_dir=stat.S_ISDIR(mode))

    def symlink(self, target: str, place: str) -> None:
        """Create a symlink at *place* naming *target*; raises ``FileExistsError`` where anything lies there."""
        os.symlink(target, place)

    def remove_link(self, place: str) -> None:
        """Remove the symlink at *place*; a place that holds no symlink is left as it is."""
        if os.path.islink(place):
            os.unlink(place)

    def move_aside(self, src: str, dst: str) -> None:
        """Move the file or link at *src* to *dst*, creating *dst*'s folders; a link moves as itself.

        Never over anything: *dst* is claimed by creating it — a hardlink, a
        symlink, or a file opened exclusively — which raises
        ``FileExistsError`` where something lies there by then, where a rename
        would replace it. Where the filesystem makes no hardlink to *dst*, the
        content is copied into the claimed name and flushed to disk with its
        folder before *src* goes, so a crash leaves the file whole at one name
        at least; where the copy fails, the claimed name is removed again and
        *src* stays. Where removing *src* fails after that, the error is raised
        with the file standing at both names.
        """
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
        else:
            try:
                os.link(src, dst)
            except OSError as exc:
                if exc.errno not in _LINK_UNSUPPORTED:
                    raise
                _copy_into_claimed(src, dst)
        os.unlink(src)


def _copy_into_claimed(src: str, dst: str) -> None:
    """Copy *src* into *dst*, claimed exclusively, and flush both to disk; remove the claim where it fails."""
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as source:
            shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
        shutil.copystat(src, dst)
        folder = os.open(os.path.dirname(dst), os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(dst)
        raise
