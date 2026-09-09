"""The vocabulary a firmware answer arrives in, and the one classification over it.

Which emulator wants which file is read live off the machine by the resolver
behind :class:`services.protocols.FirmwareResolver`; ``domain/`` may not import
the vendored resolver at all, so this module holds only the words the answer
comes back in — the same split :mod:`domain.sync_action` makes for the save-sync
core.

The resolver answers per *file* whether a core needs it or merely accepts it.
"Nothing wants it" and "nothing could be established" are not properties of a
file — they are properties of the **reading**, one level up on the core, which is
why :class:`FirmwareCatalogue` names the cores it could not ask. Our own file
list comes from the RomM server rather than from the resolver, so each server
file is classified by whether the catalogue holds a placement for it, and by
whether the reading was complete for the emulators the caller's platform offers:

``needed`` · ``optional`` · ``not_needed`` (the whole reading succeeded and no
emulator asked for it) · ``unknown`` (something in the reading failed, so no
claim can be made either way).

Collapsing the last two into one value is the defect this vocabulary exists to
prevent: a file nothing wants is a finished answer, and a file we could not ask
about is not.

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

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

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
    for every verdict but a satisfied one. ``caveats`` carries the stable codes
    stating why the verdict reads as it does — the codes are the contract, the
    messages are prose — and a surface takes the CAUSE from them, because
    ``satisfied`` is the verdict alone and carries none of it.
    """

    satisfied: bool | None
    images: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


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

    ``core_so`` is ``None`` for an emulator that ships no libretro core — the
    identifier space is the core's ``.so`` basename and inventing one for a
    standalone emulator would collide with it. ``required`` is the resolver's
    own two-valued answer: the core will not run without the file, or it will.

    No display label: what a core is called to a user is ES-DE's answer, read
    per platform off ``es_systems.xml``, and a second spelling arriving with the
    requirement would be a second thing to keep in step with it.
    """

    core_so: str | None
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
    ``None`` for every file declaration and for a folder nothing looked inside
    — the resolver answers it only when asked to verify contents, which costs a
    read of every candidate's bytes.

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
    caveats: tuple[str, ...] = ()
    folder: FolderVerdict | None = None
    supplied_by: str | None = None

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
    """Everything the installed emulators want, and which of them could not be asked.

    ``placements`` is machine-wide: a file is declared, or it is not, and the
    answer does not change with the surface asking. What DOES depend on the
    caller is whether an absence may be read as "nothing wants it", and that is
    :meth:`reading_complete_for` — the emulators that could not be asked are
    named in ``unread_cores``, so a caller scopes the doubt to the emulators its
    platform actually offers instead of letting one unreadable core anywhere
    silence every answer.

    ``resolved`` is ``False`` when the reading did not happen at all — no
    installation found, or the resolver refused. Then nothing may be ruled out
    for anyone, whatever the scope.

    ``caveats`` carries the resolver's stable degradation codes, never its human
    messages: the codes are the contract, the messages are prose.

    ``core_verdicts`` is the per-core half — keyed by the core's ``.so`` stem, in
    the plugin's own identifier space — and it is deliberately separate from
    ``placements``: a placement is one file every surface reads the same way,
    while a verdict is about the emulator a particular game will launch with.
    Empty for a reading that did not happen, and a core it holds no entry for is
    a core nothing was recorded about (:meth:`verdict_for`).
    """

    placements: tuple[FirmwarePlacement, ...]
    unread_cores: frozenset[str]
    resolved: bool
    caveats: tuple[str, ...] = ()
    core_verdicts: Mapping[str, CoreFirmwareVerdict] = field(default_factory=dict)

    def verdict_for(self, core_so: str | None) -> CoreFirmwareVerdict | None:
        """What was recorded about *core_so*, or ``None`` where nothing was.

        ``None`` for a core with no entry, and for a caller with no core to name
        — an unresolvable active core is not a licence to answer for one.
        """
        return self.core_verdicts.get(core_so) if core_so is not None else None

    def by_file_name(self) -> dict[str, FirmwarePlacement]:
        """The placements indexed by file name — the shape every lookup wants.

        A caller classifying a whole server listing builds this once and reads
        it per file; one built per file would rescan a few hundred placements
        for every row.
        """
        return {placement.file_name: placement for placement in self.placements}

    def reading_complete_for(self, core_sos: Collection[str] | None) -> bool:
        """May an absence be read as "nothing wants it" for the scope *core_sos*?

        ``core_sos`` is the scope the caller is answering for — the libretro
        cores its platform offers — and the question is whether every one of
        them was asked. ``None`` means the caller could not establish its own
        scope, which is itself a reason to rule nothing out.

        **An empty scope is refused too**, though every emulator in it was
        vacuously asked. Read as complete it would license the strongest claim
        this vocabulary can make — "no emulator here wants any of these files" —
        off asking nobody, which is the very collapse the four values exist to
        prevent. A caller reaching this with no core to name has not established
        a reading, whether it says so with ``None`` or with an empty list, so
        both answer the same. That makes the refusal belt-and-braces rather than
        the only guard: ``services/firmware/status.py``'s ``_core_scope`` already returns ``None``
        for a platform ES-DE offers no libretro core for.
        """
        if not self.resolved or not core_sos:
            return False
        return not self.unread_cores.intersection(core_sos)


def classify_wanted(placement: FirmwarePlacement | None, complete: bool) -> str:
    """Classify one server file against the catalogue — one of :data:`WANTED_VALUES`.

    *placement* is the catalogue's entry for the file, ``None`` when it holds
    none; *complete* is the catalogue's own reading state.
    """
    if placement is not None:
        return WANTED_NEEDED if placement.required_by_any else WANTED_OPTIONAL
    return WANTED_NOT_NEEDED if complete else WANTED_UNKNOWN


def unanswered_folder_cores(
    placements: Mapping[str, FirmwarePlacement], core_sos: Collection[str] | None
) -> tuple[str, ...]:
    """The cores in *core_sos* whose folder declaration still has no verdict.

    The scope of the one question that costs a content read: the resolver
    answers a folder declaration from the folder's own listing, and a listing it
    was not asked to make leaves ``folder`` unset. A row the machine-wide
    reading already settled is left alone — asking about it would pay a whole
    verified per-core resolve for an answer in hand — and which rows those are
    is the resolver's own verdict rather than a list of shapes kept here.

    Sorted and deduplicated: an ES-DE catalogue can list one core under two
    entries, and the caller asks the resolver once per name it is handed.
    """
    if not core_sos:
        return ()
    scope = set(core_sos)
    return tuple(
        sorted(
            {
                want.core_so
                for placement in placements.values()
                if placement.declares_directory and placement.folder is None
                for want in placement.wants
                if want.core_so is not None and want.core_so in scope
            }
        )
    )


def merge_folder_verdicts(
    placements: Mapping[str, FirmwarePlacement], verdicts: Mapping[str, FolderVerdict]
) -> dict[str, FirmwarePlacement]:
    """*placements* with each named folder's verdict folded into its row.

    The verdict's caveats join the destination's rather than replacing them:
    both are statements about one place, and a row that says a folder holds no
    image and that something obstructs it is saying two true things.

    A verdict for a file declaration is dropped. The resolver states one only
    over a folder it listed, so such an entry would mean the two disagree about
    what the emulator opens, and the declaration is the half that decides.
    """
    merged = dict(placements)
    for file_name, verdict in verdicts.items():
        placement = merged.get(file_name)
        if placement is None or not placement.declares_directory:
            continue
        merged[file_name] = replace(placement, folder=verdict, caveats=(*placement.caveats, *verdict.caveats))
    return merged
