"""In-memory ``ResolvedPathFn`` implementation for service tests."""

from __future__ import annotations


class FakeResolvedPath:
    """In-memory ``ResolvedPathFn`` for tests.

    Backed by a ``dict[str, str]`` of link spelling → target spelling, applied
    to a path's prefix so one entry stages a whole tree the way the real symlink
    does: ``{"/home": "/var/home"}`` resolves ``/home/deck/retrodeck`` to
    ``/var/home/deck/retrodeck``. A path no entry covers answers as itself,
    which is what ``os.path.realpath`` does for a location with no link in it —
    including one that is no longer on disk.
    """

    def __init__(self, links: dict[str, str] | None = None) -> None:
        self.links: dict[str, str] = dict(links) if links else {}

    def __call__(self, path: str) -> str:
        for link, target in self.links.items():
            if path == link:
                return target
            if path.startswith(link + "/"):
                return target + path[len(link) :]
        return path
