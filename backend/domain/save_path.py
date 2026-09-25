"""The local filename a server-supplied save lands under, hardened against its name.

Where a save's directory is, is the resolver's answer and not this module's;
what is here is only the server name's side of the path — sanitising it and
deriving the canonical local filename from it.

No I/O, no service/adapter/lib imports. Pure functions only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def sanitize_save_filename(name: str) -> str:
    """Reduce *name* to a safe filename component for joining onto ``saves_dir``.

    Defends path joins against compromised-server data (e.g. a malicious
    ``file_extension``) and frontend-supplied filenames (e.g. the
    ``resolve_sync_conflict`` callable parameter). Pure: no I/O, stdlib only.

    Returns the basename of *name* unchanged when it is already a single
    safe component. Raises :class:`ValueError` for inputs that cannot be
    coerced into a usable filename:

    - empty string
    - ``"."`` or ``".."``
    - any string containing a NUL byte
    - inputs whose basename is empty (e.g. trailing path separator)
    """
    if "\x00" in name:
        raise ValueError("filename contains a NUL byte")
    if name in ("", ".", ".."):
        raise ValueError(f"filename is not a valid path component: {name!r}")
    base = os.path.basename(name)
    if base in ("", ".", ".."):
        raise ValueError(f"filename has no valid basename: {name!r}")
    return base


@dataclass(frozen=True)
class LocalSaveTarget:
    """Resolved local filename for a server save with sanitization diagnostics.

    ``filename`` is the canonical on-disk name.

    ``fallback_extension`` is set to the offending value when the
    server-supplied ``file_extension`` produced an unusable filename and
    the function fell back to ``"srm"``. Service callers should log a
    warning when this is non-None.

    ``sanitized_from`` is set to the pre-sanitization filename when
    path-traversal characters were stripped (e.g. ``../etc/passwd``).
    Service callers should log a warning when this is non-None.

    Both flags are mutually exclusive: an unusable extension triggers
    fallback; a sanitizable one triggers strip-and-keep.
    """

    filename: str
    fallback_extension: str | None = None
    sanitized_from: str | None = None


def compute_local_save_target(server_save: dict[str, Any], rom_name: str) -> LocalSaveTarget:
    """The canonical local filename for a server save: ``<rom_name>.<ext>``.

    ``rom_name`` is the deterministic identity from RetroArch's
    perspective — the ROM file's basename without extension, the same
    string RetroArch uses to look up SRAM. Callers must have already
    resolved the ROM via an "installed?" check; there is no fallback to
    server-derived names because those can mismatch RetroArch's lookup
    path and silently break the sync.
    """
    ext = server_save.get("file_extension", "srm")
    target = f"{rom_name}.{ext}"
    try:
        sanitized = sanitize_save_filename(target)
    except ValueError:
        return LocalSaveTarget(f"{rom_name}.srm", fallback_extension=ext)
    if sanitized != target:
        return LocalSaveTarget(sanitized, sanitized_from=target)
    return LocalSaveTarget(sanitized)
