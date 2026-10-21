"""In-memory ``FirmwareFileStore`` implementation for service tests."""

from __future__ import annotations

import hashlib
import os

from domain.firmware_places import PlaceLook


class FakeFirmwareFileStore:
    """In-memory ``FirmwareFileStore`` for tests.

    Backed by a ``dict[str, bytes]`` so file ops are deterministic and
    free of filesystem side effects. ``remove_file`` is idempotent per
    the Protocol contract. ``exists`` reports True for any stored file
    or any path explicitly registered as a directory (via ``make_dirs``).

    Failure injection:
    - ``remove_failures`` — paths that raise ``OSError`` when removed,
      letting tests assert error handling without a real filesystem.
    - ``checksum_overrides`` — pinned hex digests returned by
      ``checksum_md5`` for specific paths, sidestepping the in-memory
      ``hashlib`` call when tests want a deterministic mismatch.
    """

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(files) if files else {}
        self.dirs: set[str] = set()
        # place -> the path the symlink there names; a link is not a file.
        self.links: dict[str, str] = {}
        self.remove_failures: set[str] = set()
        self.link_failures: set[str] = set()
        self.checksum_overrides: dict[str, str] = {}

    def exists(self, path: str) -> bool:
        if path in self.links:
            return self.exists(self.links[path])
        if path in self.files or path in self.dirs:
            return True
        prefix = path.rstrip("/") + "/"
        return any(stored.startswith(prefix) for stored in self.files)

    def remove_file(self, path: str) -> None:
        if path in self.remove_failures:
            raise OSError(f"simulated remove failure: {path}")
        self.files.pop(path, None)

    def rename(self, src: str, dst: str) -> None:
        if src not in self.files:
            raise FileNotFoundError(src)
        self.files[dst] = self.files.pop(src)

    def make_dirs(self, path: str) -> None:
        self.dirs.add(path)

    def checksum_md5(self, path: str) -> str:
        path = self.links.get(path, path)
        if path in self.checksum_overrides:
            return self.checksum_overrides[path]
        if path not in self.files:
            raise FileNotFoundError(path)
        return hashlib.md5(self.files[path]).hexdigest()

    def read_bytes(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def look(self, path: str) -> PlaceLook:
        if path in self.links:
            return PlaceLook(lexists=True, link_target=self.links[path])
        if path in self.files:
            return PlaceLook(lexists=True)
        if path in self.dirs:
            return PlaceLook(lexists=True, is_dir=True)
        return PlaceLook(lexists=False, folder_exists=self.exists(os.path.dirname(path)))

    def symlink(self, target: str, place: str) -> None:
        if place in self.link_failures:
            raise PermissionError(f"simulated link failure: {place}")
        if self.look(place).lexists:
            raise FileExistsError(place)
        self.links[place] = target

    def remove_link(self, place: str) -> None:
        if place in self.remove_failures:
            raise OSError(f"simulated remove failure: {place}")
        self.links.pop(place, None)

    def move_aside(self, src: str, dst: str) -> None:
        if src in self.links:
            self.links[dst] = self.links.pop(src)
        elif src in self.files:
            self.files[dst] = self.files.pop(src)
        else:
            raise FileNotFoundError(src)

    def is_file(self, path: str) -> bool:
        return path in self.files
