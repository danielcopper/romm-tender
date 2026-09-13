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

That reading costs 67-350 ms per system on the reference machine and a whole
answer for one platform 106-486 ms, so the whole-library overview does not pay
it at all: it answers WHICH platforms the page can speak for, and each
platform's state is asked for on its own. The caller decides the order, because
it is the one that knows which row the reader is looking at and when to stop.

The doubt is narrower still, and is narrowed to ONE emulator: the pick the
platform launches with. An emulator ES-DE also offers, that nothing could be read
for, says nothing about a launch that does not use it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from domain import firmware_paths
from domain.bios_status import (
    BIOS_LEVEL_UNKNOWN,
    SYSTEM_IMAGE_NOT_DEMANDED,
    classify_system_image,
    collect_firmware_status,
    compute_bios_label,
    compute_bios_level,
    count_required,
    count_required_withheld,
    count_wanted,
    format_bios_status,
)
from domain.emulator_commands import options_to_payload, resolve_platform_option
from domain.firmware_wants import DECLARED_DIRECTORY

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.bios_file import BiosFile
    from domain.emulator_commands import EmulatorOption, LaunchingEmulator
    from domain.firmware_wants import FirmwareCatalogue
    from services.firmware.demand import FirmwareDemand
    from services.firmware.listing import FirmwareListing
    from services.protocols import (
        CoreInfoProvider,
        FirmwareFileStore,
        PlatformCoreReader,
        SystemResolver,
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
    resolve_system: SystemResolver
    platform_core_reader: PlatformCoreReader
    firmware_file_store: FirmwareFileStore
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class FirmwareStatusReader:
    """Every status-bearing BIOS query the panel runs — the overview and the per-game check."""

    def __init__(self, *, config: FirmwareStatusReaderConfig) -> None:
        self._demand = config.demand
        self._listing = config.listing
        self._core_info = config.core_info
        self._resolve_system = config.resolve_system
        self._platform_core_reader = config.platform_core_reader
        self._firmware_file_store = config.firmware_file_store
        self._uow_factory = config.uow_factory
        self._loop = config.loop
        self._logger = config.logger

    # ── Rows and aggregates ──────────────────────────────────

    def _platform_demand(
        self, system: str, server_rows: list[dict[str, Any]], in_library: set[str]
    ) -> tuple[FirmwareCatalogue, list[dict[str, Any]]]:
        """One platform's demand and its whole row set, in one worker hop. Blocking.

        The reading, the destination each server row resolves to under it, the
        presence answer that follows, and the rows for files the emulators want
        that the library does not hold — every one of them a disk read, and all
        of them behind one catalogue so a row and the verdict over it can never
        come from two readings.

        A server row whose ``file_name`` fails the path-safety check cannot be on
        disk, so it is dropped rather than crashing the query (logged in
        ``FirmwareDemand.safe_dest_path``).
        """
        catalogue = self._demand.platform_catalogue(system)
        placements = catalogue.by_file_name()
        rows: list[dict[str, Any]] = []
        for row in server_rows:
            placement = placements.get(row["file_name"])
            dest = self._demand.safe_dest_path(row, placement)
            if dest is None:
                continue
            rows.append({**row, "local_path": dest, "downloaded": self._demand.is_downloaded(placement, dest)})
        rows.extend(_overview_row(item) for item in self._demand.wanted_beyond_server(placements, in_library))
        return catalogue, rows

    def _platform_emulator(self, platform_slug: str, options: dict[str, Any]) -> EmulatorOption | None:
        """The emulator this platform resolves to — the one pick every surface here reads.

        :func:`resolve_platform_option` over the emulators ES-DE lists and the
        per-platform override, which is the read-path precedence minus the
        per-game layer. Both projections of it are taken from this one call: the
        label a pane displays and the identity its BIOS answers key on.

        Takes the already-read *options* rather than the system name, because
        every caller needs the emulator list anyway and reading it costs a
        catalogue read plus a glob per bakeable standalone entry.
        """
        return resolve_platform_option(options["options"], self._platform_core_reader.get_platform_core(platform_slug))

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

    def _bios_aggregates(self, files, platform_slug: str, complete: bool, system_image: str) -> dict[str, Any]:
        """The counts, level and label every surface reads off one classified file list.

        One derivation for the per-game paths and the overview, so a platform
        and its games can never show a different level for the same files.

        Three axes, and each counts a different set on purpose.
        ``required_count`` is the launching emulator's — the badge's — and includes a
        required file the library does not hold, because that file is a
        prerequisite whether or not anything here can fetch it.
        ``required_withheld`` rides with it as the part of that count nothing
        could judge, so a surface warning about an absence can subtract what was
        never established rather than reading a declined verdict as a gap.
        ``server_count`` / ``local_count`` are the library's, and count only what
        it holds: "N/M files ready" is a progress bar over a set the user can
        actually complete, and folding in files that were never uploaded would
        read as work outstanding on a system that needs nothing — a SNES page
        would say ``0 / 26 files, 26 missing`` for twenty-six optional files no
        core requires. ``known_count`` / ``unknown_count`` are the machine's
        answer about the files themselves, over the library's set too — and
        neither is weighed against anything: ``_nothing_established`` asks
        whether ``known_count`` was supplied at all and then decides on
        ``reading_complete`` alone, and ``unknown_count`` has no reader on either
        side of the wire (:func:`count_wanted`).

        *system_image* is a fourth axis beside those three counted sets, and the
        only one of the four that is not a count at all
        (:func:`classify_system_image`): the console's own demand on the
        launching emulator is disjunctive — one of these images, not each of them —
        so it travels as a value and every surface words it rather than printing
        it as a ratio.
        """
        on_server = [f for f in files if f.on_server]
        server_count = len(on_server)
        local_count = sum(1 for f in on_server if f.downloaded)
        required_count, required_downloaded = count_required(files)
        known_count, unknown_count = count_wanted(files)

        result = {
            "needs_bios": True,
            "server_count": server_count,
            "local_count": local_count,
            "all_downloaded": local_count >= server_count,
            "required_count": required_count,
            "required_downloaded": required_downloaded,
            "required_withheld": count_required_withheld(files),
            "unknown_count": unknown_count,
            "known_count": known_count,
            "system_image": system_image,
        }
        # The bios_level state ("unknown" / "ok" / "partial" / "missing") and the
        # compact bios_label beside it, so every consumer reads the verdict
        # straight off this payload instead of re-deriving the threshold logic.
        # "unknown" replaces the false "ok" a bare count would give, for a
        # platform whose server files went entirely unanswered, for one holding
        # no file at all under a reading that never happened, and for one whose
        # launching emulator requires a file nothing could judge.
        #
        # Derived HERE and only here because this is where ``complete`` is known:
        # a second derivation elsewhere would have to be handed the same reading
        # state to reach the same answer, and would agree by coincidence if it
        # were not.
        #
        # The rows travel with the counts here because one of those two shapes
        # turns on there being no row; ``result`` itself stays row-free, because
        # it is the aggregate half of a payload that carries them separately.
        status = format_bios_status(
            {**result, "files": files}, platform_slug, reading_complete=complete, system_image=system_image
        )
        result["bios_level"] = compute_bios_level(status)
        result["bios_label"] = compute_bios_label(status)
        return result

    def _bios_payload(
        self,
        files,
        platform_slug: str,
        complete: bool,
        system_image: str,
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
            **self._bios_aggregates(files, platform_slug, complete, system_image),
            "active_core_label": launching_emulator.label if launching_emulator is not None else None,
            "files": [asdict(f) for f in files],
        }

    # ── The whole-library overview ───────────────────────────

    @staticmethod
    def _listed_slugs(firmware_list) -> set[str]:
        """Every key the RomM listing names — the page's platforms, in RomM's vocabulary.

        A firmware ``file_path`` carries the platform's FIRMWARE-directory name,
        which is routinely a different word from its platform slug (``psx`` →
        ``bios/ps/``); a path naming nothing under ``bios/`` is filed under
        ``"unknown"``, a key like any other.
        """
        return {firmware_paths.parse_firmware_slug(fw.get("file_path", "")) or "unknown" for fw in firmware_list}

    @staticmethod
    def _server_files(firmware_list, platform_slug: str) -> list[dict[str, Any]]:
        """The listing's own rows for one key, in the server's own fields.

        Matched on the key EXACTLY, never through
        :func:`firmware_paths.resolve_firmware_slugs`: a key is the firmware
        directory's own name wherever the listing named one, and a key that is a
        platform slug is there precisely because no spelling of it appears in
        the listing (:meth:`_seeded_slugs`). Widening the match would hand a
        ``psx`` entry the rows of a ``ps`` entry that keeps them too, so one file
        would be offered twice on one page. The per-game check
        (:meth:`check_platform_bios`) is keyed by platform slug instead and does
        resolve the spellings — a different question, asked in the caller's
        vocabulary rather than the listing's.

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

    @staticmethod
    def _seeded_slugs(listed: set[str], synced_slugs) -> list[str]:
        """Every synced platform the listing did not name.

        A platform whose emulators want firmware the library has never held would
        otherwise be absent from a page that is about exactly that — and with the
        server unreachable, every platform is in that position. Whether such a
        platform has anything to say is decided by its own reading
        (:func:`_has_something_to_say`), which is why these are offered to the
        caller rather than answered for here.

        A slug is seeded only when none of its firmware-directory spellings is
        already a key: RomM files a platform's firmware under its own directory
        name (``psx`` → ``bios/ps/``), so the raw slug and the listing's key are
        routinely different words for one platform.
        """
        return [
            slug
            for slug in sorted(synced_slugs)
            if not any(fw_slug in listed for fw_slug in firmware_paths.resolve_firmware_slugs(slug))
        ]

    def _read_synced_slugs(self) -> set[str]:
        """Return platform slugs with at least one ROM bound to a Steam shortcut.

        A bound ROM (``shortcut_app_id`` set) is one that is currently in the
        synced library, covering both platform- and collection-sync. Deselected
        platforms get unbound on the next sync (ADR-0007) and drop out.
        """
        with self._uow_factory() as uow:
            return {
                rom.platform_slug
                for rom in uow.roms.iter_all()
                if rom.platform_slug and rom.shortcut_app_id is not None
            }

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
        moved can only offer fewer files, never something the plugin did not
        place.

        None of this is ``downloaded``, and none of it may be replaced by that:
        it is ``os.path.exists`` at the row's own destination, equally true of
        firmware the emulator shipped with. ``local_count`` is a third set again
        — the library's progress ratio, which includes files the plugin never
        placed and drops our own downloads once RomM stops listing them; used
        for the button it was wrong in both directions.
        """
        slugs = set(firmware_paths.resolve_firmware_slugs(platform_slug))
        mine = [record for record in records if record.platform_slug in slugs]
        present = [record for record in mine if self._firmware_file_store.exists(record.file_path)]
        plat["deletable_count"] = len({record.file_path for record in present})

        names = {record.file_name for record in present}
        for f in plat["files"]:
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
        default — so the pane cannot name one emulator and judge by another.
        ``active_core`` carries that pick's IDENTITY, which stands for a
        standalone emulator as much as for a libretro core; it is ``None`` only
        where the pick could not be made or the resolver could not identify it,
        and the rows then fall back to every declaring emulator. The
        ``emulators`` list is the full classified picker payload and
        ``emulator_data_available`` flags whether ``es_systems.xml`` was readable.

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
        system = self._resolve_system(slug)
        options = self._core_info.get_emulator_options(system)
        emulator = self._platform_emulator(slug, options)
        identity = emulator.emulator if emulator is not None else None
        plat["active_core"] = identity
        plat["active_core_label"] = emulator.label if emulator is not None else None
        plat["emulators"] = options_to_payload(options["options"])
        plat["emulator_data_available"] = options["available"]
        catalogue, rows = await self._loop.run_in_executor(
            None, self._platform_demand, system, plat["files"], in_library
        )
        plat["files"] = rows
        placements = catalogue.by_file_name()
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
            catalogue.emulators_needing_one_of_their_files(),
        )
        plat["files"] = [{**raw, **_wanted_fields(entry)} for raw, entry in zip(plat["files"], files, strict=True)]
        # Alphabetical, and only here: the two halves arrive in their own
        # orders — the library's listing, then the rows it does not hold,
        # appended — so a file the plugin downloaded sat below one the
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
        self._stamp_deletable(plat, slug, records)
        system_image = classify_system_image(catalogue.verdict_for(identity), files, identity)
        self._set_platform_bios_aggregates(plat, slug, files, complete, system_image)

    def _set_platform_bios_aggregates(
        self, plat: dict[str, Any], slug: str, files, complete: bool, system_image: str
    ) -> None:
        """Stamp the per-platform BIOS aggregates onto a ``get_firmware_status`` entry.

        Adds ``server_count`` / ``local_count`` / ``required_count`` /
        ``required_downloaded`` / ``required_withheld`` / ``system_image`` and the
        ``bios_level`` state (``"unknown"`` / ``"ok"`` / ``"partial"`` /
        ``"missing"``) so the
        platform detail reads the decision and the display counts straight off
        this payload instead of re-deriving the threshold logic in the frontend. The
        whole payload comes from the same builder the per-game path uses, so the
        level a platform shows and the level its games show cannot diverge.

        ``required_withheld`` is what tells the page's two unknowns apart, and
        what it decides is WORDING: a platform nothing could speak for has no
        file list to point the reader at, so the pane offers the by-hand route
        instead, while one whose verdict was declined by a single unjudgeable
        requirement points at the rows that did answer. Neither withdraws a
        download: no download the page offers is gated on the verdict — not on
        ``bios_level``, not on this count. What those buttons read is what the
        RomM library holds and what the launching emulator declared, which are
        inventory and demand rather than readiness.

        *complete* is whether the LAUNCHING emulator could be asked at all, and
        it is what stops a platform reading a green "all ready" over an emulator
        nobody asked — including one whose page carries another emulator's rows,
        where the counts would otherwise say nothing is required.

        *system_image* is the console's own demand on the launching emulator, and it
        travels beside the counts rather than in them: the pane words it and the
        list's tooltip words it, and neither may state it as a ratio.
        """
        payload = self._bios_aggregates(files, slug, complete, system_image)
        plat["server_count"] = payload["server_count"]
        plat["local_count"] = payload["local_count"]
        plat["required_count"] = payload["required_count"]
        plat["required_downloaded"] = payload["required_downloaded"]
        plat["required_withheld"] = payload["required_withheld"]
        plat["bios_level"] = payload["bios_level"]
        plat["system_image"] = payload["system_image"]

    async def get_firmware_status(self) -> dict[str, Any]:
        """Return which platforms the page can speak for — never what it will say.

        The cheap half, and every input it reads serves the whole page: one DB
        read of the synced platforms, and the RomM listing (one round trip for
        the library, cached). What a platform's BIOS state IS costs a live
        per-system reading, so it is asked for one platform at a time through
        :meth:`get_platform_firmware_status` — paying all of them here is what
        made this call cost seconds.

        An unreachable server removes the platforms only it knows about and the
        ability to download; the synced ones stay, because what the installed
        emulators want is read locally.
        """
        server_offline = False
        synced_slugs = await self._loop.run_in_executor(None, self._read_synced_slugs)
        listed: set[str] = set()
        try:
            firmware_list = await self._loop.run_in_executor(None, self._listing.get_firmware_list)
            listed = self._listed_slugs(firmware_list)
        except Exception as e:
            self._logger.warning(f"Building the firmware overview without the server listing: {e}")
            server_offline = True

        slugs = sorted(listed.union(self._seeded_slugs(listed, synced_slugs)))
        return {
            "success": True,
            "server_offline": server_offline,
            "platforms": [{"platform_slug": slug, "has_games": slug in synced_slugs} for slug in slugs],
        }

    async def get_platform_firmware_status(self, platform_slug: str) -> dict[str, Any]:
        """Return one platform's whole overview entry, or that it has none to show.

        The half of the page that pays the reading, asked for exactly the
        platform the caller wants next. ``platform`` is ``None`` where the page
        has nothing to say — a platform the listing never named whose reading
        finished and found nothing wanted (:func:`_has_something_to_say`) — which
        is the entry a whole-page answer used to drop before returning.

        A platform the listing DOES name keeps its entry even where every row
        falls away, because the rows falling away is itself an answer about a
        platform the library holds firmware for.
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
        listed = bool(plat["files"])
        in_library = {fw.get("file_name", "") for fw in firmware_list}
        await self._enrich_platform(plat, synced_slugs, in_library, records)
        if not listed and not _has_something_to_say(plat):
            return {"success": True, "platform": None}
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
        self, platform_slug, launching_emulator: LaunchingEmulator | None = None
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

        An unreachable server costs the files only it knows about, not the
        answer: what the platform's emulators want is read locally either way.
        The one payload that still says nothing is a platform with no row to show
        AND either no complete reading or a console whose own firmware demand
        this list cannot speak for — that ``needs_bios: False`` carries
        ``bios_status_unknown: True`` and no consumer may read it as "this
        platform needs none" (#1693).
        """
        system = self._resolve_system(platform_slug)
        fw_slugs = firmware_paths.resolve_firmware_slugs(platform_slug)
        options = self._core_info.get_emulator_options(system)
        pick = self._resolve_launching_emulator(platform_slug, options, launching_emulator)
        identity = pick.emulator if pick is not None else None

        try:
            firmware_list = await self._loop.run_in_executor(None, self._listing.get_firmware_list)
        except Exception as e:
            self._logger.warning(f"Answering BIOS status without the server listing: {e}")
            firmware_list = []

        server_rows = [
            {"file_name": fw.get("file_name", ""), "on_server": True}
            for fw in firmware_list
            if firmware_paths.parse_firmware_slug(fw.get("file_path", "")) in fw_slugs
        ]
        catalogue, rows = await self._loop.run_in_executor(
            None, self._platform_demand, system, server_rows, {fw.get("file_name", "") for fw in firmware_list}
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
        placements = catalogue.by_file_name()
        complete = catalogue.reading_complete_for(identity)
        files = collect_firmware_status(
            items, placements, complete, identity, catalogue.emulators_needing_one_of_their_files()
        )
        system_image = classify_system_image(catalogue.verdict_for(identity), files, identity)

        if not files:
            settled = complete and system_image == SYSTEM_IMAGE_NOT_DEMANDED
            return {"needs_bios": False} if settled else {"needs_bios": False, "bios_status_unknown": True}

        return self._bios_payload(files, platform_slug, complete, system_image, pick)


def _has_something_to_say(plat: dict[str, Any]) -> bool:
    """Does a seeded platform have an entry to show?

    A seeded platform is one the listing never named — it is here because the
    user syncs games for it, not because anything is known to be wanted. With a
    file to show, it speaks for itself. With none, it answers only when its
    verdict is ``unknown``: answering with nothing is itself a claim, read by
    anyone looking for the platform as "nothing to manage here", and that is the
    false negative a platform whose launching emulator cannot be asked must never
    give. A platform whose reading finished and found nothing wanted really has
    nothing to manage, and answers with nothing.
    """
    return bool(plat["files"]) or plat["bios_level"] == BIOS_LEVEL_UNKNOWN


def _overview_row(item: dict[str, Any]) -> dict[str, Any]:
    """The overview row for a file the library does not hold.

    ``on_server: False`` is the load-bearing field: every download affordance
    filters on it (``src/components/library/PlatformDetail.tsx``), and so do the
    platform detail's own progress totals. ``id``
    is ``None`` as an honest absence — there is no server record to name — and
    no consumer reads it, so filling it in with a placeholder would withhold
    nothing but would make a row that cannot be fetched look fetchable to the
    next reader.
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
        "system_image_candidate": entry.system_image_candidate,
        "supplied_by": entry.supplied_by,
        "satisfied": entry.satisfied,
        "declared_kind": entry.declared_kind,
        "declaration": entry.declaration,
        "caveats": entry.caveats,
        "images": entry.images,
        "checked": entry.checked,
    }
