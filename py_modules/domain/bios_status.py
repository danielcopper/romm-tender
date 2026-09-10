"""BIOS readiness — the status shape every surface renders, and the one level over it.

Domain owns the unknown/ok/partial/missing LEVEL (``compute_bios_level``) and the
compact status token (``compute_bios_label``) — the single source of truth for
the readiness decision the game-detail panel, the play-section row and the System
page all read. Verbose per-surface phrasing and the status-dot colour are UI
concerns and deliberately do NOT live here.

Two axes run through this module and must not be folded into one. **What wants a
file** is :mod:`domain.firmware_wants`' four-valued answer, and it is a property
of the machine: the same file answers the same way on every surface. **Whether
the game in front of the user is ready to launch** is scoped to the core it will
launch with, which is why an entry carries ``required_by_active`` beside its
``wanted`` and why the counts key off the first. A file three other cores demand
is not a missing prerequisite for this launch.

A third axis joins them and is a THIRD axis rather than a third count, because
it is not counted at all: the **system image** (:func:`classify_system_image`).
Where the console does not start without one of the images the launching core
declares, what is missing is one file out of many rather than each of many —
folding it into ``required_count`` would report every one of them as required
where the truth is "one of these". It carries its own value and its own
sentence, and it can only ever make the verdict less green.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from domain.firmware_wants import (
    DECLARED_FILE,
    VERDICT_WITHHOLDING_CAVEATS,
    WANTED_NEEDED,
    WANTED_OPTIONAL,
    WANTED_UNKNOWN,
    classify_wanted,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain.firmware_wants import CoreFirmwareVerdict, FirmwarePlacement

BIOS_LEVEL_UNKNOWN = "unknown"
BIOS_LEVEL_OK = "ok"
BIOS_LEVEL_PARTIAL = "partial"
BIOS_LEVEL_MISSING = "missing"

# The compact token :func:`compute_bios_label` answers for the unknown level. A
# constant because a caller that ships the level without going through the
# function still has to name the label that goes with it.
BIOS_LABEL_UNKNOWN = "Unknown"
BIOS_LABEL_MISSING = "Missing"

# The four answers to "does the launching core have the image its CONSOLE cannot
# start without". Not a count and never one: the requirement is disjunctive.
#
# ``not_demanded`` is the neutral value and covers four different recordings —
# the core carries its own substitute, the console was established to start with
# nothing present, nobody has established which, and nothing is recorded about
# the console at all. It says this axis makes no claim, and it must never be
# read as "this console needs no firmware": the last of those four is an unasked
# question, and reading it as an answer is the collapse
# :mod:`domain.firmware_wants` exists to prevent.
#
# ``unsettled`` has two producers and no others: a row nothing could judge among
# the ones the core declares, and a console whose demand this platform's list
# carries no row for at all.
SYSTEM_IMAGE_NOT_DEMANDED = "not_demanded"
SYSTEM_IMAGE_HELD = "held"
SYSTEM_IMAGE_ABSENT = "absent"
SYSTEM_IMAGE_UNSETTLED = "unsettled"

SYSTEM_IMAGE_VALUES = (
    SYSTEM_IMAGE_NOT_DEMANDED,
    SYSTEM_IMAGE_HELD,
    SYSTEM_IMAGE_ABSENT,
    SYSTEM_IMAGE_UNSETTLED,
)


@dataclass(frozen=True)
class AvailableCore:
    """A RetroArch core available for a platform."""

    core_so: str
    label: str
    is_default: bool


@dataclass(frozen=True)
class BiosFileEntry:
    """Status of a single BIOS/firmware file on a platform's list.

    ``wanted`` is the machine's answer about the file (one of
    :data:`domain.firmware_wants.WANTED_VALUES`); ``required_by_active`` is the
    launching core's, and only that one decides whether the file is counted as a
    missing prerequisite.

    ``on_server`` is clear for a file an installed emulator asks for that the
    RomM library does not hold. Such a file is real, and missing, and nothing on
    this page can fetch it — so it is shown, and it is kept out of every count
    that offers the user an action.

    ``satisfied`` is the row's verdict and the axis the REQUIRED counts key off:
    the requirement is met, is not met, or nothing established which. It is not
    ``downloaded``, which answers only whether something is at the destination —
    for a **folder** declaration the two come apart completely, since what
    satisfies the requirement is a file inside the folder and the folder itself
    is always there on a stock RetroDECK. The library's own held/offered ratio
    is a third axis and keys off neither (``on_server`` and ``downloaded``).

    ``declared_kind`` is what the emulator opens the destination at, and it is a
    property of the DECLARATION: a folder that is not there is still a folder to
    create rather than a file to fetch, which is why no surface may offer such a
    row as a download. ``caveats`` and ``images`` are the resolver's own words
    for what it found and what a satisfied folder holds, and a surface takes the
    CAUSE of a verdict from them, because ``satisfied`` is deliberately the
    verdict alone and carries none of it.

    ``supplied_by`` is what the reading found at the destination, carried per row
    so the surfaces can say what a row is instead of describing every one of them
    as a file the library is missing. It defaults to the silent answer, which is
    the one a row nothing declares has.

    ``declared_path`` is where the emulator said the file goes, relative to the
    firmware root — ``dc/dc_boot.bin`` where a subdirectory was declared, the
    bare name otherwise. It is carried because ``file_name`` is its basename and
    ``local_path`` is it joined under a root the frontend does not know, so
    without it no surface can state the one thing a user placing a file by hand
    needs: which folder it goes in. 207 of the 695 declarations a stock
    RetroDECK ships name a subdirectory, and their descriptions spell it in only
    115 of those, so the description is not a substitute.
    """

    file_name: str
    downloaded: bool
    local_path: str
    declared_path: str
    description: str
    wanted: str
    required_by_active: bool
    # {core_so: {"required": bool, "needs_one_of": int | None}} — per core, what
    # its own declaration says about this file and, where that core states a
    # disjunction, how many files the demand is spread over. Two speakers, two
    # keys, never folded into one.
    cores: dict[str, dict[str, Any]]
    used_by_active: bool
    on_server: bool = True
    # Is this row one of the images that would answer the launching core's
    # console on its own? Set only where that core states a DISJUNCTION — see
    # :func:`build_file_entry`, which explains why it is silent for a core that
    # does state required files.
    system_image_candidate: bool = False
    supplied_by: str | None = None
    satisfied: bool | None = None
    declared_kind: str = DECLARED_FILE
    caveats: tuple[str, ...] = ()
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class BiosStatus:
    """Aggregated BIOS status for a platform, ready for frontend display."""

    platform_slug: str
    server_count: int
    local_count: int
    all_downloaded: bool
    required_count: int | None
    required_downloaded: int | None
    files: tuple[BiosFileEntry, ...]
    active_core: str | None
    active_core_label: str | None
    available_cores: tuple[AvailableCore, ...]
    # Server files the machine has an answer about (``needed`` or ``optional``).
    # ``None`` means the caller did not supply it, so the "unknown" decision is
    # not made; ``0`` alongside ``unknown_count`` means nothing about this
    # platform's firmware could be established at all.
    known_count: int | None = None
    unknown_count: int = 0
    # Whether an absence from this platform's file list may be read as "nothing
    # wants it" — ``FirmwareCatalogue.reading_complete_for`` scoped to the
    # emulators the platform offers. Defaults to ``True`` so a caller that does
    # not supply it keeps the level it always got; the one decision it moves is
    # a platform with no files at all.
    reading_complete: bool = True
    # The launching core's system-image answer (:func:`classify_system_image`),
    # one of :data:`SYSTEM_IMAGE_VALUES`. Defaults to the neutral value so a
    # caller that does not supply it keeps the verdict it always got.
    system_image: str = SYSTEM_IMAGE_NOT_DEMANDED
    cached_at: float = 0.0


def format_bios_status(
    bios: dict[str, Any],
    platform_slug: str,
    *,
    reading_complete: bool = True,
    system_image: str = SYSTEM_IMAGE_NOT_DEMANDED,
    cached_at: float = 0.0,
) -> BiosStatus:
    """Build a frontend-ready BiosStatus dataclass from raw firmware check result."""
    raw_files = bios.get("files", [])
    if raw_files and isinstance(raw_files[0], dict):
        files: tuple[BiosFileEntry, ...] = tuple(
            BiosFileEntry(
                file_name=f.get("file_name", ""),
                downloaded=f.get("downloaded", False),
                local_path=f.get("local_path", ""),
                declared_path=f.get("declared_path", "") or f.get("file_name", ""),
                description=f.get("description", ""),
                wanted=f.get("wanted", WANTED_UNKNOWN),
                required_by_active=f.get("required_by_active", False),
                cores=f.get("cores", {}),
                used_by_active=f.get("used_by_active", True),
                on_server=f.get("on_server", True),
                system_image_candidate=f.get("system_image_candidate", False),
                supplied_by=f.get("supplied_by"),
                satisfied=f.get("satisfied"),
                declared_kind=f.get("declared_kind", DECLARED_FILE),
                caveats=tuple(f.get("caveats", ())),
                images=tuple(f.get("images", ())),
            )
            for f in raw_files
        )
    else:
        files = tuple(raw_files)

    raw_cores = bios.get("available_cores", [])
    available_cores: tuple[AvailableCore, ...] = tuple(
        AvailableCore(
            core_so=c.get("core_so", c.get("core", "")),
            label=c.get("label", ""),
            is_default=c.get("is_default", False),
        )
        for c in raw_cores
    )

    return BiosStatus(
        platform_slug=platform_slug,
        server_count=bios.get("server_count", 0),
        local_count=bios.get("local_count", 0),
        all_downloaded=bios.get("all_downloaded", False),
        required_count=bios.get("required_count"),
        required_downloaded=bios.get("required_downloaded"),
        files=files,
        active_core=bios.get("active_core"),
        active_core_label=bios.get("active_core_label"),
        available_cores=available_cores,
        known_count=bios.get("known_count"),
        unknown_count=bios.get("unknown_count", 0),
        reading_complete=reading_complete,
        system_image=system_image,
        cached_at=cached_at,
    )


# The answer for a caller that has not asked which cores state a disjunctive
# demand: nothing is claimed for any core. Read-only, so the shared default
# cannot be written through.
_NO_DISJUNCTIVE_CORES: Mapping[str, int] = MappingProxyType({})


def build_file_entry(
    file_name: str,
    downloaded: bool,
    dest: str,
    placement: FirmwarePlacement | None,
    complete: bool,
    active_core_so: str | None,
    *,
    on_server: bool = True,
    cores_needing_one_of: Mapping[str, int] = _NO_DISJUNCTIVE_CORES,
) -> BiosFileEntry:
    """Build a single file status entry from the machine's answer about it.

    ``placement`` is the catalogue's entry for the file (``None`` when nothing
    declares it) and ``complete`` the reading state for the platform's own
    emulators — together they decide ``wanted``. ``active_core_so`` is the core
    the game will launch with, or ``None`` when it could not be resolved; then
    every declaring core stands in for it, which is the same permissive default
    the platform has always fallen back to.

    ``cores_needing_one_of`` is
    :meth:`~domain.firmware_wants.FirmwareCatalogue.cores_needing_one_of_their_files`
    — the cores whose console needs an image and that mark nothing required, each
    with the number of files it declares. It rides on each core's own entry in
    ``cores`` because the two statements there belong to different speakers:
    ``required`` is what that core's ``.info`` says about this file,
    ``needs_one_of`` is what the packaged table says about that core's console,
    counted over the core's whole declaration. A core can say ``optional`` about
    every one of five files while the console cannot start without one of them,
    and that pair is exactly what a surface listing the core has to be able to
    show. Nothing is folded: the declaration is carried unaltered.

    ``system_image_candidate`` is the same map read for the ACTIVE core, so the
    row and the per-core entries cannot disagree about which cores state a
    disjunction: this row is one of the images that would answer the launching
    core's console on its own.

    **It is deliberately narrower than the set**
    :func:`classify_system_image` **reads**, and the asymmetry is the point. That
    function weighs every image the active core declares; this flag marks those
    rows only where the core marks NOTHING required. Where a core does state
    required files — Beetle PSX declares three of the same PlayStation images
    ``required`` — those rows already carry the console's demand as plain
    ``required_by_active``, and marking them again would say one thing twice in
    two vocabularies. The flag is the DISPLAY axis for the disjunction, not a
    second readiness rule, so widening it to every image-demanding core would add
    no answer and would put two marks on one requirement.
    """
    folder = placement.folder if placement is not None else None
    wants = placement.wants if placement is not None else ()
    cores = {
        want.core_so: {"required": want.required, "needs_one_of": cores_needing_one_of.get(want.core_so)}
        for want in wants
        if want.core_so is not None
    }
    active_entry = cores.get(active_core_so) if active_core_so is not None else None
    if active_core_so is None:
        used_by_active = True
        required_by_active = placement.required_by_any if placement is not None else False
    else:
        used_by_active = active_core_so in cores if cores else True
        required_by_active = active_entry["required"] if active_entry is not None else False
    return BiosFileEntry(
        file_name=file_name,
        downloaded=downloaded,
        local_path=dest,
        declared_path=placement.destination if placement is not None else file_name,
        description=placement.description if placement is not None else file_name,
        wanted=classify_wanted(placement, complete),
        required_by_active=required_by_active,
        cores=cores,
        used_by_active=used_by_active,
        on_server=on_server,
        system_image_candidate=active_entry is not None and active_entry["needs_one_of"] is not None,
        supplied_by=placement.supplied_by if placement is not None else None,
        satisfied=_row_verdict(placement, downloaded),
        declared_kind=placement.declared_kind if placement is not None else DECLARED_FILE,
        caveats=placement.caveats if placement is not None else (),
        images=folder.images if folder is not None else (),
    )


def _row_verdict(placement: FirmwarePlacement | None, downloaded: bool) -> bool | None:
    """Is this row's requirement met? ``None`` where nothing established it.

    Three shapes, and the first is the reason the axis exists at all. A **folder
    declaration** is answered by the resolver's own listing of the folder, never
    by the folder being there: RetroDECK links LRPS2's ``pcsx2/bios`` onto the
    BIOS root, so it is present on every install, and reading presence as the
    verdict would report "All required ready" over a PS2 install with no BIOS
    file at all. No listing means no verdict.

    A **file** the reading found something else at — a directory in its way — is
    withheld for the mirror-image reason: something is at the destination and it
    is not the file, so neither "there" nor "absent" is a claim the reading
    supports.

    Everything else is ``downloaded``, which for a declared file is the
    resolver's own reading at the destination it will be opened from.
    """
    if placement is None:
        return downloaded
    if placement.declares_directory:
        return placement.folder.satisfied if placement.folder is not None else None
    if VERDICT_WITHHOLDING_CAVEATS.intersection(placement.caveats):
        return None
    return downloaded


def collect_firmware_status(
    items: list[dict[str, Any]],
    placements: Mapping[str, FirmwarePlacement],
    complete: bool,
    active_core_so: str | None,
    cores_needing_one_of: Mapping[str, int] = _NO_DISJUNCTIVE_CORES,
) -> tuple[BiosFileEntry, ...]:
    """Build BiosFileEntry objects for a list of pre-resolved firmware items.

    Each item must have keys: file_name, downloaded, dest; ``on_server``
    defaults to ``True`` for the items that came off the RomM listing.
    ``cores_needing_one_of`` is machine-wide and is read per row, so one core's
    console answers the same way on every file it declares.
    """
    return tuple(
        build_file_entry(
            item["file_name"],
            item["downloaded"],
            item["dest"],
            placements.get(item["file_name"]),
            complete,
            active_core_so,
            on_server=item.get("on_server", True),
            cores_needing_one_of=cores_needing_one_of,
        )
        for item in items
    )


def count_required(files: tuple[BiosFileEntry, ...]) -> tuple[int, int]:
    """``(required, of those downloaded)`` for the core the game will launch with.

    The badge's two numbers, derived in one place so the platform detail and
    the game-detail page can never disagree about which files count. A file the
    library does not hold counts here — it is genuinely required and genuinely
    absent, and excluding it would make the badge read ready over a file the
    core declares and does not have. What the count must never do is imply a
    download (whether anything is fetchable is a separate question, asked where
    the buttons are) — nor a LAUNCH outcome: at the RetroArch revision RetroDECK
    ships, the ``.info`` firmware list is read only by display surfaces
    (``menu_displaylist.c``), and neither ``task_content.c`` nor ``runloop.c``
    consults it, so a declaration cannot say whether a game starts.

    The second number is the row VERDICT, not ``downloaded``: for a folder
    declaration the two come apart, since RetroDECK links LRPS2's ``pcsx2/bios``
    onto the BIOS root and the folder is therefore present on every install.
    Counting presence there would read "All required ready" over a PS2 install
    with no BIOS file at all. A row nothing could judge raises the first number
    and not the second, and is not counted as missing either — that is
    :func:`count_required_withheld`, and the readiness verdict declines rather
    than picking one of the two.
    """
    required = [f for f in files if f.required_by_active]
    return len(required), sum(1 for f in required if f.satisfied)


def count_required_withheld(files: tuple[BiosFileEntry, ...]) -> int:
    """How many of the launching core's required files nothing could judge.

    The third number beside :func:`count_required`'s two, and the one that keeps
    a declined verdict from reading as an absence. A surface that warns about
    missing files subtracts it: what is left of ``required - withheld`` against
    ``required_downloaded`` is the requirement whose absence really was
    established.
    """
    return sum(1 for f in files if f.required_by_active and f.satisfied is None)


def classify_system_image(
    verdict: CoreFirmwareVerdict | None,
    files: tuple[BiosFileEntry, ...],
    active_core_so: str | None,
) -> str:
    """Does the launching core have the image its CONSOLE cannot start without?

    One of :data:`SYSTEM_IMAGE_VALUES`. The question only arises for a core the
    resolver's packaged table puts in that state; every other recording — a core
    carrying its own substitute, a console established to start with nothing, an
    open entry, no entry at all — answers :data:`SYSTEM_IMAGE_NOT_DEMANDED` and
    leaves the file rows to speak for themselves.

    **The requirement is a disjunction and is read as one.** The console asks for
    ONE of the images the core declares, so a single satisfied row answers it and
    the absent ones beside it are still one unmet requirement. That is why this
    is a value and not a pair of counts: put into ``required_count`` it would
    read ``0/N required files ready`` over a console that needs one image, with
    ``N`` every row the core declares — five, for the SwanStation this was
    observed on. The twenty in that page's ``0/20 files held`` is a different set
    again: the RomM library's inventory for the platform, which this answer
    neither counts nor is scoped to.

    **The demand comes from the table, the presence comes from the rows, and
    nothing here weighs one against the other.** ``requirements_met`` is not
    consulted, and reading it as a second opinion on the same question would be
    the misreading the field exists to prevent: at the resolver, ignorance is
    always ``None``, so a ``False`` is a demonstrated statement rather than a
    disagreement to be resolved. It has exactly two causes — a DIFFERENT required
    file is absent, or one that is there has the wrong bytes — and both leave one
    of our own required rows unmet, so the ordinary counts already report them,
    by name, which this axis never could. The second cause needs a content check
    to arise at all, and the whole-machine reading these rows come from is asked
    unverified by invariant, so on this path it cannot occur.

    **A row's** ``satisfied`` **is presence, not the resolver's usability
    verdict** — the name invites the second reading and does not carry it. For a
    declared file it is ``FirmwareDemand.is_downloaded``, which ends at
    ``placement.present is True``; for a folder declaration it is the verdict on
    what the folder HOLDS, and may be ``None``; and where something other than
    the expected file occupies the destination it is ``None`` too. Either
    ``None`` reads here as not held — the safe direction, since the alternative
    claims a readiness nothing established.

    *files* is this platform's list, so the disjunction spans the rows this core
    declares that the platform's own list carries — every one of them, BIOS image
    or not: a declaration mixes images with the odd data file (LRPS2 lists
    ``GameIndex.yaml`` beside its BIOS folder) and nothing on a row says which is
    which. That is upstream's reading too — ``CoreFirmware._system_image_in_place``
    folds the whole declaration in the same way — so the two agree rather than
    one of them quietly narrowing. The resolver reads it over every row the core
    declares machine-wide; the two sets come apart only for a core serving
    several systems, and upstream states that no core in its vector corpus or on
    its reference machine reaches this state while declaring for more than one.

    That set is WIDER than the rows ``BiosFileEntry.system_image_candidate``
    marks, and the difference is deliberate rather than a gap to close: the flag
    is silent for a core that states required files, whose rows already carry the
    same demand as ``required_by_active``, while this answer is the console's and
    weighs every image the core declares whatever the core called it. The reason
    lives in full at :func:`build_file_entry`.
    """
    if verdict is None or active_core_so is None or not verdict.system_needs_an_image:
        return SYSTEM_IMAGE_NOT_DEMANDED
    images = [f for f in files if active_core_so in f.cores]
    if any(f.satisfied for f in images):
        return SYSTEM_IMAGE_HELD
    if images and all(f.satisfied is False for f in images):
        return SYSTEM_IMAGE_ABSENT
    return SYSTEM_IMAGE_UNSETTLED


def _nothing_established(status: BiosStatus) -> bool:
    """Nothing about this platform's firmware could be established.

    Two shapes, and the second is why ``reading_complete`` exists. The server
    holds firmware and the machine answered for none of it — every row unknown,
    so there is nothing to base a claim on. Or the platform has no file at all
    AND its reading was not complete: an empty list under a complete reading is
    the finished answer "no emulator here wants anything", while under an
    incomplete one it is silence, and silence read as an answer is a claim about
    a question nothing finished asking. Which surface that claim reaches depends
    on the caller: the platform detail renders this level whether or not there
    are rows, so it is where an empty list would read green.

    ``reading_complete`` is False whenever ANY core in the platform's scope went
    unread, not only when nothing could be asked at all; the two are the same
    thing to this function, which is why the shape it names is an empty file
    list rather than an empty scope.

    ``known_count is None`` is a caller that did not supply the counts, and the
    decision is then left to the required-count logic as it always was.
    """
    if status.known_count is None:
        return False
    if not status.reading_complete and not status.files:
        return True
    return status.server_count > 0 and status.known_count == 0 and status.unknown_count > 0


def _requirement_verdict_withheld(status: BiosStatus) -> bool:
    """Is one of the launching core's required files one nothing could judge?

    The third shape that declines a readiness claim, and the only one that
    coexists with a real requirement. The other two (:func:`_nothing_established`)
    both imply ``required_count == 0``, so they can only ever displace a false
    ``'ok'``; this one can also displace a ``'partial'`` or a ``'missing'``, and
    that is deliberate. Where one required row cannot be judged, neither "ready"
    nor "some of it is absent" is a claim the reading supports, and the rows
    below the verdict still say which is which per file.

    A required row the reading answered ``False`` is NOT this shape: it is a
    requirement shown to be unmet, so the level goes to ``'missing'`` or
    ``'partial'`` and the play row raises its badge. A folder the resolver
    listed and found no image in is exactly that answer.
    """
    return any(f.required_by_active and f.satisfied is None for f in status.files)


def _counted_level(status: BiosStatus) -> str:
    """The level the file counts alone give — the rule that predates every decline."""
    req_count = status.required_count
    req_done = status.required_downloaded
    if req_count is not None and req_done is not None:
        if req_done >= req_count:
            return BIOS_LEVEL_OK
        if req_done > 0:
            return BIOS_LEVEL_PARTIAL
        return BIOS_LEVEL_MISSING
    if status.all_downloaded:
        return BIOS_LEVEL_OK
    if (status.local_count or 0) > 0:
        return BIOS_LEVEL_PARTIAL
    return BIOS_LEVEL_MISSING


def compute_bios_level(status: BiosStatus) -> str:
    """Compute BIOS status level: 'unknown', 'ok', 'partial', or 'missing'.

    ``'unknown'`` means no readiness claim can be made, and four shapes reach it.
    Three are declines — see :func:`_nothing_established` and
    :func:`_requirement_verdict_withheld` — checked before the required-count
    logic; the first two only fire when the caller supplied ``known_count`` (else
    the decision is deferred to the existing ok/partial/missing logic). The
    fourth is an unsettled system image over counts that would otherwise read
    ``'ok'``, described below.

    A platform whose files are all *answered for* and wanted by nothing is a
    different case entirely and reaches ``'ok'``: "no emulator here needs these"
    is a finished answer, and the file rows say which files it covers.

    The **system image** (:func:`classify_system_image`) enters at both ends and
    only ever makes the answer less green. An established absence lands on
    ``'missing'``, and is tested first so that a demonstration outranks a
    platform nothing could be established for. **Only one of the three declines
    can reach that comparison**, so the order is a rule about that one and a
    guard for the rest: ``'absent'`` needs every row the launching core declares
    answered ``False``, which a withheld required row contradicts by construction
    — it carries the active core (:func:`build_file_entry`), so it is one of
    those rows, with ``satisfied is None`` — and which an empty file list cannot
    produce at all. What is left is :func:`_nothing_established`'s first shape:
    the library's own rows all unanswered under an incomplete reading, while the
    core's declared images are rows the library does not hold and every one of
    them is absent. An unsettled image can turn a green claim grey and nothing
    else: where the counts already read ``'partial'`` or ``'missing'``, something
    is known to be absent, and a doubt about one further file does not unsay it.
    """
    if status.system_image == SYSTEM_IMAGE_ABSENT:
        return BIOS_LEVEL_MISSING
    if _nothing_established(status) or _requirement_verdict_withheld(status):
        return BIOS_LEVEL_UNKNOWN
    level = _counted_level(status)
    if status.system_image == SYSTEM_IMAGE_UNSETTLED and level == BIOS_LEVEL_OK:
        return BIOS_LEVEL_UNKNOWN
    return level


def compute_bios_label(status: BiosStatus) -> str:
    """Compute the compact BIOS status token (verbose phrasing stays per-surface).

    Declines on exactly the shapes :func:`compute_bios_level` declines on — by
    asking it rather than by repeating them, so the token beside a grey dot can
    never read as a ratio the verdict withheld.
    """
    if compute_bios_level(status) == BIOS_LEVEL_UNKNOWN:
        return BIOS_LABEL_UNKNOWN
    # A console that needs one of these images and holds none: the ratio would
    # count the wrong set, and the disjunction has no ratio to state.
    if status.system_image == SYSTEM_IMAGE_ABSENT:
        return BIOS_LABEL_MISSING
    return _counted_label(status)


def _counted_label(status: BiosStatus) -> str:
    """The token the file counts alone give — :func:`_counted_level`'s ratios in words."""
    req_count = status.required_count
    req_done = status.required_downloaded
    if req_count is not None and req_done is not None:
        if req_done >= req_count:
            return "OK"
        if req_done > 0:
            return f"{req_done}/{req_count} required"
        return BIOS_LABEL_MISSING
    if status.all_downloaded:
        return "OK"
    if (status.local_count or 0) > 0:
        return f"{status.local_count}/{status.server_count}"
    return BIOS_LABEL_MISSING


def count_wanted(files: tuple[BiosFileEntry, ...]) -> tuple[int, int]:
    """``(known, unknown)`` over the server's files — asked for, and unanswerable.

    A ``not_needed`` file is in neither: the machine answered for it, and no
    emulator asks for it. Its absence from both counts is what keeps
    ``compute_bios_level`` from reading "nothing here is needed" as "nothing
    could be established".

    **Scoped to ``on_server`` rows**, because ``_nothing_established`` weighs
    ``known_count`` against ``server_count``, which is the server's rows alone.
    A row the library does not hold exists only because an emulator declared the
    file, so it always classifies ``needed``/``optional`` and would always be
    counted as known: one such row would cancel the ``unknown`` verdict for a
    platform whose every server file went unanswered, turning the headline green
    while each row still said nothing could answer for it.
    """
    on_server = [f for f in files if f.on_server]
    known = sum(1 for f in on_server if f.wanted in (WANTED_NEEDED, WANTED_OPTIONAL))
    unknown = sum(1 for f in on_server if f.wanted == WANTED_UNKNOWN)
    return known, unknown
