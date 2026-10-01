"""Atlas firmware adapters — the seam through which firmware questions reach the resolver.

The single place the vendored `emu-atlas <https://github.com/danielcopper/emu-atlas>`_
resolver is asked what the installed emulators want. Services see a
:class:`domain.firmware_wants.FirmwareCatalogue` and never an atlas type — which
is not a stylistic choice: ``domain/`` may not import ``_vendor`` at all (the
``domain-stdlib-only`` contract), so the vocabulary and the resolver have to
meet at an adapter.

Two properties of the resolver decide this module's shape:

- **It raises on its own invariant violations** rather than returning a
  degraded answer — a ``ValueError`` out of an answer's ``__post_init__``, a
  strict packaged-data loader — and promises nothing in writing about not
  raising. So every call is wrapped, and a failure becomes a catalogue with no
  placements and ``resolved`` clear. That reads downstream as "nothing could be
  established", never as "nothing is needed": the second would clear a real
  BIOS warning off a game that cannot launch without the file.
- **It never logs.** Caveats are its whole degradation channel, and their
  ``code`` is the stable half of that contract while ``message`` is prose that
  may change freely. The codes are carried on the catalogue and traced through
  the injected debug logger; nothing here parses a message.

**Two questions, and the scope is what tells them apart.**
:class:`AtlasFirmwareAdapter` asks ``firmware_inventory()`` — every installed
libretro core plus, since the resolver's 0.19.0, the standalone emulators a
packaged card covers, all unverified — and it is the right question only where
the caller has no platform to name: the RetroDECK-home migration's
untracked-BIOS sweep, and the download of one firmware id. **Its standalone
entries say only what a card can name unverified.** A card may identify its
image by CONTENT, and this route reads no bytes, so such an entry comes back
declaring nothing at all — DuckStation's inventory entry names no file here
while the verified per-system reading below has it naming the image it found.
So an absence here is still never "this emulator wants nothing", and no
readiness answer is built from this route.

:class:`AtlasPlatformFirmwareAdapter` asks
``firmware_for_system(<system>, verify=True)`` — every emulator ES-DE offers for
one system, libretro and standalone alike — and it is what every platform-scoped
answer reads. **The content check is not optional there.** A packaged card may
identify its image by CONTENT, so unverified it names no file at all while still
answering ``declaration="packaged"``: DuckStation comes back with an empty
requirement list and no system recording, which a reader that counts the list
would render as a green "nothing required" over a console that does not boot
without an image. The same check is what settles a folder declaration, whose
verdict is a file INSIDE the folder and can never be read off the folder's own
presence. Its cost is bounded by the system's own scope rather than by the BIOS
root: 64-318 ms per system on the reference machine, against 248 ms for one
unverified whole-machine sweep. Nothing is cached in either — a firmware answer
is about files on disk that the user is actively adding and removing, and a
cached one would outlive the download that changed it.

**One emulator is one identity, and a catalogue lists rows.** ES-DE declares one
``pcsx2_libretro.so`` under two labels, ``LRPS2`` and ``PCSX2``, so a per-system
answer carries that emulator twice with identical declarations. The identity is
the key everywhere here (:mod:`adapters.atlas_identity`) and a second row of one
identity is folded into the first — which is safe because the two rows differ in
the launch command they were built from, never in the declaration they read.

**A ``dir`` on a caveat is not a folder declaration's own.** On this route the
resolver states one for a standalone emulator's SEARCH directory too — the
directory DuckStation ranks its images in is the BIOS root, which is also where a
declaration that collapses onto the root resolves — so a row takes a statement
only where the caveat is not attributed to another emulator
(:func:`_speaks_for`), under the resolver's own attribution keys
(:data:`_ATTRIBUTION_KEYS`); a caveat that names none of them is a statement
about the place with no owner, and stays. What a row may hear at all is decided
by what it declares, and only a folder row hears a listing
(:func:`_speaking_for`).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from _vendor.atlas import (
    CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,
    CAVEAT_FIRMWARE_IMAGE_IDENTIFIED,
    CAVEAT_FIRMWARE_IMAGE_UNLISTED,
    CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
    CAVEAT_FIRMWARE_SCAN_INCOMPLETE,
    CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,
    detect,
)

# Neither name is in emu-atlas's ``__all__``; whether they are public API is
# emu-atlas#555's question.
from _vendor.atlas.firmware import core_short_name, declared_directory_of

from adapters.atlas_identity import emulator_identity
from domain.firmware_wants import (
    DECLARED_DIRECTORY,
    DECLARED_FILE,
    CoreFirmwareVerdict,
    FirmwareCatalogue,
    FirmwareGroup,
    FirmwareOption,
    FirmwarePlacement,
    FirmwareWant,
    FolderVerdict,
    MissingConfiguredImage,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# The declaration state in which an emulator stated what it wants off its own
# installation — a libretro ``.info`` read from the machine.
_DECLARATION_READ = "read"

# The state in which a packaged rule card stands in for an emulator that ships
# no declaration. It is stated ONLY together with a requirement: a card may
# identify its image by content, so ``packaged`` with an empty list means the
# card exists and this query established nothing — the same unasked question a
# missing ``.info`` leaves, and never "this emulator needs no firmware".
_DECLARATION_PACKAGED = "packaged"

# The keys a caveat names an emulator under. Three, because the resolver states
# the owner in whichever vocabulary the finding came from: ``core_so`` the core
# file, ``token`` a packaged card's token, and ``core`` the libretro SHORT name
# (``pcsx2``), which is none of this plugin's identities — so a row is matched
# against its core's short name as well (:func:`_names_of`). A caveat naming
# any of them belongs to that emulator and to no other row.
_ATTRIBUTION_KEYS = ("core_so", "token", "core")

# The two codes that name an image the emulator would boot. ``unlisted`` counts
# as much as ``identified``: both mean the file passed the header check the
# emulator makes itself, and they differ only in whether the packaged identity
# table also files those bytes — the table lists what System.dat lists, so an
# uncatalogued dump is the ordinary case rather than a lesser answer.
_IDENTIFIED_IMAGE_CODES = frozenset({CAVEAT_FIRMWARE_IMAGE_IDENTIFIED, CAVEAT_FIRMWARE_IMAGE_UNLISTED})

# The codes an entry states beside a one-of group that name console regions
# under ``regions``. The first two name regions whose answer rests on a read that
# settled nothing — a listing that failed, files nobody hashed — and the third
# regions for which nothing boots. A region named here may have an option too;
# the verdict's precedence decides between the two
# (:mod:`domain.firmware_groups`). The group's own ``core-mode-unestablished``
# names regions as well, and they are the regions its options DO serve, so it
# says nothing about a region left out. A region whose every name the resolver
# refused (it leaves the root) arrives with neither an option nor one of these
# codes — emu-atlas#556 asks for it.
_UNCHECKED_REGION_CODES = frozenset({CAVEAT_FIRMWARE_SCAN_INCOMPLETE, CAVEAT_FIRMWARE_SEARCH_UNVERIFIED})
_ABSENT_REGION_CODES = frozenset({CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE})


class AtlasFirmwareAdapter:
    """Reads what every installed libretro core wants, live, for the callers with no platform."""

    def __init__(self, *, user_home: str, log_debug: Callable[[str], None]) -> None:
        self._user_home = user_home
        self._log_debug = log_debug

    def __call__(self) -> FirmwareCatalogue:
        """The whole machine's firmware demand, or an empty unresolved catalogue."""
        try:
            return self._read_catalogue()
        except Exception as exc:
            # Deliberately broad: the resolver's failure modes are its own
            # invariant assertions and its packaged-data loaders, neither of
            # which is an exception type this adapter should enumerate. The
            # honest answer to "we could not ask" is the same whatever raised.
            self._log_debug(f"[firmware] resolver failed, answering unresolved: {exc!r}")
            return _unresolved()

    def _read_catalogue(self) -> FirmwareCatalogue:
        installation = _installation(self._user_home, self._log_debug)
        if installation is None:
            return _unresolved()
        answer = installation.firmware_inventory()
        self._log_debug(f"[firmware] machine: {_trace(answer)}")
        return _catalogue(answer)


class AtlasPlatformFirmwareAdapter:
    """Reads what one system's emulators want, verified, live off the machine."""

    def __init__(self, *, user_home: str, log_debug: Callable[[str], None]) -> None:
        self._user_home = user_home
        self._log_debug = log_debug

    def __call__(self, system: str) -> FirmwareCatalogue:
        """*system*'s firmware demand, or an empty unresolved catalogue."""
        try:
            return self._read_catalogue(system)
        except Exception as exc:
            self._log_debug(f"[firmware] resolver failed for {system}, answering unresolved: {exc!r}")
            return _unresolved()

    def _read_catalogue(self, system: str) -> FirmwareCatalogue:
        installation = _installation(self._user_home, self._log_debug)
        if installation is None:
            return _unresolved()
        answer = installation.firmware_for_system(system, verify=True)
        self._log_debug(f"[firmware] {system}: {_trace(answer)}")
        return _catalogue(answer)


def _installation(user_home: str, log_debug: Callable[[str], None]) -> Any | None:
    """The installation every question here is put to, or ``None`` where there is none.

    Detection returns the arrangements it found highest-priority first and never
    picks a winner itself; the plugin is a RetroDECK plugin, and RetroDECK leads
    that order where it is present.
    """
    installations = detect(user_home)
    if not installations:
        log_debug("[firmware] no emulator installation detected")
        return None
    return installations[0]


def _unresolved() -> FirmwareCatalogue:
    """The answer to "we could not ask" — no placements, nothing ruled out."""
    return FirmwareCatalogue(placements=(), unread_emulators=frozenset(), resolved=False)


def _catalogue(answer: Any) -> FirmwareCatalogue:
    """One resolver answer in the plugin's own vocabulary."""
    if answer.root is None:
        return FirmwareCatalogue(
            placements=(),
            unread_emulators=frozenset(),
            resolved=False,
            caveats=_caveat_codes(answer),
        )
    return FirmwareCatalogue(
        placements=_placements(answer),
        unread_emulators=_unread_emulators(answer),
        resolved=True,
        caveats=_caveat_codes(answer),
        emulator_verdicts=_emulator_verdicts(answer),
        groups=_groups(answer),
    )


def _trace(answer: Any) -> str:
    """The answer's shape, its stable caveat codes, and the systems that need an image.

    The verdicts are traced by their exception rather than in full: the state
    that changes a reading is the one that says the console will not start, and
    an entry per emulator would bury it.
    """
    needing = sorted(
        emulator for emulator, verdict in _emulator_verdicts(answer).items() if verdict.system_needs_an_image
    )
    return (
        f"root={answer.root!r} entries={len(answer.cores)} verified={answer.hash_checked} "
        f"caveats={sorted(set(_caveat_codes(answer)))} system-firmware-needed={needing}"
    )


def _stated(core: Any) -> bool:
    """Did this entry state what it wants?

    Two states say yes and every other one says the emulator was not asked.
    ``read`` is its own declaration off the machine, and an empty one there is
    the finished answer that it needs nothing. ``packaged`` is a rule card
    standing in for an emulator that ships none, and it is an answer only where
    it named something: a card may identify its image by CONTENT, so a card with
    no requirement in hand has established nothing at all. A refused declaration
    counts as unread too — the emulator does want something the resolver would
    not follow to a destination, so what it wants is no better known than for one
    whose declaration was missing.
    """
    if core.refused:
        return False
    if core.declaration == _DECLARATION_READ:
        return True
    return core.declaration == _DECLARATION_PACKAGED and bool(core.requirements)


def _caveat_codes(answer: Any) -> tuple[str, ...]:
    """Every stable caveat code the answer states, at the answer and at each entry."""
    return tuple(caveat.code for caveat in _every_caveat(answer))


def _unread_emulators(answer: Any) -> frozenset[str]:
    """The emulators that did not state what they want, by identity.

    **Read over the identity rather than over the row**, because that is what a
    caller asks about: one emulator declared twice is unread only where NEITHER
    of its rows stated. Judging per row would put an identity in this set while
    its declaration sat in the placements under the same name — a file wanted by
    an emulator we had just called unaskable. The two rows resolve the same
    declaration today, so the difference is latent; it is the shape that would
    make it a defect that is worth closing.

    An entry the resolver could not identify contributes nothing: there is no
    name to put in the set, and no caller can ask about it. Its requirements
    still reach the placements as wants with no owner, and the launching
    emulator is never one of them — an unidentified pick is refused one layer up.
    """
    stated: dict[str, bool] = {}
    for core in answer.cores:
        identity = emulator_identity(core)
        if identity is not None:
            stated[identity] = stated.get(identity, False) or _stated(core)
    return frozenset(identity for identity, was_read in stated.items() if not was_read)


def _emulator_verdicts(answer: Any) -> dict[str, CoreFirmwareVerdict]:
    """Each emulator's two answers about itself, by identity.

    Keyed on the emulator rather than folded onto a file row, because that is
    what they are about: one machine's PlayStation BIOS images read one way under
    SwanStation and another under PCSX ReARMed, and a per-file row has no room
    for a fact that changes with the emulator the game launches with.

    An unidentified entry is skipped; an identity the answer names twice keeps
    the first entry, the same rule :func:`_placement_for` applies to a
    destination read twice.
    """
    verdicts: dict[str, CoreFirmwareVerdict] = {}
    for core in answer.cores:
        identity = emulator_identity(core)
        if identity is None:
            continue
        verdicts.setdefault(
            identity,
            CoreFirmwareVerdict(system_firmware=core.system_firmware, requirements_met=core.requirements_met),
        )
    return verdicts


def _requirement_entries(core: Any) -> list[tuple[Any, bool]]:
    """An entry's requirements, each with whether it is an option of a one-of group.

    Every option is a file the emulator declared, so it is a row like any other
    and belongs in the per-file placements; the group it stands in is the
    requirement over those rows, and :func:`_groups` carries it. The flag is
    what keeps an option from speaking for its row's NEED: an option is needed
    only where its region is the launch's, which no per-file answer can say.
    """
    entries: list[tuple[Any, bool]] = []
    for entry in core.requirements:
        options = getattr(entry, "options", None)
        if options is None:
            entries.append((entry, False))
        else:
            entries.extend((option, True) for option in options)
    return entries


def _groups(answer: Any) -> tuple[FirmwareGroup, ...]:
    """Every one-of group the answer states, keyed on the emulator that states it.

    An entry the resolver could not identify contributes none — a group nothing
    can be scoped to answers for no launch. Of two rows of one identity that
    state groups, the first stands and the second is skipped, the rule
    :func:`_wants` applies for the same reason; a row stating no group takes no
    part in that, so it never hides a later row's group. The regions an entry
    names beside its group come off that entry's own caveats
    (:data:`_UNCHECKED_REGION_CODES`, :data:`_ABSENT_REGION_CODES`).
    """
    groups: list[FirmwareGroup] = []
    seen: set[str] = set()
    for core in answer.cores:
        identity = emulator_identity(core)
        stated = [entry for entry in core.requirements if getattr(entry, "options", None) is not None]
        if identity is None or identity in seen or not stated:
            continue
        seen.add(identity)
        names = _names_of(core)
        spoken = [caveat for caveat in core.caveats if _speaks_for(caveat, names)]
        unchecked = _caveat_regions(spoken, _UNCHECKED_REGION_CODES)
        absent = _caveat_regions(spoken, _ABSENT_REGION_CODES)
        groups.extend(
            FirmwareGroup(
                emulator=identity,
                options=tuple(
                    FirmwareOption(
                        file_name=option.file_name, regions=tuple(option.regions or ()), satisfied=option.satisfied
                    )
                    for option in entry.options
                ),
                unchecked_regions=unchecked,
                absent_regions=absent,
            )
            for entry in stated
        )
    return tuple(groups)


def _caveat_regions(caveats: list[Any], codes: frozenset[str]) -> tuple[str, ...]:
    """The regions the caveats of *codes* name under ``regions``, in the order stated."""
    named = (
        region
        for caveat in caveats
        if caveat.code in codes and isinstance(caveat.data.get("regions"), (list, tuple))
        for region in caveat.data["regions"]
        if isinstance(region, str) and region
    )
    return tuple(dict.fromkeys(named))


def _declared_location(requirement: Any, root: str) -> str | None:
    """The location the emulator declared, or ``None`` when nothing under *root* honours it.

    Two fields of one requirement answer two different questions, and only the
    first belongs here. ``declared`` is the string the emulator spelled and the
    name it will open; ``path`` is where that lands once the kernel has followed
    every symlink, which is what says whether the destination is inside the root
    the plugin owns. Reconstructing the declaration from ``path`` instead —
    ``relpath(path, root)`` — agrees with it only while no link re-roots the
    way: RetroDECK points ``<bios>/pcsx2/bios`` back at ``<bios>``, so LRPS2's
    ``pcsx2/bios`` collapses onto the root and comes back as ``.``.

    A FILE requirement whose ``declared`` is a FOLDER its core declares goes in
    that folder under its own name: LRPS2 declares ``pcsx2/bios`` and, with its
    ``pcsx2_bios`` option set, the resolver states the requirement as the file
    that option names while keeping the folder as ``declared``, so the location
    as it stands would place the file where the folder is. Whether ``declared``
    is such a folder is the resolver's own table's answer
    (``declared_directory_of``), the one it reads the folder declaration by.
    No other mismatch between ``declared`` and ``file_name`` is a folder: Beetle
    PSX names the spelling it reached in ``file_name`` (``SCPH-5501.bin``) and
    the one its ``.info`` spells in ``declared`` (``scph5501.bin``), and that
    second one is where an image belongs.

    ``None`` where there is no location below the root to honour, so the caller
    falls back to its own flat layout. Three shapes reach it. A resolved
    destination outside the root — a standalone emulator's own XDG tree. A
    declaration that is absent, absolute, or climbs out of the root, which names
    a place the caller cannot express as a segment under a root that is its to
    own. And a declaration that normalises to ``.``, which is the root itself:
    that is not a location under it, and a caller joining it would place every
    file of that name at the directory rather than in it.
    """
    relative = os.path.relpath(requirement.path, root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return None
    declared = requirement.declared
    if not declared or os.path.isabs(declared):
        return None
    normalised = os.path.normpath(declared)
    if normalised == os.curdir or normalised == os.pardir or normalised.startswith(os.pardir + os.sep):
        return None
    if _a_file_in_a_declared_folder(requirement):
        return os.path.join(normalised, requirement.file_name)
    return normalised


def _a_file_in_a_declared_folder(requirement: Any) -> bool:
    """Is *requirement* a file its core opens inside a folder it declares — see :func:`_declared_location`."""
    if requirement.declared_kind == DECLARED_DIRECTORY or requirement.core_so is None:
        return False
    return declared_directory_of(requirement.core_so.removesuffix(".so"), requirement.declared) is not None


def _placements(answer: Any) -> tuple[FirmwarePlacement, ...]:
    """One placement per declared file name, folding every emulator that declares it.

    Two emulators routinely want the same file — gambatte and SameBoy both ask
    for a Game Boy boot ROM — so the file is the key and the emulators are the
    entries under it. That is also what keeps one file from reading differently
    on two surfaces: there is one row per name in the whole answer.

    What each row then says about its own destination is
    :func:`_placement_for`'s, and the answer's caveats are indexed once here
    rather than per row because one index serves every name in it.
    """
    at_path, in_dir = _caveats_by_destination(answer)
    by_name: dict[str, list[Any]] = {}
    for core in answer.cores:
        for requirement, option in _requirement_entries(core):
            by_name.setdefault(requirement.file_name, []).append((core, requirement, option))

    placements = [
        _placement_for(file_name, pairs, answer.root, at_path, in_dir) for file_name, pairs in by_name.items()
    ]
    return tuple(sorted(placements, key=lambda placement: placement.file_name))


def _wants(pairs: list[Any]) -> tuple[FirmwareWant, ...]:
    """Who declared this file, one entry per emulator rather than per catalogue row.

    A system's launch list can name one emulator twice — ES-DE declares
    ``pcsx2_libretro.so`` as both ``LRPS2`` and ``PCSX2``, EmuDeck declares Cemu
    natively and under Proton — and both rows resolve the same declaration,
    because what differs between them is the command that launches the emulator
    and not the file it reads its requirements from. So the first row of an
    identity stands for it and the rest are dropped; carrying them would put one
    emulator twice in a row's declaring set, and every count over that set would
    be off by the number of labels ES-DE happens to use.

    An entry the resolver could not identify keeps its want, with no owner. It
    genuinely declared the file, so dropping it would understate the demand,
    and it can be told apart from every other unidentified entry by nothing —
    which is why the identity-keyed readings pass it over instead of folding it.

    ``required`` is read off the emulator's PLAIN declarations of the file and
    never off a one-of option, whose ``need`` speaks for the group: Beetle PSX
    lists ``scph5501.bin`` both as a plain row — its ``.info`` marks it required
    and the resolver states that row ``optional``, because the group beside it
    speaks for the image — and as its group's NTSC-U option, and the option's
    ``required`` would make the file a prerequisite of every launch where it is
    one only of an NTSC-U one.
    """
    wants: list[FirmwareWant] = []
    at: dict[str, int] = {}
    for core, requirement, option in pairs:
        required = requirement.need == "required" and not option
        identity = emulator_identity(core)
        if identity is None:
            wants.append(FirmwareWant(emulator=None, required=required))
        elif identity not in at:
            at[identity] = len(wants)
            wants.append(FirmwareWant(emulator=identity, required=required))
        elif required:
            wants[at[identity]] = FirmwareWant(emulator=identity, required=True)
    return tuple(wants)


def _placement_for(
    file_name: str,
    pairs: list[Any],
    root: str,
    at_path: dict[str, tuple[Any, ...]],
    in_dir: dict[str, tuple[Any, ...]],
) -> FirmwarePlacement:
    """One name's row: who wants it, and what was read where it goes.

    The two halves answer to different things. ``wants`` is every emulator under
    the name, because that is what the name is keyed on. Everything about the
    DESTINATION comes from the first requirement alone — those are statements
    about one place, and reading them off different requirements would describe
    two places as one row.

    With no location to honour the destination half goes silent together, which
    is the early return: a standalone emulator's own XDG tree holding the file
    says nothing about the BIOS root the caller will write to, so the reading is
    dropped rather than travelling on to describe somewhere else. What survives
    is the declaration — its kind is what the emulator OPENS, not what is there,
    and its ``declaration`` state says which register the description is written
    in, which is a property of the entry that stated it rather than of the place.

    ``checked`` goes with the destination half, and off the same entry the
    declaration does: it is what became of the bytes AT that place, so carrying
    it past the early return would describe a read of somewhere else.
    """
    first_core, first, _ = pairs[0]
    directory = first.declared_kind == DECLARED_DIRECTORY
    declared_kind = DECLARED_DIRECTORY if directory else DECLARED_FILE
    wants = _wants(pairs)
    location = _declared_location(first, root)
    if location is None:
        return FirmwarePlacement(
            file_name=file_name,
            relative_path=None,
            description=first.description,
            wants=wants,
            declared_kind=declared_kind,
            declaration=first_core.declaration,
        )

    speaking = _speaking_for(at_path, in_dir, first, _names_of(first_core))
    folder = _folder_verdict(first, speaking) if directory else None
    supplied = first.supplied_by
    return FirmwarePlacement(
        file_name=file_name,
        relative_path=location,
        description=first.description,
        wants=wants,
        present=first.present,
        declared_kind=declared_kind,
        declaration=first_core.declaration,
        caveats=tuple(dict.fromkeys(caveat.code for caveat in speaking)),
        folder=folder,
        supplied_by=supplied.label if supplied is not None else None,
        checked=first.checked,
        missing_configured_image=_missing_configured_image(first_core, speaking),
    )


def _missing_configured_image(core: Any, speaking: tuple[Any, ...]) -> MissingConfiguredImage | None:
    """The stale setting a row's statements name, with the label of the emulator it belongs to.

    The statements are the row's own (:func:`_speaking_for`), so the setting is
    the one of the emulator whose reading the row carries — LRPS2's
    ``pcsx2_bios`` on its ``pcsx2/bios`` row.
    """
    for caveat in speaking:
        if caveat.code == CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING:
            name = caveat.data.get("name")
            return MissingConfiguredImage(
                emulator_label=core.label, file_name=name if isinstance(name, str) and name else None
            )
    return None


def _folder_verdict(requirement: Any, speaking: tuple[Any, ...]) -> FolderVerdict:
    """One folder declaration's verdict, with the images the read identified.

    ``satisfied`` is the resolver's own three-valued answer about what the folder
    HOLDS, never about the folder being there: RetroDECK links LRPS2's
    ``pcsx2/bios`` onto the BIOS root, so the folder is present on every install
    and reading presence as the verdict would report a satisfied requirement over
    a PS2 install with no BIOS file at all.

    The images are the descriptions of what the read identified, which is the
    emulator's own option-label text. Only a satisfied verdict has any: an image
    the emulator's own test rejected, or one it never got to read, is not
    something the folder holds for the purpose of this row.

    The two halves count differently, which is why the row's codes are collapsed
    and the descriptions are not. One statement per image is what the read states
    — the reference machine's PS2 folder answers three
    ``firmware-image-identified``, one per BIOS image in it — and the row wants
    all three descriptions and the code once, because the code says what kind of
    finding these are and repeating it says nothing further.
    """
    named = (caveat.data.get("description") for caveat in speaking if caveat.code in _IDENTIFIED_IMAGE_CODES)
    return FolderVerdict(
        satisfied=requirement.satisfied,
        images=tuple(description for description in named if description),
    )


def _speaking_for(
    at_path: dict[str, tuple[Any, ...]],
    in_dir: dict[str, tuple[Any, ...]],
    requirement: Any,
    names: frozenset[str],
) -> tuple[Any, ...]:
    """The caveats that speak for one row: about its destination, and not another emulator's.

    **What a row may hear depends on what it declares.** Every row hears what was
    found AT its own destination. Only a FOLDER row also hears what a listing
    found INSIDE that place, and that restriction is load-bearing: on a linked
    root the listed folder IS the firmware root, which is the resolved
    destination of any declaration that collapses onto it, so a file row hearing
    listings would describe a folder read as its own.

    The second filter is what a per-system answer needs and a whole-machine one
    never did. The resolver states a ``dir`` for a standalone emulator's SEARCH
    directory as well as for a folder declaration's own folder, and on a
    RetroDECK install those are routinely the same directory — the BIOS root is
    where DuckStation ranks its images and also where LRPS2's ``pcsx2/bios``
    resolves. Keyed by place alone, a row would carry the other emulator's
    findings and word them as its own.

    A caveat is another emulator's when it names one and that is not this row's
    (:func:`_speaks_for`). A caveat naming none is a statement about the place
    with no owner — a listing that failed is the case that matters — and it stays
    on the row, which is the permissive direction: the row keeps a cause it might
    not own rather than losing one it does.
    """
    here = at_path.get(requirement.path, ())
    if requirement.declared_kind == DECLARED_DIRECTORY:
        here = (*here, *in_dir.get(requirement.path, ()))
    return tuple(caveat for caveat in here if _speaks_for(caveat, names))


def _names_of(core: Any) -> frozenset[str]:
    """Every name a caveat may give *core*'s emulator under :data:`_ATTRIBUTION_KEYS`.

    The identity, and for a libretro core its short name too
    (:data:`_ATTRIBUTION_KEYS` says why), read through the resolver's own rule
    (``core_short_name``) so the two cannot drop the suffix differently. Empty
    for an entry with neither, which no attributed caveat can then name.
    """
    names: set[str] = set()
    identity = emulator_identity(core)
    if identity is not None:
        names.add(identity)
    if core.core_so:
        names.add(core_short_name(os.path.splitext(core.core_so)[0]))
    return frozenset(names)


def _speaks_for(caveat: Any, names: frozenset[str]) -> bool:
    """Is this caveat unattributed, or attributed to one of *names*?"""
    named = {caveat.data[key] for key in _ATTRIBUTION_KEYS if isinstance(caveat.data.get(key), str)}
    return not named or not named.isdisjoint(names)


def _caveats_by_destination(answer: Any) -> tuple[dict[str, tuple[Any, ...]], dict[str, tuple[Any, ...]]]:
    """The answer's caveats, indexed by the destination each one names — two indexes.

    They are two indexes because the resolver names a destination two ways and
    those are different statements. ``path`` is the thing the caveat is ABOUT, so
    it belongs to the requirement resolving there. ``dir`` is a directory that
    was listed, and the caveat is about what the listing found IN it — as is a
    ``path`` one level down, which is how a verified folder read's per-image
    findings reach the folder row that asked for them.

    One level down is a place, not a role, so a caveat about a DECLARED file
    sitting directly in a folder is taken the same way. That needs one emulator
    declaring both a folder and a file in it; LRPS2 declares ``pcsx2/bios`` and
    ``pcsx2/resources/GameIndex.yaml``, which is two levels down, so no shape on
    a reference machine collides.

    Statements are kept whole rather than collapsed to codes, because a row needs
    two things off them that a code cannot carry: which emulator a finding
    belongs to, and what the read called an image.
    """
    at_path: dict[str, list[Any]] = {}
    in_dir: dict[str, list[Any]] = {}
    for caveat in _every_caveat(answer):
        listed = caveat.data.get("dir")
        if isinstance(listed, str):
            in_dir.setdefault(listed, []).append(caveat)
        path = caveat.data.get("path")
        if isinstance(path, str):
            at_path.setdefault(path, []).append(caveat)
            in_dir.setdefault(os.path.dirname(path), []).append(caveat)
    return _frozen(at_path), _frozen(in_dir)


def _frozen(index: dict[str, list[Any]]) -> dict[str, tuple[Any, ...]]:
    """One destination index with its statement lists closed."""
    return {destination: tuple(caveats) for destination, caveats in index.items()}


def _every_caveat(answer: Any) -> tuple[Any, ...]:
    """Every caveat the answer states, at the answer and at each entry."""
    return (*answer.caveats, *(caveat for core in answer.cores for caveat in core.caveats))
