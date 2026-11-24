"""What each emulator's save files hold, as packaged knowledge — the facts the save-portability question compares.

A save answer says where an entry keeps a game's save and which files make it
up. Whether another entry can read those files is a question about their
*contents*, and nothing on the machine states it: it is read out of each
emulator's source at a pinned build, so it is world knowledge, packaged here
(``atlas/data/save_formats.json``) with a citation per fact and a pin per
emulator (#577 D5).

The knowledge is stated per emulator, never per pair. Each record labels the
file groups of that emulator's own save answer, mode by mode: which memory of
the console a file holds (:data:`SAVE_MEMORIES`), which slot, and its
**format** — an identity string two records share exactly where the sources
say the bytes are laid out alike. A pair's verdict is then computed from the
two records and the two save answers (:mod:`atlas.save_portability`), so a
system with *n* entries needs *n* records rather than a table of every pair.
The few ways one format turns into another that a source states are listed
apart, as conversions.

The method and the evidence are in ``docs/research/save-portability.md``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from ._data import packaged_text

SAVE_FORMATS_SCHEMA = 1

SaveMemory = Literal[
    "memory-card",
    "internal-backup-ram",
    "cartridge-backup-ram",
    "cartridge-ram",
    "cartridge-clock",
    "internal-storage",
]
MEMORY_MEMORY_CARD: SaveMemory = "memory-card"
"""A memory card in one of the console's card slots, which ``slot`` names."""
MEMORY_INTERNAL_BACKUP_RAM: SaveMemory = "internal-backup-ram"
"""The battery-backed save memory built into the console, such as the Saturn's 32 KiB."""
MEMORY_CARTRIDGE_BACKUP_RAM: SaveMemory = "cartridge-backup-ram"
"""A backup memory cartridge plugged into the console beside the game, such as a Saturn backup cartridge."""
MEMORY_CARTRIDGE_RAM: SaveMemory = "cartridge-ram"
"""The game cartridge's own save memory, such as a Game Boy cartridge's battery-backed RAM or EEPROM, or the EEPROM,
FRAM or flash on a DS game card."""
MEMORY_CARTRIDGE_CLOCK: SaveMemory = "cartridge-clock"
"""The game cartridge's real-time clock. Only a cartridge of a kind in :data:`CLOCK_CARTRIDGE_KINDS` has one; for
every other kind a clock part holds no game data, so nothing of the game is lost with it (#577 D16)."""
MEMORY_INTERNAL_STORAGE: SaveMemory = "internal-storage"
"""The console's own file storage a game keeps its save in as files, such as a title's data directory in the Wii's
NAND."""
# What one part of a save holds, the vocabulary of a part's ``memory``. It
# grows with the systems the records cover, and only with words a record
# states.
SAVE_MEMORIES = (
    MEMORY_MEMORY_CARD,
    MEMORY_INTERNAL_BACKUP_RAM,
    MEMORY_CARTRIDGE_BACKUP_RAM,
    MEMORY_CARTRIDGE_RAM,
    MEMORY_CARTRIDGE_CLOCK,
    MEMORY_INTERNAL_STORAGE,
)

SaveRegion = Literal["USA", "EUR", "JAP"]
SAVE_REGION_USA: SaveRegion = "USA"
"""The memory an emulator keeps for games of the North American region, such as Dolphin's ``GC/USA/Card A``."""
SAVE_REGION_EUR: SaveRegion = "EUR"
"""The memory an emulator keeps for games of the European region, such as Dolphin's ``GC/EUR/Card A``."""
SAVE_REGION_JAP: SaveRegion = "JAP"
"""The memory an emulator keeps for games of the Japanese region, such as Dolphin's ``GC/JAP/Card A``."""
# The regions an emulator keeps a memory apart for (#577 D21), the vocabulary
# of a part's ``region`` and of the ``<region>`` hole a rule's names spell,
# which matches these words alone. It grows only with regions a record names.
SAVE_REGIONS = (SAVE_REGION_USA, SAVE_REGION_EUR, SAVE_REGION_JAP)
_REGION_HOLE = "<region>"

CartridgeKind = Literal[
    "mbc2", "rom-ram", "mmm01", "mbc3-timer", "mbc3", "mbc5-rumble", "mbc6", "mbc7", "tpp1", "pocket-camera", "tama5", "huc3", "huc1"
]
CARTRIDGE_MBC2: CartridgeKind = "mbc2"
"""A Game Boy MBC2 cartridge (header byte 0x147 of 0x05 or 0x06): 512 four-bit cells of RAM built into the mapper."""
CARTRIDGE_ROM_RAM: CartridgeKind = "rom-ram"
"""A Game Boy cartridge with RAM and no mapper (header byte 0x147 of 0x08 or 0x09)."""
CARTRIDGE_MMM01: CartridgeKind = "mmm01"
"""A Game Boy MMM01 multi-game cartridge (header byte 0x147 of 0x0B, 0x0C or 0x0D)."""
CARTRIDGE_MBC3_TIMER: CartridgeKind = "mbc3-timer"
"""A Game Boy MBC3 cartridge with its clock (header byte 0x147 of 0x0F or 0x10)."""
CARTRIDGE_MBC3: CartridgeKind = "mbc3"
"""A Game Boy MBC3 cartridge without a clock (header byte 0x147 of 0x11, 0x12 or 0x13)."""
CARTRIDGE_MBC5_RUMBLE: CartridgeKind = "mbc5-rumble"
"""A Game Boy MBC5 cartridge with a rumble motor (header byte 0x147 of 0x1C, 0x1D or 0x1E), whose RAM bank select
carries the motor bit."""
CARTRIDGE_MBC6: CartridgeKind = "mbc6"
"""A Game Boy MBC6 cartridge (header byte 0x147 of 0x20): RAM beside flash memory the game writes."""
CARTRIDGE_MBC7: CartridgeKind = "mbc7"
"""A Game Boy MBC7 cartridge (header byte 0x147 of 0x22): an EEPROM of 16-bit words instead of RAM."""
CARTRIDGE_TPP1: CartridgeKind = "tpp1"
"""A Game Boy TPP1 cartridge (header byte 0x147 of 0xBC with the TPP1 signature), whose header may declare a clock."""
CARTRIDGE_POCKET_CAMERA: CartridgeKind = "pocket-camera"
"""The Game Boy Camera (header byte 0x147 of 0xFC): 128 KiB of RAM holding its photos."""
CARTRIDGE_TAMA5: CartridgeKind = "tama5"
"""A Game Boy Bandai TAMA5 cartridge (header byte 0x147 of 0xFD): a small memory and a clock behind the mapper."""
CARTRIDGE_HUC3: CartridgeKind = "huc3"
"""A Game Boy HuC3 cartridge (header byte 0x147 of 0xFE): RAM and a clock behind Hudson's mapper."""
CARTRIDGE_HUC1: CartridgeKind = "huc1"
"""A Game Boy HuC1 cartridge (header byte 0x147 of 0xFF): RAM behind Hudson's infrared mapper."""
# The kinds of cartridge a save-format record tells apart where a part's
# layout, or whether an emulator keeps the part at all, differs between them
# (#577 D15) — the vocabulary of a rule's ``kinds``, a record's ``not_run`` and
# the ``cartridges`` a part's caveat states, in header-byte order. It grows only
# with kinds a record names. The header bytes are those Gambatte's loader
# switches on (libgambatte/src/mem/cartridge.cpp:552-586 at gambatte-libretro
# 6924c76) and, for TPP1, SameBoy's signature check (Core/mbc.c:181-186 at
# libretro/SameBoy be2c352). atlas does not read a cartridge's kind (#768 is
# that question).
CARTRIDGE_KINDS = (
    CARTRIDGE_MBC2,
    CARTRIDGE_ROM_RAM,
    CARTRIDGE_MMM01,
    CARTRIDGE_MBC3_TIMER,
    CARTRIDGE_MBC3,
    CARTRIDGE_MBC5_RUMBLE,
    CARTRIDGE_MBC6,
    CARTRIDGE_MBC7,
    CARTRIDGE_TPP1,
    CARTRIDGE_POCKET_CAMERA,
    CARTRIDGE_TAMA5,
    CARTRIDGE_HUC3,
    CARTRIDGE_HUC1,
)

# A write through a mirror of a small RAM lands on the RAM's own address, as on
# the cartridge; is kept at its window offset, past the RAM's own size; or is
# not kept at all.
MirrorHandling = Literal["folds", "stores-unfolded", "drops"]
MIRRORS_FOLD: MirrorHandling = "folds"
MIRRORS_STORED_UNFOLDED: MirrorHandling = "stores-unfolded"
MIRRORS_DROPPED: MirrorHandling = "drops"
# How an emulator handles a write through a mirror of a cartridge RAM smaller
# than its window (#577 D18), the vocabulary of a record's ``mirrors``, which
# no answer serializes. Two emulators that handle it differently keep a game
# that writes through a mirror at different offsets, so a kind every cartridge
# of which is that small is not established between them.
MIRROR_HANDLINGS = (MIRRORS_FOLD, MIRRORS_STORED_UNFOLDED, MIRRORS_DROPPED)

SaveLengthRule = Literal["rom-list", "written-extent"]
LENGTH_ROM_LIST: SaveLengthRule = "rom-list"
"""The save is as long as the save type a ROM list built into the emulator gives the game's code, as a melonDS build
sizes a DS card's save; the list differs between builds, so the build's revision is stated beside it."""
LENGTH_WRITTEN_EXTENT: SaveLengthRule = "written-extent"
"""The save grows as far as the game writes, padded to the emulator's next size, and the file states its own length,
as DeSmuME keeps a DS card's save in its .dsv."""
# What decides how long an emulator keeps a part per game (#577 D19), the
# vocabulary of a record's ``length.rule`` and of the ``source_length`` and
# ``target_length`` a part's caveat states. It grows only with rules a record
# names.
SAVE_LENGTH_RULES = (LENGTH_ROM_LIST, LENGTH_WRITTEN_EXTENT)

# The kinds that can carry a clock. A cartridge-clock part holds game data for
# these alone (#577 D16).
CLOCK_CARTRIDGE_KINDS = (CARTRIDGE_MBC3_TIMER, CARTRIDGE_TPP1, CARTRIDGE_TAMA5, CARTRIDGE_HUC3)

# The rules a conversion can be stated by as data (#577 D4), where a source
# pins one as plain as a length. None does yet: every conversion the records
# carry is one a program performs without a rule a client could apply, so it
# is stated as convertible with its citation and no rule. DeSmuME's 122-byte
# .dsv footer is one a source pins, and it becomes a rule once a verdict uses
# it, which waits for the game code to be read (#577 D19).
CONVERSION_RULES: tuple[str, ...] = ()

# What a pinned build string names, which the answer's sources say beside a
# confirmed build (#577 D12): a commit, a release, or a channel whose name
# follows the newest build.
_PIN_KINDS = ("commit", "release", "channel")
_KINDS = ("libretro", "standalone")

# A file name's holes: ``<rom_stem>``, ``<save_id>`` and the rest stand for
# whatever the save answer filled in, or for the hole itself where it filled
# nothing.
_HOLE = re.compile(r"<[a-z_]+>")


@dataclass(frozen=True, slots=True)
class FormatBuild:
    """The build one emulator's records were read at, as the machine names it.

    ``pins`` are the strings the machine states for that build — a core's
    ``library_version``, a standalone emulator's RetroDECK ``component_version``
    — and a build is confirmed by an exact match of one of them (#577 D5, D12).
    ``names`` says what such a string names: ``commit``, ``release`` or
    ``channel``. ``revision`` is the commit the citations were read at.
    """

    pins: tuple[str, ...]
    names: str
    revision: str
    citation: str


@dataclass(frozen=True, slots=True)
class SaveLength:
    """What decides how long an emulator keeps a part, and the citation that says so (#577 D19).

    A DS game card's save is as long as the emulator sizes the card's chip,
    and each emulator decides that per game its own way: a melonDS build by a
    ROM list of its own (:data:`LENGTH_ROM_LIST`, at the ``revision`` whose
    list it is), DeSmuME by how far the game writes
    (:data:`LENGTH_WRITTEN_EXTENT`, with no revision). Two lengths whose rule
    and revision agree size every game's save alike; any other two may size one
    game differently, which atlas cannot tell without the game code, so the
    part answers ``unestablished``.
    """

    rule: str
    revision: str | None
    citation: str

    def agrees_with(self, other: SaveLength) -> bool:
        """Whether *other* sizes every game's save as this one does."""
        return (self.rule, self.revision) == (other.rule, other.revision)


@dataclass(frozen=True, slots=True)
class UnreadKinds:
    """Cartridge kinds an emulator runs whose layout for this part no read establishes, and the citation saying why.

    A verdict for such a kind would be a guess, so the portability answer
    judges it ``unestablished`` (#577 D15) rather than letting it fall into
    every other kind's verdict.
    """

    kinds: tuple[str, ...]
    citation: str


@dataclass(frozen=True, slots=True)
class PartRule:
    """How one part of an emulator's save is told apart in its save answer, and what it holds.

    A file of the answer is this part where its name matches one of ``names`` —
    templates whose holes match whatever the answer filled in — or, where the
    answer's readings state ``reading``, where its name is that reading's file
    name. ``modes`` is a pattern over the answer's live mode: the rule speaks
    only where the mode matches it, and everywhere when it is ``None``. A
    ``folder`` rule matches a group whose names are not stated and whose
    directory carries the name instead — PCSX2's folder card — by the
    directory's trailing segments, as many as the name spells (Dolphin's
    ``<region>/Card A``). A ``<region>`` hole matches only a word of
    :data:`SAVE_REGIONS`, and the word it matched is the part's region
    (#577 D21). Where the save answer states the slots that read a group, a
    rule with a ``slot`` claims it only where its slot is one of them
    (#577 D23).

    ``kinds`` states, for a cartridge kind (:data:`CARTRIDGE_KINDS`) whose part
    this emulator keeps differently, the format it keeps it in — or ``None``
    where it keeps no such part for that kind although it runs the cartridge
    (mGBA's HuC3 clock). ``format`` is the format for every kind ``kinds`` does
    not name, and ``None`` where the part exists for the named kinds alone (a
    clock only MBC3 cartridges get).
    """

    memory: str
    slot: str | None
    format: str | None
    names: tuple[str, ...]
    citation: str
    modes: re.Pattern[str] | None = None
    reading: str | None = None
    folder: bool = False
    frontend: bool = False
    """The file is RetroArch's: the core hands it over through a memory id and RetroArch writes and loads it, so
    RetroArch's own ``save_file_compression`` applies to it."""
    kinds: tuple[tuple[str, str | None], ...] = ()
    unread: tuple[UnreadKinds, ...] = ()
    """The kinds whose layout for this part no read establishes, each list with its citation."""
    length: SaveLength | None = None
    """What decides the part's length per game, where that differs between emulators of the system; ``None`` where
    the records of the system state none."""

    def unread_for(self, kind: str | None) -> str | None:
        """The citation saying the layout for *kind* is not read, or ``None`` where it is."""
        return next((group.citation for group in self.unread if kind in group.kinds), None)

    def format_for(self, kind: str | None) -> str | None:
        """The format this part takes for a cartridge of *kind* — ``None`` for every kind ``kinds`` does not name —
        or ``None`` where it holds no such part for that kind."""
        return dict(self.kinds).get(kind, self.format) if kind is not None else self.format

    def applies(self, mode: str | None) -> bool:
        """Whether this rule speaks for a save answer in *mode*."""
        if self.modes is None:
            return True
        return mode is not None and self.modes.search(mode) is not None

    def specificity(self, name: str, readings: Mapping[str, str]) -> int | None:
        """How closely *name* matches this rule — the literal characters matched — or ``None`` where it does not."""
        claim = self.claim(name, readings)
        return None if claim is None else claim[0]

    def claim(self, name: str, readings: Mapping[str, str]) -> tuple[int, str | None] | None:
        """How closely *name* matches this rule and the region its ``<region>`` hole matched, or ``None``.

        A folder rule's *name* is the group's whole directory, matched by its
        trailing segments.
        """
        stated = readings.get(self.reading) if self.reading is not None else None
        if stated is not None:
            wanted = stated.rsplit("/", 1)[-1]
            given = name.rstrip("/").rsplit("/", 1)[-1] if self.folder else name
            return (len(wanted), None) if given == wanted else None
        best: tuple[int, str | None] | None = None
        for template in self.names:
            found = _template_match(template, name, tail=self.folder)
            if found is not None:
                literal = len(_HOLE.sub("", template))
                if best is None or literal > best[0]:
                    best = (literal, found.groupdict().get("region"))
        return best


@dataclass(frozen=True, slots=True)
class IgnoredGroup:
    """What of an emulator's save answer is no part of a game's save on this system, and why.

    Either a directory (``dir_suffix``): Kronos's ST-V files, which a Saturn
    disc never writes. Or files by name (``names``, templates as a part rule
    spells them): Beetle Saturn's ``.smpc``, the console's own clock and
    settings memory rather than a game's progress (#577 D13). The citation
    rides the answer's sources wherever the statement sets something aside.
    """

    citation: str
    dir_suffix: str | None = None
    names: tuple[str, ...] = ()

    def covers(self, directory: str) -> bool:
        """Whether *directory* is the one this statement is about."""
        return self.dir_suffix is not None and directory.rstrip("/").endswith("/" + self.dir_suffix)

    def claims(self, name: str) -> bool:
        """Whether the file *name* is one this statement sets aside."""
        return any(_template_pattern(template).fullmatch(name) is not None for template in self.names)


@dataclass(frozen=True, slots=True)
class NotRun:
    """Cartridge kinds an emulator does not run at all, and the citation that says so.

    As the source, such an emulator has no save of the kind to carry, so the
    portability answer judges none; as the target, it reads none of a save of
    that kind, so the kind is not readable (#577 D15).
    """

    kinds: tuple[str, ...]
    citation: str


@dataclass(frozen=True, slots=True)
class Mirrors:
    """How an emulator handles a write through a mirror of the small RAM of some cartridge kinds, and why (#577 D18)."""

    kinds: tuple[str, ...]
    handling: str
    citation: str


@dataclass(frozen=True, slots=True)
class RegionlessContent:
    """Content whose header names no region, so the emulator runs it in a fallback region of its own (#577 D22).

    A file whose extension, lowercased, is one of ``extensions`` boots without
    a region; which memory of a per-region part it then uses is the emulator's
    fallback region, which atlas does not read, so such a part is not
    established. The citation says how the emulator decides it.
    """

    extensions: tuple[str, ...]
    citation: str

    def covers(self, content_path: str | None) -> bool:
        """Whether *content_path* is content of this kind."""
        return content_path is not None and os.path.splitext(content_path)[1].lower() in self.extensions


@dataclass(frozen=True, slots=True)
class SystemFormats:
    """One emulator's part rules for one system, the directories that hold no part of it, and the kinds it never runs."""

    parts: tuple[PartRule, ...]
    ignore: tuple[IgnoredGroup, ...] = ()
    not_run: tuple[NotRun, ...] = ()
    mirrors: tuple[Mirrors, ...] = ()
    regionless: RegionlessContent | None = None
    """The content this emulator runs in a fallback region, where the system keeps a memory per region."""

    def mirrors_for(self, kind: str) -> Mirrors | None:
        """The statement of how this emulator handles a mirror write for *kind*, or ``None`` where none says."""
        return next((statement for statement in self.mirrors if kind in statement.kinds), None)

    def does_not_run(self, kind: str) -> NotRun | None:
        """The statement that this emulator does not run *kind*, or ``None`` where none says so."""
        return next((statement for statement in self.not_run if kind in statement.kinds), None)


@dataclass(frozen=True, slots=True)
class EmulatorFormats:
    """One emulator's save-format record: the build it was read at and its parts per system.

    ``key`` is the core's short name for a libretro core and the catalogue
    token for a standalone emulator — the keys of the save cards it labels.
    """

    key: str
    kind: str
    build: FormatBuild
    systems: Mapping[str, SystemFormats]


@dataclass(frozen=True, slots=True)
class FormatConversion:
    """A way one format becomes another that a source states, with the rule where it is plain (#577 D4)."""

    source: str
    target: str
    rule: str | None
    citation: str


@dataclass(frozen=True, slots=True)
class SaveFormats:
    """The whole packaged table: the formats, the conversions between them, and every emulator's record.

    ``compressed`` is the citation for what RetroArch's ``save_file_compression``
    does to a file it writes for a core: an RZIP stream RetroArch reads back
    and an emulator that does not load the file through RetroArch has to have
    decompressed first. ``loads_raw`` is the citation for how RetroArch loads a
    file it keeps for a core: byte for byte into the core's buffer, so a file
    laid out another way is read as the target's own layout.
    """

    formats: Mapping[str, str]
    conversions: tuple[FormatConversion, ...]
    emulators: Mapping[str, EmulatorFormats]
    compressed: str
    loads_raw: str

    def conversion(self, source: str, target: str) -> FormatConversion | None:
        """The stated conversion from *source* to *target*, or ``None``."""
        return next((c for c in self.conversions if c.source == source and c.target == target), None)


def _template_pattern(template: str) -> re.Pattern[str]:
    """*template* as a pattern: each hole matches whatever filled it, the hole's own spelling included — but for
    ``<region>``, which matches a word of :data:`SAVE_REGIONS` alone and captures it."""
    parts = _HOLE.split(template)
    holes = _HOLE.findall(template)
    pattern = re.escape(parts[0])
    for hole, part in zip(holes, parts[1:]):
        hole_pattern = f"(?P<region>{'|'.join(SAVE_REGIONS)})" if hole == _REGION_HOLE else ".+"
        pattern += hole_pattern + re.escape(part)
    return re.compile(pattern)


def _template_match(template: str, name: str, *, tail: bool) -> re.Match[str] | None:
    """*name* against *template*: whole, or by its trailing path segments where *tail*."""
    pattern = _template_pattern(template)
    if not tail:
        return pattern.fullmatch(name)
    return re.search(rf"(?:^|/)(?:{pattern.pattern})\Z", name.rstrip("/"))


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-blank string, got {value!r}")
    return value


def _exact(entry: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object, got {entry!r}")
    keys = set(entry)
    if not required <= keys or not keys <= required | optional:
        raise ValueError(
            f"{where}: expected keys {sorted(required)} and optionally {sorted(optional)}, got {sorted(keys)}"
        )
    return entry


def _build(where: str, entry: Any) -> FormatBuild:
    entry = _exact(entry, {"pins", "names", "revision", "citation"}, set(), where)
    pins = entry["pins"]
    if not isinstance(pins, list) or not pins:
        raise ValueError(f"{where}: pins must be a non-empty list, got {pins!r}")
    names = entry["names"]
    if names not in _PIN_KINDS:
        raise ValueError(f"{where}: names must be one of {_PIN_KINDS}, got {names!r}")
    return FormatBuild(
        pins=tuple(_expect_str(pin, f"{where}.pins") for pin in pins),
        names=names,
        revision=_expect_str(entry["revision"], f"{where}.revision"),
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _kinds(where: str, entry: Any, *, memory: str, formats: Mapping[str, str]) -> tuple[tuple[str, str | None], ...]:
    """A rule's per-kind formats, each kind one of :data:`CARTRIDGE_KINDS` and each format one the table states."""
    if not isinstance(entry, dict) or not entry:
        raise ValueError(f"{where}: kinds must be a non-empty object, got {entry!r}")
    for kind, value in entry.items():
        if kind not in CARTRIDGE_KINDS:
            raise ValueError(f"{where}: kind must be one of {CARTRIDGE_KINDS}, got {kind!r}")
        if memory == MEMORY_CARTRIDGE_CLOCK and kind not in CLOCK_CARTRIDGE_KINDS:
            raise ValueError(f"{where}: a clock part names only kinds that carry a clock, got {kind!r}")
        if value is not None and value not in formats:
            raise ValueError(f"{where}: format {value!r} for {kind!r} is not one the table states")
    return tuple((kind, entry[kind]) for kind in CARTRIDGE_KINDS if kind in entry)


def _part(where: str, entry: Any, *, formats: Mapping[str, str], revision: str) -> PartRule:
    entry = _exact(
        entry,
        {"memory", "slot", "format", "names", "citation"},
        {"modes", "reading", "folder", "frontend", "kinds", "unread", "length"},
        where,
    )
    if entry["memory"] not in SAVE_MEMORIES:
        raise ValueError(f"{where}: memory must be one of {SAVE_MEMORIES}, got {entry['memory']!r}")
    slot = entry["slot"]
    if slot is not None:
        _expect_str(slot, f"{where}.slot")
    kinds = (
        _kinds(f"{where}.kinds", entry["kinds"], memory=entry["memory"], formats=formats) if "kinds" in entry else ()
    )
    if entry["format"] is None:
        # A part that exists for the named kinds alone names at least one it
        # keeps; otherwise it states nothing.
        if not any(value is not None for _, value in kinds):
            raise ValueError(f"{where}: a null format needs kinds that name a format")
    elif entry["format"] not in formats:
        raise ValueError(f"{where}: format {entry['format']!r} is not one the table states")
    names = _names(where, entry["names"])
    citation = _expect_str(entry["citation"], f"{where}.citation")
    if revision not in citation:
        raise ValueError(f"{where}: the citation does not name the revision {revision!r} it was read at")
    folder, frontend = entry.get("folder", False), entry.get("frontend", False)
    if type(folder) is not bool or type(frontend) is not bool:
        raise ValueError(f"{where}: folder and frontend must be true or false, got {folder!r}, {frontend!r}")
    unread = tuple(
        _unread(f"{where}.unread[{i}]", item, memory=entry["memory"], revision=revision)
        for i, item in enumerate(entry.get("unread", []))
    )
    both = {kind for kind, _ in kinds} & {kind for group in unread for kind in group.kinds}
    if both:
        raise ValueError(f"{where}: a kind is either stated or unread, not both: {sorted(both)}")
    modes = entry.get("modes")
    return PartRule(
        memory=entry["memory"],
        slot=slot,
        format=entry["format"],
        names=names,
        citation=citation,
        modes=None if modes is None else re.compile(_expect_str(modes, f"{where}.modes")),
        reading=None if entry.get("reading") is None else _expect_str(entry["reading"], f"{where}.reading"),
        folder=folder,
        frontend=frontend,
        kinds=kinds,
        unread=unread,
        length=_length(f"{where}.length", entry.get("length"), revision=revision),
    )


def _names(where: str, names: Any) -> tuple[str, ...]:
    """A rule's name templates: a non-empty list of strings, each spelling the ``<region>`` hole once at most."""
    if not isinstance(names, list) or not names:
        raise ValueError(f"{where}: names must be a non-empty list, got {names!r}")
    stated = tuple(_expect_str(name, f"{where}.names") for name in names)
    if any(name.count(_REGION_HOLE) > 1 for name in stated):
        raise ValueError(f"{where}: a name spells the {_REGION_HOLE} hole once at most, got {names!r}")
    return stated


def _length(where: str, entry: Any, *, revision: str) -> SaveLength | None:
    """A rule's length, or ``None`` where it states none."""
    if entry is None:
        return None
    entry = _exact(entry, {"rule", "citation"}, {"revision"}, where)
    rule = entry["rule"]
    if rule not in SAVE_LENGTH_RULES:
        raise ValueError(f"{where}: rule must be one of {SAVE_LENGTH_RULES}, got {rule!r}")
    # A ROM list differs between builds, so a length by one names the revision
    # whose list it is, and two agree only where the revision does too.
    if ("revision" in entry) != (rule == LENGTH_ROM_LIST):
        raise ValueError(f"{where}: a revision stands exactly where the rule is {LENGTH_ROM_LIST!r}")
    citation = _expect_str(entry["citation"], f"{where}.citation")
    length_revision = None if "revision" not in entry else _expect_str(entry["revision"], f"{where}.revision")
    for named in (revision, length_revision):
        if named is not None and named not in citation:
            raise ValueError(f"{where}: the citation does not name the revision {named!r} it was read at")
    return SaveLength(rule=rule, revision=length_revision, citation=citation)


def _unread(where: str, entry: Any, *, memory: str, revision: str) -> UnreadKinds:
    entry = _exact(entry, {"kinds", "citation"}, set(), where)
    kinds = entry["kinds"]
    allowed = CLOCK_CARTRIDGE_KINDS if memory == MEMORY_CARTRIDGE_CLOCK else CARTRIDGE_KINDS
    if not isinstance(kinds, list) or not kinds or any(kind not in allowed for kind in kinds):
        raise ValueError(f"{where}: kinds must be a non-empty list drawn from {allowed}, got {kinds!r}")
    citation = _expect_str(entry["citation"], f"{where}.citation")
    if revision not in citation:
        raise ValueError(f"{where}: the citation does not name the revision {revision!r} it was read at")
    return UnreadKinds(kinds=tuple(kinds), citation=citation)


def _not_run(where: str, entry: Any) -> NotRun:
    entry = _exact(entry, {"kinds", "citation"}, set(), where)
    kinds = entry["kinds"]
    if not isinstance(kinds, list) or not kinds or any(kind not in CARTRIDGE_KINDS for kind in kinds):
        raise ValueError(f"{where}: kinds must be a non-empty list drawn from {CARTRIDGE_KINDS}, got {kinds!r}")
    return NotRun(kinds=tuple(kinds), citation=_expect_str(entry["citation"], f"{where}.citation"))


def _mirrors(where: str, entry: Any) -> Mirrors:
    entry = _exact(entry, {"kinds", "handling", "citation"}, set(), where)
    kinds = entry["kinds"]
    if not isinstance(kinds, list) or not kinds or any(kind not in CARTRIDGE_KINDS for kind in kinds):
        raise ValueError(f"{where}: kinds must be a non-empty list drawn from {CARTRIDGE_KINDS}, got {kinds!r}")
    if entry["handling"] not in MIRROR_HANDLINGS:
        raise ValueError(f"{where}: handling must be one of {MIRROR_HANDLINGS}, got {entry['handling']!r}")
    return Mirrors(
        kinds=tuple(kinds), handling=entry["handling"], citation=_expect_str(entry["citation"], f"{where}.citation")
    )


def _regionless(where: str, entry: Any, *, revision: str) -> RegionlessContent | None:
    if entry is None:
        return None
    entry = _exact(entry, {"extensions", "citation"}, set(), where)
    extensions = entry["extensions"]
    if (
        not isinstance(extensions, list)
        or not extensions
        or any(not isinstance(ext, str) or not ext.startswith(".") or ext != ext.lower() for ext in extensions)
    ):
        raise ValueError(f"{where}: extensions must be a non-empty list of lowercase '.ext' strings, got {extensions!r}")
    citation = _expect_str(entry["citation"], f"{where}.citation")
    if revision not in citation:
        raise ValueError(f"{where}: the citation does not name the revision {revision!r} it was read at")
    return RegionlessContent(extensions=tuple(extensions), citation=citation)


def _ignored(where: str, entry: Any) -> IgnoredGroup:
    entry = _exact(entry, {"citation"}, {"dir_suffix", "names"}, where)
    if ("dir_suffix" in entry) == ("names" in entry):
        raise ValueError(f"{where}: an ignore states exactly one of dir_suffix and names, got {sorted(entry)}")
    names = entry.get("names", [])
    if "names" in entry and (not isinstance(names, list) or not names):
        raise ValueError(f"{where}: names must be a non-empty list, got {names!r}")
    return IgnoredGroup(
        citation=_expect_str(entry["citation"], f"{where}.citation"),
        dir_suffix=None if "dir_suffix" not in entry else _expect_str(entry["dir_suffix"], f"{where}.dir_suffix"),
        names=tuple(_expect_str(name, f"{where}.names") for name in names),
    )


def _system(where: str, entry: Any, *, formats: Mapping[str, str], revision: str) -> SystemFormats:
    entry = _exact(entry, {"parts"}, {"ignore", "not_run", "mirrors", "regionless"}, where)
    parts = entry["parts"]
    if not isinstance(parts, list) or not parts:
        raise ValueError(f"{where}: parts must be a non-empty list, got {parts!r}")
    return SystemFormats(
        parts=tuple(
            _part(f"{where}.parts[{i}]", part, formats=formats, revision=revision) for i, part in enumerate(parts)
        ),
        ignore=tuple(_ignored(f"{where}.ignore[{i}]", item) for i, item in enumerate(entry.get("ignore", []))),
        not_run=tuple(_not_run(f"{where}.not_run[{i}]", item) for i, item in enumerate(entry.get("not_run", []))),
        mirrors=tuple(_mirrors(f"{where}.mirrors[{i}]", item) for i, item in enumerate(entry.get("mirrors", []))),
        regionless=_regionless(f"{where}.regionless", entry.get("regionless"), revision=revision),
    )


def _systems(where: str, entry: Any, *, formats: Mapping[str, str], revision: str) -> dict[str, SystemFormats]:
    if not isinstance(entry, dict) or not entry:
        raise ValueError(f"{where}: systems must be a non-empty object, got {entry!r}")
    stated = {
        system: _system(f"{where}.{system}", value, formats=formats, revision=revision)
        for system, value in entry.items()
        if not (isinstance(value, dict) and "same_as" in value)
    }
    # A system whose files are exactly another's — saturnjp beside saturn —
    # says so rather than repeating the rules, so the two cannot drift.
    for system, value in entry.items():
        if isinstance(value, dict) and "same_as" in value:
            _exact(value, {"same_as"}, set(), f"{where}.{system}")
            if value["same_as"] not in stated:
                raise ValueError(f"{where}.{system}: same_as names {value['same_as']!r}, which states no rules")
            stated[system] = stated[value["same_as"]]
    return stated


def _emulator(key: str, entry: Any, *, formats: Mapping[str, str]) -> EmulatorFormats:
    where = f"save_formats: {key}"
    entry = _exact(entry, {"kind", "build", "systems"}, set(), where)
    if entry["kind"] not in _KINDS:
        raise ValueError(f"{where}: kind must be one of {_KINDS}, got {entry['kind']!r}")
    build = _build(f"{where}.build", entry["build"])
    return EmulatorFormats(
        key=key,
        kind=entry["kind"],
        build=build,
        systems=MappingProxyType(
            _systems(f"{where}.systems", entry["systems"], formats=formats, revision=build.revision)
        ),
    )


def _conversion(where: str, entry: Any, *, formats: Mapping[str, str]) -> FormatConversion:
    entry = _exact(entry, {"from", "to", "rule", "citation"}, set(), where)
    for side in ("from", "to"):
        if entry[side] not in formats:
            raise ValueError(f"{where}: {side} names {entry[side]!r}, which is not a format the table states")
    rule = entry["rule"]
    if rule is not None and rule not in CONVERSION_RULES:
        raise ValueError(f"{where}: rule must be null or one of {CONVERSION_RULES}, got {rule!r}")
    return FormatConversion(
        source=entry["from"],
        target=entry["to"],
        rule=rule,
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def load_save_formats(text: str | None = None) -> SaveFormats:
    """Load the packaged save-format table (or *text* when supplied, for tests)."""
    raw = json.loads(packaged_text("save_formats.json") if text is None else text)
    if not isinstance(raw, dict) or raw.get("schema") != SAVE_FORMATS_SCHEMA:
        raise ValueError(
            f"save_formats: unsupported schema {raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {SAVE_FORMATS_SCHEMA})"
        )
    raw = _exact(raw, {"schema", "spec", "formats", "conversions", "emulators", "retroarch"}, set(), "save_formats")
    retroarch = _exact(raw["retroarch"], {"compressed", "loads_raw"}, set(), "save_formats: retroarch")
    formats = raw["formats"]
    if not isinstance(formats, dict) or not formats:
        raise ValueError(f"save_formats: formats must be a non-empty object, got {formats!r}")
    for name, description in formats.items():
        _expect_str(description, f"save_formats: formats.{name}")
    if not isinstance(raw["emulators"], dict) or not isinstance(raw["conversions"], list):
        raise ValueError("save_formats: emulators must be an object and conversions a list")
    return SaveFormats(
        formats=MappingProxyType(dict(formats)),
        conversions=tuple(
            _conversion(f"save_formats: conversions[{i}]", entry, formats=formats)
            for i, entry in enumerate(raw["conversions"])
        ),
        emulators=MappingProxyType(
            {key: _emulator(key, entry, formats=formats) for key, entry in raw["emulators"].items()}
        ),
        compressed=_expect_str(retroarch["compressed"], "save_formats: retroarch.compressed"),
        loads_raw=_expect_str(retroarch["loads_raw"], "save_formats: retroarch.loads_raw"),
    )


_PACKAGED: SaveFormats | None = None


def save_formats() -> SaveFormats:
    """The packaged table, loaded once."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_save_formats()
    return _PACKAGED
