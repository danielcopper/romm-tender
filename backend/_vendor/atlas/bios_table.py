"""PlayStation BIOS recognition tables — what a directory holds, read by content.

Two emulators here recognise a BIOS the same way and by two different tables.
DuckStation names no file at all: it lists a directory, skips every file whose
size is none of three, and looks up what is left by hashing it against a table
compiled into its binary. SwanStation — a fork of it — opens a configured name
first and falls back to exactly that search, against its own, older table, and
over the first 512 KiB of each candidate rather than the whole file.

So one class serves both, and what differs between them is data on the table
rather than code beside it: the sizes it accepts, the **hash scope** the
emulator reads before hashing, what it does with an image no row holds, and
whether it recognises a signature-matched replacement BIOS. A table is world
knowledge under DESIGN.md's boundary rule — nothing on a running machine states
it — so each is generated from upstream's source, pinned to the revision it was
read at, and packaged under ``atlas/data/``.

The class lives here rather than beside either emulator because it belongs to
neither: a module named for DuckStation cannot own the table a libretro core
boots from without its own name saying something false.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ._data import packaged_text

# What the emulator does with a file of an accepted size whose bytes no row of
# its table holds. Both are read out of upstream source and neither is a
# default anyone may assume: they decide whether an unrecognised image is a
# thing that boots or a thing that does not.
UNKNOWN_BOOTED = "booted"
UNKNOWN_REFUSED = "refused"
UNKNOWN_POLICIES = (UNKNOWN_BOOTED, UNKNOWN_REFUSED)

# The system each size class belongs to, in the vocabulary every atlas answer
# speaks. A table's ``sizes`` block is keyed by the console whose BIOS is that
# long — ``BIOS_SIZE``, ``BIOS_SIZE_PS2`` and ``BIOS_SIZE_PS3`` in upstream's
# ``bios.h``, which the generator files under ``ps1``/``ps2``/``ps3`` — and the
# PlayStation is ``psx`` in ES-DE's names, so the first key is the one that
# needs translating at all. It sits here because it is the other half of the
# size rule below: the size decides whether a file is looked up, and the class
# it was kept at is the only thing a table ever says about which machine the
# bytes belong to. A class no name here covers stops the load
# (:func:`load_bios_table`) rather than answering an unstated system. Both
# tables are keyed this way, because both forks read the same three constants.
SIZE_CLASS_SYSTEMS = {"ps1": "psx", "ps2": "ps2", "ps3": "ps3"}

# What a row may state and what it must. ``name`` and ``region`` and ``md5``
# are the recognition itself and every table has them; ``priority`` and
# ``fast_boot_patch`` are DuckStation's own columns, which its fork's older
# rows do not carry. Which of the optional ones a table states is read off
# the table — see :func:`_row_shape`, where every row of one table must state
# the same keys — so no caller has to tell the loader which shape it is loading.
_ROW_REQUIRED = ("name", "region", "md5")
_ROW_OPTIONAL = ("priority", "fast_boot_patch")


@dataclass(frozen=True, slots=True)
class BiosImage:
    """One row of a recognition table: what these bytes are, and how much the emulator wants them.

    ``priority`` is DuckStation's own de-prioritisation and reads backwards
    from the word: **lower wins**. Launch-console images sit at 50, PS2 ones at
    100 and PAL PS2 ones at 150, each with a comment saying why
    (bios.cpp:42-45). A table that states no priority per row leaves every row
    at 0, which ranks them all alike — and that is what its emulator does: the
    fork's rows have no such column and its search takes the first
    region-valid file the directory hands it.

    ``fast_boot_patch`` is the same shape: DuckStation names the patch variant
    each image accepts, and the fork's rows do not — theirs carry a
    ``patch_compatible`` flag instead, which says whether an image may be
    patched at all rather than which variant it takes, gates patching an image
    the core has already loaded rather than recognising one, and is therefore
    not generated into the table. The empty string is a table that says nothing
    about the variant, never a row that refuses the patch.
    """

    name: str
    region: str
    md5: str
    priority: int = 0
    fast_boot_patch: str = ""


@dataclass(frozen=True, slots=True)
class BiosCandidate:
    """One file the search kept: it is of an accepted size, and this is what it is.

    ``image`` is ``None`` for bytes the table does not know — a state
    DuckStation boots anyway, with a warning, so it belongs among the
    candidates rather than outside them. What the emulator then does with it is
    the table's ``unknown`` policy, not this flag.

    ``unreadable`` keeps that state apart from the one it used to be collapsed
    into: bytes atlas could not read are not bytes the table does not know.
    The first is a read failure and settles nothing; the second is a verdict
    about content that was actually seen. Both leave ``image`` at ``None``,
    which is why the flag is here rather than being inferred from it.

    ``size`` is the accepted size this file was kept at — one of
    :attr:`BiosTable.sizes`, carried from the stat that kept it rather than
    read a second time, because a table pins no size per row and an identity
    built from one needs the size class the bytes were seen at.
    """

    path: str
    image: BiosImage | None
    size: int
    unreadable: bool = False


@dataclass(frozen=True, slots=True)
class BiosPick:
    """The file a launch would boot, and every file that ranks exactly with it."""

    chosen: BiosCandidate
    tied: tuple[BiosCandidate, ...]

    @property
    def decided(self) -> bool:
        """Did the files alone decide it? ``False`` when only directory order would."""
        return len(self.tied) == 1


class BiosTable:
    """One packaged recognition table, read-only, by content.

    ``sizes`` is the pre-filter and is part of the same rule: a file of any
    other size is skipped before a byte of it is read. Each of them is a size
    class named after a console (:data:`SIZE_CLASS_SYSTEMS`), which is what
    :meth:`system_of_size` answers from.

    ``hash_scope`` is how much of a kept file the emulator hashes, and ``None``
    is the whole of it. SwanStation reads a fixed 512 KiB out of every
    candidate whatever its length, so over a 4 MiB image its md5 is not the
    file's md5 — which is why the scope is a property of the table rather than
    of the caller. Three rows of its table hold md5s the DuckStation table does
    not carry at all, which is consistent with the scopes differing and is not
    proven by it: no row of either table was measured against the other's
    bytes.

    ``unknown`` is what the emulator does with a kept file no row holds:
    DuckStation boots it with a warning, SwanStation's search refuses it. It
    decides whether such a file can be the pick at all (:meth:`pick`).

    ``openbios`` is the replacement BIOS one of them recognises by a signature
    at a fixed offset rather than by a hash, and it is empty for a table whose
    emulator has no such door.
    """

    def __init__(
        self,
        images: tuple[BiosImage, ...],
        sizes: Mapping[str, int],
        meta: Mapping[str, Any],
        openbios: Mapping[str, Any] | None = None,
        hash_scope: int | None = None,
        unknown: str = UNKNOWN_BOOTED,
    ) -> None:
        self._images = images
        self._by_md5 = {image.md5: image for image in images}
        self._sizes = tuple(sorted(sizes.values()))
        self._systems = {size: SIZE_CLASS_SYSTEMS[name] for name, size in sizes.items()}
        self._openbios = dict(openbios or {})
        self._meta = dict(meta)
        self._hash_scope = hash_scope
        self._unknown = unknown

    @property
    def meta(self) -> dict[str, Any]:
        """The table's ``_meta`` block — upstream revision and generation date."""
        return dict(self._meta)

    @property
    def images(self) -> tuple[BiosImage, ...]:
        """Every row, in upstream's order."""
        return self._images

    @property
    def sizes(self) -> tuple[int, ...]:
        """The file sizes the search accepts, ascending."""
        return self._sizes

    @property
    def openbios(self) -> dict[str, Any]:
        """The signature-recognised replacement BIOS: its bytes and their offset."""
        return dict(self._openbios)

    @property
    def hash_scope(self) -> int | None:
        """How many leading bytes the emulator hashes, or ``None`` for the whole file."""
        return self._hash_scope

    @property
    def unknown(self) -> str:
        """What the emulator does with a kept file no row holds — one of :data:`UNKNOWN_POLICIES`."""
        return self._unknown

    def accepts_size(self, size: int | None) -> bool:
        """Would the search keep a file of this size? ``None`` (unknown) is not a yes."""
        return size is not None and size in self._sizes

    def system_of_size(self, size: int | None) -> str | None:
        """Which console's BIOS is this long, in atlas's system vocabulary.

        A table pins no size per row — it recognises an image by md5 alone —
        so the size a file was kept at is the only thing it says about which
        machine the bytes belong to, and that is what the size classes are
        (:data:`SIZE_CLASS_SYSTEMS`). ``None`` for a size no class names,
        which is every size this table's search would have skipped.
        """
        return None if size is None else self._systems.get(size)

    def identify(self, md5: str) -> BiosImage | None:
        """The row these bytes are, or ``None`` — which is not by itself a verdict on the file.

        What "not in the table" means is the ``unknown`` policy's business:
        DuckStation boots such an image and says so in a warning (``Using an
        unknown BIOS: {}``), while SwanStation's search hands one back as no
        image at all. Here it means only that no row holds these bytes.
        """
        return self._by_md5.get(md5.lower())

    def pick(self, candidates: Sequence[BiosCandidate], region: str) -> BiosPick | None:
        """Which candidate a console of *region* would boot, and what ties with it.

        Three tests in upstream's order (bios.cpp:387-395 at
        stenzek/duckstation@64655818e): a known image is never displaced by an
        unknown one, a region match is never displaced by a mismatch, and
        between two known images the lower ``priority`` number holds. A table
        that pins no priority leaves every row at 0, so for it the third test
        never separates anything — which is the fork's search exactly, where
        the first region-valid file the directory hands over wins.

        What upstream does with what is left is the part atlas cannot copy — it
        keeps one of the equally ranked files by the order ``readdir`` handed
        them over — so a tie is reported as a tie rather than resolved into a
        claim. The seam enumerates sorted, which is not that order.

        Under :data:`UNKNOWN_REFUSED` a file that was READ and holds no row is
        dropped before the ranking, because its emulator's search returns
        nothing for it; a file whose bytes did not come back is **not** dropped
        with it, because that is atlas's read failing rather than the table
        refusing, and it may well be the image the emulator boots.
        """
        ranked = sorted(self._ranking(candidates), key=lambda c: self._rank(c, region))
        if not ranked:
            return None
        best = self._rank(ranked[0], region)
        tied = tuple(c for c in ranked if self._rank(c, region) == best)
        return BiosPick(chosen=tied[0], tied=tied)

    def _ranking(self, candidates: Sequence[BiosCandidate]) -> list[BiosCandidate]:
        if self._unknown == UNKNOWN_BOOTED:
            return list(candidates)
        return [c for c in candidates if c.image is not None or c.unreadable]

    def _rank(self, candidate: BiosCandidate, region: str) -> tuple[int, int, int]:
        image = candidate.image
        if image is None:
            return (1, 1, 0)
        return (0, 0 if self.matches_region(image, region) else 1, image.priority)

    @staticmethod
    def matches_region(image: BiosImage, region: str) -> bool:
        """``any`` on either side matches — the same test in both forks.

        ``IsValidBIOSForRegion`` at bios.cpp:228-231 (stenzek/duckstation@64655818e)
        and ``IsValidHashForRegion`` at bios.cpp:118-125 (libretro/swanstation@4d309c05f),
        which reads it off the row the hash found rather than off a row handed in.
        """
        return region == "any" or image.region == "any" or image.region == region


def _image(entry: Any, index: int, stated: frozenset[str], *, source: str) -> BiosImage:
    where = f"{source}: images[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object, got {entry!r}")
    keys = frozenset(str(key) for key in entry)
    if keys != stated:
        raise ValueError(
            f"{where}: states {sorted(keys)} where the table's rows state {sorted(stated)} — "
            "one table, one row shape"
        )
    return BiosImage(
        name=str(entry["name"]),
        region=str(entry["region"]),
        md5=str(entry["md5"]).lower(),
        priority=int(entry["priority"]) if "priority" in entry else 0,
        fast_boot_patch=str(entry["fast_boot_patch"]) if "fast_boot_patch" in entry else "",
    )


def _row_shape(images: list[Any], *, source: str) -> frozenset[str]:
    """The keys every row of this table states, refusing a shape no reader could trust.

    Read off the first row and then held against every other one, so a table
    whose rows disagree about their columns is refused rather than half-read.
    The required three are the recognition itself; the optional two are one
    emulator's own columns, absent in its fork's older table, and a key outside
    both lists is a column this code does not read — which a table must not
    ship quietly, because the whole point of a packaged table is that what it
    states is what the answer uses.
    """
    first = images[0]
    if not isinstance(first, dict):
        raise ValueError(f"{source}: images[0]: expected an object, got {first!r}")
    stated = frozenset(first)
    missing = sorted(set(_ROW_REQUIRED) - stated)
    if missing:
        raise ValueError(f"{source}: images[0]: missing {missing}")
    unread = sorted(stated - set(_ROW_REQUIRED) - set(_ROW_OPTIONAL))
    if unread:
        raise ValueError(f"{source}: images[0]: states {unread}, which nothing here reads")
    return stated


def _hash_scope(raw: Any, *, source: str) -> int | None:
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise ValueError(f"{source}: hash_scope must be a positive number of bytes, got {raw!r}")
    return raw


def _unknown_policy(raw: Any, *, source: str) -> str:
    if raw is None:
        return UNKNOWN_BOOTED
    if raw not in UNKNOWN_POLICIES:
        raise ValueError(f"{source}: unknown must be one of {list(UNKNOWN_POLICIES)}, got {raw!r}")
    return raw


def load_bios_table(text: str, *, source: str, openbios: bool = False) -> BiosTable:
    """Load one recognition table from *text*, failing closed on anything it cannot read.

    *source* names the table in every message, because two of them ship and a
    refusal that named neither would send a reader to the wrong file.
    *openbios* says whether this emulator recognises a signature-matched
    replacement BIOS: where it does, the block is required, since the offset it
    states speaks in a caveat's sentence and a silent default would ship
    "offset None" rather than fail the load.
    """
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: expected an object at the top level")
    images = raw.get("images")
    if not isinstance(images, list) or not images:
        raise ValueError(f"{source}: images must be a non-empty list")
    sizes = raw.get("sizes")
    if not isinstance(sizes, dict) or not sizes:
        raise ValueError(f"{source}: sizes must be a non-empty object")
    # Loudly too, and for the reason the blocks below are loud: a size class
    # this code has no system for would answer "which machine" with silence
    # over bytes the table recognised perfectly, which is the one answer a
    # vendored newer table must not ship quietly.
    unnamed = sorted(set(sizes) - set(SIZE_CLASS_SYSTEMS))
    if unnamed:
        raise ValueError(
            f"{source}: sizes states the class(es) {unnamed} and "
            f"{sorted(SIZE_CLASS_SYSTEMS)} are the ones this atlas names a system for "
            "— the table and the code shipped out of step"
        )
    shape = _row_shape(images, source=source)
    rows = tuple(_image(entry, index, shape, source=source) for index, entry in enumerate(images))
    # Loudly, like the blocks above: both feed answers (the OpenBIOS offset
    # speaks in a caveat's sentence, the revision in its data), so a table
    # without them would ship "offset None" and an empty pin instead of
    # failing the load.
    block = raw.get("openbios")
    if openbios and (not isinstance(block, dict) or {"signature", "offset"} - set(block)):
        raise ValueError(f"{source}: openbios must state signature and offset")
    meta = raw.get("_meta")
    if not isinstance(meta, dict) or "revision" not in meta:
        raise ValueError(f"{source}: _meta must state the upstream revision")
    return BiosTable(
        images=rows,
        sizes={name: int(size) for name, size in sizes.items()},
        meta=meta,
        openbios=block if isinstance(block, dict) else None,
        hash_scope=_hash_scope(raw.get("hash_scope"), source=source),
        unknown=_unknown_policy(raw.get("unknown"), source=source),
    )


_PACKAGED: dict[tuple[str, bool], BiosTable] = {}


def packaged_bios_table(name: str, *, openbios: bool = False) -> BiosTable:
    """One packaged table by its data file name, loaded once.

    The name is data rather than a symbol because the SwanStation route learns
    which table its core reads from that core's own knowledge entry
    (``atlas/data/core_firmware.json``), and a lookup written against a symbol
    would make adding a table an edit to the route as well as to the knowledge.
    """
    key = (name, openbios)
    if key not in _PACKAGED:
        _PACKAGED[key] = load_bios_table(
            packaged_text(name), source=name.removesuffix(".json"), openbios=openbios
        )
    return _PACKAGED[key]
