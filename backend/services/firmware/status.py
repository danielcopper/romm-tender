"""The BIOS status the QAM panel reads — always one platform at a time.

Joins the RomM listing with the machine's demand and answers in rows plus
aggregates: what each file is, whether the requirement it carries is met, and
the platform-wide verdict that follows. Both answers come off one builder, so a
platform and its games can never show a different level for the same files.

**The demand is read per platform**, because which emulators can run a system is
the only thing that says whether a standalone emulator's declarations belong to
it. A whole-machine reading does carry standalone entries, and still cannot serve
this: it is asked unverified, so a card that identifies its image by content
names no file there, and it is scoped to the machine rather than to the
emulators ES-DE offers for one system. Within a platform the two surfaces share
every answer: the pane and its games read one catalogue, one row set, one
verdict.

That reading is a live reading of the machine, too costly to pay for every
platform at once (measured in ``docs/architecture/qam-panel.md``, "Library"), so
the whole-library overview does not pay it at all: it answers only whether the
RomM listing could be read, and each platform's state is asked for on its own.
The caller decides which platforms and in what order, because it is the one
that holds the list, knows which row the reader is looking at and when to stop.

The doubt is narrower still, and is narrowed to ONE emulator: the pick the
platform launches with. An emulator ES-DE also offers, that nothing could be read
for, says nothing about a launch that does not use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain import firmware_paths
from domain.bios_status import (
    classify_content_search,
    collect_firmware_status,
    compute_bios_label,
    compute_bios_level,
    count_required,
    count_required_withheld,
    count_wanted,
    format_bios_status,
)
from domain.emulator_commands import options_to_payload, resolve_firmware_option
from domain.firmware_groups import console_regions_of, judge_groups
from domain.firmware_wants import DECLARED_DIRECTORY
from services.firmware.emulator_answers import (
    EMULATOR_FIELDS,
    EmulatorAnswer,
    emulator_answer,
    emulator_fields,
    emulator_tabs,
    row_payload,
)

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.bios_file import BiosFile
    from domain.emulator_commands import EmulatorOption, LaunchingEmulator
    from domain.firmware_groups import GroupVerdict
    from domain.firmware_wants import FirmwareCatalogue
    from domain.platform_system import PlatformSystem
    from domain.rom_install import RomInstall
    from services.firmware.demand import FirmwareDemand
    from services.firmware.listing import FirmwareListing
    from services.protocols import (
        CoreInfoProvider,
        FirmwareFileStore,
        PlatformCoreReader,
        PlatformSystems,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class FirmwareStatusReaderConfig:
    """Frozen wiring bundle handed to ``FirmwareStatusReader.__init__``.

    Holds the two peer sub-services the answers are built from — the platform's
    demand and the RomM listing — plus the ES-DE core reads, the slug/system
    mapping, the per-platform emulator override, the file store the delete
    count probes through, the Unit-of-Work factory, and runtime infrastructure.
    """

    demand: FirmwareDemand
    listing: FirmwareListing
    core_info: CoreInfoProvider
    platform_systems: PlatformSystems
    platform_core_reader: PlatformCoreReader
    firmware_file_store: FirmwareFileStore
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class FirmwareStatusReader:
    """Every BIOS status query the panel runs — the listing check, each platform's entry and the per-game check."""

    def __init__(self, *, config: FirmwareStatusReaderConfig) -> None:
        self._demand = config.demand
        self._listing = config.listing
        self._core_info = config.core_info
        self._platform_systems = config.platform_systems
        self._platform_core_reader = config.platform_core_reader
        self._firmware_file_store = config.firmware_file_store
        self._uow_factory = config.uow_factory
        self._loop = config.loop
        self._logger = config.logger

    # ── Rows and aggregates ──────────────────────────────────

    def _platform_demand(
        self, system: str | None, server_rows: list[dict[str, Any]], in_library: set[str], emulator: str | None
    ) -> tuple[FirmwareCatalogue, list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """One platform's demand and its whole row set, in one worker hop. Blocking.

        The reading, the destination each server row resolves to under it, the
        presence answer that follows, and the rows for files the emulators want
        that the library does not hold — every one of them a disk read, and all
        of them behind one catalogue so a row and the verdict over it can never
        come from two readings.

        A server row whose ``file_name`` fails the path-safety check cannot be on
        disk, so it is dropped rather than crashing the query (logged in
        ``FirmwareDemand.safe_dest_path``). Where no BIOS download may land
        (``FirmwareDemand.status_root``) every row is kept, with no
        ``local_path``: the resolver's answers stand, and a row only Tender's
        own probe could answer is left without a verdict.

        Each row is *emulator*'s own requirement wherever it declares the file
        (:meth:`FirmwareCatalogue.placements_for`), so its place, presence and
        verdict are the launching emulator's.

        The third answer is every OTHER emulator's own requirements, keyed by
        identity — the items each one's entry is built from (:func:`emulator_tabs`),
        each at that emulator's place and read in the same hop.
        """
        catalogue = self._demand.platform_catalogue(system)
        placements = catalogue.placements_for(emulator)
        bios_base = self._demand.status_root()
        rows: list[dict[str, Any]] = []
        for row in server_rows:
            placement = placements.get(row["file_name"])
            dest = None if bios_base is None else self._demand.safe_dest_path(row, placement, bios_base)
            if bios_base is not None and dest is None:
                continue
            rows.append({**row, "local_path": dest, "downloaded": self._demand.is_downloaded(placement, dest)})
        rows.extend(
            _overview_row(item) for item in self._demand.wanted_beyond_server(placements, in_library, bios_base)
        )
        others = {
            other: self._own_items(catalogue, other, in_library, bios_base)
            for other in catalogue.emulator_verdicts
            if other != emulator
        }
        return catalogue, rows, others

    def _own_items(
        self, catalogue: FirmwareCatalogue, emulator: str, in_library: set[str], bios_base: str | None
    ) -> list[dict[str, Any]]:
        """*emulator*'s own requirements as status items, at its own places. Blocking.

        Nothing is folded across emulators: each item is this emulator's
        placement of the name, with its presence read where it opens
        the file. A placement whose destination fails the path-safety check is
        dropped, as a server row's is.
        """
        items: list[dict[str, Any]] = []
        for placement in catalogue.placements:
            if placement.emulator != emulator:
                continue
            dest: str | None = None
            if bios_base is not None:
                dest = self._demand.safe_dest_path({"file_name": placement.file_name}, placement, bios_base)
                if dest is None:
                    continue
            items.append(
                {
                    "file_name": placement.file_name,
                    "downloaded": self._demand.is_downloaded(placement, dest),
                    "dest": dest,
                    "on_server": placement.file_name in in_library,
                }
            )
        return items

    async def _emulator_options(self, platform: PlatformSystem) -> dict[str, Any]:
        """The emulator options for *platform*'s system, or the unavailable answer where it has none."""
        if platform.taken is None:
            return platform.unavailable_options()
        return await self._loop.run_in_executor(None, self._core_info.get_emulator_options, platform.taken)

    def _platform_emulator(self, platform_slug: str, options: dict[str, Any]) -> EmulatorOption | None:
        """The emulator this platform resolves to — the one pick every surface here reads.

        :func:`resolve_firmware_option` over the emulators ES-DE lists and the
        per-platform override: the read-path precedence minus the per-game
        layer, and where nothing is bakeable the emulator RetroDECK declares
        first, which its plain launch starts unless an alternative emulator is
        set in ES-DE. Both projections of it are taken from this
        one call: the label a pane displays and the identity its BIOS answers
        key on.

        Takes the already-read *options* rather than the system name, because
        every caller needs the emulator list anyway and reading it costs a
        catalogue read plus a glob per bakeable standalone entry.
        """
        return resolve_firmware_option(options["options"], self._platform_core_reader.get_platform_core(platform_slug))

    def _resolve_launching_emulator(
        self, platform_slug: str, options: dict[str, Any], launching_emulator: LaunchingEmulator | None
    ) -> LaunchingEmulator | None:
        """Return the emulator PICK the firmware answer is scoped to.

        The pick, not its identity: the answer states both the emulator it is
        scoped to and the name it prints, and handing those two on separately is
        how one surface comes to name one emulator and judge by another
        (:class:`~domain.emulator_commands.LaunchingEmulator`).

        A non-``None`` *launching_emulator* is the pre-resolved per-game pick (the
        game-detail path runs ``ActiveCoreResolver`` upstream) and is used as-is —
        libretro or standalone, since the identity names both. ``None`` is the
        platform-level callers' "use the platform's own pick" signal, and it is
        also what the per-game path passes when nothing could be resolved for the
        ROM at all. Both are answered by :meth:`_platform_emulator`, so the game
        page and the platform pane cannot key their answers on two different
        emulators.
        """
        if launching_emulator is not None:
            return launching_emulator
        return self._platform_emulator(platform_slug, options)

    def _bios_aggregates(self, files, platform_slug: str, answer: EmulatorAnswer) -> dict[str, Any]:
        """The counts, level and label every surface reads off one classified file list.

        One derivation for the per-game paths and the overview, so a platform
        and its games can never show a different level for the same files.

        **The level is the resolver's verdict for the launching emulator**
        (:func:`compute_bios_level`), and every count here only words
        it. Three counted sets, each on purpose. ``required_count`` is the
        launching emulator's and includes a required file the library does not
        hold, because that file is a prerequisite whether or not anything here
        can fetch it; ``required_downloaded`` counts what the resolver judges
        met, and ``required_withheld`` what it could not judge. ``server_count``
        / ``local_count`` are the library's, and count only what it holds: "N/M
        files ready" is a progress bar over a set the user can actually
        complete. ``known_count`` / ``unknown_count`` are the machine's answer
        about the files themselves, over the library's set too, and nothing
        weighs either (:func:`count_wanted`).

        ``ratio_server_count`` / ``ratio_local_count`` are the library's pair
        again, over only the rows whose verdict was established: the pair the
        surfaces STATE as "n/N RomM library files", where a row that reads
        "could not be established" would otherwise stand as a file missing.

        Beside them travel the resolver's own words about the launching emulator
        (:func:`emulator_fields`), which the surfaces word and never weigh.

        The groups are the verdicts over the launching emulator's one-of groups.
        Each is one requirement inside ``required_count``; ``one_of_groups``
        carries them, so a surface words a group off the regions it covers and
        misses.
        """
        groups = answer.groups
        on_server = [f for f in files if f.on_server]
        server_count = len(on_server)
        local_count = sum(1 for f in on_server if f.downloaded)
        established = [f for f in on_server if f.satisfied is not None]
        required_count, required_downloaded = count_required(files, groups)
        known_count, unknown_count = count_wanted(files)

        result = {
            "needs_bios": True,
            "server_count": server_count,
            "local_count": local_count,
            "all_downloaded": local_count >= server_count,
            "ratio_server_count": len(established),
            "ratio_local_count": sum(1 for f in established if f.downloaded),
            "required_count": required_count,
            "required_downloaded": required_downloaded,
            "required_withheld": count_required_withheld(files, groups),
            "unknown_count": unknown_count,
            "known_count": known_count,
            "one_of_groups": [_group_payload(group) for group in groups],
            **emulator_fields(answer),
            "content_search": classify_content_search(answer.verdict, files, groups=groups),
        }
        # The bios_level state ("unknown" / "ok" / "partial" / "missing") and the
        # compact bios_label beside it, so every consumer reads the verdict
        # straight off this payload rather than from a rule of its own.
        status = format_bios_status(
            {**result, "files": files},
            platform_slug,
            verdict=answer.verdict,
            region_decides=answer.region_decides,
            groups=groups,
            game_level=answer.game_level,
        )
        result["bios_level"] = compute_bios_level(status)
        result["bios_label"] = compute_bios_label(status)
        return result

    def _bios_payload(
        self,
        files,
        platform_slug: str,
        answer: EmulatorAnswer,
        launching_emulator: LaunchingEmulator | None,
    ) -> dict[str, Any]:
        """The aggregates plus the per-file rows — what the per-game surfaces read.

        ``active_core_label`` is the NAME of the pick everything else here was
        scoped to, taken off that same pick rather than resolved again, so the
        sentence the game page heads its BIOS section with names the emulator the
        counts under it were filtered by. ``None`` where no pick could be made or
        it carries no label, and the surface then words the sentence without a
        name. The IDENTITY is deliberately not served back: what the launch is
        keyed on reaches the frontend through ``get_platform_core_info``, and this
        payload's own filtering already applied it (#923).
        """
        return {
            **self._bios_aggregates(files, platform_slug, answer),
            "active_core_label": launching_emulator.label if launching_emulator is not None else None,
            "files": [row_payload(f) for f in files],
        }

    # ── The whole-library overview ───────────────────────────

    @staticmethod
    def _server_files(firmware_list, platform_slug: str) -> list[dict[str, Any]]:
        """The listing's own rows for one key, in the server's own fields.

        *platform_slug* is the RomM platform slug the page lists, matched
        EXACTLY against the firmware directory a listed file sits under, never
        through :func:`firmware_paths.resolve_firmware_slugs`. What that costs:
        a library that files a platform's firmware under a directory named
        otherwise than the platform's slug does not reach that platform's rows —
        the pane shows the emulator's own rows and verdict, without the library
        files. The per-game check (:meth:`check_platform_bios`) does resolve the
        spellings.

        Where each file goes and whether it is there are answers about ONE
        platform's emulators, so they are filled in by :meth:`_platform_demand`
        once that platform's reading is in hand.
        """
        return [
            {
                "id": fw.get("id"),
                "file_name": fw.get("file_name", ""),
                "size": fw.get("file_size_bytes", 0),
                "md5": fw.get("md5_hash", ""),
                "on_server": True,
            }
            for fw in firmware_list
            if (firmware_paths.parse_firmware_slug(fw.get("file_path", "")) or "unknown") == platform_slug
        ]

    def _read_synced_slugs(self) -> set[str]:
        """The platforms the firmware page calls synced: those with a ROM still bound to a Steam shortcut.

        A ROM stays bound while the synced library holds it, through its platform
        or through an enabled collection, so a deselected platform drops out on
        the next sync unless a collection still lists one of its games
        (ADR-0007).
        """
        with self._uow_factory() as uow:
            return uow.roms.get_bound_platform_slugs()

    def _stamp_deletable(self, plat: dict[str, Any], platform_slug: str, records: list[BiosFile]) -> None:
        """Stamp what a delete would take — per row, and for the platform.

        One pass over the download records and one probe each, so every answer
        on the pane comes from the same set. The platform count and the rows are
        not the same NUMBER, and are not meant to be: the count is drawn from
        the download records, while a row exists only where something declares
        the file or the library offers it — a download RomM has since dropped
        and no core declares raises the count with no row to show it, and only
        the platform-wide button can reach it.

        The delete is authorised by the record and unlinks the path that record
        holds, so this is exactly that set: recorded paths under one of the
        platform's firmware slugs that are still on disk, counted as distinct
        PATHS because two records naming one file are one unlink.

        **A row's count is answered two ways, because a row is one of two
        things.** A declared FILE matches a record by name — the row knows its
        file name and not our record. A declared FOLDER has no name a record
        could carry, so it counts the distinct files our records name
        *underneath* it: the emulator lists that folder and whatever we
        downloaded into it is ours to remove, which is a rule about files and is
        independent of the separate rule that a folder is never offered as a
        download. Distinct for the reason the platform count is — a row's number
        is what its button promises to unlink. The folder path is used to NARROW
        the record set and never to widen it, so a destination that has since
        moved can only offer fewer files, never something Tender did not
        place.

        None of this is ``downloaded``, and none of it may be replaced by that:
        it is ``os.path.exists`` at the row's own destination, equally true of
        firmware the emulator shipped with. ``local_count`` is a third set again
        — the library's progress ratio, which includes files Tender never
        placed and drops our own downloads once RomM stops listing them; used
        for the button it was wrong in both directions.
        """
        slugs = set(firmware_paths.resolve_firmware_slugs(platform_slug))
        mine = [record for record in records if record.platform_slug in slugs]
        present = [record for record in mine if self._firmware_file_store.exists(record.file_path)]
        plat["deletable_count"] = len({record.file_path for record in present})

        names = {record.file_name for record in present}
        tab_rows = [row for tab in plat.get("emulator_tabs", []) for row in tab.get("rows", [])]
        for f in [*plat["files"], *tab_rows]:
            if f.get("declared_kind") == DECLARED_DIRECTORY:
                root = (f.get("local_path") or "").rstrip("/")
                f["deletable_count"] = (
                    len({record.file_path for record in present if record.file_path.startswith(f"{root}/")})
                    if root
                    else 0
                )
            else:
                f["deletable_count"] = 1 if f["file_name"] in names else 0

    async def _enrich_platform(
        self,
        plat: dict[str, Any],
        synced_slugs,
        in_library: set[str],
        records: list[BiosFile],
    ) -> None:
        """Add emulator info, wants, and game-installed flags to one platform entry.

        The core read seams key by the resolved RetroDECK ``system`` (ADR-0010
        §2), so each entry's raw RomM/BIOS-folder slug is normalized before the
        ``get_emulator_options`` call; ``has_games`` and the BIOS-folder file
        lookups stay on the raw slug (their own vocabulary). ``active_core`` and
        ``active_core_label`` are the two projections of ONE pick
        (:meth:`_platform_emulator`) — the per-platform override
        (``platform_cores``) when set and still resolvable, else the es_systems
        default, else, where nothing is bakeable, the emulator RetroDECK
        declares first — so the pane cannot name one emulator and judge by
        another.
        ``active_core`` carries that pick's IDENTITY, which stands for a
        standalone emulator as much as for a libretro core; it is ``None`` only
        where the pick could not be made or the resolver could not identify it,
        and then no row is its requirement and the answer reads as not
        established. The
        ``emulators`` list is the full classified picker payload and
        ``emulator_data_available`` flags whether the answering emulator source
        gave an emulator list, with ``emulator_data_reason`` and
        ``emulator_source`` saying why not and which source answered.

        **The demand is read per platform**, because a standalone emulator's
        declarations belong to a platform only by way of the emulators ES-DE
        offers for it. That is the cost of answering at all for a platform whose
        emulators are all standalone, and it is why this runs one platform at a
        time. *in_library* is the whole listing's file names rather than this
        platform's slice — see :meth:`FirmwareDemand.wanted_beyond_server` — and
        *records* is every BIOS download row, sliced to this platform by
        :meth:`_stamp_deletable`.
        """
        slug = plat["platform_slug"]
        platform = await self._loop.run_in_executor(None, self._platform_systems.platform_system, slug)
        system = platform.taken
        options = await self._emulator_options(platform)
        emulator = self._platform_emulator(slug, options)
        identity = emulator.emulator if emulator is not None else None
        plat["active_core"] = identity
        plat["active_core_label"] = emulator.label if emulator is not None else None
        plat["emulators"] = options_to_payload(options["options"])
        plat["emulator_data_available"] = options["available"]
        plat["emulator_data_reason"] = options["reason"]
        plat["emulator_source"] = options["source"]
        plat["platform_system"] = platform.payload()
        catalogue, rows, others = await self._loop.run_in_executor(
            None, self._platform_demand, system, plat["files"], in_library, identity
        )
        plat["files"] = rows
        placements = catalogue.placements_for(identity)
        complete = catalogue.reading_complete_for(identity)
        files = collect_firmware_status(
            [
                {
                    "file_name": f["file_name"],
                    "downloaded": f["downloaded"],
                    "dest": f["local_path"],
                    "on_server": f["on_server"],
                }
                for f in plat["files"]
            ],
            placements,
            complete,
            identity,
            catalogue.groups,
        )
        plat["files"] = [{**raw, **_wanted_fields(entry)} for raw, entry in zip(plat["files"], files, strict=True)]
        # Alphabetical, and only here: the two halves arrive in their own
        # orders — the library's listing, then the rows it does not hold,
        # appended — so a file Tender downloaded sat below one the
        # library still offers for no reason a reader could see. Sorted
        # AFTER the merge, because the zip above is positional and because
        # `declared_path` only exists once `_wanted_fields` has run.
        #
        # The key is what the row DISPLAYS: `declared_path` is the folder
        # prefix and the name together (`dolphin-emu/Sys/codehandler.bin`),
        # and the bare name where nothing declared a subdirectory. Folded to
        # lower case, because a corpus that mixes `BS-X.bin` with
        # `sgb_boot.bin` reads as unsorted under a case-sensitive one.
        plat["files"].sort(key=lambda f: (f.get("declared_path") or f.get("file_name", "")).lower())
        plat["has_games"] = slug in synced_slugs
        plat["all_downloaded"] = all(f["downloaded"] for f in plat["files"])
        plat["emulator_tabs"] = emulator_tabs(
            catalogue, identity, plat["active_core_label"], options["options"], others
        )
        self._stamp_deletable(plat, slug, records)
        answer = emulator_answer(catalogue, identity, judge_groups(catalogue.groups_for(identity)))
        self._set_platform_bios_aggregates(plat, slug, files, answer)

    def _set_platform_bios_aggregates(self, plat: dict[str, Any], slug: str, files, answer: EmulatorAnswer) -> None:
        """Stamp the per-platform BIOS aggregates onto a ``get_platform_firmware_status`` entry.

        The counts, ``one_of_groups``, the resolver's own words about the
        launching emulator (:func:`emulator_fields`) and the ``bios_level`` state
        (``"unknown"`` / ``"ok"`` / ``"partial"`` / ``"missing"``), so the platform
        detail reads the verdict and the display counts straight off this
        payload. The whole payload comes from the same builder the per-game path
        uses, so the level a platform shows and the level its games show cannot
        diverge.

        No download the page offers is gated on the verdict — not on
        ``bios_level``, not on any count. What those buttons read is what the
        RomM library holds and what the launching emulator declared, which are
        inventory and demand rather than readiness.
        """
        payload = self._bios_aggregates(files, slug, answer)
        for key in (
            "server_count",
            "local_count",
            "ratio_server_count",
            "ratio_local_count",
            "required_count",
            "required_downloaded",
            "required_withheld",
            "bios_level",
            "one_of_groups",
            *EMULATOR_FIELDS,
        ):
            plat[key] = payload[key]

    async def get_firmware_status(self) -> dict[str, Any]:
        """Return whether the RomM firmware listing could be read — and nothing about any platform.

        The cheap half: one round trip for the library's listing, cached. The
        page reads ``server_offline`` off it, which is what its downloads stand
        on. Which platforms get a BIOS answer is the page's list, not this
        payload's, and what a platform's state IS costs a live per-system
        reading, asked for one platform at a time through
        :meth:`get_platform_firmware_status`.
        """
        try:
            await self._loop.run_in_executor(None, self._listing.get_firmware_list)
        except Exception as e:
            self._logger.warning(f"Answering the firmware overview without the server listing: {e}")
            return {"success": True, "server_offline": True}
        return {"success": True, "server_offline": False}

    async def get_platform_firmware_status(self, platform_slug: str) -> dict[str, Any]:
        """Return one platform's whole BIOS entry.

        The half of the page that pays the reading, asked for exactly the
        platform the caller wants next. Every platform gets its entry, with or
        without a row to show: a reading that found nothing wanted is the answer
        that the launching emulator needs nothing, and a reading that did not
        happen is the answer that nothing could be established — the verdict
        tells the two apart, and dropping either would leave the page to guess.
        """
        synced_slugs, records = await self._loop.run_in_executor(None, self._read_status_inputs)
        try:
            firmware_list = await self._loop.run_in_executor(None, self._listing.get_firmware_list)
        except Exception as e:
            self._logger.warning(f"Answering for {platform_slug} without the server listing: {e}")
            firmware_list = []

        plat: dict[str, Any] = {
            "platform_slug": platform_slug,
            "files": self._server_files(firmware_list, platform_slug),
        }
        in_library = {fw.get("file_name", "") for fw in firmware_list}
        await self._enrich_platform(plat, synced_slugs, in_library, records)
        return {"success": True, "platform": plat}

    def _read_status_inputs(self) -> tuple[set[str], list[BiosFile]]:
        """The two blocking DB reads one platform's entry needs, in one worker hop.

        Both open a UoW, so both belong off the loop thread, and neither depends
        on the other. The firmware reading is not here: it needs the platform's
        resolved system name and must not run inside a UoW at all
        (:meth:`_platform_demand`).
        """
        return self._read_synced_slugs(), self._read_bios_records()

    def _read_bios_records(self) -> list[BiosFile]:
        """Every BIOS download record, for the platform's delete count.

        Read whole rather than filtered to the platform: it is one small table,
        and the slicing rule — which of its rows are this platform's — belongs
        with the count it feeds (:meth:`_stamp_deletable`). One short read UoW,
        closed before the file probes that method runs.
        """
        with self._uow_factory() as uow:
            return list(uow.bios_files.iter_all())

    # ── The per-game check ───────────────────────────────────

    async def check_platform_bios(
        self,
        platform_slug,
        launching_emulator: LaunchingEmulator | None = None,
        rom_regions: tuple[str, ...] = (),
        install: RomInstall | None = None,
    ) -> dict[str, Any]:
        """Check if RomM has firmware for this platform and whether it's downloaded.

        Returns BIOS status only. ``launching_emulator`` is the pre-resolved
        per-game PICK — the whole resolution rather than its identity, because
        this answer states both the emulator it was filtered by and the name it
        prints, and a caller handing those on separately is how a surface comes
        to name one emulator and judge by another
        (:class:`~domain.emulator_commands.LaunchingEmulator`). Its identity
        filters the firmware list by what THIS emulator needs (an INPUT to
        ``collect_firmware_status`` so ``required_count`` / the missing-BIOS
        badge stay launch-aware) and names a standalone emulator as readily as a
        libretro core; its label rides back out as ``active_core_label`` and
        nothing else does (#923 — what a game launches with reaches the frontend
        through ``get_platform_core_info``). ``None`` means "use the platform's
        own pick" (:meth:`_resolve_launching_emulator`).

        *rom_regions* are the game's own regions as RomM names them. Where any
        maps onto a console region, the launching emulator's one-of groups are
        judged for those regions alone; where none does — or for the platform's
        own check, which passes none — they are judged over every region.

        An unreachable server costs the files only it knows about, not the
        answer: what the platform's emulators want is read locally either way.
        The one payload that still says nothing is a platform with no row to show
        whose launching emulator the resolver answered neither met nor not met —
        that ``needs_bios: False`` carries ``bios_status_unknown: True`` and no
        consumer may read it as "this platform needs none". A verdict of
        not met is an answer with no row to show — DuckStation over a BIOS folder
        its search found nothing in declares no file — and is sent whole.
        """
        platform = await self._loop.run_in_executor(None, self._platform_systems.rom_system, platform_slug, install)
        system = platform.taken
        fw_slugs = firmware_paths.resolve_firmware_slugs(platform_slug)
        options = await self._emulator_options(platform)
        pick = self._resolve_launching_emulator(platform_slug, options, launching_emulator)
        identity = pick.emulator if pick is not None else None

        try:
            firmware_list = await self._loop.run_in_executor(None, self._listing.get_firmware_list)
        except Exception as e:
            self._logger.warning(f"Answering BIOS status without the server listing: {e}")
            firmware_list = []

        # A platform can read two firmware folders (``psx`` reads ``psx`` and
        # ``ps``), and RomM may list one name in both. A row carries nothing but
        # the name, so the copies are the same row and the first is kept.
        server_names = dict.fromkeys(
            fw.get("file_name", "")
            for fw in firmware_list
            if firmware_paths.parse_firmware_slug(fw.get("file_path", "")) in fw_slugs
        )
        server_rows = [{"file_name": name, "on_server": True} for name in server_names]
        catalogue, rows, others = await self._loop.run_in_executor(
            None,
            self._platform_demand,
            system,
            server_rows,
            {fw.get("file_name", "") for fw in firmware_list},
            identity,
        )
        items = [
            {
                "file_name": r["file_name"],
                "downloaded": r["downloaded"],
                "dest": r["local_path"],
                "on_server": r["on_server"],
            }
            for r in rows
        ]
        placements = catalogue.placements_for(identity)
        complete = catalogue.reading_complete_for(identity)
        files = collect_firmware_status(items, placements, complete, identity, catalogue.groups)
        groups = judge_groups(catalogue.groups_for(identity), console_regions_of(rom_regions))
        answer = emulator_answer(catalogue, identity, groups)

        met = answer.verdict.requirements_met if answer.verdict is not None else None
        if not files and met is not False:
            return {"needs_bios": False} if met is True else {"needs_bios": False, "bios_status_unknown": True}

        payload = self._bios_payload(files, platform_slug, answer, pick)
        payload["emulator_tabs"] = emulator_tabs(
            catalogue, identity, pick.label if pick is not None else None, options["options"], others
        )
        return payload


def _overview_row(item: dict[str, Any]) -> dict[str, Any]:
    """The overview row for a file the library does not hold.

    ``on_server: False`` is the load-bearing field: every download affordance
    filters on it (``frontend/src/bigpicture/library/PlatformDetail.tsx``), and
    so do the platform detail's own progress totals. ``id`` is ``None`` as an
    honest absence — there is no server record to name — and no consumer reads
    it, so filling it in with a placeholder would withhold nothing but would
    make a row that cannot be fetched look fetchable to the next reader.
    """
    return {
        "id": None,
        "file_name": item["file_name"],
        "size": 0,
        "md5": "",
        "local_path": item["dest"],
        "downloaded": item["downloaded"],
        "on_server": False,
    }


def _group_payload(group: GroupVerdict) -> dict[str, Any]:
    """One group verdict on the wire — its state, the region sets as lists, and every option."""
    return {
        "state": group.state,
        "covered": list(group.covered),
        "missing": list(group.missing),
        "unchecked": list(group.unchecked),
        "unstated": list(group.unstated),
        "game_regions": list(group.game_regions),
        "regions": list(group.regions),
        "options": [
            {"file_name": option.file_name, "regions": list(option.regions), "satisfied": option.satisfied}
            for option in group.options
        ],
    }


def _wanted_fields(entry) -> dict[str, Any]:
    """The overview projection of one classified file.

    The overview's rows keep the server's own fields (id, size, md5) and gain
    only what the machine answered, so the two vocabularies stay separable.
    """
    return {
        "declared_path": entry.declared_path,
        "description": entry.description,
        "wanted": entry.wanted,
        "required_by_active": entry.required_by_active,
        "used_by_active": entry.used_by_active,
        "one_of": entry.one_of,
        "fetch_for_required": entry.fetch_for_required,
        "supplied_by": entry.supplied_by,
        "satisfied": entry.satisfied,
        "declared_kind": entry.declared_kind,
        "declaration": entry.declaration,
        "caveats": entry.caveats,
        "images": entry.images,
        "checked": entry.checked,
        "missing_configured_image": entry.missing_configured_image,
        "declared_by": entry.declared_by,
    }
