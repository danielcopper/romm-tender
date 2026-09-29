"""The staging directory an update is downloaded into before its installer runs.

Owns ``<cache root>/update/``: the tarball and its checksum file under the
names the installer checks them by, and the installer unpacked out of that
tarball. Nothing in it outlives an attempt, which is why it sits under the cache
root and is removed whole.

The installer is unpacked here rather than beside the program because the
installer removes ``<code>.new`` and moves ``<code>`` while it runs.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile

from domain.update_install import INSTALLER_MEMBER, INSTALLER_NAME
from domain.update_release import tarball_name

_HASH_BLOCK_BYTES = 1024 * 1024


class UpdateStagingAdapter:
    """The files one update attempt stages, under *directory*."""

    def __init__(self, *, directory: str) -> None:
        self._directory = directory

    def remove_all(self) -> None:
        """Remove the directory and everything in it; a missing one is nothing to do.

        A symlink under the name is removed as the link, never followed.
        """
        if os.path.islink(self._directory):
            os.remove(self._directory)
        elif os.path.lexists(self._directory):
            shutil.rmtree(self._directory)

    def prepare(self) -> None:
        """Start the directory afresh, empty.

        Removed and made again rather than emptied: an installer an earlier
        attempt started may still be reading the file it was started from, and
        a new file under the old name must not be the one it reads next.
        """
        self.remove_all()
        os.makedirs(self._directory)

    def tarball_path(self, version: str) -> str:
        """Where *version*'s tarball goes, under the asset's own name, which its checksum file names."""
        return os.path.join(self._directory, tarball_name(version))

    def sha256_of(self, path: str) -> str:
        """The lowercase sha256 hex of the file at *path*."""
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(_HASH_BLOCK_BYTES):
                digest.update(block)
        return digest.hexdigest()

    def extract_installer(self, tarball: str) -> str:
        """Copy the tarball's installer into the directory, and answer its path.

        Only the one member is read, and only its bytes: where it goes is
        chosen here, never taken from the archive, so no member name can place
        a file anywhere else. A member that is a link or a directory rather
        than a regular file is refused, as is a tarball without one.
        """
        target = os.path.join(self._directory, INSTALLER_NAME)
        with tarfile.open(tarball, "r:gz") as archive:
            member = archive.getmember(INSTALLER_MEMBER)
            if not member.isreg():
                raise ValueError(f"{INSTALLER_MEMBER} in {tarball} is not a regular file")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"{INSTALLER_MEMBER} in {tarball} could not be read")
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
        return target
