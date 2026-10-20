"""TenderFolderService — where Tender's folder lies, whether it can be reached, and the user's say over its place.

Owns the use cases of Settings → Tender folder and of Main's notices about
it: answer the place, its state and the drives a place may lie on; check a
typed place and choose one; go back to a folder something is recorded under;
forget what is recorded under a folder. It also creates the folder at start,
makes it ready for a write, and answers the folder rule (GLOSSARY.md →
Conflict rules).

What is recorded under a folder is each store's own answer
(``TenderFolderRecordReader``); this service only asks. The place is stored
in ``settings.json`` through its one writer, and the folder's id — the one
written into every folder Tender creates — in ``kv_config``, so a reset of
the settings does not lose it. Refusals are raised, never returned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.tender_folder import (
    FOLDER_MISSING,
    FOLDER_NAME,
    FOLDER_NOT_WRITABLE,
    PLACE_SETTING,
    drive_below,
    drive_not_mounted,
    not_writable,
    place_asks,
    place_setting,
    recorded_elsewhere,
    rom_folder_refusal,
    sources_unreadable,
    typed_place,
    unreachable_message,
    unreachable_state,
)
from lib.errors import Refused

if TYPE_CHECKING:
    import asyncio

    from domain.tender_folder import TenderPlace
    from services.protocols import (
        ConflictRules,
        DebugLogger,
        EmulatorSourcesReader,
        SettingsPersister,
        TenderFolderRecordReader,
        TenderFolderStore,
        UnitOfWorkFactory,
        UuidGen,
    )

# Where the folder's id is recorded.
FOLDER_ID_KEY = "tender_folder_id"

FOLDER_UNREACHABLE = "folder_unreachable"


@dataclass(frozen=True)
class TenderFolderServiceConfig:
    """Frozen wiring bundle handed to ``TenderFolderService.__init__``.

    Carries the folder seam, the emulator sources a place is judged against,
    every store that records something under the folder, the live settings
    dict and its persister, the unit of work the folder's id is kept through
    and the id source, the conflict rules a choice holds, the event loop the
    I/O is run off, and the debug logger.
    """

    store: TenderFolderStore
    sources: EmulatorSourcesReader
    records: tuple[TenderFolderRecordReader, ...]
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    uow_factory: UnitOfWorkFactory
    uuid_gen: UuidGen
    conflict_rules: ConflictRules
    loop: asyncio.AbstractEventLoop
    log_debug: DebugLogger


class TenderFolderService:
    """Answers where Tender's folder lies and whether it can be reached, and stores the user's choice of place."""

    def __init__(self, *, config: TenderFolderServiceConfig) -> None:
        self._store = config.store
        self._sources = config.sources
        self._records = config.records
        self._settings = config.settings
        self._settings_persister = config.settings_persister
        self._uow_factory = config.uow_factory
        self._uuid_gen = config.uuid_gen
        self._conflict_rules = config.conflict_rules
        self._loop = config.loop
        self._log_debug = config.log_debug

    async def get_tender_folder(self) -> dict[str, Any]:
        """The folder's place and state, the folders recorded elsewhere, and the drives a place may lie on.

        ``{"place", "folder", "default", "condition", "unreachable",
        "elsewhere", "drives"}``: ``condition`` is why the folder cannot be
        used now, or ``None``; ``unreachable`` is that condition only while
        something is recorded under the folder; ``elsewhere`` the other
        folders something is recorded under; ``drives`` each writable drive
        as ``{"name", "mount_point", "folder", "free_bytes"}``.
        """
        return await self._loop.run_in_executor(None, self._state_io)

    async def check_tender_place(self, path: object) -> dict[str, Any]:
        """What to ask before the typed *path* is chosen, or why it may not be.

        ``{"place", "folder", "tender_parent", "existing", "missing"}``
        (:class:`domain.tender_folder.PlaceAsks`). Refuses
        ``place_not_absolute``, ``place_drive_not_mounted``,
        ``sources_unreadable``, ``place_in_rom_folder`` and
        ``place_above_rom_folder``.
        """
        text = _typed(path)
        return await self._loop.run_in_executor(None, self._check_io, text)

    async def choose_tender_place(self, path: object) -> dict[str, Any]:
        """Make *path* the folder's place — ``None`` for the default — and answer the state as it now stands.

        Creates the place's missing folders and the folder inside it, and
        refuses as :meth:`check_tender_place` does, and ``place_not_writable``
        where they cannot be created or written. Switches the setting and
        nothing else; an old folder that holds nothing but its id file is
        removed. Holds off a removed-game cleanup, which seals into the folder.
        """
        text = None if path is None else _typed(path)
        async with self._conflict_rules.hold("choose_tender_place", prune=True):
            return await self._loop.run_in_executor(None, self._choose_io, text)

    async def use_recorded_tender_folder(self, folder: object) -> dict[str, Any]:
        """Go back to *folder*, which something is recorded under, as the folder's place; copy nothing.

        Refuses ``invalid_value`` for a folder nothing is recorded under
        besides the current one. Holds off a removed-game cleanup, as a choice
        does.
        """
        text = _typed(folder)
        async with self._conflict_rules.hold("use_recorded_tender_folder", prune=True):
            return await self._loop.run_in_executor(None, self._use_recorded_io, text)

    async def forget_tender_folder_records(self, folder: object) -> dict[str, Any]:
        """Have every store forget what it recorded under *folder*, and answer the state as it now stands.

        No file is touched. Refuses ``invalid_value`` for a folder nothing is
        recorded under.
        """
        text = _typed(folder)
        return await self._loop.run_in_executor(None, self._forget_io, text)

    def create_at_start(self) -> None:
        """Create the folder where it is missing, nothing is recorded under it and its drive is mounted."""
        place = self._store.place()
        if self._store.condition(place) != FOLDER_MISSING or self._recorded_under(place.folder):
            return
        created = self._store.create_folder(place.place, place.folder, self._folder_id_io())
        self._log_debug(f"[folder] created {place.folder} at start: {created}")

    def do_prepare_for_write(self) -> str:
        """The folder, ready for one write: created where it is missing and nothing is recorded under it.

        Refuses ``folder_unreachable`` where its drive is gone, it cannot be
        written, or it is missing while something is recorded under it.
        """
        place = self._store.place()
        condition = self._store.condition(place)
        if condition is None:
            return place.folder
        if condition == FOLDER_MISSING and not self._recorded_under(place.folder):
            try:
                self._store.create_folder(place.place, place.folder, self._folder_id_io())
            except OSError as exc:
                self._log_debug(f"[folder] could not create {place.folder}: {exc!r}")
                condition = FOLDER_NOT_WRITABLE
            else:
                condition = self._store.condition(place)
            if condition is None:
                return place.folder
        raise _unreachable(place.folder, condition)

    def unreachable_refusal(self) -> Refused | None:
        """The folder rule's refusal while the folder is unreachable, or ``None``.

        Asked on the event loop: the stores are asked first, and the folder is
        looked at only where something is recorded under it.
        """
        place = self._store.place()
        if not self._recorded_under(place.folder):
            return None
        state = self._store.condition(place)
        return None if state is None else _unreachable(place.folder, state)

    def _state_io(self) -> dict[str, Any]:
        place = self._store.place()
        recorded = self._recorded()
        folder = self._store.resolved(place.folder)
        condition = self._store.condition(place)
        return {
            "place": place.place,
            "folder": place.folder,
            "default": place.default,
            "condition": condition,
            "unreachable": unreachable_state(folder, recorded, condition),
            "elsewhere": [recorded[resolved] for resolved in recorded_elsewhere(folder, recorded)],
            "drives": [
                {
                    "name": drive.name,
                    "mount_point": drive.mount_point,
                    "folder": os.path.join(drive.mount_point, FOLDER_NAME),
                    "free_bytes": drive.free_bytes,
                }
                for drive in self._store.drives()
            ],
        }

    def _check_io(self, text: str) -> dict[str, Any]:
        place = typed_place(text, self._store.home())
        self._judge(place)
        asks = place_asks(place, self._store.deepest_existing(place))
        return {
            "place": asks.place,
            "folder": os.path.join(asks.place, FOLDER_NAME),
            "tender_parent": asks.tender_parent,
            "existing": asks.existing,
            "missing": asks.missing,
        }

    def _choose_io(self, text: str | None) -> dict[str, Any]:
        home = self._store.home()
        place = home if text is None else typed_place(text, home)
        if text is not None:
            self._judge(place)
        folder = os.path.join(place, FOLDER_NAME)
        try:
            self._store.create_folder(place, folder, self._folder_id_io())
        except OSError as exc:
            self._log_debug(f"[folder] could not create {folder}: {exc!r}")
            raise not_writable(place) from exc
        if not self._store.writable(folder):
            raise not_writable(place)
        self._switch(place, self._store.mount_point_of(place))
        return self._state_io()

    def _use_recorded_io(self, text: str) -> dict[str, Any]:
        recorded = self._recorded()
        current = self._store.resolved(self._store.place().folder)
        target = self._store.resolved(text)
        if target not in recorded_elsewhere(current, recorded):
            raise Refused("invalid_value", f"Nothing is recorded under {text}.")
        place = os.path.dirname(os.path.normpath(text))
        drive = drive_below(place, self._store.drives_root())
        mount_point = (
            os.path.join(self._store.drives_root(), drive) if drive is not None else self._store.mount_point_of(place)
        )
        self._switch(place, mount_point)
        return self._state_io()

    def _forget_io(self, text: str) -> dict[str, Any]:
        if self._store.resolved(text) not in self._recorded():
            raise Refused("invalid_value", f"Nothing is recorded under {text}.")
        for records in self._records:
            records.forget_records_under(text)
        self._log_debug(f"[folder] forgot what is recorded under {text}")
        return self._state_io()

    def _judge(self, place: str) -> None:
        """Refuse *place* where its drive is not connected or it lies in or above a ROM folder."""
        drive = drive_below(place, self._store.drives_root())
        if drive is not None and not self._store.is_mount(os.path.join(self._store.drives_root(), drive)):
            raise drive_not_mounted(drive)
        folders = self._sources.rom_folders()
        if folders is None:
            raise sources_unreadable()
        refusal = rom_folder_refusal(self._store.resolved(os.path.join(place, FOLDER_NAME)), folders)
        if refusal is not None:
            raise refusal

    def _switch(self, place: str, mount_point: str) -> None:
        old: TenderPlace = self._store.place()
        self._settings[PLACE_SETTING] = place_setting(place, mount_point, self._store.home())
        self._settings_persister.save_settings()
        new = self._store.place()
        self._log_debug(f"[folder] place {old.place} -> {new.place} (mount {new.mount_point})")
        if self._store.resolved(old.folder) != self._store.resolved(new.folder):
            self._store.remove_if_only_id(old.folder)

    def _recorded(self) -> dict[str, str]:
        """Every folder something is recorded under, resolved, mapped to the path a store gave for it."""
        recorded: dict[str, str] = {}
        for records in self._records:
            for folder in records.recorded_folders():
                recorded.setdefault(self._store.resolved(folder), folder)
        return recorded

    def _recorded_under(self, folder: str) -> bool:
        if not self._records:
            return False
        return self._store.resolved(folder) in self._recorded()

    def _folder_id_io(self) -> str:
        """The folder's id, made once and kept."""
        with self._uow_factory() as uow:
            folder_id = uow.kv_config.get(FOLDER_ID_KEY)
            if folder_id is None:
                folder_id = self._uuid_gen.uuid4()
                uow.kv_config.set(FOLDER_ID_KEY, folder_id)
            return folder_id


def _typed(value: object) -> str:
    """*value* off the untrusted wire as a path string; refuses ``invalid_value`` for anything else."""
    if not isinstance(value, str):
        raise Refused("invalid_value", f"A folder is a path, not {value!r}.")
    return value


def _unreachable(folder: str, state: str) -> Refused:
    return Refused(FOLDER_UNREACHABLE, unreachable_message(folder, state), folder=folder, state=state)
