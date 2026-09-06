"""Generic filesystem path questions services must not ask ``os.path`` directly.

Holds the adapters for the path seams that carry no implication about which
subtree the caller is reasoning about: whether a path exists, and which
directory a path names once its symlinks are resolved. Anything that belongs to
one tree (ROMs, saves, BIOS, a recovery bundle) has its own domain-shaped store
instead.
"""

from __future__ import annotations

import os


class PathProbeAdapter:
    """Thin wrapper over ``os.path.exists`` for the ``PathExistsReader`` Protocol."""

    def exists(self, path: str) -> bool:
        return os.path.exists(path)


class ResolvedPathAdapter:
    """Thin wrapper over ``os.path.realpath`` for the ``ResolvedPathFn`` Protocol."""

    def __call__(self, path: str) -> str:
        return os.path.realpath(path)
