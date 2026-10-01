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
from types import MappingProxyType
from typing import Any, Literal, Mapping

from ._data import packaged_text
from .bios_table import UNKNOWN_POLICIES
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
class CoreFirmwareRegionOption:
    """The core option that pins which console region a launch is, and what its values mean.

    ``values`` maps each value the option takes to the region token it pins,
    and to ``None`` where it pins none: SwanStation's ``Auto`` is the running
    disc's own region, which no configuration records, so an answer under it
    speaks for every region rather than for one. The tokens are the ones the
    packaged standalone cards already use (``ntsc-u``, ``ntsc-j``, ``pal``), so
    a region reads the same whichever emulator stated it.

    ``default`` is what the core declares for the option, which is what
    RetroArch answers with wherever its options file holds no entry for the
    key — so the pair is a complete reading of the option even on a machine
    whose options file has never mentioned it.
    """

    key: str
    default: str
    values: Mapping[str, str | None]
    citation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True, slots=True)
class CoreFirmwareRegionKey:
    """One region's name option: which key names the image, and what the core defaults it to."""

    region: str
    key: str
    default: str


@dataclass(frozen=True, slots=True)
class CoreFirmwareNameRoute:
    """The first door: the options that decide which name a launch opens.

    One option pins the console region and one option per region names the
    image that region's launch opens, so reading them in that order is reading
    the same two settings the core reads.
    """

    region_option: CoreFirmwareRegionOption
    region_keys: tuple[CoreFirmwareRegionKey, ...]
    citation: str


@dataclass(frozen=True, slots=True)
class CoreFirmwareSpellings:
    """One list of names a core tries in order, and the image it expects behind them.

    The order is the core's own: it opens the first name that exists and never
    looks at the rest, so the list is a sequence and not a set. ``sha1`` is the
    one image the core compares what it opened against — one digest per list,
    however many names the list holds, which is why the two live in one type.
    A core that warns and boots on a mismatch (Beetle PSX) makes that digest a
    statement about the image the names are FOR, never a condition on the
    launch.
    """

    spellings: tuple[str, ...]
    sha1: str


@dataclass(frozen=True, slots=True)
class CoreFirmwareRegionList:
    """The names one console region's launch tries, in the core's own order."""

    region: str
    names: CoreFirmwareSpellings


@dataclass(frozen=True, slots=True)
class CoreFirmwareOverrideOption:
    """The option whose list is tried ahead of the region lists, and what its values select.

    ``values`` maps each value the option takes to the list that value selects,
    and to ``None`` where it selects none — the shipped ``disabled``, under
    which no name is tried ahead of the region's. ``default`` is what the core
    declares, which is what RetroArch answers with wherever its options file
    holds no entry for the key, so the pair reads the option on a machine that
    has never mentioned it.

    A hit here ends the search: the image is region-free, so the launch opens
    it whichever region the disc turns out to be, and the region lists are
    never consulted.
    """

    key: str
    default: str
    values: Mapping[str, CoreFirmwareSpellings | None]
    citation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True, slots=True)
class CoreFirmwareSpellingRoute:
    """The one door of a core that opens names it was never configured with.

    The other shape a ``name_route`` has (:class:`CoreFirmwareNameRoute`) is
    about settings: one option pins the region and one option per region names
    the image. This one is about spellings the core carries itself. Nothing
    here is configurable but the override, so a launch's image is decided by
    the disc's region and by what is on disk — and an answer under it speaks
    for every region rather than for one.
    """

    override_option: CoreFirmwareOverrideOption
    regions: tuple[CoreFirmwareRegionList, ...]
    citation: str


@dataclass(frozen=True, slots=True)
class CoreFirmwareContentRoute:
    """The second door: the table the search recognises a directory's files by.

    ``table`` is the packaged data file name (:func:`atlas.bios_table.packaged_bios_table`).
    ``hash_scope`` and ``unknown`` are stated here as well because they are
    read out of the **core's** source and cited to it — the table states them
    too, and the resolver holds the two against each other rather than
    trusting either alone: a table regenerated from a build that changed
    either one would otherwise be read under this entry's citation.
    """

    table: str
    hash_scope: int
    unknown: str
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
    name_route: CoreFirmwareNameRoute | CoreFirmwareSpellingRoute | None = None
    """How the core decides the name it opens — one of two shapes, or nothing.

    :class:`CoreFirmwareNameRoute` is the first door of a
    ``by-name-then-content`` core: options name the image and a search follows
    where it will not load. :class:`CoreFirmwareSpellingRoute` is the whole
    door of a ``by-name`` core that tries several spellings of one image, which
    no option names and no ``.info`` can state.

    Which shape a word admits is fixed, and the two are told apart by a key
    rather than by guessing (:func:`_route_shape`): a
    ``by-name-then-content`` card carrying the spelling shape would name
    spellings its core never tries, and a ``by-name`` card carrying the
    two-door shape would state a search its core has not got. A ``by-name``
    card with no route at all is a core read to open the names it declares and
    nothing else.
    """
    content_route: CoreFirmwareContentRoute | None = None
    """The table and the reading rule behind the search that follows a name that will not load."""

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


def _region_option(where: str, entry: Any) -> CoreFirmwareRegionOption:
    where = f"{where}.region_option"
    if not isinstance(entry, dict) or set(entry) != {"key", "default", "values", "citation"}:
        raise ValueError(f"{where}: expected exactly key/default/values/citation, got {entry!r}")
    values = entry["values"]
    if not isinstance(values, dict) or not values:
        raise ValueError(f"{where}.values must be a non-empty object, got {values!r}")
    for value, region in values.items():
        if region is not None:
            _expect_str(region, f"{where}.values[{value!r}]")
    default = _expect_str(entry["default"], f"{where}.default")
    if default not in values:
        raise ValueError(
            f"{where}.default is {default!r}, which {where}.values does not map — the value the "
            "core falls back to is the one reading that must never be missing"
        )
    return CoreFirmwareRegionOption(
        key=_expect_str(entry["key"], f"{where}.key"),
        default=default,
        values=dict(values),
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _region_keys(where: str, entry: Any) -> tuple[CoreFirmwareRegionKey, ...]:
    where = f"{where}.region_keys"
    if not isinstance(entry, list) or not entry:
        raise ValueError(f"{where} must be a non-empty list, got {entry!r}")
    keys = []
    for index, row in enumerate(entry):
        at = f"{where}[{index}]"
        if not isinstance(row, dict) or set(row) != {"region", "key", "default"}:
            raise ValueError(f"{at}: expected exactly region/key/default, got {row!r}")
        keys.append(
            CoreFirmwareRegionKey(
                region=_expect_str(row["region"], f"{at}.region"),
                key=_expect_str(row["key"], f"{at}.key"),
                default=_expect_str(row["default"], f"{at}.default"),
            )
        )
    regions = [row.region for row in keys]
    if len(set(regions)) != len(regions):
        raise ValueError(f"{where}: one region, one key — {sorted(regions)} repeats one")
    return tuple(keys)


def _name_route(key: str, entry: Any) -> CoreFirmwareNameRoute:
    where = f"core firmware card {key!r}: name_route"
    if not isinstance(entry, dict) or set(entry) != {"region_option", "region_keys", "citation"}:
        raise ValueError(
            f"{where}: expected exactly region_option/region_keys/citation, got {entry!r}"
        )
    option = _region_option(where, entry["region_option"])
    keys = _region_keys(where, entry["region_keys"])
    # Every region the option can pin has to have a key that names its image,
    # or a launch this option pins is one the route can say nothing about —
    # which is the hole the two halves exist to close together.
    named = {row.region for row in keys}
    unnamed = sorted({region for region in option.values.values() if region is not None} - named)
    if unnamed:
        raise ValueError(
            f"{where}: region_option pins {unnamed} and region_keys names no key for those regions"
        )
    return CoreFirmwareNameRoute(
        region_option=option,
        region_keys=keys,
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


_SHA1_DIGITS = 40


def _spellings(where: str, entry: Any) -> CoreFirmwareSpellings:
    """One list of names and the digest behind it, held to the shape a core's list has.

    Each name is a **bare** one: the core joins it under the system directory
    itself, so a value carrying a separator would state a second directory this
    knowledge has no citation for. A repeat inside one list is refused because
    the list is an order — the same name twice is a second look at a path the
    first one settled.
    """
    if not isinstance(entry, dict) or set(entry) != {"spellings", "sha1"}:
        raise ValueError(f"{where}: expected exactly spellings/sha1, got {entry!r}")
    raw = entry["spellings"]
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{where}.spellings must be a non-empty list, got {raw!r}")
    names = [_expect_str(name, f"{where}.spellings[{index}]") for index, name in enumerate(raw)]
    for name in names:
        if "/" in name:
            raise ValueError(
                f"{where}.spellings states {name!r}, and a spelling is a bare file name — the "
                "core composes the directory itself"
            )
    if len(set(names)) != len(names):
        raise ValueError(f"{where}.spellings repeats a name: {names}")
    sha1 = _expect_str(entry["sha1"], f"{where}.sha1")
    if len(sha1) != _SHA1_DIGITS or any(digit not in "0123456789abcdef" for digit in sha1):
        raise ValueError(
            f"{where}.sha1 must be {_SHA1_DIGITS} lowercase hex digits, got {sha1!r} — one "
            "spelling of one digest, so two readings of the same image compare equal"
        )
    return CoreFirmwareSpellings(spellings=tuple(names), sha1=sha1)


def _override_option(where: str, entry: Any) -> CoreFirmwareOverrideOption:
    where = f"{where}.override_option"
    if not isinstance(entry, dict) or set(entry) != {"key", "default", "values", "citation"}:
        raise ValueError(f"{where}: expected exactly key/default/values/citation, got {entry!r}")
    raw = entry["values"]
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{where}.values must be a non-empty object, got {raw!r}")
    values = {
        value: None if names is None else _spellings(f"{where}.values[{value!r}]", names)
        for value, names in raw.items()
    }
    default = _expect_str(entry["default"], f"{where}.default")
    if default not in values:
        raise ValueError(
            f"{where}.default is {default!r}, which {where}.values does not map — the value the "
            "core falls back to is the one reading that must never be missing"
        )
    if not any(names is not None for names in values.values()):
        raise ValueError(
            f"{where}.values maps no value to a list of names, so this option selects nothing and "
            "the block states a door that never opens"
        )
    return CoreFirmwareOverrideOption(
        key=_expect_str(entry["key"], f"{where}.key"),
        default=default,
        values=values,
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _region_lists(where: str, entry: Any) -> tuple[CoreFirmwareRegionList, ...]:
    where = f"{where}.regions"
    if not isinstance(entry, list) or not entry:
        raise ValueError(f"{where} must be a non-empty list, got {entry!r}")
    lists = []
    for index, row in enumerate(entry):
        at = f"{where}[{index}]"
        if not isinstance(row, dict) or set(row) != {"region", "names"}:
            raise ValueError(f"{at}: expected exactly region/names, got {row!r}")
        lists.append(
            CoreFirmwareRegionList(
                region=_expect_str(row["region"], f"{at}.region"),
                names=_spellings(f"{at}.names", row["names"]),
            )
        )
    regions = [row.region for row in lists]
    if len(set(regions)) != len(regions):
        raise ValueError(f"{where}: one region, one list — {sorted(regions)} repeats one")
    return tuple(lists)


def _spelling_route(key: str, entry: Any) -> CoreFirmwareSpellingRoute:
    where = f"core firmware card {key!r}: name_route"
    if not isinstance(entry, dict) or set(entry) != {"override_option", "regions", "citation"}:
        raise ValueError(
            f"{where}: expected exactly override_option/regions/citation, got {entry!r}"
        )
    option = _override_option(where, entry["override_option"])
    regions = _region_lists(where, entry["regions"])
    # One name, one list. A spelling two lists hold would be a file whose
    # region — and whose expected image — depends on which list the reader
    # walked first, and the whole point of packaging the lists is that the
    # order is the core's rather than the reader's.
    stated = [
        name
        for names in (*(row.names for row in regions), *(v for v in option.values.values() if v))
        for name in names.spellings
    ]
    twice = sorted({name for name in stated if stated.count(name) > 1})
    if twice:
        raise ValueError(f"{where}: {twice} is stated by more than one list — one name, one list")
    return CoreFirmwareSpellingRoute(
        override_option=option,
        regions=regions,
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


# The key each ``name_route`` shape is recognised by. A shape is never guessed
# at from what parses: an entry states the key of the shape it means, and one
# that states both or neither is refused rather than read as the other one.
_TWO_DOOR_ROUTE_KEY = "region_option"
_SPELLING_ROUTE_KEY = "override_option"


def _route_shape(where: str, entry: Any) -> str:
    """Which of the two ``name_route`` shapes an entry states, by the key that says so."""
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected a name_route object, got {entry!r}")
    shapes = sorted({key for key in (_TWO_DOOR_ROUTE_KEY, _SPELLING_ROUTE_KEY) if key in entry})
    if len(shapes) != 1:
        raise ValueError(
            f"{where}: a name_route states exactly one of {_TWO_DOOR_ROUTE_KEY!r} and "
            f"{_SPELLING_ROUTE_KEY!r}, and this one states {shapes}"
        )
    return shapes[0]


def _content_route(key: str, entry: Any) -> CoreFirmwareContentRoute:
    where = f"core firmware card {key!r}: content_route"
    if not isinstance(entry, dict) or set(entry) != {"table", "hash_scope", "unknown", "citation"}:
        raise ValueError(
            f"{where}: expected exactly table/hash_scope/unknown/citation, got {entry!r}"
        )
    table = _expect_str(entry["table"], f"{where}.table")
    if "/" in table or not table.endswith(".json"):
        raise ValueError(
            f"{where}.table is {table!r} and must name one packaged data file — a bare "
            "<name>.json beside the others, never a path"
        )
    scope = entry["hash_scope"]
    if not isinstance(scope, int) or isinstance(scope, bool) or scope < 1:
        raise ValueError(f"{where}.hash_scope must be a positive number of bytes, got {scope!r}")
    unknown = _expect_str(entry["unknown"], f"{where}.unknown")
    if unknown not in UNKNOWN_POLICIES:
        raise ValueError(f"{where}.unknown must be one of {list(UNKNOWN_POLICIES)}, got {unknown!r}")
    return CoreFirmwareContentRoute(
        table=table,
        hash_scope=scope,
        unknown=unknown,
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _routes(
    key: str, mode: FirmwareLocating, entry: dict[str, Any]
) -> tuple[CoreFirmwareNameRoute | CoreFirmwareSpellingRoute | None, CoreFirmwareContentRoute | None]:
    """The route blocks, required and refused by the word the entry states.

    ``by-name-then-content`` is a claim about two doors, and an entry stating
    it without saying what either one reads would leave the resolver to guess
    the option keys and the table — which is the guess this file exists to
    replace. The converse refusal matters as much: a ``by-name`` entry
    carrying a search would describe a door its core has not got.

    ``by-name`` takes a route too, and only the other shape of one
    (:class:`CoreFirmwareSpellingRoute`): a core that tries several spellings
    of one image opens names no ``.info`` lists and no option holds, so the
    names are packaged here — while the search block stays refused, because
    the word still says there is no second door. A ``by-name`` entry with no
    route is the ordinary case and stays legal: the core opens the names it
    was declared with.
    """
    where = f"core firmware card {key!r}"
    stated = {block for block in ("name_route", "content_route") if block in entry}
    if mode == LOCATING_BY_NAME_THEN_CONTENT:
        if stated != {"name_route", "content_route"}:
            raise ValueError(
                f"{where}: {mode!r} names two doors and this entry states {sorted(stated)} — both "
                "name_route and content_route are what the word claims"
            )
        shape = _route_shape(f"{where}: name_route", entry["name_route"])
        if shape != _TWO_DOOR_ROUTE_KEY:
            raise ValueError(
                f"{where}: {mode!r} composes the name it opens out of its options, and this "
                f"name_route states {shape!r} — the shape of a core that carries its own names"
            )
        return _name_route(key, entry["name_route"]), _content_route(key, entry["content_route"])
    if "content_route" in entry:
        raise ValueError(f"{where}: {mode!r} opens one door and this entry states a content_route")
    if "name_route" not in entry:
        return None, None
    shape = _route_shape(f"{where}: name_route", entry["name_route"])
    if shape != _SPELLING_ROUTE_KEY:
        raise ValueError(
            f"{where}: {mode!r} opens names of its own, and this name_route states {shape!r} — "
            "the shape of a core whose options name the image and whose search follows"
        )
    return _spelling_route(key, entry["name_route"]), None


def _card(key: str, entry: Any) -> CoreFirmwareCard:
    where = f"core firmware card {key!r}"
    if not isinstance(entry, dict) or not {"locating", "build", "provenance"} <= set(entry):
        raise ValueError(f"{where}: expected at least locating/build/provenance, got {entry!r}")
    stray = sorted(set(entry) - {"locating", "build", "provenance", "name_route", "content_route"})
    if stray:
        raise ValueError(f"{where}: states {stray}, which nothing here reads")
    provenance = entry["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError(f"{where}: expected a 'provenance' object, got {provenance!r}")
    locating = _locating(key, entry["locating"])
    name_route, content_route = _routes(key, locating.mode, entry)
    return CoreFirmwareCard(
        key=key,
        locating=locating,
        build=_build(key, entry["build"]),
        provenance=_expect_str(provenance.get("source"), f"{where}: provenance.source"),
        name_route=name_route,
        content_route=content_route,
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
