"""Packaged firmware declarations — for a libretro core shipped without its ``.info``.

A libretro core declares its firmware in its ``.info`` and nowhere else on the
machine, so where a distribution deploys the ``.so`` and not the ``.info``,
the firmware route has nothing to read and answers that nothing could be
established. RetroDECK 0.10.10b deploys cores that way, PCEE2 among them:
its ``.so`` comes from RetroArch's nightly core bundle, while its ``.info``
set comes from a stable bundle that predates the core's entry in
libretro-core-info.

For such a core this module carries the declaration in the ``.info``'s place:
the firmware the core opens, read from its source at a pinned revision, one
citation per slot (``atlas/data/core_declarations.json``). It is world
knowledge — written nowhere on the machine — so it is packaged, versioned and
cited the way ``atlas/data/core_firmware.json`` is, and the resolver marks the
answer it feeds as ``packaged`` rather than ``read``. A published ``.info`` is
deliberately not what is packaged: libretro-core-info's ``pcee2_libretro.info``
declares ``pcsx2/resources`` required, and this build embeds what that folder
was needed for, so importing the file would import a requirement the core does
not have. Each path a published ``.info`` declares and the build does not need
is stated under ``left_out`` with its citation, so the omission is a reading
rather than a gap.

An entry is consulted only where the core's ``.info`` is **missing**. One that
is there is the machine's own declaration and is read as it stands; one that
is there and cannot be read stays unreadable, because a file that is present
and broken is a different fact from a file that was never shipped.

The ``build`` block pins the revision the citations were read at, and its
``stamp`` is the string constant the deployed binary carries for it, so the
entry can be held against the build (``tests/test_core_declarations_tripwire.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ._data import packaged_text

CORE_DECLARATIONS_SCHEMA = 1

# The two values a firmware slot's need takes — the firmware module's
# ``FIRMWARE_NEEDS``, spelled here because that module imports this one. A set
# rather than a tuple: it checks membership and publishes no vocabulary of its
# own beside the one that module exports.
_NEEDS = frozenset({"required", "optional"})


@dataclass(frozen=True, slots=True)
class PackagedFirmwareSlot:
    """One firmware path the core opens, as a ``.info`` slot would declare it.

    ``path`` is relative to the frontend's system directory, the way a
    ``firmwareN_path`` is; ``need`` is ``required`` or ``optional``; the
    citation is the source reading that establishes both.
    """

    path: str
    description: str
    need: str
    citation: str


@dataclass(frozen=True, slots=True)
class LeftOutPath:
    """A path a published ``.info`` for the core declares and this build does not need."""

    path: str
    citation: str


@dataclass(frozen=True, slots=True)
class PackagedCoreBuild:
    """The upstream revision the citations were read at, and the constant the deployed build carries for it.

    ``stamp`` is searched for in the deployed binary as raw bytes with a NUL
    on each side, so a hit is the whole string constant and never the head or
    the tail of a longer one. It has to begin with ``revision``: the two are
    one commit, spelled short and in full.
    """

    revision: str
    stamp: str
    citation: str


@dataclass(frozen=True, slots=True)
class PackagedCoreDeclaration:
    """What atlas states in the place of one core's missing ``.info``.

    ``key`` is the ``.so`` short name (``pcee2``), the spelling
    ``core_firmware.json`` and ``core_audit.json`` key on. ``systemname`` is
    what a ``.info`` for the core states, so the slots are filed through the
    same map a read ``.info`` goes through; it is the one field taken from
    upstream's packaging rather than from the core's source, and
    ``systemname_citation`` says where.
    """

    key: str
    systemname: str
    systemname_citation: str
    firmware: tuple[PackagedFirmwareSlot, ...]
    left_out: tuple[LeftOutPath, ...]
    build: PackagedCoreBuild
    provenance: str

    @property
    def stem(self) -> str:
        """The ``.so`` stem this entry describes — the key plus the suffix."""
        return f"{self.key}_libretro"


def _expect_str(value: Any, where: str) -> str:
    """A stated string, refusing the blank one as well as the empty one."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-blank string, got {value!r}")
    return value


def _exact(entry: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != keys:
        raise ValueError(f"{where}: expected exactly {'/'.join(sorted(keys))}, got {entry!r}")
    return entry


def _relative_path(value: Any, where: str) -> str:
    """A path under the system directory, the way a ``.info`` slot states one.

    Neither absolute nor climbing nor ending in a separator: the packaged
    entry states what the core composes under the frontend's system
    directory, and every one of those shapes would state something else.
    """
    path = _expect_str(value, where)
    if path.startswith("/") or path.endswith("/") or ".." in path.split("/"):
        raise ValueError(f"{where}: {path!r} is not a path relative to the system directory")
    return path


def _slot(where: str, entry: Any) -> PackagedFirmwareSlot:
    entry = _exact(entry, {"path", "description", "need", "citation"}, where)
    need = _expect_str(entry["need"], f"{where}.need")
    if need not in _NEEDS:
        raise ValueError(f"{where}.need must be one of {sorted(_NEEDS)}, got {need!r}")
    return PackagedFirmwareSlot(
        path=_relative_path(entry["path"], f"{where}.path"),
        description=_expect_str(entry["description"], f"{where}.description"),
        need=need,
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _left_out(where: str, entry: Any) -> LeftOutPath:
    entry = _exact(entry, {"path", "citation"}, where)
    return LeftOutPath(
        path=_relative_path(entry["path"], f"{where}.path"),
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _build(where: str, entry: Any) -> PackagedCoreBuild:
    entry = _exact(entry, {"revision", "stamp", "citation"}, where)
    revision = _expect_str(entry["revision"], f"{where}.revision")
    stamp = _expect_str(entry["stamp"], f"{where}.stamp")
    if not stamp.startswith(revision):
        raise ValueError(
            f"{where}: the stamp {stamp!r} does not begin with the revision {revision!r} — "
            "the two spell one commit"
        )
    return PackagedCoreBuild(
        revision=revision, stamp=stamp, citation=_expect_str(entry["citation"], f"{where}.citation")
    )


def _listed(where: str, entry: Any, *, may_be_empty: bool) -> list[Any]:
    if not isinstance(entry, list) or (not entry and not may_be_empty):
        raise ValueError(f"{where} must be a {'' if may_be_empty else 'non-empty '}list, got {entry!r}")
    return entry


def _declaration(key: str, entry: Any) -> PackagedCoreDeclaration:
    where = f"packaged core declaration {key!r}"
    entry = _exact(entry, {"systemname", "firmware", "left_out", "build", "provenance"}, where)
    systemname = _exact(entry["systemname"], {"name", "citation"}, f"{where}: systemname")
    firmware = tuple(
        _slot(f"{where}: firmware[{index}]", slot)
        for index, slot in enumerate(_listed(f"{where}: firmware", entry["firmware"], may_be_empty=False))
    )
    left_out = tuple(
        _left_out(f"{where}: left_out[{index}]", path)
        for index, path in enumerate(_listed(f"{where}: left_out", entry["left_out"], may_be_empty=True))
    )
    paths = [slot.path for slot in firmware]
    if len(set(paths)) != len(paths):
        raise ValueError(f"{where}: firmware states one path twice: {paths}")
    both = sorted(set(paths) & {path.path for path in left_out})
    if both:
        raise ValueError(f"{where}: {both} is declared and left out at once")
    provenance = _exact(entry["provenance"], {"source"}, f"{where}: provenance")
    return PackagedCoreDeclaration(
        key=key,
        systemname=_expect_str(systemname["name"], f"{where}: systemname.name"),
        systemname_citation=_expect_str(systemname["citation"], f"{where}: systemname.citation"),
        firmware=firmware,
        left_out=left_out,
        build=_build(f"{where}: build", entry["build"]),
        provenance=_expect_str(provenance["source"], f"{where}: provenance.source"),
    )


def load_core_declarations(text: str | None = None) -> tuple[PackagedCoreDeclaration, ...]:
    """Load the packaged core declarations (or *text* when supplied, for tests)."""
    if text is None:
        text = packaged_text("core_declarations.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != CORE_DECLARATIONS_SCHEMA:
        raise ValueError(
            f"core_declarations: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {CORE_DECLARATIONS_SCHEMA})"
        )
    cores = raw.get("cores", {})
    if not isinstance(cores, dict):
        raise ValueError(f"core_declarations: expected a 'cores' object, got {cores!r}")
    return tuple(_declaration(key, entry) for key, entry in cores.items())


_PACKAGED: tuple[PackagedCoreDeclaration, ...] | None = None


def packaged_core_declarations() -> tuple[PackagedCoreDeclaration, ...]:
    """Every packaged declaration, loaded once."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_core_declarations()
    return _PACKAGED


def packaged_declaration_of(stem: str) -> PackagedCoreDeclaration | None:
    """The packaged declaration for the core whose ``.so`` stem is *stem*, or ``None`` — no fuzzy matching."""
    return next((entry for entry in packaged_core_declarations() if entry.stem == stem), None)
