"""CoreService — RetroArch core selection and overrides per platform/ROM.

Owns Tender's two core-selection deviations: the per-platform core (the
``settings.json`` ``platform_cores`` map) and the per-game emulator override (the
``roms.emulator_override`` pin). Enumerating the cores available for a platform —
by slug, or for a ROM with its per-game pin layered on top — toggling the
per-platform default and pinning/clearing a per-game core all live here.

Neither selection is written to the retired ES-DE gamelist: the per-platform core
lands in ``settings.json`` via the injected ``SettingsPersister`` and the per-game
pin lands on the ``Rom`` aggregate via the Unit-of-Work. Neither touches a Steam
shortcut: the shortcut carries its appId, and the start asks the shared
``ActiveCoreReader`` for the entry in effect, so a changed selection takes effect
at the next start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_choice import (
    NO_ENTRY,
    PLATFORM_PICK,
    EmulatorChoice,
    choose_emulator,
    start_payload,
    unspoken_start,
    vanished_payload,
)
from domain.emulator_commands import (
    NOT_INSTALLED,
    default_option,
    option_for_label,
    options_to_payload,
    shipped_default,
)
from domain.emulator_pick import pick_for, platform_pick, platform_pick_value, source_kind
from domain.platform_names import PLATFORM_NAMES_KEY, platform_display_name
from domain.rom_files import asked_path
from lib.errors import Refused

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.emulator_choice import ResolvedEmulator
    from domain.platform_system import PlatformSystem
    from domain.rom import Rom
    from domain.rom_install import RomInstall
    from services.protocols import (
        ActiveCoreReader,
        ConflictRules,
        CoreInfoProvider,
        DiscResolver,
        PlatformSystems,
        SettingsPersister,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class CoreServiceConfig:
    """Frozen wiring bundle handed to ``CoreService.__init__``.

    Carries the runtime infrastructure (event loop, logger), the ES-DE
    core-info read seam, the platform's system in the answering source, the live
    ``settings`` dict + its persister (where the per-platform core lands), the
    SQLite Unit-of-Work factory (to read the ROM + its install and write the
    per-game pin), the shared per-ROM active-core resolver (the menu's active
    marker), the shared per-ROM disc resolver (so a question about the start is
    asked with the ROM's pinned disc), and the ``ConflictRules`` the three
    writes check at their entry.
    Bundled here so the ctor stays within the S107 parameter budget.
    """

    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    core_info: CoreInfoProvider
    platform_systems: PlatformSystems
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    uow_factory: UnitOfWorkFactory
    active_core: ActiveCoreReader
    disc_resolver: DiscResolver
    conflict_rules: ConflictRules


class CoreService:
    """RetroArch core override reads and writes — per-platform (settings) + per-game (DB).

    Each write checks its endpoint's conflict rules at its entry, under that
    endpoint's name, and raises the rule's refusal when one holds
    (GLOSSARY.md → Conflict rules).
    """

    def __init__(self, *, config: CoreServiceConfig) -> None:
        self._loop = config.loop
        self._logger = config.logger
        self._core_info = config.core_info
        self._platform_systems = config.platform_systems
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._uow_factory = config.uow_factory
        self._active_core = config.active_core
        self._disc_resolver = config.disc_resolver
        self._rules = config.conflict_rules

    async def get_platform_core_info(self, rom_id: int) -> dict[str, Any]:
        """Return the emulators available for ``rom_id``'s platform + the active one.

        The emulator list is platform-wide (system-level): every ES-DE
        ``<command>`` for the ROM's system, each annotated ``{label, kind,
        core_so, emulator, is_default, bakeable, reason}`` (libretro AND
        standalone). The active selection is the per-ROM resolution from
        :class:`ActiveCoreResolver`, so a pinned ``emulator_override`` (or
        per-platform core) surfaces over the system default and the menu can
        highlight the active emulator (or offer Reset).

        ``active_core`` and ``active_core_label`` are two projections of that ONE
        resolution, and ``active_core`` is the emulator IDENTITY — the same
        spelling the ``emulator`` field of each picker entry carries and the same
        one a firmware row's per-emulator map is keyed on, so the BIOS pane can
        match its answer against the pick it was scoped to. ``active_core_for_rom``
        cannot serve it: its ``core_so`` is ``None`` for every standalone
        emulator and omits the extension for a libretro one, which is two
        identifier spaces on one payload. ``platform_core_label``
        carries the per-platform override label (``settings.json``
        ``platform_cores``) so the menu can mark the system-level selection
        distinctly from the active emulator. ``has_game_override`` reports
        whether a per-game pin is set — the menu can't infer this from the active
        emulator alone (pinning the same emulator as the per-platform override is
        indistinguishable), so the flag drives the "follow the system" reset
        item's checkmark. ``emulator_data_available`` is ``False`` where the
        answering emulator source gives no emulator list, so the menu can say so
        instead of showing an empty list; ``emulator_data_reason`` says why and
        ``emulator_source`` names the answering source
        (:meth:`adapters.atlas_catalogue.AtlasCatalogueAdapter.get_emulator_options`);
        where the platform has no switched-on system there, the reason is
        ``no_platform_system`` or ``platform_system_off``. ``platform_system`` is
        :meth:`domain.platform_system.PlatformSystem.payload` — the install
        record's system for an installed ROM, which no source was asked for.
        When ``rom_id`` is unknown the emulator list is empty and the active
        emulator is ``(None, None)``.

        The list is asked with the installed game's path, so the entry the user
        chose for this game inside ES-DE is marked ``chosen_in_frontend``.
        ``active_core`` and ``active_core_label`` name the entry the game starts
        with, whether or not Tender can bake it. ``start`` is what starting with
        it comes to (:func:`domain.emulator_choice.start_payload`), and
        ``vanished_pick`` the stored pick whose label no longer exists, ``None``
        where none was passed over (:func:`domain.emulator_choice.vanished_payload`).
        """
        return await self._loop.run_in_executor(None, self._platform_core_info_io, rom_id)

    def _platform_core_info_io(self, rom_id: int) -> dict[str, Any]:
        rom, install = self._read_rom_and_install(rom_id)
        if rom is None:
            return {
                "emulators": [],
                "emulator_data_available": True,
                "emulator_data_reason": None,
                "emulator_source": None,
                "platform_system": None,
                "active_core": None,
                "active_core_label": None,
                "platform_core_label": None,
                "has_game_override": False,
                "start": start_payload(_NO_CHOICE, []),
                "launch": None,
                "vanished_pick": None,
            }
        platform = self._platform_systems.rom_system(rom.platform_slug, install)
        options = self._emulator_options(platform, content_path=install.file_path if install is not None else None)
        resolved = self._active_core.resolve_rom(rom_id)
        choice = resolved.choice if resolved is not None else _NO_CHOICE
        option = choice.option
        stored = pick_for(
            platform_pick(self._settings.get("platform_cores", {}).get(rom.platform_slug)), source_kind(options)
        )
        return {
            "emulators": options_to_payload(options["options"]),
            "emulator_data_available": options["available"],
            "emulator_data_reason": options["reason"],
            "emulator_source": options["source"],
            "platform_system": platform.payload(),
            "active_core": option.emulator if option is not None else None,
            "active_core_label": option.label if option is not None else None,
            "platform_core_label": stored.label if stored is not None else None,
            "has_game_override": rom.emulator_override is not None,
            "start": start_payload(choice, resolved.options if resolved is not None else []),
            "launch": self._chosen_launch(rom, install, resolved),
            "vanished_pick": vanished_payload(choice),
        }

    def _chosen_launch(
        self, rom: Rom, install: RomInstall | None, resolved: ResolvedEmulator | None
    ) -> dict[str, Any] | None:
        """Whether the installed game's file starts with the entry it starts with, or ``None`` where nothing is asked.

        Nothing is asked for a game that is not installed, has no system to
        ask, or has no entry chosen — no default and no pick (#2195 D7): its
        start is refused for that reason already.
        """
        if install is None or resolved is None or resolved.choice.option is None:
            return None
        path = _asked_path(install, rom, self._disc_resolver)
        verdict = self._core_info.launchability(install.system, path, label=resolved.choice.option.label)
        return verdict.payload(platform_name=self._platform_name(rom.platform_slug))

    async def get_emulator_launches(self, rom_id: int) -> list[dict[str, Any]]:
        """Whether each emulator entry of ``rom_id``'s system starts the game's installed file.

        One verdict per entry, in the order the picker lists them, each asked
        with that entry's label (:meth:`~services.protocols.CoreInfoProvider.launchability`)
        — those not established included — so the emulator menu shows which
        entries can start the game and why the others cannot (#2195 D22, D29).
        Empty for a ROM that is not installed or has no system to ask.
        Asked when the menu is opened, for one game: a verdict may read its
        entry's core, which the first time costs a probe (#2195 D31).
        """
        return await self._loop.run_in_executor(None, self._emulator_launches_io, rom_id)

    def _emulator_launches_io(self, rom_id: int) -> list[dict[str, Any]]:
        question = self._launch_question(rom_id)
        if question is None:
            return []
        system, path, resolved, platform_name = question
        reading = self._core_info.sources_reading()
        return [
            self._core_info.launchability(system, path, label=option.label, reading=reading).payload(
                platform_name=platform_name
            )
            for option in resolved.options
        ]

    def _launch_question(self, rom_id: int) -> tuple[str, str, ResolvedEmulator, str] | None:
        """What ``rom_id``'s question is asked with, and the platform name its answers carry.

        The system, the path (:func:`domain.rom_files.asked_path`), the emulator resolution and
        the platform's name.

        ``None`` for a ROM that is not installed or has no system to ask.
        """
        rom, install = self._read_rom_and_install(rom_id)
        if rom is None or install is None:
            return None
        resolved = self._active_core.resolve_rom(rom_id)
        if resolved is None:
            return None
        return (
            install.system,
            _asked_path(install, rom, self._disc_resolver),
            resolved,
            self._platform_name(rom.platform_slug),
        )

    async def get_system_core_info(self, platform_slug: str) -> dict[str, Any]:
        """Return the emulators available for *platform_slug* + the active one.

        The read half of :meth:`set_system_core`, keyed by platform rather than
        by ROM: the Library page's Platforms detail asks it once per selected
        platform. ``emulators`` is the full classified picker payload for the
        platform's ES-DE system, ``emulator_data_available`` is ``False`` where
        the answering emulator source gives no emulator list (with
        ``emulator_data_reason`` and ``emulator_source`` as in
        :meth:`get_platform_core_info`), ``platform_system`` the system the
        platform is in the answering source, and
        ``active_core_label`` names the per-platform pick while its label still
        names an entry, else Tender's default: the default of the first enabled
        source in the user's order that offers one (#2195 D4); ``None`` where
        nothing is picked and Tender's default names no entry — no source offers
        one, or the one it offers could not be established installed. ``start``
        and ``vanished_pick`` are as in :meth:`get_platform_core_info`, for the
        platform's own choice in the answering source. ``source_defaults`` is every enabled source in the
        user's order with the entry it ships as default for the platform, the
        one the user chose in it inside ES-DE where that differs, and why there
        is no default where there is none; ``tender_default`` names the source
        and entry Tender's default is, ``None`` where no source offers one;
        ``default_elsewhere`` is whether that default, with nothing picked,
        comes from a source games do not start through (#2195 D24).

        Distinct from :meth:`get_platform_core_info`, which answers the same
        question for one ROM and layers that ROM's ``emulator_override`` on top;
        a platform-keyed caller has no ROM to layer and must be answerable for a
        platform holding no synced ROM at all.
        """
        return await self._loop.run_in_executor(None, self._system_core_info_io, platform_slug)

    def _system_core_info_io(self, platform_slug: str) -> dict[str, Any]:
        platform = self._platform_systems.platform_system(platform_slug)
        options = self._emulator_options(platform)
        stored = pick_for(
            platform_pick(self._settings.get("platform_cores", {}).get(platform_slug)), source_kind(options)
        )
        choice = choose_emulator(options["options"], game=None, platform=stored)
        source_defaults = self._source_defaults(platform_slug, answered=(platform, options))
        tender_default = next(
            ({"kind": entry["kind"], "label": entry["default"]} for entry in source_defaults if entry["offers"]),
            None,
        )
        picked = choice.layer == PLATFORM_PICK and choice.option is not None
        if picked and choice.option is not None:
            active_label: str | None = choice.option.label
        elif tender_default is not None:
            active_label = tender_default["label"]
        else:
            active_label = None
        # Tender's default from a source games do not start through: the page
        # shows that default and none of the starting source's own lines beneath
        # it, which would contradict it (#2195 D24).
        elsewhere = not picked and tender_default is not None and tender_default["kind"] != source_kind(options)
        return {
            "emulators": options_to_payload(options["options"]),
            "emulator_data_available": options["available"],
            "emulator_data_reason": options["reason"],
            "emulator_source": options["source"],
            "platform_system": platform.payload(),
            "active_core_label": active_label,
            "start": (unspoken_start(active_label) if elsewhere else start_payload(choice, options["options"])),
            "vanished_pick": None if elsewhere else vanished_payload(choice),
            "source_defaults": [
                {key: entry[key] for key in ("kind", "default", "chosen_in_frontend", "reason")}
                for entry in source_defaults
            ],
            "tender_default": tender_default,
            "default_elsewhere": elsewhere,
        }

    def _source_defaults(
        self, platform_slug: str, *, answered: tuple[PlatformSystem, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Every enabled source in the user's order, with its own default for *platform_slug*.

        ``offers`` says the source lists an entry for the platform's system that
        it ships as default; ``default`` is that entry's label, ``None`` where
        its installation is not established (``reason`` then carries its
        note), and ``chosen_in_frontend`` the entry the user chose in that
        source inside ES-DE where it differs. A source with no list answers its
        refusal in ``reason``. *answered* is the answering source's platform
        system and list, already asked, so that source is not asked twice.
        """
        reading = self._core_info.sources_reading()
        entries: list[dict[str, Any]] = []
        for source in reading.sources:
            if not source.enabled:
                continue
            if source.kind == source_kind(answered[1]):
                answer = answered[1]
            else:
                platform = self._platform_systems.platform_system(platform_slug, source=source.kind, reading=reading)
                answer = (
                    platform.unavailable_options()
                    if platform.taken is None
                    else self._core_info.get_emulator_options(platform.taken, reading=reading, source=source.kind)
                )
            options = answer["options"]
            shipped = shipped_default(options)
            default = default_option(options)
            chosen = next((option.label for option in options if option.chosen_in_frontend), None)
            if not answer["available"]:
                reason = answer["reason"]
            elif shipped is None:
                reason = NO_ENTRY
            else:
                reason = shipped.not_established
            entries.append(
                {
                    "kind": source.kind,
                    "offers": shipped is not None,
                    "default": default.label if default is not None else None,
                    "chosen_in_frontend": chosen,
                    "reason": reason,
                }
            )
        return entries

    def _set_system_core_io(self, platform_slug: str, core_label: str) -> None:
        """Write the per-platform core selection.

        Mutates ``settings["platform_cores"]`` — stores ``{label, source}`` under
        *platform_slug* when *core_label* is non-empty, ``source`` the kind of
        the emulator source answering for the platform, and pops the slug when
        blank (revert to the default) — and persists ``settings.json`` through
        the injected persister. A label with no source answering for the
        platform is refused with ``core_unavailable`` and nothing is written.
        """
        self._store_platform_pick(platform_slug, core_label)

    def _store_platform_pick(self, platform_slug: str, core_label: str) -> None:
        """Store *core_label* as the platform's pick, or drop the pick when blank, and persist it.

        The previous value is put back where the save fails.
        """
        platform_cores = self._settings["platform_cores"]
        previous = platform_cores.get(platform_slug)
        if core_label:
            source = source_kind(self._emulator_options(self._platform_systems.platform_system(platform_slug)))
            if source is None:
                raise Refused("core_unavailable", f"No emulator source answers for {platform_slug}")
            platform_cores[platform_slug] = platform_pick_value(core_label, source)
        else:
            platform_cores.pop(platform_slug, None)
        try:
            self._settings_persister.save_settings()
        except OSError as e:
            if previous is None:
                platform_cores.pop(platform_slug, None)
            else:
                platform_cores[platform_slug] = previous
            self._logger.error(f"Failed to set system core: {e}")
            raise Refused("save_failed", f"Save failed: {e}") from e

    async def set_system_core(self, platform_slug: str, core_label: str) -> dict[str, Any]:
        """Set or clear the per-platform core selection for a platform.

        Empty ``core_label`` clears the selection (reverts to the es_systems
        default). On success the per-platform core is written to ``settings.json``
        and the answer is ``{"success": True}``; the next start of every game on
        the platform that follows it asks with the new selection. A settings
        file that cannot be written refuses with ``save_failed`` and leaves the
        previous selection in effect.
        """
        async with self._rules.hold("set_system_core", update=True, migration=True, prune=True):
            await self._loop.run_in_executor(None, self._set_system_core_io, platform_slug, core_label)
            return {"success": True}

    async def set_game_core(self, rom_id: int, label: str) -> dict[str, Any]:
        """Pin the per-game emulator override for ``rom_id`` to *label*.

        The picked LABEL is checked FIRST against the emulators the ROM's
        system lists, libretro OR standalone. A label the system does not list,
        one the resolver states not installed (#2195 D9), or one for which no
        emulator source answers is refused with ``core_unavailable`` and
        **nothing is written**; an entry the resolver gives no complete start
        command for is pinned like any other, and the game page then says Tender
        cannot start it. An unknown ROM is refused with ``not_found``. On success
        the pin is written via the Unit-of-Work and the answer is
        ``{"success": True}``; the next start asks with it.
        """
        async with self._rules.hold("set_game_core", update=True, migration=True, prune=True):
            return await self._loop.run_in_executor(None, self._set_game_core_io, rom_id, label)

    def _set_game_core_io(self, rom_id: int, label: str) -> dict[str, Any]:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None:
                raise Refused("not_found", f"ROM {rom_id} is not tracked")
            platform_slug = rom.platform_slug
            install = uow.rom_installs.get(rom_id)
        # Resolve the label between the two transactions: the platform's system
        # and the emulator-options read each ask the resolver on every call, and
        # a UoW holds SQLite's BEGIN IMMEDIATE write lock — file I/O inside one
        # stalls every other writer for its duration.
        options = self._emulator_options(self._platform_systems.rom_system(platform_slug, install))
        source = source_kind(options)
        option = option_for_label(options["options"], label)
        if option is None or option.reason == NOT_INSTALLED or source is None:
            # Refuse BEFORE any write — never persist a label the system does not
            # list, or one whose emulator the resolver states not installed.
            raise Refused("core_unavailable", f"Emulator '{label}' is not available for {platform_slug}")
        with self._uow_factory() as uow:
            # The row is re-read because the label was resolved against a
            # snapshot: a background sync, a finishing download or the
            # removed-game cleanup can retire the ROM between the two
            # transactions, each on its own connection.
            rom = uow.roms.get(rom_id)
            if rom is None:
                raise Refused("not_found", f"ROM {rom_id} is not tracked")
            if rom.platform_slug != platform_slug:
                # A re-sync rewrites platform_slug (it is a synced-identity
                # column). The label was validated against the platform the
                # snapshot named, so pinning it now would persist a label that
                # was never checked against the platform it will resolve under.
                # The message says exactly that rather than calling the label
                # unavailable: re-resolving to find out would cost the ES-DE
                # read this split exists to keep out of the transaction, and on
                # a label valid for both platforms "unavailable" is simply
                # false — the user retries and it works.
                raise Refused(
                    "core_unavailable", f"Emulator '{label}' was not verified for {rom.platform_slug}; try again"
                )
            # Enforce the aggregate invariant (strip / reject blank) via the
            # verb method, then persist the resulting label through the pin-only
            # write path (never the sync UPSERT).
            rom.pin_emulator_override(label, source=source)
            uow.roms.set_emulator_override(rom_id, rom.emulator_override, rom.emulator_override_source)
        return {"success": True}

    async def clear_game_core(self, rom_id: int) -> dict[str, Any]:
        """Clear the per-game override for ``rom_id`` (Follow default / Reset).

        Drops the pin (stores SQL NULL) so the ROM follows the per-platform/system
        default from its next start, and answers ``{"success": True}``. Clearing
        is always valid — there is no label to resolve. An unknown ROM is
        refused with ``not_found``.
        """
        async with self._rules.hold("clear_game_core", update=True, migration=True, prune=True):
            return await self._loop.run_in_executor(None, self._clear_game_core_io, rom_id)

    def _clear_game_core_io(self, rom_id: int) -> dict[str, Any]:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            if rom is None:
                raise Refused("not_found", f"ROM {rom_id} is not tracked")
            rom.clear_emulator_override()
            uow.roms.set_emulator_override(rom_id, rom.emulator_override, rom.emulator_override_source)
        return {"success": True}

    def _platform_name(self, platform_slug: str | None) -> str:
        """The name the game page shows *platform_slug* under, which a launch answer's sentences name (#2195 D36)."""
        with self._uow_factory() as uow:
            return platform_display_name(uow.kv_config.get(PLATFORM_NAMES_KEY), platform_slug)

    def _read_rom_and_install(self, rom_id: int) -> tuple[Rom | None, RomInstall | None]:
        with self._uow_factory() as uow:
            return uow.roms.get(rom_id), uow.rom_installs.get(rom_id)

    def _emulator_options(self, platform: PlatformSystem, *, content_path: str | None = None) -> dict[str, Any]:
        """The emulator options for *platform*'s system, or the unavailable answer where it has none."""
        if platform.taken is None:
            return platform.unavailable_options()
        if content_path is None:
            return self._core_info.get_emulator_options(platform.taken)
        return self._core_info.get_emulator_options(platform.taken, content_path=content_path)


def _asked_path(install: RomInstall, rom: Rom, disc_resolver: DiscResolver) -> str:
    """The path a question about the installed game's start is asked with (:func:`domain.rom_files.asked_path`)."""
    return asked_path(install.file_path, install.rom_dir, disc_resolver.resolve_for_install(install, rom.selected_disc))


# A ROM with no system to ask has no choice to make.
_NO_CHOICE = EmulatorChoice(option=None, layer=None, vanished=None)
