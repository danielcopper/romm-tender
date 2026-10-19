"""The vocabulary a firmware answer arrives in, and the one classification over it.

Which emulator wants which file is read live off the machine by the resolver
behind :class:`services.protocols.FirmwareResolver`; ``domain/`` may not import
the vendored resolver at all, so this module holds only the words the answer
comes back in — the same split :mod:`domain.sync_action` makes for the save-sync
core.

The resolver answers per *file* whether an emulator needs it or merely accepts
it. "Nothing wants it" and "nothing could be established" are not properties of a
file — they are properties of the **reading**, one level up on the emulator,
which is why :class:`FirmwareCatalogue` names the emulators it could not ask. Our
own file list comes from the RomM server rather than from the resolver, so each
server file is classified by whether the catalogue holds a placement for it, and
by whether the reading was complete for the emulator the game will launch with:

``needed`` · ``optional`` · ``not_needed`` · ``unknown`` (:func:`classify_wanted`).

Collapsing the last two into one value is the defect this vocabulary exists to
prevent: a file nothing wants is a finished answer, and a file we could not ask
about is not.

**One emulator is one identity, whatever launches it.** The resolver states that
identity in the spelling the launch command uses — a libretro entry's core file
basename (``dolphin_libretro.so``), a standalone entry's own command name
(``DOLPHIN``, ``PCSX2``) — and both a catalogue entry and a firmware answer carry
it under that one name, which is what lets a pick made in the emulator picker be
matched against the firmware answer it should be judged by. A standalone
emulator therefore has an identity here where it has no ``core_so``, and the
identity is never derived from a display label: ES-DE lists one PCSX2 under two
labels, so a label is a presentation and not a name.

The identity can be absent, and that is a state rather than an error: an entry
the resolver could not identify cannot be scoped to, so nothing may be ruled out
for it. It reaches the answer as "could not be established", never as "nothing
needed" — the same rule :data:`WANTED_UNKNOWN` carries one level down.

A second axis runs beside that one and is not a property of any file: what the
resolver says about each **emulator** as a whole (:class:`CoreFirmwareVerdict`).
Its verdict over the emulator's whole declaration — every required file, every
one-of group, and what is recorded about the console it declares for — is
``requirements_met``, and it is the readiness answer: Tender words it and does
not rebuild it from the rows. What is recorded about the system comes from a
packaged table whose ``None`` means nobody has looked at the system, never that
the system needs nothing.

Where the resolver knows how the emulator picks its image, it states the "one of
these" itself, as a **one-of group** (:class:`FirmwareGroup`): the files a launch
needs exactly one of, each serving the console regions whose launch opens it.
That is a requirement of its own and not a property of any file in it, so it
rides beside the placements rather than on them — the files are rows like any
other, and a group's option never makes its row required
(:mod:`domain.firmware_groups` judges the group).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

WANTED_NEEDED = "needed"
WANTED_OPTIONAL = "optional"
WANTED_NOT_NEEDED = "not_needed"
WANTED_UNKNOWN = "unknown"

WANTED_VALUES = (WANTED_NEEDED, WANTED_OPTIONAL, WANTED_NOT_NEEDED, WANTED_UNKNOWN)

# What is recorded about the SYSTEM one core declares firmware for — the half a
# libretro ``.info`` has no way to state, so it can never be read off a file
# list. Spelled here the way the resolver spells them, and held equal to its
# constants by ``tests/adapters/test_atlas_firmware.py``.
SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT = "cannot-run-without-firmware"
SYSTEM_FIRMWARE_CORE_ALTERNATIVE = "core-supplies-an-alternative"
SYSTEM_FIRMWARE_RUNS_WITHOUT = "runs-without-firmware"
SYSTEM_FIRMWARE_OPEN = "open"

SYSTEM_FIRMWARE_STATES = (
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    SYSTEM_FIRMWARE_OPEN,
)

# What the emulator opens the declaration AT — a file it reads, or a folder it
# lists. A property of the DECLARATION, so it survives an empty destination:
# nothing is at a missing folder to read a kind off, and the row is still a
# folder to create rather than a file to fetch.
DECLARED_FILE = "file"
DECLARED_DIRECTORY = "directory"

# How the emulator finds the firmware it boots — the resolver's ``locating``,
# world knowledge about the emulator's code rather than a reading of this
# machine, spelled as the resolver spells it and held equal to its constants by
# ``tests/adapters/test_atlas_firmware.py``. ``by-name``: it opens the names it
# declares, so the declared list is the whole requirement. ``by-name-then-content``:
# a configured name first, then a search of the BIOS folder that recognises an
# image by its bytes. ``unestablished``: nobody has read how it does it. It words
# the answer and moves no verdict; a value the resolver did not state, or one
# this list does not know, reads as ``unestablished``.
LOCATING_BY_NAME = "by-name"
LOCATING_BY_NAME_THEN_CONTENT = "by-name-then-content"
LOCATING_UNESTABLISHED = "unestablished"

LOCATING_VALUES = (LOCATING_BY_NAME, LOCATING_BY_NAME_THEN_CONTENT, LOCATING_UNESTABLISHED)

# The emulator-level notes a surface words (:class:`EmulatorNote`). ``bios-unchosen``:
# a setting that names the emulator's BIOS file is empty, so no game boots until
# one is chosen. ``images-tied``: several images rank alike for the same console,
# and which one the emulator opens is not established. ``search-found-nothing``:
# a settled search of a folder reached no image to boot — no file of a size the
# emulator accepts, or files of that size it recognises none of.
NOTE_BIOS_UNCHOSEN = "bios-unchosen"
NOTE_IMAGES_TIED = "images-tied"
NOTE_SEARCH_FOUND_NOTHING = "search-found-nothing"

# The listing of a directory an emulator searches did not finish, so the search
# saw only part of what a launch would see. Spelled as the resolver spells it.
CAVEAT_SCAN_INCOMPLETE = "firmware-scan-incomplete"

# The place an emulator opens a file at could not be looked at (permissions or an
# I/O failure), so whether anything is there is unknown. Spelled as the resolver
# spells it.
CAVEAT_PATH_INACCESSIBLE = "firmware-path-inaccessible"

# The ``checked`` values a reading of an emulator's search is worded by: bytes
# asked for that did not come back, and bytes read that no table the emulator
# keeps knows. Spelled as the resolver spells them.
CHECKED_UNREAD = "unread"
CHECKED_UNRECOGNISED = "unrecognised"

# The codes with which the resolver states that a search identified a file by
# its contents: an image its table names, and one the emulator's own test
# accepts that the table does not list. Spelled as the resolver spells them.
CAVEATS_IDENTIFIED_BY_CONTENT = frozenset({"firmware-image-identified", "firmware-image-unlisted"})


@dataclass(frozen=True)
class FolderVerdict:
    """What listing a declared folder established about what is inside it.

    The resolver's answer to the one question a file's presence cannot settle:
    a core that lists a folder needs a file *in* it, and the folder being there
    says nothing about that. ``satisfied`` is the verdict — ``True`` for a
    folder holding an image the core's own test accepts, ``False`` for one
    holding none, ``None`` for everything the read did not establish.

    ``images`` names what was found, in the resolver's own words, and is empty
    for every verdict but a satisfied one.

    The CAUSE of the verdict is not here: ``satisfied`` is the verdict alone, and
    what speaks for it rides on the row's own ``caveats`` beside every other
    statement about that destination. One list per row rather than two, because a
    surface wording a row has one place to look and cannot show the same code
    twice.
    """

    satisfied: bool | None
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissingConfiguredImage:
    """An emulator's own setting names a file in its folder declaration, and nothing is at it.

    The emulator lists the folder instead, so the folder's verdict is what its
    launch rests on and this changes nothing about it: it is the setting that is
    stale, never the requirement. ``emulator_label`` is the label of the entry
    the row's reading came from and ``file_name`` the name the setting holds —
    either ``None`` where the answer did not state it.
    """

    emulator_label: str | None
    file_name: str | None


@dataclass(frozen=True)
class RefusedDeclaration:
    """A declaration the resolver would not follow to a destination, and why.

    Not a row — there is no place to state — but a file the emulator asked for,
    so it stays visible. ``reason`` is the resolver's stable code for why it was
    refused (the declaration leaves the firmware root, or names no resolvable
    place). A refused REQUIRED declaration is why the resolver withholds the
    emulator's verdict; a refused optional one changes nothing about it.
    """

    declared: str
    required: bool
    reason: str


@dataclass(frozen=True)
class EmulatorNote:
    """One statement the resolver makes about the emulator rather than about a file.

    ``kind`` is one of :data:`NOTE_BIOS_UNCHOSEN` / :data:`NOTE_IMAGES_TIED` /
    :data:`NOTE_SEARCH_FOUND_NOTHING`. ``setting`` names the empty setting,
    ``tied`` how many images rank alike, ``dir`` the folder a search looked in
    and ``candidates`` how many files of a size the emulator accepts it held,
    and ``regions`` the console regions the statement is about — each ``None``
    or empty where the resolver did not state it.
    """

    kind: str
    setting: str | None = None
    tied: int | None = None
    dir: str | None = None
    candidates: int | None = None
    regions: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoreFirmwareVerdict:
    """What the resolver says about one emulator beyond the files it declares.

    ``requirements_met`` is the resolver's own three-valued verdict over the
    emulator's whole declaration, weighed against what is on disk and against
    what is recorded about its console: met, not met, or not established. **It
    is the readiness answer**: every BIOS colour is this verdict for
    the launching emulator, and the one exception — a one-of group whose answer
    turns on the disc's region — is read off the fields below rather than
    counted (:func:`domain.bios_status.region_decides`). A ``False`` is always a
    demonstration; ignorance is ``None``.

    ``system_firmware`` is world knowledge about the console — one of
    :data:`SYSTEM_FIRMWARE_STATES`, or ``None`` where the packaged table records
    nothing about the system at all, which is an unasked question and never
    "nothing is needed". ``world_knowledge`` is whether the answer carries the
    resolver's mark that it stated something about the system from that table.
    Together they are how a surface says the console does not start without an
    image; whether one is in place is the per-file verdicts', never presence.

    ``system_firmware_met`` is the resolver's own verdict over that console-level
    need, folded into ``requirements_met``: the console has a usable image of its
    own, demonstrably has none, or ``None`` where that is unjudged or the need
    itself was not established. Its ``False`` is the one statement that the
    console's image is absent, and it holds for every region, so the game page
    reads it rather than inferring the absence from a group or the rows
    (:func:`domain.bios_status.game_region_level`).

    ``locating`` is how the emulator finds its firmware
    (:data:`LOCATING_VALUES`); ``declaration`` is the resolver's word for how it
    stated what it wants (``read``, ``packaged``, ``unsupported`` …).

    ``undetermined`` names the plain required files whose verdict was not
    established and ``unmet`` those shown not met, ``refused`` every declaration
    the resolver would not follow, and ``region_decided_by_disc`` whether the
    resolver stated that a one-of group's option is picked by the region of the
    disc being booted. ``notes`` are the emulator-level statements a surface
    words (:class:`EmulatorNote`), and ``caveats`` the stable codes stated on
    the emulator's own entry.
    """

    system_firmware: str | None = None
    requirements_met: bool | None = None
    system_firmware_met: bool | None = None
    world_knowledge: bool = False
    locating: str = LOCATING_UNESTABLISHED
    declaration: str | None = None
    undetermined: tuple[str, ...] = ()
    unmet: tuple[str, ...] = ()
    refused: tuple[RefusedDeclaration, ...] = ()
    region_decided_by_disc: bool = False
    notes: tuple[EmulatorNote, ...] = ()
    caveats: tuple[str, ...] = ()

    @property
    def system_needs_an_image(self) -> bool:
        """Does the console need a firmware image this emulator does not carry itself?

        True for exactly one of the four states. The other three are recorded
        answers that leave the file rows to speak for themselves — the emulator
        supplies its own substitute, the system was established to start without
        one, or nobody has established which.
        """
        return self.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT

    @property
    def refused_required(self) -> tuple[RefusedDeclaration, ...]:
        """The refused declarations the emulator requires — the ones that withhold its verdict."""
        return tuple(refused for refused in self.refused if refused.required)


@dataclass(frozen=True)
class FirmwareWant:
    """One emulator's demand for one firmware file.

    ``emulator`` is that emulator's identity — one string for both kinds, so a
    standalone declaration is an owner here rather than an orphan. ``None`` is
    the entry the resolver could not identify: it owns the file as much as any
    other, which is why it still stands in ``wants``, but nothing can be scoped
    to it, so every identity-keyed reading passes it over. ``required`` is the
    resolver's own two-valued answer: the emulator will not run without the
    file, or it will.

    No display label: what an emulator is called to a user is ES-DE's answer,
    read per platform off ``es_systems.xml``, and a second spelling arriving with
    the requirement would be a second thing to keep in step with it — and one
    that does not identify, since ES-DE lists one PCSX2 under two labels.
    """

    emulator: str | None
    required: bool


@dataclass(frozen=True)
class FirmwareOption:
    """One file a one-of group accepts, and the console regions whose launch opens it.

    ``regions`` are the resolver's own region words (``ntsc-u``, ``pal``) and
    are never empty. ``satisfied`` is the resolver's three-valued verdict over
    the file: it is in place and the emulator takes it, it is not, or nothing
    established which — a file that is there and could not be read is the
    third, never the first.
    """

    file_name: str
    regions: tuple[str, ...]
    satisfied: bool | None


@dataclass(frozen=True)
class FirmwareGroup:
    """One emulator's one-of requirement: a launch needs exactly one of these options.

    Which option a launch needs is decided by the console region of the disc in
    it, a run-time fact no file states — so the group is judged over regions
    (:func:`domain.firmware_groups.judge_group`), and it is ONE requirement
    however many options it lists. The options' region sets are disjoint; one
    file may still stand under several options, each serving its own regions.

    ``unchecked_regions`` are regions the resolver named as resting on a read
    that did not settle anything — a listing that failed, bytes nobody hashed —
    and ``absent_regions`` are regions it stated nothing boots for. They are
    statements about regions rather than about any option, which is why they
    are carried beside the options rather than on them; a region named there
    may have an option as well, and the verdict's precedence decides
    (:mod:`domain.firmware_groups`). Without them a group of one NTSC-U option
    would read as covering every region it knows about.

    ``unstated_regions`` are the regions the emulator's route knows and the
    group carries no option for, as the resolver names them on the group's
    ``core-mode-unestablished`` statement. A region whose every name it refused
    arrives with neither an option nor one of the codes above, so this is the
    only place it is stated; such a region is one nothing was established for,
    never one that is covered.

    ``emulator`` is the identity the group belongs to and is never ``None``: a
    group nothing can be scoped to answers for no launch.

    ``satisfied`` is the resolver's own verdict over the group: every option
    met, every option not met, or neither — a mixed group, whose answer for a
    launch turns on the disc's region.
    """

    emulator: str
    options: tuple[FirmwareOption, ...]
    unchecked_regions: tuple[str, ...] = ()
    absent_regions: tuple[str, ...] = ()
    satisfied: bool | None = None
    unstated_regions: tuple[str, ...] = ()

    @property
    def regions(self) -> tuple[str, ...]:
        """Every region the group speaks about, options first, in the order stated."""
        stated = (region for option in self.options for region in option.regions)
        return tuple(dict.fromkeys((*stated, *self.unchecked_regions, *self.absent_regions, *self.unstated_regions)))

    def regions_of(self, file_name: str) -> tuple[str, ...]:
        """The regions *file_name* serves in this group, merged over every option naming it."""
        return tuple(
            dict.fromkeys(
                region for option in self.options if option.file_name == file_name for region in option.regions
            )
        )


@dataclass(frozen=True)
class FirmwarePlacement:
    """One firmware file the machine asks for: where it goes and who wants it.

    ``relative_path`` is the location the emulator declared, relative to the
    firmware root, so a consumer joins it under the BIOS directory it owns
    rather than trusting an absolute path from outside. ``None`` when there is
    no location under that root to honour — a standalone emulator keeping its
    firmware in its own XDG tree — and the consumer then falls back to its own
    layout.

    ``present``, ``caveats`` and ``folder`` are what the resolver read AT that
    destination, and a consumer takes them from here rather than probing the
    path a second time: the resolver follows the symlinks a distribution
    strings through the firmware tree, while a bare existence check answers
    about whatever the consumer's own join happens to name. ``present`` is
    three-valued because "could not look" is not "not there"; ``caveats`` names
    what else the reading found there, in the resolver's stable codes.

    ``folder`` is the verdict about what a **declared folder** holds, and it is
    ``None`` for every file declaration. On a folder declaration it always
    stands, carrying the resolver's own three-valued answer: the folder holds an
    image the emulator accepts, it holds none, or nothing established which.
    Reading contents costs a read of every candidate's bytes, so a reading asked
    without that check settles the shapes a stat can settle and answers ``None``
    for the rest.

    ``supplied_by`` names the distribution whose own copy is sitting at the
    destination, as the resolver writes that distribution's name — a display
    form it derives from its own identifier, never one mapped here. ``None``
    claims nothing: the resolver states it only where it established the
    provenance.

    All four go silent together with ``relative_path``: with no location to
    honour, the consumer's fallback layout is a different place from the one
    that was read, and a reading carried across would describe somewhere the
    consumer will never write. ``declared_kind`` does not, because it is a
    property of the declaration rather than of the destination.

    ``declaration`` is the resolver's own word for HOW the entry ``description``
    came from stated what it wants — ``read`` off a libretro core's own ``.info``
    beside it, ``packaged`` out of the card that stands in for an emulator
    shipping none. It rides here because the two speak in different registers:
    a ``.info``'s prose is a packager's label for what the file IS
    (``(PS1 JP BIOS)``), a card's is atlas explaining the requirement in whole
    sentences, and a surface that shows one may not want the other. It is the
    declaration of the entry this placement was read from (``emulator``).

    ``checked`` is what became of the BYTES at that destination, in the
    resolver's own stable vocabulary, and it is read off the same entry
    ``declaration`` is. It is carried rather than interpreted because the eight
    values are not one axis: three of them say a comparison HAPPENED and differ
    only in what it found, and folding them into the verdict beside them would
    make a file the emulator read and did not recognise indistinguishable from
    one whose bytes never came back.
    ``None`` is no statement at all — the ordinary answer for a file that is
    simply absent, and for every reading that asked no content question.

    It is not a second verdict. ``satisfied`` says whether the requirement is
    met; this says what was done to establish it, which is what a surface needs
    to word a withheld or unmet answer honestly.

    ``missing_configured_image`` is set where the emulator the reading came
    from is configured to open a file in this folder that is not there
    (:class:`MissingConfiguredImage`). It goes silent with the destination half,
    and it is never a verdict either.

    ``wants`` is never empty: a placement exists because at least one emulator
    declared the file, and a placement without an owning emulator is exactly the
    orphaned entry this model removes.

    **A placement is ONE emulator's requirement**: ``emulator`` is the
    identity whose declaration it was read from, ``None`` for an entry the
    resolver could not identify. Two emulators declaring one name at two places
    — Kronos's ``kronos/saturn_bios.bin`` beside YabaSanshiro's
    ``saturn_bios.bin`` — are two placements, never folded into one.

    ``satisfied`` is the resolver's verdict over that requirement: the right file
    is where the emulator opens it, it is not (absent, the wrong bytes, refused,
    the wrong shape), or nothing established which (the place could not be
    looked at, the bytes could not be read, recognised or compared). For a
    folder declaration it is the verdict over what the folder HOLDS. It does not
    go silent with the destination half: it is about the place the emulator
    opens, wherever that is.
    """

    file_name: str
    relative_path: str | None
    description: str
    wants: tuple[FirmwareWant, ...]
    present: bool | None = None
    declared_kind: str = DECLARED_FILE
    declaration: str | None = None
    caveats: tuple[str, ...] = ()
    folder: FolderVerdict | None = None
    supplied_by: str | None = None
    checked: str | None = None
    missing_configured_image: MissingConfiguredImage | None = None
    emulator: str | None = None
    satisfied: bool | None = None

    @property
    def required_by_any(self) -> bool:
        """Does any emulator refuse to run without this file?"""
        return any(want.required for want in self.wants)

    @property
    def declares_directory(self) -> bool:
        """Does the emulator open this declaration as a folder it lists?"""
        return self.declared_kind == DECLARED_DIRECTORY

    @property
    def place_unreadable(self) -> bool:
        """Could the resolver not look at the place this file goes?

        Such a place is never downloaded into: a download could
        overwrite a file nobody could see, and a place the resolver cannot read
        is usually one the emulator cannot read either. Once it is readable
        again, the verdict follows by itself and so does the download.
        """
        return CAVEAT_PATH_INACCESSIBLE in self.caveats

    @property
    def destination(self) -> str:
        """Where the file belongs under the BIOS directory, relative to its root.

        The declared location where there is one, and the bare file name
        otherwise — the flat default that has always applied to a file with no
        stated subdirectory. Callers still join this under their own root
        through ``safe_join``; it is a path segment, not an address.
        """
        return self.relative_path or self.file_name


@dataclass(frozen=True)
class FirmwareCatalogue:
    """Everything one platform's emulators want, and which of them could not be asked.

    ``placements`` holds one entry per emulator and declared file name in the
    answer's scope (:class:`FirmwarePlacement`), each that emulator's own place
    and verdict; which emulators declare the name at all is each entry's
    ``wants``. A launch reads them through :meth:`placements_for`, which never
    lets another emulator's place stand for the launching one's. What does NOT
    live on a row is whether an absence
    may be read as "nothing wants it" — that is :meth:`reading_complete_for`, and
    it is asked about the ONE emulator the game will launch with, because an
    emulator the platform offers and the user does not launch with cannot make
    the launch's answer doubtful. The emulators that could not be asked are named
    in ``unread_emulators``.

    ``resolved`` is ``False`` when the reading did not happen at all — no
    installation found, or the resolver refused. Then nothing may be ruled out
    for anyone, whatever the scope.

    ``caveats`` carries the resolver's stable degradation codes, never its human
    messages: the codes are the contract, the messages are prose.

    ``emulator_verdicts`` is the per-emulator half — keyed by identity — and it
    is deliberately separate from ``placements``: a placement is one file every
    surface reads the same way, while a verdict is about the emulator a
    particular game will launch with. Empty for a reading that did not happen,
    and an emulator it holds no entry for is one nothing was recorded about
    (:meth:`verdict_for`).

    ``groups`` are the one-of requirements the answer states, per emulator
    (:meth:`groups_for`). Every option's file is a placement as well; a group
    is the requirement over them, which no placement can carry.
    """

    placements: tuple[FirmwarePlacement, ...]
    unread_emulators: frozenset[str]
    resolved: bool
    caveats: tuple[str, ...] = ()
    emulator_verdicts: Mapping[str, CoreFirmwareVerdict] = field(default_factory=dict)
    groups: tuple[FirmwareGroup, ...] = ()

    def verdict_for(self, emulator: str | None) -> CoreFirmwareVerdict | None:
        """What was recorded about *emulator*, or ``None`` where nothing was.

        ``None`` for an emulator with no entry, and for a caller with no identity
        to name — an unidentified emulator is not a licence to answer for one.
        """
        return self.emulator_verdicts.get(emulator) if emulator is not None else None

    def groups_for(self, emulator: str | None) -> tuple[FirmwareGroup, ...]:
        """The one-of groups *emulator* states, in the order the answer gave them.

        Empty for an emulator that states none and for a caller with no identity
        to name — an unidentified emulator owns no group.
        """
        if emulator is None:
            return ()
        return tuple(group for group in self.groups if group.emulator == emulator)

    def by_file_name(self) -> dict[str, FirmwarePlacement]:
        """The placements indexed by file name, the first declarer of each name standing for it.

        The index for a caller with no launching emulator to scope to — one
        firmware id, the home migration's sweep. A caller that has one asks
        :meth:`placements_for` instead.
        """
        index: dict[str, FirmwarePlacement] = {}
        for placement in self.placements:
            index.setdefault(placement.file_name, placement)
        return index

    def placements_for(self, emulator: str | None) -> dict[str, FirmwarePlacement]:
        """The placements indexed by file name, *emulator*'s own wherever it declares the name.

        Where the launching emulator declares a file, the row is its requirement,
        at the place it opens and with its verdict: YabaSanshiro's
        ``saturn_bios.bin`` is the one directly under the BIOS root, whatever
        Kronos declares under ``kronos/``. A name it does not declare keeps the
        first declarer's placement, as :meth:`by_file_name` does. *emulator*
        ``None`` is :meth:`by_file_name` itself.
        """
        index = self.by_file_name()
        if emulator is None:
            return index
        for placement in self.placements:
            if placement.emulator == emulator:
                index[placement.file_name] = placement
        return index

    def reading_complete_for(self, emulator: str | None) -> bool:
        """May an absence be read as "nothing wants it" for the launching *emulator*?

        *emulator* is the one the game will launch with, and the question is
        whether it was asked. **The doubt is scoped to it alone**: an unreadable
        emulator the platform offers and this launch does not use says nothing
        about this launch, so letting it withhold the verdict would grey out a
        platform over an emulator nobody is running. What it costs is that a user
        switching emulators can move a platform from a finished answer to a
        withheld one — which is the truth about the new launch rather than a
        regression in the old one.

        ``None`` is an emulator that could not be identified or resolved at all,
        and it is refused: read as complete it would license the strongest claim
        this vocabulary can make — "nothing here wants any of these files" — off
        asking nobody, which is the very collapse the four values exist to
        prevent. So is an emulator the answer holds no entry for:
        nobody asked it, whatever the reading answered about the others.
        """
        if not self.resolved or emulator is None:
            return False
        return emulator in self.emulator_verdicts and emulator not in self.unread_emulators


def classify_wanted(placement: FirmwarePlacement | None, complete: bool) -> str:
    """Classify one server file against the catalogue — one of :data:`WANTED_VALUES`.

    *placement* is the catalogue's entry for the file, ``None`` when it holds
    none; *complete* is :meth:`FirmwareCatalogue.reading_complete_for` asked
    about the emulator the game will launch with.
    """
    if placement is not None:
        return WANTED_NEEDED if placement.required_by_any else WANTED_OPTIONAL
    return WANTED_NOT_NEEDED if complete else WANTED_UNKNOWN
