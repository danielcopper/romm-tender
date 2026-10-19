"""Tender's folder on disk — the one place its place is read from the settings and its folder is looked at.

Answers where the folder lies (from the live settings), what state it is in,
which drives a place may be offered on, and creates and removes the folder
itself. Which of those answers counts, and what is refused, is
:mod:`domain.tender_folder`'s.

**A drive is a mount, never a folder.** The drives are the mounts directly
under ``/run/media/<user>/``, where udisks mounts a removable drive; a folder
there that is not a mount is a drive that is not connected, and writing into
it would land on whatever lies underneath.
"""

from __future__ import annotations

import contextlib
import os
import pwd
from typing import TYPE_CHECKING, Any

from domain.tender_folder import (
    DRIVE_NOT_MOUNTED,
    FOLDER_ID_FILENAME,
    FOLDER_MISSING,
    FOLDER_NOT_WRITABLE,
    PLACE_SETTING,
    Drive,
    TenderPlace,
    read_place,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def drives_root_for_current_user() -> str:
    """``/run/media/<user>``, the folder udisks mounts this user's removable drives in."""
    return os.path.join("/run/media", pwd.getpwuid(os.getuid()).pw_name)


class TenderFolderAdapter:
    """Reads the folder's place from the live settings and looks at the folder on disk.

    Implements the ``TenderFolderStore`` Protocol structurally. *drives_root*
    is where the drives are mounted, the current user's folder under
    ``/run/media`` when none is given; *is_mount* answers whether a path is a
    mount point.
    """

    def __init__(
        self,
        *,
        settings: Mapping[str, Any],
        user_home: str,
        drives_root: str | None = None,
        is_mount: Callable[[str], bool] = os.path.ismount,
    ) -> None:
        self._settings = settings
        self._home = os.path.abspath(user_home)
        self._drives_root = None if drives_root is None else os.path.normpath(drives_root)
        self._is_mount = is_mount

    def home(self) -> str:
        """The home folder, the default place."""
        return self._home

    def drives_root(self) -> str:
        """The folder the drives are mounted in."""
        if self._drives_root is None:
            self._drives_root = drives_root_for_current_user()
        return self._drives_root

    def place(self) -> TenderPlace:
        """The place the settings name now."""
        return read_place(self._settings.get(PLACE_SETTING), self._home)

    def folder(self) -> str:
        """The folder the settings name now."""
        return self.place().folder

    def condition(self, place: TenderPlace) -> str | None:
        """Why *place*'s folder cannot be used now, or ``None``: its drive gone, the folder missing, or not writable."""
        if place.mount_point is not None and not self._is_mount(place.mount_point):
            return DRIVE_NOT_MOUNTED
        if not os.path.isdir(place.folder):
            return FOLDER_MISSING
        if not self.writable(place.folder):
            return FOLDER_NOT_WRITABLE
        return None

    def is_mount(self, path: str) -> bool:
        """Whether *path* is a mount point."""
        return self._is_mount(path)

    def resolved(self, path: str) -> str:
        """*path* with every symlink resolved, as far as it exists."""
        return os.path.realpath(path)

    def deepest_existing(self, path: str) -> str:
        """The deepest folder of *path* that exists, *path* itself included."""
        candidate = os.path.normpath(path)
        while not os.path.isdir(candidate):
            parent = os.path.dirname(candidate)
            if parent == candidate:
                break
            candidate = parent
        return candidate

    def mount_point_of(self, path: str) -> str:
        """The mount point *path* lies on, judged from its deepest existing folder."""
        candidate = os.path.realpath(self.deepest_existing(path))
        while not self._is_mount(candidate):
            parent = os.path.dirname(candidate)
            if parent == candidate:
                break
            candidate = parent
        return candidate

    def writable(self, folder: str) -> bool:
        """Whether *folder* can be written to: permitted, and not on a read-only mount."""
        try:
            read_only = bool(os.statvfs(folder).f_flag & os.ST_RDONLY)
        except OSError:
            return False
        return not read_only and os.access(folder, os.W_OK | os.X_OK)

    def create_folder(self, place: str, folder: str, folder_id: str) -> bool:
        """Create *place* and *folder* inside it as needed; answer whether *folder* was created.

        A folder created here gets the id file holding *folder_id*; one that
        was there already is left as it is. Raises ``OSError`` where a folder
        cannot be created or the id file cannot be written.
        """
        os.makedirs(place, exist_ok=True)
        try:
            os.mkdir(folder)
        except FileExistsError:
            return False
        with open(os.path.join(folder, FOLDER_ID_FILENAME), "x", encoding="utf-8") as handle:
            handle.write(folder_id + "\n")
        return True

    def remove_if_only_id(self, folder: str) -> bool:
        """Remove *folder* where it holds nothing but its id file; answer whether it was removed."""
        try:
            if os.listdir(folder) != [FOLDER_ID_FILENAME]:
                return False
            os.remove(os.path.join(folder, FOLDER_ID_FILENAME))
            os.rmdir(folder)
        except OSError:
            return False
        return True

    def drives(self) -> tuple[Drive, ...]:
        """Every writable drive mounted directly under the drives root, by name."""
        root = self.drives_root()
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return ()
        drives: list[Drive] = []
        for name in names:
            mount_point = os.path.join(root, name)
            if not self._is_mount(mount_point) or not self.writable(mount_point):
                continue
            free = 0
            with contextlib.suppress(OSError):
                stat = os.statvfs(mount_point)
                free = stat.f_bavail * stat.f_frsize
            drives.append(Drive(name=name, mount_point=mount_point, free_bytes=free))
        return tuple(drives)
