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

A second axis runs beside that one and is not a property of any file: what is
recorded about the **system** an emulator declares for. A libretro ``.info`` can
mark a slot required or optional and nothing else — no way to say "one of
these", and no way to say the console does not start without one. An author who
knows a PlayStation needs a BIOS image therefore has two lossy moves, and the
deployed catalogue takes both: SwanStation marks all five of its images
optional, which reads per file as a finished answer that nothing is missing,
while Beetle PSX marks three of its own required, which reads as three separate
prerequisites where the console asks for one. Neither states the console's
demand, so no reading of the declaration can be relied on to carry it. The
resolver answers that half from a packaged table
(:class:`CoreFirmwareVerdict`), and its ``None`` means nobody has looked at the
system, never that the system needs nothing.
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

# The resolver's stable degradation codes this vocabulary acts on, spelled here
# because ``domain/`` may not import the vendored resolver. Every one of them is
# held equal to its upstream constant by ``tests/adapters/test_atlas_firmware.py``,
# so a rename upstream is a red test rather than a rule that quietly stops firing.
CAVEAT_PATH_OBSTRUCTED = "firmware-path-obstructed"

# Codes that withhold a FILE row's verdict: something is at the destination and
# it is not the file, so neither "there" nor "absent" is a claim the reading
# supports. A folder declaration is not judged from here at all — its verdict is
# :class:`FolderVerdict`, which the resolver answers by listing the folder.
VERDICT_WITHHOLDING_CAVEATS = frozenset({CAVEAT_PATH_OBSTRUCTED})


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
class CoreFirmwareVerdict:
    """What the resolver says about one core BEYOND the files it declares.

    ``system_firmware`` is world knowledge about the console — one of
    :data:`SYSTEM_FIRMWARE_STATES`, or ``None`` where the packaged table records
    nothing about the system at all. **``None`` is not "nothing is needed"**: the
    table covers the systems somebody has looked at, so an absent entry is an
    unasked question, and the same rule holds for it that holds for
    :data:`WANTED_UNKNOWN`.

    ``requirements_met`` is the resolver's own three-valued verdict over that
    core's whole declaration weighed against what is on disk and against the
    system entry. It is **carried and not read**, and that is a decision rather
    than an omission: it folds the per-file conjunction and the system's own
    disjunction into one answer, so it can say *this core will not start* and
    cannot say which of the two is why — while both of the things it can be
    saying (a required file absent, or one present with the wrong bytes) already
    reach the surfaces through the file rows, by name. Reading it as a second
    opinion on a question the rows have answered is the misreading it exists to
    prevent: ignorance here is ``None``, so a ``False`` is a demonstrated
    statement and never a doubt.
    """

    system_firmware: str | None = None
    requirements_met: bool | None = None

    @property
    def system_needs_an_image(self) -> bool:
        """Does the console need a firmware image this core does not carry itself?

        True for exactly one of the four states. The other three are recorded
        answers that leave the file rows to speak for themselves — the core
        supplies its own substitute, the system was established to start without
        one, or nobody has established which.
        """
        return self.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT


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
    sentences, and a surface that shows one may not want the other. It names the
    declaration the DESCRIPTION came from and no other — a file two emulators
    declare has one description here, taken from the first of them
    (``adapters.atlas_firmware._placement_for``), so any other entry's word for
    it would describe prose this row does not carry.

    ``checked`` is what became of the BYTES at that destination, in the
    resolver's own stable vocabulary, and it is read off the same entry
    ``declaration`` is — the first of the pairs under this name. It is carried
    rather than interpreted because the eight values are not one axis: three of
    them say a comparison HAPPENED and differ only in what it found, and folding
    them into the verdict beside them is what made a file the emulator read and
    did not recognise indistinguishable from one whose bytes never came back.
    ``None`` is no statement at all — the ordinary answer for a file that is
    simply absent, and for every reading that asked no content question.

    It is not a second verdict. ``present`` and ``folder`` say whether the
    requirement is met; this says what was done to establish it, which is what a
    surface needs to word a withheld answer honestly.

    ``wants`` is never empty: a placement exists because at least one emulator
    declared the file, and a placement without an owning emulator is exactly the
    orphaned entry this model removes.
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

    @property
    def required_by_any(self) -> bool:
        """Does any emulator refuse to run without this file?"""
        return any(want.required for want in self.wants)

    @property
    def declares_directory(self) -> bool:
        """Does the emulator open this declaration as a folder it lists?"""
        return self.declared_kind == DECLARED_DIRECTORY

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

    ``placements`` is one file per name across every emulator in the answer's
    scope: a file is declared, or it is not, and which emulators declare it is
    the row's own ``wants``. What does NOT live on a row is whether an absence
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
    """

    placements: tuple[FirmwarePlacement, ...]
    unread_emulators: frozenset[str]
    resolved: bool
    caveats: tuple[str, ...] = ()
    emulator_verdicts: Mapping[str, CoreFirmwareVerdict] = field(default_factory=dict)

    def verdict_for(self, emulator: str | None) -> CoreFirmwareVerdict | None:
        """What was recorded about *emulator*, or ``None`` where nothing was.

        ``None`` for an emulator with no entry, and for a caller with no identity
        to name — an unidentified emulator is not a licence to answer for one.
        """
        return self.emulator_verdicts.get(emulator) if emulator is not None else None

    def emulators_needing_a_system_image(self) -> frozenset[str]:
        """The emulators whose CONSOLE the table says will not start without an image.

        The per-emulator half of :meth:`verdict_for`, read over every emulator at
        once — the widest form of the answer, and the set
        :meth:`emulators_needing_one_of_their_files` narrows to the emulators that
        state the demand as a disjunction. Every other recording is left out,
        including the absent entry: an emulator the table says nothing about is an
        unasked question, and this set answers only where something was recorded.
        """
        return frozenset(
            emulator for emulator, verdict in self.emulator_verdicts.items() if verdict.system_needs_an_image
        )

    def emulators_needing_one_of_their_files(self) -> dict[str, int]:
        """Emulator → how many files it declares, for those that state a DISJUNCTION.

        The narrower half of :meth:`emulators_needing_a_system_image`, and the one
        a surface can word on a row. An emulator is here only where its console
        needs an image **and** it marks nothing required anywhere in the
        catalogue, because that is the only shape in which "one of these" is the
        whole of what it says. Where an emulator does mark files required, the
        console's demand already reaches every surface as those rows' own
        requirement, and a second statement of it beside them would say the same
        thing twice in weaker words. The deployed catalogue has both shapes over
        one PlayStation: SwanStation marks all five of its images optional, Beetle
        PSX marks three of its own required.

        The count is the emulator's whole declaration in this answer rather than a
        platform's row set — it is the number a surface says "one of its N BIOS
        files" with, and a platform whose list happens to carry four of the five
        would otherwise word the demand as a number the emulator never stated. An
        emulator with no entry here is silent, which is also every one the packaged
        table records nothing about.
        """
        demanding = self.emulators_needing_a_system_image()
        declared: dict[str, int] = {}
        requires_something: set[str] = set()
        for placement in self.placements:
            for want in placement.wants:
                if want.emulator is None:
                    continue
                declared[want.emulator] = declared.get(want.emulator, 0) + 1
                if want.required:
                    requires_something.add(want.emulator)
        return {
            emulator: count
            for emulator, count in declared.items()
            if emulator in demanding and emulator not in requires_something
        }

    def by_file_name(self) -> dict[str, FirmwarePlacement]:
        """The placements indexed by file name — the shape every lookup wants.

        A caller classifying a whole server listing builds this once and reads
        it per file; one built per file would rescan a few hundred placements
        for every row.
        """
        return {placement.file_name: placement for placement in self.placements}

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
        prevent.
        """
        if not self.resolved or emulator is None:
            return False
        return emulator not in self.unread_emulators


def classify_wanted(placement: FirmwarePlacement | None, complete: bool) -> str:
    """Classify one server file against the catalogue — one of :data:`WANTED_VALUES`.

    *placement* is the catalogue's entry for the file, ``None`` when it holds
    none; *complete* is :meth:`FirmwareCatalogue.reading_complete_for` asked
    about the emulator the game will launch with.
    """
    if placement is not None:
        return WANTED_NEEDED if placement.required_by_any else WANTED_OPTIONAL
    return WANTED_NOT_NEEDED if complete else WANTED_UNKNOWN
