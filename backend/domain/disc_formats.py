"""Disc-image container formats — the irreducible hardcoded disc set.

The file extensions that denote a launchable disc *image* (a single-disc
container the emulator opens directly), as opposed to a sidecar owned by
another file (``.bin``, referenced by its ``.cue``) or a playlist that
points at several discs (``.m3u``). This knowledge is format-semantic and
emulator-independent: it answers "is this file shape a disc image?", which
``es_systems.xml`` cannot — that file is a flat per-system accept-list with
no per-token role metadata, so it can say a system *accepts* ``.cue`` but
never that ``.cue`` is a disc while ``.bin`` is its sidecar.

Membership here is the disc *unit* itself: a single-file image, or the
descriptor of a multi-track set — never the track files a descriptor
references (``.bin``, ``.raw``, ``.img``, ``.sub``, ``.mdf``). The live
per-system capability list (which of these a given emulator can actually
launch) is read from es_systems and intersected with this set at enumeration
time — it is NOT hardcoded here. See ADR-0014.

No I/O, no service/adapter/lib imports. Constants only.
"""

from __future__ import annotations

DISC_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".cue",
        ".chd",
        ".iso",
        ".gdi",
        ".cdi",
        ".ccd",
        ".toc",
        ".mds",
        ".gcm",
        ".rvz",
        ".wbfs",
        ".wia",
        ".gcz",
        ".ciso",
        ".cso",
        ".pbp",
    }
)

# The descriptors of a multi-track disc (GLOSSARY.md, "Disc"), in the order one
# is preferred where two describe the same disc — a ``.cue`` and a ``.ccd``
# beside one ``.img``.
TRACK_DESCRIPTORS: tuple[str, ...] = (".cue", ".gdi", ".ccd", ".toc", ".mds")
