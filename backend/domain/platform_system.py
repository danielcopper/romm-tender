"""Which system a RomM platform is in one emulator source, read from the resolver's answer.

RomM names a platform by its own slug and by its folder in RomM (``fs_slug``),
neither of which is a system a source declares. The resolver is asked with
both, as (source, key) pairs (``adapters/atlas_platforms.py``), and answers in
the source's own systems; this module holds what that answer comes to — one
system, several, a switched-off one, one this machine does not have, none, a
platform the resolver cannot match, or a slug and a folder that name
different systems — and why there is none to use. Tender
picks no system among several and translates none: a later question names the
system as the answer named it.

What is kept of each platform RomM lists lives offline beside the platform
names (``kv_config``), under the platform's folder in RomM (``fs_slug``);
decoding and encoding that value is here, reading and writing it is the
caller's.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from domain.emulator_sources import ALL_SOURCES_SWITCHED_OFF, NO_SOURCE_DETECTED, ArrangedSource
from domain.platform_listing import listed_text, platform_entry_of
from domain.retrodeck_folders import FolderRefused, not_installed, switched_off, unanswered_refusal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Where the listed platforms are kept in ``kv_config``, beside ``platform_names``.
PLATFORM_IDS_KEY = "platform_ids"


class SystemPair(NamedTuple):
    """A system as the resolver names it: the source whose key spells it, and that key as the source spells it."""

    source: str
    key: str


# What a question about a game names its system by: the pair the platform's
# answer named, the name an install record holds — a bare key, which the
# resolver reads in the answering source's own catalogue — or, where the answer
# named several systems, the pairs the platform was asked with, which the
# resolver answers for alike or names no system for.
SystemArgument = str | SystemPair | tuple[SystemPair, ...]


def system_key(system: SystemArgument) -> str | None:
    """The key *system* names its one system by, for a record or a sentence; ``None`` for several pairs.

    The one place that tells the three forms apart: a ``SystemPair`` is itself
    a tuple, so it is tested before the tuple of several pairs.
    """
    if isinstance(system, SystemPair):
        return system.key
    return None if isinstance(system, tuple) else system


def system_keys(system: SystemArgument) -> tuple[str, ...]:
    """Every key *system* names, each once: its one key, or the key of each of several pairs."""
    key = system_key(system)
    if key is not None:
        return (key,)
    return tuple(dict.fromkeys(pair.key for pair in system if isinstance(pair, SystemPair)))


class SystemStatus(StrEnum):
    """How firmly a system is in the source: switched on, switched off, or not there at all."""

    ON = "on"
    OFF = "off"
    NOT_HERE = "not_here"


PlatformState = Literal[
    "found", "several", "switched_off", "not_here", "no_system", "unmatched", "contradicted", "unasked"
]

FOUND: PlatformState = "found"
# Several switched-on systems answer the platform; Tender picks none.
SEVERAL: PlatformState = "several"
SWITCHED_OFF: PlatformState = "switched_off"
# The source's frontend has a system for the platform, and this machine has not set it up.
NOT_HERE: PlatformState = "not_here"
# The resolver knows the platform, and the source's frontend has no system for it.
NO_SYSTEM: PlatformState = "no_system"
# The resolver cannot match the platform to a system: it knows neither pair.
UNMATCHED: PlatformState = "unmatched"
# RomM's slug and the platform's folder name different systems, so the resolver names none (#2751 D2).
CONTRADICTED: PlatformState = "contradicted"
# No source was asked: none answers, the one named is not detected, or the
# question raised. ``PlatformSystem.unasked`` says why.
UNASKED: PlatformState = "unasked"


class PlatformRefusal(StrEnum):
    """Why a platform has no system to use, as its refusal and its emulator list name it (#2751 D2, #2225 D8)."""

    NO_SYSTEM = "no_platform_system"
    SWITCHED_OFF = "platform_system_off"
    NOT_HERE = "platform_system_not_here"
    UNMATCHED = "platform_unmatched"
    CONTRADICTED = "platform_pairs_contradict"
    SEVERAL = "platform_systems_several"


@dataclass(frozen=True, slots=True)
class _NoSystemToUse:
    """What a state with no system to use is refused with."""

    platform_refusal: PlatformRefusal


_CASE_OF_STATE = {
    NO_SYSTEM: _NoSystemToUse(PlatformRefusal.NO_SYSTEM),
    SWITCHED_OFF: _NoSystemToUse(PlatformRefusal.SWITCHED_OFF),
    NOT_HERE: _NoSystemToUse(PlatformRefusal.NOT_HERE),
    UNMATCHED: _NoSystemToUse(PlatformRefusal.UNMATCHED),
    CONTRADICTED: _NoSystemToUse(PlatformRefusal.CONTRADICTED),
    SEVERAL: _NoSystemToUse(PlatformRefusal.SEVERAL),
}


@dataclass(frozen=True, slots=True)
class PlatformKeys:
    """What a platform is asked about by: RomM's slug for it, and its folder in RomM where one is known.

    Which folder that is: ``docs/architecture/core-emulator-selection.md``, "A platform's system".
    """

    slug: str
    folder: str | None = None

    @property
    def kept_as(self) -> str:
        """What the platform is kept and named under: its folder, else its slug."""
        return self.folder or self.slug

    @classmethod
    def of(cls, slug: str | None, folder: str | None) -> PlatformKeys:
        """The keys of a platform by *slug* and *folder*, either as a row or RomM gives it.

        An empty folder is no folder; a missing slug is the empty slug, which no platform has.
        """
        return cls(slug or "", folder or None)

    @classmethod
    def of_detail(cls, rom_detail: Mapping[str, Any]) -> PlatformKeys:
        """The keys of the platform RomM's detail of a game puts it in."""
        return cls.of(rom_detail.get("platform_slug"), rom_detail.get("platform_fs_slug"))


@dataclass(frozen=True, slots=True)
class KeptPlatform:
    """What is kept of one platform RomM lists, under its folder in RomM (``fs_slug``).

    ``ids`` holds every field of RomM's platform whose name ends in ``_id`` or
    ``_slug``, each as RomM sent it; Tender reads none of them. ``romm_version``
    is the RomM version known when the listing was read, ``None`` where none
    was.
    """

    slug: str | None = None
    fs_slug: str | None = None
    platform_id: int | None = None
    name: str | None = None
    is_identified: bool | None = None
    romm_version: str | None = None
    ids: dict[str, Any] = field(default_factory=dict)


def kept_platform_of(platform: Mapping[str, Any], romm_version: str | None) -> KeptPlatform:
    """What is kept of one RomM platform read with *romm_version*; a named field of the wrong kind counts as none."""
    entry = platform_entry_of(platform)
    return KeptPlatform(
        slug=entry.slug,
        fs_slug=entry.fs_slug,
        platform_id=entry.platform_id,
        name=entry.name,
        is_identified=entry.is_identified,
        romm_version=romm_version,
        ids=dict(entry.ids),
    )


def kept_platforms(platforms: Iterable[Mapping[str, Any]], romm_version: str | None) -> dict[str, KeptPlatform]:
    """Every listed platform kept, keyed by its folder in RomM; a platform without one is left out."""
    return {
        folder: entry
        for entry in (kept_platform_of(p, romm_version) for p in platforms)
        if (folder := entry.fs_slug) is not None
    }


def kept_by_slug(kept: Mapping[str, KeptPlatform], slug: str) -> KeptPlatform | None:
    """The kept platform whose slug is *slug* — of several, the last kept — or ``None``.

    Only a platform asked about by its slug alone is found this way, and only
    its name is read off it (:func:`kept_entry`).
    """
    return next((entry for entry in reversed(list(kept.values())) if entry.slug == slug), None)


def kept_entry(kept: Mapping[str, KeptPlatform], keys: PlatformKeys) -> KeptPlatform | None:
    """What is kept for the platform *keys* names: the record of its folder, or — without one — of its slug."""
    if keys.folder is not None:
        return kept.get(keys.folder)
    return kept_by_slug(kept, keys.slug)


def kept_folders_of_slug(raw: str | None, slug: str) -> frozenset[str]:
    """The ``fs_slug`` of every platform of *slug* kept under :data:`PLATFORM_IDS_KEY` in *raw*."""
    kept = decode_platform_ids(raw) or {}
    return frozenset(entry.fs_slug for entry in kept.values() if entry.slug == slug and entry.fs_slug)


def encode_platform_ids(kept: Mapping[str, KeptPlatform]) -> str:
    """The value kept under :data:`PLATFORM_IDS_KEY`."""
    return json.dumps(
        {
            key: {
                "slug": entry.slug,
                "fs_slug": entry.fs_slug,
                "id": entry.platform_id,
                "name": entry.name,
                "is_identified": entry.is_identified,
                "romm_version": entry.romm_version,
                "ids": entry.ids,
            }
            for key, entry in kept.items()
        }
    )


def decode_platform_ids(raw: str | None) -> dict[str, KeptPlatform] | None:
    """The kept platforms by key, or ``None`` where none are kept — absent, empty, or not a JSON object.

    An entry without an ``ids`` object was kept before the record held more
    than a platform's ids and name, keyed by its slug, and is read as such.
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return {key: _decode_entry(key, entry) for key, entry in decoded.items() if isinstance(entry, dict)}


def _decode_entry(key: str, entry: Mapping[str, Any]) -> KeptPlatform:
    ids = entry.get("ids")
    if not isinstance(ids, dict):
        return KeptPlatform(
            slug=listed_text(key),
            name=listed_text(entry.get("name")),
            ids=dict(platform_entry_of(entry).ids),
        )
    kept = kept_platform_of(entry, listed_text(entry.get("romm_version")))
    return KeptPlatform(
        slug=kept.slug,
        fs_slug=kept.fs_slug,
        platform_id=kept.platform_id,
        name=kept.name,
        is_identified=kept.is_identified,
        romm_version=kept.romm_version,
        ids=ids,
    )


def matched_state(
    matches: Iterable[tuple[SystemPair, SystemStatus]],
) -> tuple[PlatformState, tuple[SystemPair, ...]]:
    """What the systems the resolver matched to a platform come to, and the systems that state names.

    One switched-on system is :data:`FOUND`; several are :data:`SEVERAL`, and
    none of them is picked. With none switched on, the switched-off ones are
    :data:`SWITCHED_OFF`, else the ones this machine has not set up
    :data:`NOT_HERE`; with no system matched at all, :data:`NO_SYSTEM`.
    """
    matched = tuple(matches)
    on = tuple(system for system, status in matched if status == SystemStatus.ON)
    if on:
        return (FOUND if len(on) == 1 else SEVERAL), on
    fallbacks: tuple[tuple[SystemStatus, PlatformState], ...] = (
        (SystemStatus.OFF, SWITCHED_OFF),
        (SystemStatus.NOT_HERE, NOT_HERE),
    )
    for status, state in fallbacks:
        named = tuple(system for system, each in matched if each == status)
        if named:
            return state, named
    return NO_SYSTEM, ()


@dataclass(frozen=True, slots=True)
class PlatformSystem:
    """The system one platform is in one source, or why there is none to use.

    ``keys`` is what the platform was asked about by: RomM's slug and the
    folder in RomM, ``None`` where none was given. ``named`` is every system the
    answer named for its state — the one found, the several switched on, the
    switched-off ones, or the ones this machine has not set up — and ``asked``
    the pairs the platform was asked with. ``recorded`` is the name an install
    record holds, which no source was asked for. ``source`` is the source
    asked — or, where RomM's listing could not be read, the source that would
    have been — and ``None`` where there is none, and for a recorded system.
    ``unasked`` says why none was asked, in the vocabulary of
    :mod:`domain.emulator_sources` — or, where RomM's listing could not be read,
    the reason that read failed with, and ``unasked_message`` its message.
    """

    state: PlatformState
    keys: PlatformKeys
    platform_name: str
    source: ArrangedSource | None = None
    named: tuple[SystemPair, ...] = ()
    asked: tuple[SystemPair, ...] = ()
    recorded: str | None = None
    unasked: str | None = None
    unasked_message: str | None = None

    @property
    def one_system(self) -> str | SystemPair | None:
        """The one system a download lands in and a frontend link goes to; ``None`` where there is not one."""
        if self.state != FOUND:
            return None
        if self.recorded is not None:
            return self.recorded
        return self.named[0] if self.named else None

    @property
    def taken(self) -> SystemArgument | None:
        """What a question about the platform's games names its system by; ``None`` where there is none.

        The one system found, or — where several answer and Tender picks none
        (#2225 D8) — the pairs the platform was asked with, which the resolver
        answers for those systems alike or for none.
        """
        if self.state == SEVERAL and self.asked:
            return self.asked
        return self.one_system

    @property
    def no_system_reason(self) -> str | None:
        """Why the platform has no one system to use in the source asked; ``None`` where it has one, or none was asked.

        Several systems are such a reason too: what lands in one system's folder
        lands in none of them.
        """
        case = _CASE_OF_STATE.get(self.state)
        return None if case is None else case.platform_refusal.value

    def wanting_systems(self) -> tuple[str | SystemPair, ...]:
        """Every system whose emulators may want what the platform's files are linked to, each asked on its own.

        The systems the answer named — the one found, each of several, the
        switched-off ones, the ones not set up here — or the system an install
        record holds; none where the platform has no system.
        """
        if self.named:
            return self.named
        return () if self.recorded is None else (self.recorded,)

    def download_system(self, purpose: str) -> tuple[str | SystemPair, str] | FolderRefused:
        """The system a *purpose* download lands under — as its folder is asked for, and as its install records it.

        A download lands in one system's folder, so anything but one system
        found is the platform's refusal.
        """
        system = self.one_system
        key = None if system is None else system_key(system)
        if system is None or key is None:
            return self.refusal(purpose)
        return system, key

    def shown(self) -> tuple[str, ...]:
        """The keys the sentences name the platform's systems by."""
        if self.named:
            return tuple(system.key for system in self.named)
        return () if self.recorded is None else (self.recorded,)

    def refusal(self, purpose: str) -> FolderRefused:
        """Why a *purpose* download, or anything refused like one, may not go ahead for this platform.

        The panel words the refusals of its own from their details; their
        messages stand where it does not.
        """
        case = _CASE_OF_STATE.get(self.state)
        if case is not None:
            kind = self.source.kind if self.source is not None else None
            shown = self.shown()
            details = {
                "source": kind,
                "platform": self.platform_name,
                "systems": list(shown),
                "slug": self.keys.slug,
                "folder": self.keys.folder,
            }
            return FolderRefused(case.platform_refusal.value, _refusal_message(self, kind, shown), **details)
        if self.state != UNASKED:
            raise ValueError(f"a platform with system {self.one_system!r} is not refused")
        if self.unasked is not None and self.unasked_message is not None:
            return FolderRefused(self.unasked, self.unasked_message)
        if self.unasked == NO_SOURCE_DETECTED:
            return not_installed(purpose)
        if self.unasked == ALL_SOURCES_SWITCHED_OFF:
            return switched_off(purpose)
        return unanswered_refusal()

    def unavailable_options(self) -> dict[str, Any]:
        """The emulator list's "unavailable" answer for a platform with no system to list emulators for."""
        reason = self.no_system_reason or self.unasked
        source = None if self.source is None else {"kind": self.source.kind, "starts_games": self.source.starts_games}
        return {"available": False, "options": [], "reason": reason, "source": source}

    def payload(self) -> dict[str, Any] | None:
        """The platform's system as the pages read it; ``None`` where no source was asked."""
        if self.state == UNASKED or self.source is None:
            return None
        return {
            "state": self.state,
            "source": self.source.kind,
            "systems": list(self.shown()),
            "platform": self.platform_name,
            "slug": self.keys.slug,
            "folder": self.keys.folder,
        }


def _refusal_message(platform: PlatformSystem, kind: str | None, shown: tuple[str, ...]) -> str:
    """The fact a refusal for a platform with no system to use carries, for the log and a caller without the panel.

    The sentences a user reads are the panel's (``frontend/src/utils/emulatorSourceWording.ts``),
    worded from the refusal's details; these are no copy of them.
    """
    systems = ", ".join(shown)
    name = platform.platform_name
    if platform.state == SWITCHED_OFF:
        return f"System {systems} is switched off in {kind}."
    if platform.state == NOT_HERE:
        return f"{kind} has a system for {name} ({systems}) that is not set up on this machine."
    if platform.state == UNMATCHED:
        return f"No system matches {name}."
    if platform.state == CONTRADICTED:
        return f'RomM\'s slug "{platform.keys.slug}" and folder "{platform.keys.folder or ""}" name different systems.'
    if platform.state == SEVERAL:
        return f"{kind} has several systems for {name} ({systems})."
    return f"{kind} has no system for {name}."
