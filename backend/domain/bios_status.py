"""BIOS readiness — the status shape every surface renders, and the one level over it.

Domain owns the unknown/ok/partial/missing LEVEL (``compute_bios_level``) and the
compact status token (``compute_bios_label``) — the single source of truth for
the readiness decision the game-detail panel, the play-section row and the System
page all read. Verbose per-surface phrasing and the status-dot colour are UI
concerns and deliberately do NOT live here.

**The level is the resolver's verdict, not a count** (ADR-0042 §1).
``requirements_met`` for the launching emulator is met (``ok``), not met
(``missing``) or not established (``unknown``); the one exception is a one-of
group whose answer turns on the disc's region alone, which reads ``partial``
(:func:`region_decides`). Nothing here weighs the rows against each other to
reach a colour: the counts below exist to WORD the answer — "2 of 3 files … are
in place" under a red verdict — and never to decide it.

Two axes run through this module and must not be folded into one. **What wants a
file** is :mod:`domain.firmware_wants`' four-valued answer. **Whether the game in
front of the user is ready to launch** is scoped to the emulator it will launch
with — libretro or standalone, one identity either way — which is why an entry
carries ``required_by_active`` beside its ``wanted`` and why the counts key off
the first. A file three other emulators demand is not a missing prerequisite for
this launch.

Where the launching emulator states a **one-of group**
(:mod:`domain.firmware_groups`), the group is ONE requirement and is counted as
one in the counts a surface words. Its options are rows like any other, and an
option alone never makes its row ``required_by_active`` — a launch needs one of
them, chosen by the disc's region.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_groups import GROUP_MET, GROUP_UNKNOWN, GROUP_UNMET, fetched_as_required
from domain.firmware_wants import (
    CAVEAT_SCAN_INCOMPLETE,
    CAVEATS_IDENTIFIED_BY_CONTENT,
    CHECKED_UNREAD,
    DECLARED_FILE,
    LOCATING_BY_NAME_THEN_CONTENT,
    WANTED_NEEDED,
    WANTED_OPTIONAL,
    WANTED_UNKNOWN,
    classify_wanted,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from domain.firmware_groups import GroupVerdict
    from domain.firmware_wants import CoreFirmwareVerdict, FirmwareGroup, FirmwarePlacement

BIOS_LEVEL_UNKNOWN = "unknown"
BIOS_LEVEL_OK = "ok"
BIOS_LEVEL_PARTIAL = "partial"
BIOS_LEVEL_MISSING = "missing"

# The compact token :func:`compute_bios_label` answers for the unknown level. A
# constant because a caller that ships the level without going through the
# function still has to name the label that goes with it.
BIOS_LABEL_UNKNOWN = "Unknown"
BIOS_LABEL_MISSING = "Missing"

# What the launching emulator's search of the BIOS folder established, for an
# emulator that finds its firmware ``by-name-then-content`` and states no one-of
# group (:func:`classify_content_search`) — words for a surface, never a colour.
# ``found``: every file it requires is met and was identified by its contents.
# The other two are the ways its search did not settle, each one a statement of
# the resolver's: its listing did not finish (``incomplete``), or a file the
# search reached could not be read (``unreadable``). A search that settled and
# found nothing is the emulator's own note (``search-found-nothing``), never
# read off a verdict here.
CONTENT_SEARCH_FOUND = "found"
CONTENT_SEARCH_INCOMPLETE = "incomplete"
CONTENT_SEARCH_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class BiosFileEntry:
    """Status of a single BIOS/firmware file on a platform's list.

    ``wanted`` is the need of the emulator whose requirement the row is (one of
    :data:`domain.firmware_wants.WANTED_VALUES`) — its own word on a file it
    declares, and for a library file nobody declares whether that emulator was
    read; ``required_by_active`` is whether the emulator the row was built for
    requires it — the launching emulator on the payload's own rows, which alone
    decide whether the file is counted as a missing prerequisite, and the
    entry's emulator on another emulator's entry's rows.

    ``on_server`` is clear for a file an installed emulator asks for that the
    RomM library does not hold. Nothing on this page can fetch such a file — so
    it is shown, and it is kept out of every count that offers the user an
    action.

    ``satisfied`` is the row's verdict and the axis the REQUIRED counts key off:
    the resolver's own per-file answer — met, not met (absent, the
    wrong image, refused, the wrong shape), or not established (the place could
    not be looked at, a folder stands where a file is opened, the bytes could
    not be read, recognised or compared). It
    is not ``downloaded``, which answers only whether something is at the
    destination: a wrong image is there and not met, and a folder is there on
    every stock RetroDECK whatever it holds. The library's own held/offered
    ratio is a third axis and keys off neither (``on_server`` and
    ``downloaded``); only the ratio a surface states is narrowed to the rows
    whose ``satisfied`` is not ``None``.

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

    ``declaration`` is the resolver's own word for how the emulator that supplied
    ``description`` stated what it wants — ``read`` off its own ``.info``,
    ``packaged`` out of the card standing in for one that ships none — and it is
    what a surface reads to decide whether that prose is the packager's label for
    the file or atlas explaining the requirement in sentences. ``None`` for a row
    no emulator declared, whose ``description`` is the file name itself.

    ``checked`` is what became of the row's BYTES, carried from the same entry
    ``declaration`` is. It exists because ``satisfied`` cannot distinguish the
    reasons a verdict was withheld, and one of them is not a withholding at all:
    a file the emulator READ and does not recognise is a file that was checked,
    and saying it could not be was untrue. ``None`` is no statement — the
    ordinary answer for a file that is simply absent.

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
    local_path: str | None
    declared_path: str
    description: str
    wanted: str
    required_by_active: bool
    # {emulator identity: {"required": bool, "one_of": {...} | None}} — per
    # emulator, what its own declaration says about this file and, where the
    # file is an option of one of its one-of groups, the regions it serves there
    # (:func:`_group_membership`). Two statements, never folded into one: an
    # option's own need speaks for the group and never makes the file required.
    # The key is the emulator's identity, so a standalone emulator is a speaker
    # here like any other; the field keeps the name the wire has always carried.
    cores: dict[str, dict[str, Any]]
    used_by_active: bool
    on_server: bool = True
    # The launching emulator's ``one_of`` entry, read off ``cores`` — this row as
    # an option of that emulator's group, or ``None`` where it is none.
    one_of: dict[str, Any] | None = None
    # Does "Download required" fetch this row? :func:`domain.firmware_groups.fetched_as_required`,
    # the rule the download applies too.
    fetch_for_required: bool = False
    supplied_by: str | None = None
    satisfied: bool | None = None
    declared_kind: str = DECLARED_FILE
    declaration: str | None = None
    caveats: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    checked: str | None = None
    # {"emulator_label": str | None, "file_name": str | None} where the
    # emulator's own setting names a file in this folder that is not there —
    # ``FirmwarePlacement.missing_configured_image``. Never a verdict.
    missing_configured_image: dict[str, str | None] | None = None
    # The identity of the emulator whose requirement this row is, ``None`` for
    # a library file no emulator claims. A surface shows a row with its own
    # emulator, and an unclaimed one in the library's own section.
    declared_by: str | None = None
    # Tender's own facts about the row (:mod:`domain.firmware_rows`): the
    # library file that fills it, this platform's store copy that does, what
    # lies at its place, why no link may go there, and whether "Replace…" is
    # offered. ``in_folder`` is the folder declaration a row stands in, for one
    # file inside the folder an emulator lists. ``linked_for_name`` is the name
    # the platform ``linked_for`` slugs is shown under.
    library: dict[str, Any] | None = None
    store_file: str | None = None
    link: str | None = None
    linked_for: str | None = None
    linked_for_name: str | None = None
    place_refusal: dict[str, Any] | None = None
    replace_offered: bool = False
    in_folder: str | None = None


@dataclass(frozen=True)
class BiosStatus:
    """One platform's classified files and the verdict over them, in the shape the level reads.

    **Not a wire shape, and it never reaches the wire.** Its one caller builds it
    to ask :func:`compute_bios_level` and :func:`compute_bios_label` and keeps
    nothing else of it; what the frontend receives is the plain dict assembled
    beside it (``FirmwareStatusReader._bios_aggregates``).

    ``verdict`` is what the resolver says about the launching emulator, ``None``
    where the answer holds nothing about it — no pick, no identity, a reading
    that did not happen, or an emulator missing from the answer, which counts as
    not read. ``region_decides`` is :func:`region_decides` over its
    groups, taken where the resolver's own groups are at hand.
    """

    platform_slug: str
    server_count: int
    local_count: int
    all_downloaded: bool
    required_count: int | None
    required_downloaded: int | None
    files: tuple[BiosFileEntry, ...]
    # Server files some emulator asks for (``needed`` or ``optional``) and files
    # nothing could answer for — :func:`count_wanted`. Carried to the wire; no
    # level reads them.
    known_count: int | None = None
    unknown_count: int = 0
    verdict: CoreFirmwareVerdict | None = None
    region_decides: bool = False
    # The verdicts over the launching emulator's one-of groups, each ONE
    # requirement already inside ``required_count``.
    groups: tuple[GroupVerdict, ...] = ()
    # The level a game's own region gives (:func:`game_region_level`), or
    # ``None`` where it gives none and the verdict stands.
    game_level: str | None = None


def format_bios_status(
    bios: dict[str, Any],
    platform_slug: str,
    *,
    verdict: CoreFirmwareVerdict | None = None,
    region_decides: bool = False,
    groups: tuple[GroupVerdict, ...] = (),
    game_level: str | None = None,
) -> BiosStatus:
    """Assemble the :class:`BiosStatus` the level decision reads, from one raw check result."""
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
                one_of=f.get("one_of"),
                fetch_for_required=f.get("fetch_for_required", False),
                supplied_by=f.get("supplied_by"),
                satisfied=f.get("satisfied"),
                declared_kind=f.get("declared_kind", DECLARED_FILE),
                declaration=f.get("declaration"),
                caveats=tuple(f.get("caveats", ())),
                images=tuple(f.get("images", ())),
                checked=f.get("checked"),
                missing_configured_image=f.get("missing_configured_image"),
                declared_by=f.get("declared_by"),
            )
            for f in raw_files
        )
    else:
        files = tuple(raw_files)

    return BiosStatus(
        platform_slug=platform_slug,
        server_count=bios.get("server_count", 0),
        local_count=bios.get("local_count", 0),
        all_downloaded=bios.get("all_downloaded", False),
        required_count=bios.get("required_count"),
        required_downloaded=bios.get("required_downloaded"),
        files=files,
        known_count=bios.get("known_count"),
        unknown_count=bios.get("unknown_count", 0),
        verdict=verdict,
        region_decides=region_decides,
        groups=groups,
        game_level=game_level,
    )


def build_file_entry(
    file_name: str,
    downloaded: bool,
    dest: str | None,
    placement: FirmwarePlacement | None,
    complete: bool,
    launching_emulator: str | None,
    *,
    on_server: bool = True,
    groups: tuple[FirmwareGroup, ...] = (),
) -> BiosFileEntry:
    """Build a single file status entry from the machine's answer about it.

    ``placement`` is the catalogue's entry for the file (``None`` when nothing
    declares it) and ``complete`` the reading state for the launching emulator —
    together they decide ``wanted``. What the emulator the game will launch with
    says about the row is :func:`_active_core_answer`'s, and
    ``launching_emulator`` is passed straight through to it.

    ``groups`` are every one-of group of the answer, and each declaring
    emulator's entry in ``cores`` reads its own (:func:`_group_membership`).
    """
    folder = placement.folder if placement is not None else None
    wants = placement.wants if placement is not None else ()
    cores = {
        want.emulator: {
            "required": want.required,
            "one_of": _group_membership(file_name, _groups_of(groups, want.emulator)),
        }
        for want in wants
        if want.emulator is not None
    }
    active = _active_core_answer(cores, launching_emulator)
    stale = placement.missing_configured_image if placement is not None else None
    return BiosFileEntry(
        file_name=file_name,
        downloaded=downloaded,
        local_path=dest,
        declared_path=placement.destination if placement is not None else file_name,
        description=placement.description if placement is not None else file_name,
        wanted=_wanted(placement, complete, launching_emulator),
        required_by_active=active.required_by_active,
        cores=cores,
        used_by_active=active.used_by_active,
        on_server=on_server,
        one_of=active.one_of,
        fetch_for_required=fetched_as_required(placement, launching_emulator, _groups_of(groups, launching_emulator)),
        supplied_by=placement.supplied_by if placement is not None else None,
        satisfied=_row_verdict(placement),
        declared_kind=placement.declared_kind if placement is not None else DECLARED_FILE,
        declaration=placement.declaration if placement is not None else None,
        caveats=placement.caveats if placement is not None else (),
        images=folder.images if folder is not None else (),
        checked=placement.checked if placement is not None else None,
        missing_configured_image=(
            {"emulator_label": stale.emulator_label, "file_name": stale.file_name} if stale is not None else None
        ),
        declared_by=placement.emulator if placement is not None else None,
    )


def _wanted(placement: FirmwarePlacement | None, complete: bool, launching_emulator: str | None) -> str:
    """The row's ``wanted``: the launching emulator's own need wherever the row is its requirement.

    A row the launching emulator declares is shown with that emulator, so its need
    there is that emulator's — required or merely accepted — and never another
    emulator's requirement of the same name. Every other row keeps the
    machine's four-valued answer (:func:`classify_wanted`).
    """
    if placement is not None and launching_emulator is not None and placement.emulator == launching_emulator:
        own = any(want.emulator == launching_emulator and want.required for want in placement.wants)
        return WANTED_NEEDED if own else WANTED_OPTIONAL
    return classify_wanted(placement, complete)


def _groups_of(groups: tuple[FirmwareGroup, ...], emulator: str | None) -> tuple[FirmwareGroup, ...]:
    """The groups *emulator* states — none for an emulator with no identity."""
    if emulator is None:
        return ()
    return tuple(group for group in groups if group.emulator == emulator)


def _group_membership(file_name: str, groups: tuple[FirmwareGroup, ...]) -> dict[str, Any] | None:
    """This file as an option of *groups*: the regions it serves, and whether that is all of them.

    ``every_region`` is what lets a surface say "every region" rather than list
    them: true where the file serves every region each group naming it speaks
    about — SwanStation's search find, which the core boots whatever the disc.
    ``None`` where no group names the file.
    """
    naming = [group for group in groups if group.regions_of(file_name)]
    if not naming:
        return None
    regions = list(dict.fromkeys(region for group in naming for region in group.regions_of(file_name)))
    every = all(set(group.regions_of(file_name)) >= set(group.regions) for group in naming)
    return {"regions": regions, "every_region": every}


@dataclass(frozen=True)
class _ActiveCoreAnswer:
    """The launching emulator's say about one row — the three launch-scoped fields.

    These three are the emulator the game will launch with speaking about one
    row, and :class:`BiosFileEntry` carries each of them.
    """

    used_by_active: bool
    required_by_active: bool
    one_of: dict[str, Any] | None


def _active_core_answer(cores: Mapping[str, dict[str, Any]], launching_emulator: str | None) -> _ActiveCoreAnswer:
    """What the emulator the game will launch with says about one row.

    ``launching_emulator`` is that emulator's identity, or ``None`` when it could
    not be resolved or identified; then no row is presented as its requirement
    or as one it uses — no other emulator stands in for it — and no
    group is its, so ``one_of`` stays silent. The empty required set that leaves
    is "could not be established", never "nothing required": the verdict for
    no identity is none, so the level is unknown and ``emulator_read`` false.

    ``one_of`` reads the launching emulator's own entry in ``cores``, so the row
    and the per-emulator entries cannot disagree about which group it stands in.
    """
    active_entry = cores.get(launching_emulator) if launching_emulator is not None else None
    if launching_emulator is None:
        used_by_active = False
        required_by_active = False
    else:
        used_by_active = launching_emulator in cores if cores else True
        required_by_active = active_entry["required"] if active_entry is not None else False
    return _ActiveCoreAnswer(
        used_by_active=used_by_active,
        required_by_active=required_by_active,
        one_of=active_entry["one_of"] if active_entry is not None else None,
    )


def _row_verdict(placement: FirmwarePlacement | None) -> bool | None:
    """Is this row's requirement met? ``None`` where nothing established it.

    Wherever an emulator declared the file it is the resolver's own verdict over
    that requirement, read at the place the emulator opens — a wrong
    image is not met though it is there, a file it could not compare is not
    established though it is there, and a place it could not look at is not
    established rather than missing. A folder declaration is answered by what
    the folder HOLDS, never by the folder being there.

    A library file no emulator declared is no requirement of anything, so the
    resolver has no verdict to give, and the verdict is withheld.
    """
    return placement.satisfied if placement is not None else None


def count_required(
    files: tuple[BiosFileEntry, ...], groups: tuple[GroupVerdict, ...] = (), folders: tuple[bool | None, ...] = ()
) -> tuple[int, int]:
    """``(required, of those met)`` for the emulator the game will launch with.

    Each of its one-of *groups* is ONE requirement here, met only where every
    region it speaks about is covered.

    The two numbers a surface WORDS — "2 of 3 files … are in place" — derived in
    one place so the platform detail and the game-detail page can never disagree
    about which files count. **No level is computed from them**: the
    colour is the resolver's verdict, and these say how much of the requirement
    is there under it. A file the library does not hold counts here — it is
    genuinely required and genuinely absent — and nothing here implies a
    download.

    The second number is the row VERDICT, never ``downloaded``: a file counts as
    in place only where the resolver judges it met, so a wrong image
    or one it could not compare is in the first number and not the second. A
    row nothing could judge is :func:`count_required_withheld`'s.

    *folders* are the verdicts over the folders the emulator requires and
    lists. A folder is ONE requirement whatever it holds, and its files are
    rows of their own that count for nothing here.
    """
    required = [f for f in files if f.required_by_active]
    met = sum(1 for group in groups if group.state == GROUP_MET) + sum(1 for folder in folders if folder is True)
    return len(required) + len(groups) + len(folders), sum(1 for f in required if f.satisfied) + met


def count_required_withheld(
    files: tuple[BiosFileEntry, ...], groups: tuple[GroupVerdict, ...] = (), folders: tuple[bool | None, ...] = ()
) -> int:
    """How many of the launching emulator's requirements nothing could judge.

    The third number beside :func:`count_required`'s two, and the one that keeps
    a declined verdict from reading as an absence. The sentence a surface words
    over the counts subtracts it: what is left of ``required - withheld`` against
    ``required_downloaded`` is the requirement whose absence really was
    established. A group whose state is ``unknown`` is one such requirement.
    """
    withheld_groups = sum(1 for group in groups if group.state == GROUP_UNKNOWN)
    withheld_folders = sum(1 for folder in folders if folder is None)
    return sum(1 for f in files if f.required_by_active and f.satisfied is None) + withheld_groups + withheld_folders


def region_decides(verdict: CoreFirmwareVerdict | None, groups: Iterable[FirmwareGroup]) -> bool:
    """Is the launching emulator's verdict not established because of the disc's region alone?

    Every part of it is the resolver's: its verdict is not
    established; it stated that a group's option is picked by the region of the
    disc being booted (``region_decided_by_disc``); at least one of its groups
    has an image met and one not met and none not established; every other
    group is met; and nothing else in its answer is open — no plain required
    file it could not judge, no refused required declaration. Tender counts
    nothing of its own here.

    A group naming regions that rest on a read that settled nothing (its
    ``unchecked_regions``), or regions it carries no option for (its
    ``unstated_regions``), is something open too, and makes this ``False``.
    """
    if verdict is None or verdict.requirements_met is not None:
        return False
    if not verdict.region_decided_by_disc or verdict.undetermined or verdict.refused_required:
        return False
    mixed = False
    for group in groups:
        if group.satisfied is True:
            continue
        images = {option.satisfied for option in group.options}
        open_regions = group.unchecked_regions or group.unstated_regions
        if group.satisfied is None and images == {True, False} and not open_regions:
            mixed = True
            continue
        return False
    return mixed


def classify_content_search(
    verdict: CoreFirmwareVerdict | None,
    files: tuple[BiosFileEntry, ...],
    *,
    groups: tuple[GroupVerdict, ...] = (),
) -> str | None:
    """What the launching emulator's search of the BIOS folder established, or ``None``.

    For an emulator that finds its firmware ``by-name-then-content`` and states
    no one-of *groups* — where it states one, the group says region by region
    what the search reached, and this stays silent rather than being a second
    answer beside it. ``None`` for every other emulator.

    Every input is the resolver's: the rows' per-file verdicts, ``checked`` and
    caveat codes, and the emulator's own caveat codes. ``found`` needs the
    resolver to state that each file was identified by its contents — a file
    opened by its configured name is met and was no search's finding, so the
    answer says nothing of a search there. Nothing is reached by elimination:
    where none of the three statements is made, the answer is ``None`` — a
    required file not met is the verdict's, and a search that found nothing is
    the emulator's note. It words the answer and decides no colour.
    """
    if groups or verdict is None or verdict.locating != LOCATING_BY_NAME_THEN_CONTENT:
        return None
    required = [f for f in files if f.required_by_active]
    if required and all(
        f.satisfied is True and CAVEATS_IDENTIFIED_BY_CONTENT.intersection(f.caveats) for f in required
    ):
        return CONTENT_SEARCH_FOUND
    if CAVEAT_SCAN_INCOMPLETE in verdict.caveats:
        return CONTENT_SEARCH_INCOMPLETE
    if any(f.checked == CHECKED_UNREAD for f in required):
        return CONTENT_SEARCH_UNREADABLE
    return None


def game_region_level(
    verdict: CoreFirmwareVerdict | None,
    groups: Iterable[FirmwareGroup],
    narrowed: tuple[GroupVerdict, ...],
) -> str | None:
    """The level for a game whose own region RomM names, or ``None`` where none applies.

    The game page narrows to the game's region wherever the
    launching emulator states a one-of group and the game's region maps
    (*narrowed* are its groups judged for those regions,
    :func:`domain.firmware_groups.judge_group_for_game`).

    Everything outside the groups decides first, off the resolver's own words: a
    plain required file not met, or the console's own image shown absent
    (``system_firmware_met`` false, which holds whatever the disc's region), is
    ``'missing'``; a plain required file it could not judge, or a refused
    required declaration, is ``'unknown'``. A verdict not met that nothing the
    resolver states explains is ``'unknown'`` too — the absence is read off the
    resolver's console-level verdict and never inferred from a group or the
    rows. Otherwise the image serving the game's region decides: met ``'ok'``,
    not met ``'missing'``, and not established — or no option of the group
    serves that region — ``'unknown'``, which holds even under a met verdict,
    because the resolver states that a region no option lists is outside its
    verdict.
    """
    stated = tuple(groups)
    if verdict is None or not stated or not narrowed or not narrowed[0].game_regions:
        return None
    if verdict.unmet or verdict.system_firmware_met is False:
        return BIOS_LEVEL_MISSING
    if verdict.undetermined or verdict.refused_required:
        return BIOS_LEVEL_UNKNOWN
    if verdict.requirements_met is False and not any(g.satisfied is False for g in stated):
        return BIOS_LEVEL_UNKNOWN
    states = {group.state for group in narrowed}
    if GROUP_UNMET in states:
        return BIOS_LEVEL_MISSING
    return BIOS_LEVEL_OK if states == {GROUP_MET} else BIOS_LEVEL_UNKNOWN


def compute_bios_level(status: BiosStatus) -> str:
    """Compute BIOS status level: 'unknown', 'ok', 'partial', or 'missing'.

    The resolver's verdict for the launching emulator: met is
    ``'ok'``, not met is ``'missing'``, not established is ``'unknown'``. An
    emulator the answer holds nothing about is not read, and so is
    ``'unknown'`` — never ``'ok'`` over a launch nobody asked about.

    ``'partial'`` is the one exception, and it is not a count: the verdict is
    not established only because it depends on the disc's region
    (:func:`region_decides`), so the platform shows the regions its images serve
    and miss. On a game's own page, where RomM names the game's region, that
    region decides instead (``game_level``, :func:`game_region_level`).
    """
    if status.game_level is not None:
        return status.game_level
    verdict = status.verdict
    if verdict is None:
        return BIOS_LEVEL_UNKNOWN
    if verdict.requirements_met is True:
        return BIOS_LEVEL_OK
    if verdict.requirements_met is False:
        return BIOS_LEVEL_MISSING
    return BIOS_LEVEL_PARTIAL if status.region_decides else BIOS_LEVEL_UNKNOWN


def compute_bios_label(status: BiosStatus) -> str:
    """Compute the compact BIOS status token (verbose phrasing stays per-surface).

    The token words the level and never decides it. Under a not-met verdict it
    is the plain required files' ratio where one of them is not in place —
    "1/2 required" — and ``"Missing"`` where what is missing is no plain file (a
    one-of group, or the console's own image). Under a met or region-decided
    verdict it is ``"OK"`` where every plain required file is in place, and the
    ratio otherwise; under a verdict nothing established it is ``"Unknown"``.
    """
    level = compute_bios_level(status)
    if level == BIOS_LEVEL_UNKNOWN:
        return BIOS_LABEL_UNKNOWN
    plain = [f for f in status.files if f.required_by_active]
    done = sum(1 for f in plain if f.satisfied)
    if plain and done < len(plain):
        return f"{done}/{len(plain)} required"
    return BIOS_LABEL_MISSING if level == BIOS_LEVEL_MISSING else "OK"


def count_wanted(files: tuple[BiosFileEntry, ...]) -> tuple[int, int]:
    """``(known, unknown)`` over the server's files — asked for, and unanswerable.

    A ``not_needed`` file is in neither: the machine answered for it, and no
    emulator asks for it.

    **Neither number is read as a number**, and no level reads either: the
    level is the resolver's verdict. Both travel to the wire, where the frontend
    declares their types (``frontend/src/types/firmware.ts``) without using them.

    **Scoped to ``on_server`` rows**, and no consumer can currently tell that
    scoping from any other. A row the library does not hold exists only because
    an emulator declared the file, so it always classifies
    ``needed``/``optional`` and would always be counted as known.
    """
    on_server = [f for f in files if f.on_server]
    known = sum(1 for f in on_server if f.wanted in (WANTED_NEEDED, WANTED_OPTIONAL))
    unknown = sum(1 for f in on_server if f.wanted == WANTED_UNKNOWN)
    return known, unknown
