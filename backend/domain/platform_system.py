"""Which system a RomM platform is in one emulator source, read from the resolver's answer.

RomM names a platform by its own slug and by its folder in RomM (``fs_slug``),
neither of which is a system a source declares. The resolver is asked with
both, as (source, key) pairs (``adapters/atlas_platforms.py``), and answers in
the source's own systems; this module holds what that answer comes to — one
system, several, a switched-off one, one this machine does not have, none, or
a platform the resolver cannot match — and why there is none to use. Tender
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
from typing import TYPE_CHECKING, Any, NamedTuple

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
# answer named, the name an install record holds, or — where the answer named
# several systems — the pairs the platform was asked with, which the resolver
# answers for alike or names no system for.
SystemArgument = str | SystemPair | tuple[SystemPair, ...]


def system_key(system: SystemArgument) -> str | None:
    """The key *system* names its one system by, for a record or a sentence; ``None`` for several pairs."""
    if isinstance(system, SystemPair):
        return system.key
    return None if isinstance(system, tuple) else system


class SystemStatus(StrEnum):
    """How firmly a system is in the source: switched on, switched off, or not there at all."""

    ON = "on"
    OFF = "off"
    NOT_HERE = "not_here"


FOUND = "found"
# Several switched-on systems answer the platform; Tender picks none.
SEVERAL = "several"
SWITCHED_OFF = "switched_off"
# The source's frontend has a system for the platform, and this machine has not set it up.
NOT_HERE = "not_here"
# The resolver knows the platform, and the source's frontend has no system for it.
NO_SYSTEM = "no_system"
# The resolver cannot match the platform to a system: it knows neither pair.
UNMATCHED = "unmatched"
# RomM's slug and the platform's folder name different systems, so the resolver names none (#2751 D2).
CONTRADICTED = "contradicted"
# No source was asked: none answers, the one named is not detected, or the
# question raised. ``PlatformSystem.unasked`` says why.
UNASKED = "unasked"


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
    """What a platform is asked about by: RomM's slug for it, and its folder in RomM where one is kept."""

    slug: str
    folder: str | None = None


@dataclass(frozen=True, slots=True)
class KeptPlatform:
    """What is kept of one platform RomM lists, under its folder in RomM (``fs_slug``).

    ``ids`` holds every field of RomM's platform whose name ends in ``_id`` or
    ``_slug``, each as RomM sent it; Tender reads none of them. The platform
    is asked about by its slug and its folder (:meth:`keys`). ``romm_version``
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

    def keys(self, slug: str) -> PlatformKeys:
        """What the platform of *slug* is asked about by: that slug, and the folder kept for it."""
        return PlatformKeys(slug, self.fs_slug)


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
    """The kept platform whose slug is *slug* — of several, the last kept — or ``None``."""
    # Folders that share a slug carry the same ids: RomM looks every
    # metadata id up by the platform's slug (``handler/scan_handler.py::
    # scan_platform``, 5.4.1), so which of them answers changes no question.
    return next((entry for entry in reversed(list(kept.values())) if entry.slug == slug), None)


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


def matched_state(matches: Iterable[tuple[SystemPair, SystemStatus]]) -> tuple[str, tuple[SystemPair, ...]]:
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
    for status, state in ((SystemStatus.OFF, SWITCHED_OFF), (SystemStatus.NOT_HERE, NOT_HERE)):
        named = tuple(system for system, each in matched if each == status)
        if named:
            return state, named
    return NO_SYSTEM, ()


@dataclass(frozen=True, slots=True)
class PlatformSystem:
    """The system one platform is in one source, or why there is none to use.

    ``system`` is what a later question names the platform's system by: under
    :data:`FOUND` the pair the source's answer named, or the name an install
    record holds; under :data:`SEVERAL` the pairs the platform was asked with,
    so each question is answered for the platform as the resolver answers it
    for those systems alike, or not at all. ``named`` is every system the
    answer named for its state — the one found, the several switched on, the
    switched-off ones, or the ones this machine has not set up. ``source`` is
    the source asked — or, where RomM's listing could not be read, the source
    that would have been — and ``None`` where there is none, and for a system
    an install record holds, which no source was asked for. ``unasked`` says
    why none was asked, in the vocabulary of :mod:`domain.emulator_sources` —
    or, where RomM's listing could not be read, the reason that read failed
    with, and ``unasked_message`` its message. ``platform_folder`` is the
    platform's folder in RomM it was asked with, ``None`` where none was kept;
    the sentence for a slug and a folder that contradict each other names both.
    """

    state: str
    platform_slug: str
    platform_name: str
    system: SystemArgument | None = None
    source: ArrangedSource | None = None
    unasked: str | None = None
    unasked_message: str | None = None
    named: tuple[SystemPair, ...] = ()
    platform_folder: str | None = None

    @property
    def taken(self) -> SystemArgument | None:
        """What a question about the platform's games names its system by; ``None`` where there is none."""
        return self.system if self.state in (FOUND, SEVERAL) else None

    @property
    def one_system(self) -> SystemArgument | None:
        """The one system a download lands in and a frontend link goes to; ``None`` where there is not one."""
        return self.system if self.state == FOUND else None

    def shown(self) -> tuple[str, ...]:
        """The keys the sentences name the platform's systems by."""
        if self.named:
            return tuple(system.key for system in self.named)
        key = None if self.system is None else system_key(self.system)
        return () if key is None else (key,)

    def refusal(self, purpose: str) -> FolderRefused:
        """Why a *purpose* download, or anything refused like one, may not go ahead for this platform.

        The panel words the refusals of its own from their details; their
        messages stand where it does not.
        """
        kind = self.source.kind if self.source is not None else None
        shown = self.shown()
        details = {
            "source": kind,
            "platform": self.platform_name,
            "system": shown[0] if len(shown) == 1 else None,
            "systems": list(shown),
            "slug": self.platform_slug,
            "folder": self.platform_folder,
        }
        case = _CASE_OF_STATE.get(self.state)
        if case is not None:
            return FolderRefused(case.platform_refusal.value, _refusal_message(self, kind, shown), **details)
        if self.state != UNASKED:
            raise ValueError(f"a platform with system {self.system!r} is not refused")
        if self.unasked is not None and self.unasked_message is not None:
            return FolderRefused(self.unasked, self.unasked_message)
        if self.unasked == NO_SOURCE_DETECTED:
            return not_installed(purpose)
        if self.unasked == ALL_SOURCES_SWITCHED_OFF:
            return switched_off(purpose)
        return unanswered_refusal()

    def unavailable_options(self) -> dict[str, Any]:
        """The emulator list's "unavailable" answer for a platform with no system to list emulators for."""
        case = _CASE_OF_STATE.get(self.state)
        reason = case.platform_refusal.value if case is not None else self.unasked
        source = None if self.source is None else {"kind": self.source.kind, "starts_games": self.source.starts_games}
        return {"available": False, "options": [], "reason": reason, "source": source}

    def payload(self) -> dict[str, Any] | None:
        """The platform's system as the pages read it; ``None`` where no source was asked."""
        if self.state == UNASKED or self.source is None:
            return None
        shown = self.shown()
        return {
            "state": self.state,
            "source": self.source.kind,
            "system": shown[0] if len(shown) == 1 else None,
            "systems": list(shown),
            "platform": self.platform_name,
            "slug": self.platform_slug,
            "folder": self.platform_folder,
        }


def _refusal_message(platform: PlatformSystem, kind: str | None, shown: tuple[str, ...]) -> str:
    """The message a refusal for a platform with no system to use carries where the panel words none of its own.

    The panel's own sentences name the source as the user knows it; these name it by its kind.
    """
    systems = ", ".join(shown)
    name = platform.platform_name
    if platform.state == SWITCHED_OFF:
        return f"System {systems} is switched off in {kind}."
    if platform.state == NOT_HERE:
        return (
            f"{kind} has a system for {name} ({systems}), but it is not set up on this machine, "
            "so Tender cannot download its games."
        )
    if platform.state == UNMATCHED:
        return f"Tender cannot match {name} to a system yet, so it cannot download its games."
    if platform.state == CONTRADICTED:
        return (
            f'RomM\'s slug "{platform.platform_slug}" and folder "{platform.platform_folder}" point to different '
            f"systems, so Tender cannot match {name} to a system."
        )
    if platform.state == SEVERAL:
        return f"{kind} has several systems for {name} ({systems}), so Tender cannot download its games yet."
    return f"{kind} has no system for {name}, so Tender cannot download its games."
