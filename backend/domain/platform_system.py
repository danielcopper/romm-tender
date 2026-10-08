"""Which system a RomM platform is in one emulator source, decided from the resolver's answers.

RomM names a platform by its own slug, which only sometimes equals a system a
source declares, and by the ids it holds for the platform in four public
vocabularies. The source
answers per id which of its systems belong to that platform
(``systems_for_platform``); this module asks nothing itself and decides from
those answers which system the platform is there, or why it is none. RomM's
slug is never a system name: it only chooses among the platforms the resolver
named.

The ids are kept offline beside the platform names (``kv_config``); decoding
and encoding that value is here, reading and writing it is the caller's.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_sources import ALL_SOURCES_SWITCHED_OFF, NO_SOURCE_DETECTED, ArrangedSource
from domain.retrodeck_folders import FolderRefused, not_installed, switched_off, unanswered_refusal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Where the ids are kept in ``kv_config``, beside ``platform_names``.
PLATFORM_IDS_KEY = "platform_ids"

# The resolver's vocabularies, each beside the field RomM's platform carries it
# in, in the order they are asked. RomM's other ids have no crosswalk in the
# resolver and are not asked.
_VOCABULARIES = (
    ("igdb", "igdb_id"),
    ("libretro", "libretro_slug"),
    ("screenscraper", "ss_id"),
    ("thegamesdb", "tgdb_id"),
)

# How firmly a system is in the source, as the resolver states it: switched on,
# present but switched off in the catalogue's own text, or not there at all.
STATUS_DECLARED = "declared"
STATUS_DISABLED = "disabled"

FOUND = "found"
SWITCHED_OFF = "switched_off"
NO_SYSTEM = "no_system"
# No source was asked: none answers, the one named is not detected, or the
# question raised. ``PlatformSystem.unasked`` says which.
UNASKED = "unasked"

NO_PLATFORM_SYSTEM = "no_platform_system"
PLATFORM_SYSTEM_OFF = "platform_system_off"


@dataclass(frozen=True, slots=True)
class PlatformIds:
    """A RomM platform's ids in the vocabularies the resolver knows; ``None`` where RomM holds none.

    ``name`` is RomM's display name, kept beside them because the platform
    names a sync keeps cover only the platforms it synced.
    """

    igdb_id: int | None = None
    libretro_slug: str | None = None
    ss_id: int | None = None
    tgdb_id: int | None = None
    name: str | None = None

    def questions(self) -> tuple[tuple[str, str], ...]:
        """``(vocabulary, value)`` for every id held, in the order they are asked; a numeric id as its digits."""
        return tuple(
            (vocabulary, str(value))
            for vocabulary, field in _VOCABULARIES
            if (value := getattr(self, field)) is not None
        )


def platform_ids_of(platform: Mapping[str, Any]) -> PlatformIds:
    """The ids one RomM platform carries; a value of the wrong kind counts as none."""
    return PlatformIds(
        igdb_id=_number(platform.get("igdb_id")),
        libretro_slug=_text(platform.get("libretro_slug")),
        ss_id=_number(platform.get("ss_id")),
        tgdb_id=_number(platform.get("tgdb_id")),
        name=_text(platform.get("name")) or _text(platform.get("display_name")),
    )


def platform_ids_by_slug(platforms: Iterable[Mapping[str, Any]]) -> dict[str, PlatformIds]:
    """Every listed platform's ids, keyed by its RomM slug; a platform without a slug is left out."""
    return {slug: platform_ids_of(p) for p in platforms if (slug := _text(p.get("slug"))) is not None}


def encode_platform_ids(ids: Mapping[str, PlatformIds]) -> str:
    """The value kept under :data:`PLATFORM_IDS_KEY`."""
    return json.dumps(
        {
            slug: {**{field: getattr(entry, field) for _vocabulary, field in _VOCABULARIES}, "name": entry.name}
            for slug, entry in ids.items()
        }
    )


def decode_platform_ids(raw: str | None) -> dict[str, PlatformIds] | None:
    """The kept ids by slug, or ``None`` where none are kept — absent, empty, or not a JSON object."""
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return {slug: platform_ids_of(entry) for slug, entry in decoded.items() if isinstance(entry, dict)}


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True, slots=True)
class SystemMatch:
    """One system of the source answering to an id: its name, its status, and its platform tags."""

    system: str
    status: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IdAnswer:
    """The source's answer to one id.

    ``platforms`` is what the id was resolved to, ``systems`` the one system an
    id stands for alone (then ``platforms`` is empty), and ``matches`` every
    system answering to either, in the resolver's order.
    """

    platforms: tuple[str, ...]
    systems: tuple[str, ...]
    matches: tuple[SystemMatch, ...]


@dataclass(frozen=True, slots=True)
class SystemPick:
    """What the answers decide: a switched-on system, a switched-off one, or none."""

    state: str
    system: str | None = None


def pick_system(answers: Iterable[IdAnswer], platform_slug: str) -> SystemPick:
    """The system a platform is in the source, from its ids' answers in the order they are asked.

    The first id that gives a switched-on system decides. An answer naming
    several platforms gives one only where one of them is RomM's slug with its
    hyphens dropped and has a switched-on system; otherwise the next id is
    asked, and where none gives one, the first platform with a switched-on
    system of the first answer that has one is taken. Among several systems of
    one platform, the one named like a platform the id was resolved to is
    taken, else the first in the resolver's order. Where no id gives a
    switched-on system, the first switched-off one is named; where none gives
    any, there is no system.

    *answers* is consumed lazily, so an id after the deciding one is never asked.
    """
    wanted = platform_slug.replace("-", "")
    fallback: str | None = None
    first_off: str | None = None
    for answer in answers:
        platform = _platform_of(answer, wanted)
        if platform is not None or not answer.platforms:
            on = _choose(answer, platform, STATUS_DECLARED)
            if on is not None:
                return SystemPick(FOUND, on)
            first_off = first_off or _choose(answer, platform, STATUS_DISABLED)
        fallback = fallback or _first_of_platforms(answer, STATUS_DECLARED)
        first_off = first_off or _first_of_platforms(answer, STATUS_DISABLED)
    if fallback is not None:
        return SystemPick(FOUND, fallback)
    if first_off is not None:
        return SystemPick(SWITCHED_OFF, first_off)
    return SystemPick(NO_SYSTEM)


def _platform_of(answer: IdAnswer, wanted: str) -> str | None:
    """The platform an answer stands for: its only one, or the one equal to *wanted*; ``None`` otherwise."""
    if len(answer.platforms) == 1:
        return answer.platforms[0]
    return wanted if wanted in answer.platforms else None


def _first_of_platforms(answer: IdAnswer, status: str) -> str | None:
    """The system with *status* of the first of *answer*'s platforms that has one."""
    return next(
        (system for platform in answer.platforms if (system := _choose(answer, platform, status)) is not None), None
    )


def _choose(answer: IdAnswer, platform: str | None, status: str) -> str | None:
    """Among *answer*'s systems of *platform* with *status*, the main one; ``None`` where there is none."""
    candidates = [
        match.system
        for match in answer.matches
        if match.status == status and (platform is None or platform in match.tags)
    ]
    named = (platform,) if platform is not None else answer.platforms
    return next((system for system in candidates if system in named), candidates[0] if candidates else None)


@dataclass(frozen=True, slots=True)
class PlatformSystem:
    """The system one platform is in one source, or why there is none to use.

    ``system`` is the system taken under :data:`FOUND`, and the switched-off one
    named under :data:`SWITCHED_OFF`. ``source`` is the source asked, ``None``
    where none was — and for a system an install record holds, which no source
    was asked for. ``unasked`` says why none was asked, in the vocabulary of
    :mod:`domain.emulator_sources`.
    """

    state: str
    platform_slug: str
    platform_name: str
    system: str | None = None
    source: ArrangedSource | None = None
    unasked: str | None = None

    @property
    def taken(self) -> str | None:
        """The system to use — ``None`` where there is none, a switched-off one included."""
        return self.system if self.state == FOUND else None

    def refusal(self, purpose: str) -> FolderRefused:
        """Why a *purpose* download, or anything refused like one, may not go ahead for this platform.

        The panel words the two refusals of its own from their details; their
        messages stand where it does not.
        """
        kind = self.source.kind if self.source is not None else None
        details = {"source": kind, "platform": self.platform_name, "system": self.system}
        if self.state == NO_SYSTEM:
            return FolderRefused(NO_PLATFORM_SYSTEM, f"{kind} has no system for {self.platform_name}.", **details)
        if self.state == SWITCHED_OFF:
            return FolderRefused(PLATFORM_SYSTEM_OFF, f"System {self.system} is switched off in {kind}.", **details)
        if self.state != UNASKED:
            raise ValueError(f"a platform with system {self.system!r} is not refused")
        if self.unasked == NO_SOURCE_DETECTED:
            return not_installed(purpose)
        if self.unasked == ALL_SOURCES_SWITCHED_OFF:
            return switched_off(purpose)
        return unanswered_refusal()

    def unavailable_options(self) -> dict[str, Any]:
        """The emulator list's "unavailable" answer for a platform with no system to list emulators for."""
        reason = {NO_SYSTEM: NO_PLATFORM_SYSTEM, SWITCHED_OFF: PLATFORM_SYSTEM_OFF}.get(self.state, self.unasked)
        source = None if self.source is None else {"kind": self.source.kind, "starts_games": self.source.starts_games}
        return {"available": False, "options": [], "reason": reason, "source": source}

    def payload(self) -> dict[str, Any] | None:
        """The platform's system as the pages read it; ``None`` where no source was asked."""
        if self.state == UNASKED or self.source is None:
            return None
        return {"state": self.state, "source": self.source.kind, "system": self.system, "platform": self.platform_name}
