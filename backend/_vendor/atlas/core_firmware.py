"""How a core finds the firmware it boots — the fact a declaration cannot state.

A libretro ``.info`` lists names. It cannot say whether those names are what
the core actually opens, and the difference decides what a missing file means:
a core that opens ``scph5501.bin`` and nothing else is answered by that name,
while one that falls back to reading every file in the directory and
recognising it by its bytes may boot from a file the declaration never
mentions. Both shapes are deployed, and until this module existed an answer
reproduced the declaration for both and left a consumer to guess which it had.

So every core states :data:`FirmwareLocating` — one word for the door the
emulator uses. For a libretro core the word comes from this module's packaged
knowledge file (``atlas/data/core_firmware.json``, one entry per ``.so`` short
name, every fact carrying its own citation); a core with no entry answers
``unestablished``, which is a claim about atlas rather than about the core. For
a standalone emulator it comes from the shape of its own card
(:func:`locating_of_card`), because a card already says which door it
describes.

The word is world knowledge — written nowhere on the machine, read out of
upstream source at a pinned revision — so it is packaged, versioned and cited
the way ``atlas/data/system_firmware.json`` is, and never derived from a
reading of this installation. The method and the evidence behind each entry
are in ``docs/research/core-firmware-locating.md``.

:data:`FIRMWARE_LOCATING` carries only words something here can produce. A
value no entry states and no rule returns is a claim with no mechanism behind
it, and a consumer branching on it would be writing a branch nothing reaches.
The list therefore grows with the readings rather than ahead of them — which
costs nothing, because adding a value is a compatible change and removing one
is not. A core read to search a directory while naming no file at all would be
a new word, added then.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from ._data import packaged_text
from .standalone_firmware import StandaloneFirmwareCard

CORE_FIRMWARE_SCHEMA = 1

FirmwareLocating = Literal["by-name", "by-name-then-content", "unestablished"]

LOCATING_BY_NAME: FirmwareLocating = "by-name"
"""The core opens files by name: the names it was configured or declared with are what it reads.

A file the core does not name is not firmware to it, however right its bytes
are, and a file under a named spelling is opened whatever it holds. So the
declared set is the whole answer, and a name is the thing a consumer has to get
right.
"""
LOCATING_BY_NAME_THEN_CONTENT: FirmwareLocating = "by-name-then-content"
"""A configured name is opened first, and a search by content follows only where it cannot be.

Both doors, in that order: the named file is the normal path, and the
directory search is what happens when the name is absent or the file behind it
is one the emulator refuses. So a missing named file is not the end of the
question — the directory may still answer it — and a present one settles it
without any table being consulted.
"""
LOCATING_UNESTABLISHED: FirmwareLocating = "unestablished"
"""No source was read for this core, so which door it uses is unknown.

A claim about atlas, never about the core: something locates the firmware, and
nobody here has established what. Whatever the answer lists beside this word is
a **lower bound of unknown kind** — the declaration, faithfully reproduced,
with no statement about whether those names are what the launch opens.
"""

FIRMWARE_LOCATING = ("by-name", "by-name-then-content", "unestablished")


@dataclass(frozen=True, slots=True)
class CoreFirmwareLocatingFact:
    """One core's locating word and the source reading behind it."""

    mode: FirmwareLocating
    citation: str


@dataclass(frozen=True, slots=True)
class CoreFirmwareBuild:
    """The upstream revision the citations were read at, as the deployed build stamps it.

    ``revision`` is the short hash a core reports through
    :attr:`atlas.machine.CoreInfo.library_version`, so a deployed build can be
    held against the revision its entry was written from
    (``tests/test_core_firmware_tripwire.py``). A build that moved past the pin
    fails that check rather than letting the entry describe code the machine no
    longer runs.
    """

    revision: str
    citation: str


@dataclass(frozen=True, slots=True)
class CoreFirmwareCard:
    """What is established about one libretro core's firmware door.

    ``key`` is the ``.so`` **short** name the way the save rule cards are keyed
    (``swanstation``, not a nickname and not ``swanstation_libretro.so``) — the
    same spelling :class:`atlas.oddities.CoreCard` uses, and deliberately not
    the spelling :attr:`atlas.firmware.CoreFirmware.core_so` carries, which is
    the full basename. One name for two shapes is how a lookup ends up being
    written against the wrong one.
    """

    key: str
    locating: CoreFirmwareLocatingFact
    build: CoreFirmwareBuild
    provenance: str

    @property
    def so_name(self) -> str:
        """The ``.so`` basename this card describes — the key plus the suffix."""
        return f"{self.key}_libretro.so"


def _expect_str(value: Any, where: str) -> str:
    """A stated string, refusing the blank one as well as the empty one.

    Every string in this file is a citation or the source behind one, and a
    citation of spaces is a citation nobody can follow — it reads as filled in
    to anything that only checks for emptiness, which is the failure this table
    exists to make impossible.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-blank string, got {value!r}")
    return value


def _locating(key: str, entry: Any) -> CoreFirmwareLocatingFact:
    where = f"core firmware card {key!r}: locating"
    if not isinstance(entry, dict) or set(entry) != {"mode", "citation"}:
        raise ValueError(f"{where}: expected exactly mode/citation, got {entry!r}")
    mode = _expect_str(entry["mode"], f"{where}.mode")
    if mode not in FIRMWARE_LOCATING:
        raise ValueError(f"{where}.mode must be one of {FIRMWARE_LOCATING}, got {mode!r}")
    if mode == LOCATING_UNESTABLISHED:
        raise ValueError(
            f"{where}.mode: {LOCATING_UNESTABLISHED!r} is what a core with no entry "
            "answers — an entry that states it would cite a source for having read none"
        )
    return CoreFirmwareLocatingFact(
        mode=mode, citation=_expect_str(entry["citation"], f"{where}.citation")
    )


def _build(key: str, entry: Any) -> CoreFirmwareBuild:
    where = f"core firmware card {key!r}: build"
    if not isinstance(entry, dict) or set(entry) != {"revision", "citation"}:
        raise ValueError(f"{where}: expected exactly revision/citation, got {entry!r}")
    return CoreFirmwareBuild(
        revision=_expect_str(entry["revision"], f"{where}.revision"),
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _card(key: str, entry: Any) -> CoreFirmwareCard:
    where = f"core firmware card {key!r}"
    if not isinstance(entry, dict) or set(entry) != {"locating", "build", "provenance"}:
        raise ValueError(f"{where}: expected exactly locating/build/provenance, got {entry!r}")
    provenance = entry["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError(f"{where}: expected a 'provenance' object, got {provenance!r}")
    return CoreFirmwareCard(
        key=key,
        locating=_locating(key, entry["locating"]),
        build=_build(key, entry["build"]),
        provenance=_expect_str(provenance.get("source"), f"{where}: provenance.source"),
    )


def load_core_firmware(text: str | None = None) -> tuple[CoreFirmwareCard, ...]:
    """Load the packaged core firmware cards (or *text* when supplied, for tests)."""
    if text is None:
        text = packaged_text("core_firmware.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != CORE_FIRMWARE_SCHEMA:
        raise ValueError(
            f"core_firmware: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {CORE_FIRMWARE_SCHEMA})"
        )
    cores = raw.get("cores", {})
    if not isinstance(cores, dict):
        raise ValueError(f"core_firmware: expected a 'cores' object, got {cores!r}")
    return tuple(_card(key, entry) for key, entry in cores.items())


_PACKAGED: tuple[CoreFirmwareCard, ...] | None = None


def core_firmware_cards() -> tuple[CoreFirmwareCard, ...]:
    """Every packaged card, loaded once."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_core_firmware()
    return _PACKAGED


def _short_name(core_so: str) -> str:
    """A card key from a core name, however the caller spelled it.

    The three spellings :func:`atlas.firmware.firmware_for_core` accepts — a
    bare stem, the ``.so`` basename, a full path — and the ``_libretro`` suffix
    dropped from whichever of them carried it.
    """
    stem = core_so.rsplit("/", 1)[-1]
    if stem.endswith(".so"):
        stem = stem[: -len(".so")]
    return stem[: -len("_libretro")] if stem.endswith("_libretro") else stem


def lookup_core_firmware(core_so: str | None) -> CoreFirmwareCard | None:
    """The packaged card for one libretro core, or ``None`` — no fuzzy matching."""
    if core_so is None:
        return None
    key = _short_name(core_so)
    return next((card for card in core_firmware_cards() if card.key == key), None)


def locating_of_core(core_so: str | None) -> FirmwareLocating:
    """How this libretro core locates firmware, or ``unestablished`` where nobody read.

    Total by construction: a core with no packaged entry is not silently
    treated as though it opened the names it declares, it is stated as a core
    whose door nobody has established.
    """
    card = lookup_core_firmware(core_so)
    return LOCATING_UNESTABLISHED if card is None else card.locating.mode


def locating_of_card(card: StandaloneFirmwareCard) -> FirmwareLocating:
    """How a carded standalone emulator locates firmware, from the shape its card states.

    The shape *is* the statement, so this needs no second table. A ``search``
    card describes a directory the emulator reads and, beside it, the per-region
    keys that may name an image inside it — a name first and the search for
    whatever the names left over, which is ``by-name-then-content``. A ``files``
    or ``config_files`` card names every path the emulator probes and the
    emulator opens exactly those, which is ``by-name``.
    """
    return LOCATING_BY_NAME_THEN_CONTENT if card.search is not None else LOCATING_BY_NAME
