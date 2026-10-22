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
untracked-BIOS sweep. **Its standalone
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
root: 59-173 ms per system on the reference machine at emu-atlas 0.22, against
172 ms for one unverified whole-machine sweep. Nothing is cached in either — a
firmware answer is about files on disk that the user is actively adding and
removing, and a cached one would outlive the download that changed it.

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
    AVAILABILITY_NOT_INSTALLED,
    CAVEAT_CORE_MODE_UNESTABLISHED,
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,
    CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_IMAGE,
    CAVEAT_FIRMWARE_FILE_LINKED_OUTSIDE_ROOT,
    CAVEAT_FIRMWARE_IMAGE_AMBIGUOUS,
    CAVEAT_FIRMWARE_IMAGE_CONTRADICTED,
    CAVEAT_FIRMWARE_IMAGE_IDENTIFIED,
    CAVEAT_FIRMWARE_IMAGE_UNLISTED,
    CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
    CAVEAT_FIRMWARE_SCAN_INCOMPLETE,
    CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,
    CAVEAT_FIRMWARE_UNREADABLE,
    CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE,
    REASON_REGION_DECIDED_BY_DISC,
)

# None of these names is in emu-atlas's ``__all__``, and emu-atlas has not said
# whether they are kept: a rename there, once vendored, fails this import at
# start-up.
from _vendor.atlas.firmware import core_short_name, declared_directory_of, load_hashes

from adapters.atlas_identity import emulator_identity
from adapters.emulator_sources import source_roots
from domain.firmware_wants import (
    DECLARED_DIRECTORY,
    DECLARED_FILE,
    LOCATING_UNESTABLISHED,
    LOCATING_VALUES,
    NOTE_BIOS_UNCHOSEN,
    NOTE_IMAGES_TIED,
    NOTE_SEARCH_FOUND_NOTHING,
    CoreFirmwareVerdict,
    EmulatorNote,
    FirmwareCatalogue,
    FirmwareGroup,
    FirmwareOption,
    FirmwarePlacement,
    FirmwareWant,
    FolderFile,
    FolderVerdict,
    MissingConfiguredImage,
    RefusedDeclaration,
    SourceCatalogues,
)
from domain.source_roots import SourceRoot, SourceRoots

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from adapters.emulator_sources import DetectedSourcesReading, EmulatorSourcesAdapter

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
# (``pcsx2``), which is none of Tender's identities — so a row is matched
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
# names regions as well: under ``regions`` the regions its options DO serve, and
# under ``unstated`` the regions it states nothing for — the one place a region
# whose every name the resolver refused (it leaves the root) is named at all,
# which :func:`_unstated_regions` reads.
_UNCHECKED_REGION_CODES = frozenset({CAVEAT_FIRMWARE_SCAN_INCOMPLETE, CAVEAT_FIRMWARE_SEARCH_UNVERIFIED})
_ABSENT_REGION_CODES = frozenset({CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE})

# The one setting whose emptiness is a BIOS nobody chose: PCSX2's ``[Filenames]
# BIOS``, which the resolver names under ``key`` in that spelling
# (``_vendor/atlas/firmware.py``, the PCSX2 card's caveat). xemu states its empty
# ``[sys.files]`` keys with the same code and shape, and two of the three do not
# hold the launch, so the shape alone says nothing about a choice.
_BIOS_CHOICE_SETTING = "Filenames/BIOS"


class AtlasFirmwareAdapter:
    """Reads what every installed libretro core wants, live, for the callers with no platform."""

    def __init__(self, *, sources: EmulatorSourcesAdapter, log_debug: Callable[[str], None]) -> None:
        self._sources = sources
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
        reading = self._sources.read()
        installation = _installation(reading, self._log_debug)
        if installation is None:
            return _unresolved()
        answer = installation.firmware_inventory()
        self._log_debug(f"[firmware] machine: {_trace(answer)}")
        return _catalogue(answer, reading.answering_kind, None, _FolderIdentities())


class AtlasPlatformFirmwareAdapter:
    """Reads what one system's emulators want, verified, live off the machine — in the answering source or in each."""

    def __init__(self, *, sources: EmulatorSourcesAdapter, log_debug: Callable[[str], None]) -> None:
        self._sources = sources
        self._log_debug = log_debug
        self._folders = _FolderIdentities()

    def __call__(self, system: str) -> FirmwareCatalogue:
        """*system*'s firmware demand in the source that answers for it, or an empty unresolved catalogue."""
        reading = self._sources.read()
        kind = reading.answering_kind
        installation = _installation(reading, self._log_debug)
        if kind is None or installation is None:
            return _unresolved()
        return self._read(system, kind, installation)

    def every_source(self, system: str) -> SourceCatalogues:
        """*system*'s firmware demand in each enabled source, in the user's order, to place links by.

        A source whose reading raised or named no root is named unread. A
        sealed emulator catalogue (EmuDeck's) is read for its places all the
        same, and marked ``sealed``: where the resolver states where a file
        goes, a link may go there, but its list never decides that a link is
        unwanted, and the rows of such an answer stay withheld
        (:func:`_catalogue`).
        """
        reading = self._sources.read()
        catalogues: list[FirmwareCatalogue] = []
        unread: set[str] = set()
        for source in reading.sources:
            installation = reading.installation(source.kind)
            if not source.enabled or installation is None:
                continue
            catalogue = self._read(system, source.kind, installation, for_links=True)
            if catalogue.resolved:
                catalogues.append(catalogue)
            else:
                unread.add(source.kind)
        return SourceCatalogues(read=tuple(catalogues), unread=frozenset(unread))

    def source_roots(self) -> dict[str, SourceRoots | None]:
        """Every detected source's roots, switched on or off, by kind; ``None`` where they could not be asked."""
        reading = self._sources.read()
        return {
            source.kind: _roots(installation)
            for source in reading.sources
            if (installation := reading.installation(source.kind)) is not None
        }

    def _read(self, system: str, kind: str, installation: Any, *, for_links: bool = False) -> FirmwareCatalogue:
        try:
            answer = installation.firmware_for_system(system, verify=True)
            self._log_debug(f"[firmware] {kind} {system}: {_trace(answer)}")
            catalogue = _catalogue(
                answer,
                kind,
                _roots(installation),
                self._folders,
                for_links=for_links,
                not_installed=self._not_installed(system, kind, installation),
            )
        except Exception as exc:
            self._log_debug(f"[firmware] resolver failed for {system} in {kind}, answering unresolved: {exc!r}")
            return _unresolved()
        return catalogue

    def _not_installed(self, system: str, kind: str, installation: Any) -> frozenset[str]:
        """The emulators *installation*'s launch reading states are not installed for *system*.

        Empty where that reading could not be asked: an emulator nothing ruled
        out is linked for, as one the reading could not establish is. Read over
        the identity rather than over the row, as :func:`_unread_emulators` is:
        an emulator the catalogue lists twice (EmuDeck's Cemu, native and
        through Proton) is not installed only where EVERY one of its rows says so.
        """
        try:
            entries = installation.emulators_for(system).entries
        except Exception as exc:
            self._log_debug(f"[firmware] launch reading failed for {system} in {kind}: {exc!r}")
            return frozenset()
        installed: dict[str, bool] = {}
        for entry in entries:
            identity = emulator_identity(entry)
            if identity is not None:
                installed[identity] = installed.get(identity, False) or entry.availability != AVAILABILITY_NOT_INSTALLED
        missing = frozenset(identity for identity, maybe in installed.items() if not maybe)
        self._log_debug(f"[firmware] {kind} {system}: not installed={sorted(missing)}")
        return missing


class AtlasFirmwareIdentifier:
    """Asks the answering source's resolver what a piece of content is, by its MD5."""

    def __init__(self, *, sources: EmulatorSourcesAdapter, log_debug: Callable[[str], None]) -> None:
        self._sources = sources
        self._log_debug = log_debug

    def __call__(self, md5: str) -> tuple[str, ...] | None:
        """Every name the resolver knows the content *md5* under — empty where it does not know it.

        ``None`` where no source answers or the question raised: nothing was
        established, which is not "not recognised".
        """
        try:
            installation = _installation(self._sources.read(), self._log_debug)
            if installation is None:
                return None
            answer = installation.identify_firmware(md5=md5)
        except Exception as exc:
            self._log_debug(f"[firmware] identifying {md5} failed: {exc!r}")
            return None
        return tuple(answer.known_as)


def _installation(reading: DetectedSourcesReading, log_debug: Callable[[str], None]) -> Any | None:
    """The answering source's installation from *reading*, or ``None`` where no source answers."""
    installation = reading.answering_installation()
    if installation is None:
        log_debug(f"[firmware] no emulator source answers ({reading.no_answer_reason()})")
    return installation


def _roots(installation: Any) -> SourceRoots | None:
    """The source's roots, each path with its links resolved as the resolver resolves a place; ``None`` where unasked.

    A place the resolver names is resolved, so a root compared with it has to
    be too: on a machine where ``/home`` is a link, an unresolved root would lie
    above no place at all.
    """
    try:
        roots = source_roots(installation.roots())
    except Exception:
        return None
    return SourceRoots(
        roots=tuple(
            SourceRoot(
                name=root.name,
                path=os.path.realpath(root.path) if root.path is not None else None,
                provenance=root.provenance,
                settings_file=root.settings_file,
            )
            for root in roots.roots
        ),
        findings=roots.findings,
    )


class _FolderIdentities:
    """The checksums the packaged table files under each declared folder's prefix, read once per adapter.

    The table is packaged data that changes only with the vendored resolver, so
    reading it once is no cached answer about the machine.
    """

    def __init__(self) -> None:
        self._by_prefix: dict[str, frozenset[str]] = {}

    def under(self, prefix: str) -> frozenset[str]:
        """Every checksum filed under *prefix*, below it at any depth — the resolver's own reading of a folder."""
        if prefix not in self._by_prefix:
            table = load_hashes()
            filed = (table.get(name) for name in table.names() if name.startswith(prefix))
            self._by_prefix[prefix] = frozenset(entry.md5.lower() for entry in filed if entry is not None)
        return self._by_prefix[prefix]


def _unresolved() -> FirmwareCatalogue:
    """The answer to "we could not ask" — no placements, nothing ruled out."""
    return FirmwareCatalogue(placements=(), unread_emulators=frozenset(), resolved=False)


def _catalogue(
    answer: Any,
    source: str | None,
    roots: SourceRoots | None,
    folders: _FolderIdentities,
    *,
    for_links: bool = False,
    not_installed: frozenset[str] = frozenset(),
) -> FirmwareCatalogue:
    """One resolver answer in Tender's own vocabulary, with the source it came from and that source's roots.

    An answer beside a sealed emulator catalogue (EmuDeck's) is built from the
    overlay entries the resolver could read, an incomplete list that would look
    complete, so it reads as nothing established — the way the catalogue and
    save adapters decline on the same code. *for_links* reads it all the same,
    marked ``sealed``: a place the resolver states is a place a link may go,
    and what the list leaves out is only a link not placed — never a link
    taken away.
    """
    sealed = any(caveat.code == CAVEAT_EMULATOR_CATALOGUE_SEALED for caveat in answer.caveats)
    if answer.root is None or (sealed and not for_links):
        return FirmwareCatalogue(
            placements=(),
            unread_emulators=frozenset(),
            resolved=False,
            caveats=_caveat_codes(answer),
        )
    return FirmwareCatalogue(
        placements=_placements(answer, folders),
        unread_emulators=_unread_emulators(answer),
        resolved=True,
        caveats=_caveat_codes(answer),
        emulator_verdicts=_emulator_verdicts(answer),
        groups=_groups(answer),
        source=source,
        roots=roots,
        not_installed=not_installed,
        sealed=sealed,
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
    no requirement in hand has stated nothing about what the emulator wants —
    which is not the same as no verdict: such a card can still be answered not
    met (``docs/architecture/backend-architecture.md``, FirmwareService notes,
    "What the narrowest decline does decide is wording"). A refused declaration
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
    """What the resolver says about each emulator as a whole, by identity.

    Keyed on the emulator rather than folded onto a file row, because that is
    what it is about: one machine's PlayStation BIOS images read one way under
    SwanStation and another under PCSX ReARMed, and a per-file row has no room
    for a fact that changes with the emulator the game launches with.

    Everything here is the entry's own statement: its verdict, what is recorded
    about its console and whether the table marked that, how it finds its
    firmware, its declaration state, its refused declarations, the plain
    required files it could not judge, and the notes a surface words.

    An unidentified entry is skipped; an identity the answer names twice keeps
    the first entry, the same rule :func:`_placements` applies to one identity's
    declarations of one file name.
    """
    verdicts: dict[str, CoreFirmwareVerdict] = {}
    for core in answer.cores:
        identity = emulator_identity(core)
        if identity is None or identity in verdicts:
            continue
        verdicts[identity] = CoreFirmwareVerdict(
            system_firmware=core.system_firmware,
            requirements_met=core.requirements_met,
            system_firmware_met=core.system_firmware_met,
            world_knowledge=any(caveat.code == CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE for caveat in core.caveats),
            locating=_locating(core),
            declaration=core.declaration,
            undetermined=_plain_required(core, None),
            unmet=_plain_required(core, False),
            refused=tuple(
                RefusedDeclaration(
                    declared=refused.declared, required=refused.need == "required", reason=refused.reason
                )
                for refused in core.refused
            ),
            region_decided_by_disc=any(_decided_by_disc(caveat) for caveat in core.caveats),
            notes=_notes(core.caveats),
            caveats=tuple(dict.fromkeys(caveat.code for caveat in core.caveats)),
        )
    return verdicts


def _decided_by_disc(caveat: Any) -> bool:
    """Is *caveat* the resolver's statement that a launch's console region is the disc's own?"""
    return caveat.code == CAVEAT_CORE_MODE_UNESTABLISHED and caveat.data.get("reason") == REASON_REGION_DECIDED_BY_DISC


def _locating(core: Any) -> str:
    """How the entry's emulator finds its firmware, in Tender's vocabulary.

    The resolver's word where it is one of :data:`LOCATING_VALUES`, and
    ``unestablished`` for anything else — reading an unknown word as
    ``by-name`` would claim the declared list is the whole requirement, which
    nobody established.
    """
    locating = getattr(core, "locating", None)
    return locating if locating in LOCATING_VALUES else LOCATING_UNESTABLISHED


def _plain_required(core: Any, satisfied: bool | None) -> tuple[str, ...]:
    """The plain required files whose resolver verdict is *satisfied* — ``None`` undetermined, ``False`` not met.

    The resolver's ``undetermined`` and ``unmet`` minus what its one-of groups put
    there: a group's own options are the group's statement, carried on the
    group, and they are what the region rules read.
    """
    return tuple(
        entry.file_name
        for entry in core.requirements
        if getattr(entry, "options", None) is None and entry.need == "required" and entry.satisfied is satisfied
    )


def _notes(caveats: tuple[Any, ...]) -> tuple[EmulatorNote, ...]:
    """The emulator-level statements a surface words, off the entry's own caveats.

    PCSX2's BIOS setting naming no file is a BIOS nobody has chosen — read off
    the setting the resolver names (:data:`_BIOS_CHOICE_SETTING`), never off the
    caveat's shape, which xemu's empty keys share; an ambiguity is images that rank
    alike, with the regions it is about where the resolver states them. A search
    that reached no image is the same code with the folder it searched (``dir``)
    and the regions it searched for, in one of two shapes the vendored
    ``firmware.py`` builds: a libretro core's search adds how many files of an
    accepted size the folder held (``candidates``, ``_search_found_nothing``),
    and DuckStation's states no count (``_duckstation_search``), so its note
    carries none. A code with neither shape words nothing. The entry's own
    caveats are its statements, so no attribution is asked.
    """
    notes: list[EmulatorNote] = []
    for caveat in caveats:
        regions = _stated_regions(caveat.data)
        if caveat.code == CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE and caveat.data.get("key") == _BIOS_CHOICE_SETTING:
            notes.append(EmulatorNote(kind=NOTE_BIOS_UNCHOSEN, setting=_BIOS_CHOICE_SETTING))
        if caveat.code == CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE and _searched(caveat.data):
            notes.append(_search_note(caveat.data, regions))
        if caveat.code == CAVEAT_FIRMWARE_IMAGE_AMBIGUOUS:
            notes.append(EmulatorNote(kind=NOTE_IMAGES_TIED, tied=_tied_count(caveat.data), regions=regions))
    return tuple(notes)


def _stated_regions(data: dict[str, Any]) -> tuple[str, ...]:
    """The regions a caveat's data states, or none where it states no list of them."""
    stated = data.get("regions")
    return tuple(r for r in stated if isinstance(r, str)) if isinstance(stated, (list, tuple)) else ()


def _search_note(data: dict[str, Any], regions: tuple[str, ...]) -> EmulatorNote:
    """The note for a search :func:`_searched` recognised, its count ``None`` where the search states none."""
    candidates = data.get("candidates")
    return EmulatorNote(
        kind=NOTE_SEARCH_FOUND_NOTHING,
        dir=data["dir"],
        candidates=int(candidates) if candidates is not None else None,
        regions=regions,
    )


def _tied_count(data: dict[str, Any]) -> int | None:
    """How many images an ambiguity says rank alike, or ``None`` where it states no count."""
    tied = data.get("tied")
    return int(tied) if isinstance(tied, str) and tied.isdigit() else None


def _searched(data: dict[str, Any]) -> bool:
    """Does a caveat's data state a search: its folder, and its count of candidates or the regions it searched for?

    ``firmware-path-names-no-file`` is stated for searches and for settings
    that name no file alike, and the fields tell them apart: a search states
    ``dir`` with ``candidates`` (a libretro core's) or ``regions``
    (DuckStation's). A setting's statement states neither beside its ``dir`` —
    PCSX2's empty BIOS setting names where a BIOS would be picked from, not a
    folder that was searched — or no ``dir`` at all (xemu's, melonDS's).
    """
    if not isinstance(data.get("dir"), str):
        return False
    candidates = data.get("candidates")
    if candidates is None:
        return isinstance(data.get("regions"), (list, tuple))
    return isinstance(candidates, str) and candidates.isdigit()


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
    state groups, the first stands and the second is skipped — one row's groups
    per identity, as :func:`_wants` keeps one want per identity; a row stating
    no group takes no part in that, so it never hides a later row's group. The
    regions an entry names beside its group come off that entry's own caveats
    (:data:`_UNCHECKED_REGION_CODES`, :data:`_ABSENT_REGION_CODES`,
    :func:`_unstated_regions`).
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
        unstated = _unstated_regions(spoken)
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
                satisfied=entry.satisfied,
                unstated_regions=unstated,
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


def _unstated_regions(caveats: list[Any]) -> tuple[str, ...]:
    """The regions a group carries no option for, off its ``region-decided-by-disc`` statement's ``unstated``.

    Read from that field alone (``_undecided_region_caveat`` in the vendored
    ``firmware.py``) and never out of the message, which names them too.
    """
    named = (
        region
        for caveat in caveats
        if _decided_by_disc(caveat) and isinstance(caveat.data.get("unstated"), (list, tuple))
        for region in caveat.data["unstated"]
        if isinstance(region, str) and region
    )
    return tuple(dict.fromkeys(named))


def _declared_location(requirement: Any, place: str, root: str) -> str | None:
    """The location the emulator declared below *root*, or ``None`` when the place lies outside it.

    Two fields of one requirement answer two different questions, and only the
    first belongs here. ``declared`` is the string the emulator spelled and the
    name it will open; *place* is where that is once the kernel has followed
    every link on the way to it, which is what says whether it lies inside the
    root. Reconstructing the declaration from the place instead —
    ``relpath(place, root)`` — agrees with it only while no link re-roots the
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

    ``None`` where there is no location below the root to state. Three shapes
    reach it. A place outside the root — a standalone emulator's own folder,
    which is still a place (:attr:`FirmwarePlacement.place`). A declaration
    that is absent, absolute, or climbs out of the root. And a declaration that
    normalises to ``.``, which is the root itself rather than a location under
    it.
    """
    relative = os.path.relpath(place, root)
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


def _placements(answer: Any, folders: _FolderIdentities) -> tuple[FirmwarePlacement, ...]:
    """One placement per emulator and declared file name — each emulator's own requirement.

    Two emulators routinely want the same name — gambatte and SameBoy both ask
    for a Game Boy boot ROM — and they do not always want it in the same place:
    Kronos opens ``kronos/saturn_bios.bin`` where YabaSanshiro opens
    ``saturn_bios.bin``. So nothing is folded across emulators by name:
    each emulator's requirement is its own placement, with its own place,
    presence and verdict, and :meth:`FirmwareCatalogue.placements_for` picks the
    launching emulator's. Two rows of ONE identity are one placement — the
    first stands, as the identity-keyed readings keep the first entry.

    Which emulators declare the name at all is still read over every entry
    (:func:`_wants`). The answer's caveats are indexed once here rather than per
    row because one index serves every name in it.
    """
    at_path, in_dir = _caveats_by_destination(answer)
    by_name: dict[str, list[Any]] = {}
    for core in answer.cores:
        for requirement, option in _requirement_entries(core):
            by_name.setdefault(requirement.file_name, []).append((core, requirement, option))

    placements: list[FirmwarePlacement] = []
    for file_name, pairs in by_name.items():
        wants = _wants(pairs)
        seen: set[str | None] = set()
        for core, requirement, _option in pairs:
            identity = emulator_identity(core)
            if identity in seen:
                continue
            seen.add(identity)
            placements.append(
                _placement_for(
                    file_name, core, requirement, wants, answer.root, (at_path, in_dir), _links_out(core), folders
                )
            )
    return tuple(sorted(placements, key=lambda placement: placement.file_name))


def _links_out(core: Any) -> dict[str, str]:
    """Each place of *core*'s that is a link leading out of the firmware root, by what it leads to.

    The resolver states a declared file reached through such a link under its
    target (``path``) and names the link beside it, on the entry
    (``firmware-file-linked-outside-root``); so does a file a folder listing
    kept. Keyed by the target, which is what the requirement carries.
    """
    links: dict[str, str] = {}
    for caveat in core.caveats:
        if caveat.code != CAVEAT_FIRMWARE_FILE_LINKED_OUTSIDE_ROOT:
            continue
        link, target = caveat.data.get("link"), caveat.data.get("target")
        if isinstance(link, str) and isinstance(target, str):
            links.setdefault(target, link)
    return links


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
    core: Any,
    requirement: Any,
    wants: tuple[FirmwareWant, ...],
    root: str,
    caveats: tuple[dict[str, tuple[Any, ...]], dict[str, tuple[Any, ...]]],
    links_out: dict[str, str],
    folders: _FolderIdentities,
) -> FirmwarePlacement:
    """One emulator's row for one name: where it opens the file, and what was read there.

    ``wants`` is every emulator under the name, because that is what the name
    is keyed on. Everything about the PLACE comes from this emulator's
    requirement alone, and so does the verdict. The place is the link where the
    declared place is a link leading out of the firmware root — the resolver
    then states the requirement at the link's target and judges the bytes there
    — and the requirement's own path otherwise, inside the root or outside it.
    """
    at_path, in_dir = caveats
    directory = requirement.declared_kind == DECLARED_DIRECTORY
    identity = emulator_identity(core)
    place: str = links_out.get(requirement.path) or requirement.path
    speaking = _speaking_for(at_path, in_dir, requirement, _names_of(core))
    supplied = requirement.supplied_by
    pinned = requirement.identity
    return FirmwarePlacement(
        file_name=file_name,
        relative_path=_declared_location(requirement, place, root),
        description=requirement.description,
        wants=wants,
        present=requirement.present,
        declared_kind=DECLARED_DIRECTORY if directory else DECLARED_FILE,
        declaration=core.declaration,
        caveats=tuple(dict.fromkeys(caveat.code for caveat in speaking)),
        folder=_folder_verdict(requirement, speaking) if directory else None,
        supplied_by=supplied.label if supplied is not None else None,
        checked=requirement.checked,
        missing_configured_image=_missing_configured_image(core, speaking),
        emulator=identity,
        satisfied=requirement.satisfied,
        place=place,
        identity_md5=pinned.md5.lower() if pinned is not None else None,
        identity_kind=pinned.kind if pinned is not None else None,
        folder_md5s=_folder_md5s(core, requirement, folders) if directory else frozenset(),
        folder_files=_folder_files(speaking, links_out) if directory else (),
    )


def _folder_md5s(core: Any, requirement: Any, folders: _FolderIdentities) -> frozenset[str]:
    """The checksums the packaged table files under the folder *requirement* declares — the images it recognises."""
    if core.core_so is None:
        return frozenset()
    row = declared_directory_of(core.core_so.removesuffix(".so"), requirement.declared)
    return folders.under(row.identities) if row is not None else frozenset()


def _folder_files(speaking: tuple[Any, ...], links_out: dict[str, str]) -> tuple[FolderFile, ...]:
    """What the resolver stated about each file it read inside a declared folder, in its own verdicts.

    An image the emulator's own test accepts is met; a file whose two reads
    disagree, or whose read did not come back, is not established; a file the
    read rejected is named only where the folder holds no image at all, and is
    not met. A rejected file beside an image gets no statement, and so no entry:
    the resolver says it is nobody's.
    """
    files: dict[str, FolderFile] = {}
    targets = {link: target for target, link in links_out.items()}
    for path, satisfied, data in _folder_statements(speaking):
        if isinstance(path, str) and path not in files:
            files[path] = _folder_file(path, satisfied, data, targets.get(path))
    return tuple(sorted(files.values(), key=lambda file: file.path))


def _folder_statements(speaking: tuple[Any, ...]) -> Iterator[tuple[Any, bool | None, dict[str, Any]]]:
    """Each file a statement about a declared folder names, its verdict, and what the statement says of it."""
    for caveat in speaking:
        if caveat.code in _IDENTIFIED_IMAGE_CODES:
            yield caveat.data.get("path"), True, caveat.data
        elif caveat.code in (CAVEAT_FIRMWARE_IMAGE_CONTRADICTED, CAVEAT_FIRMWARE_UNREADABLE):
            yield caveat.data.get("path"), None, caveat.data
        elif caveat.code == CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_IMAGE:
            for path in caveat.data.get("paths") or ():
                yield path, False, {}


def _folder_file(path: str, satisfied: bool | None, data: dict[str, Any], target: str | None) -> FolderFile:
    """One file inside a declared folder as the resolver stated it."""
    md5, description = data.get("md5"), data.get("description")
    return FolderFile(
        path=path,
        satisfied=satisfied,
        target=target,
        md5=md5.lower() if isinstance(md5, str) and md5 else None,
        description=description if isinstance(description, str) and description else None,
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
