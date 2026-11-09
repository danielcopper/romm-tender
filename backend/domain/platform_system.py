"""Which system a RomM platform is in one emulator source, decided from the resolver's answers.

RomM names a platform by its own slug, which only sometimes equals a system a
source declares, and by the ids it holds for the platform in public
vocabularies, four of which the resolver knows. The source answers per id which of its systems belong to that
platform; ``adapters/atlas_platforms.py`` asks and reads those answers into
:class:`IdAnswer`, and this module decides from them which system the
platform is there, or why it is none. RomM's slug is never a system name: it
only chooses among the platforms the resolver named.

What is kept of each platform RomM lists lives offline beside the platform
names (``kv_config``), under the platform's folder in RomM (``fs_slug``);
decoding and encoding that value is here, reading and writing it is the
caller's.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from domain.emulator_sources import ALL_SOURCES_SWITCHED_OFF, NO_SOURCE_DETECTED, ArrangedSource
from domain.retrodeck_folders import FolderRefused, not_installed, switched_off, unanswered_refusal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Where the listed platforms are kept in ``kv_config``, beside ``platform_names``.
PLATFORM_IDS_KEY = "platform_ids"

# The fields of RomM's platform the resolver can be asked about, in the order
# they are asked. RomM's other ids have no crosswalk in the resolver and are
# not asked.
ID_FIELDS = ("igdb_id", "libretro_slug", "ss_id", "tgdb_id")

# A suffix rather than a list of fields, so that an identifier RomM adds
# needs no change here.
_IDENTIFIER_SUFFIXES = ("_id", "_slug")


class SystemStatus(StrEnum):
    """How firmly a system is in the source: switched on, switched off, or not there at all."""

    ON = "on"
    OFF = "off"
    NOT_HERE = "not_here"


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

    ``name`` is the ``name`` RomM's platform listing gives it, kept beside them
    so the platform's system can name a platform the platform-name cache holds
    no name for.
    """

    igdb_id: int | None = None
    libretro_slug: str | None = None
    ss_id: int | None = None
    tgdb_id: int | None = None
    name: str | None = None

    def questions(self) -> tuple[tuple[str, str], ...]:
        """``(field, value)`` for every id held, in the order they are asked; a numeric id as its digits."""
        return tuple((field, str(value)) for field in ID_FIELDS if (value := getattr(self, field)) is not None)


def platform_ids_of(platform: Mapping[str, Any]) -> PlatformIds:
    """The ids one RomM platform carries; a value of the wrong kind counts as none."""
    return PlatformIds(
        igdb_id=_number(platform.get("igdb_id")),
        libretro_slug=_text(platform.get("libretro_slug")),
        ss_id=_number(platform.get("ss_id")),
        tgdb_id=_number(platform.get("tgdb_id")),
        name=_listed_name(platform),
    )


@dataclass(frozen=True, slots=True)
class KeptPlatform:
    """What is kept of one platform RomM lists, under its folder in RomM (``fs_slug``).

    ``ids`` holds every field of RomM's platform whose name ends in ``_id`` or
    ``_slug``, each as RomM sent it. Tender reads none of them but the ones
    :meth:`question_ids` asks the resolver with. ``romm_version`` is the RomM
    version known when the listing was read, ``None`` where none was.
    """

    slug: str | None = None
    fs_slug: str | None = None
    platform_id: int | None = None
    name: str | None = None
    is_identified: bool | None = None
    romm_version: str | None = None
    ids: dict[str, Any] = field(default_factory=dict)

    def question_ids(self) -> PlatformIds:
        """The ids and name the platform's system is asked with."""
        return platform_ids_of({**self.ids, "name": self.name})


def kept_platform_of(platform: Mapping[str, Any], romm_version: str | None) -> KeptPlatform:
    """What is kept of one RomM platform read with *romm_version*; a named field of the wrong kind counts as none."""
    identified = platform.get("is_identified")
    return KeptPlatform(
        slug=_text(platform.get("slug")),
        fs_slug=_text(platform.get("fs_slug")),
        platform_id=_number(platform.get("id")),
        name=_listed_name(platform),
        is_identified=identified if isinstance(identified, bool) else None,
        romm_version=romm_version,
        ids={key: value for key, value in platform.items() if key.endswith(_IDENTIFIER_SUFFIXES)},
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
            slug=_text(key), name=_text(entry.get("name")), ids={f: entry[f] for f in ID_FIELDS if f in entry}
        )
    return replace(kept_platform_of(entry, _text(entry.get("romm_version"))), ids=ids)


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


def _listed_name(platform: Mapping[str, Any]) -> str | None:
    return _text(platform.get("name")) or _text(platform.get("display_name"))


@dataclass(frozen=True, slots=True)
class SystemMatch:
    """One system of the source answering to an id: its name, its status, and its platform tags."""

    system: str
    status: SystemStatus
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
    several platforms speaks only for the one that is RomM's slug with its
    hyphens dropped, and an answer naming several without that one is passed
    over: another platform is never taken in its place. Among several systems
    of the platform an answer stands for, with the same status, the first
    named like that platform is taken, else the first the resolver lists —
    Tender's own choice, since the resolver names every match and prefers
    none (its order is alphabetical within each status). Where no id gives a
    switched-on system, the first switched-off one is named; where none gives
    any, there is no system. Which region each of several regional systems
    stands for is a statement the resolver does not make yet; once it does
    (emu-atlas#583), that statement replaces this rule's choice among them.

    *answers* is consumed lazily, so an id after the deciding one is never asked.
    """
    wanted = platform_slug.replace("-", "")
    first_off: str | None = None
    for answer in answers:
        platform = _platform_of(answer, wanted)
        if platform is None and answer.platforms:
            continue
        on = _choose(answer, platform, SystemStatus.ON)
        if on is not None:
            return SystemPick(FOUND, on)
        first_off = first_off or _choose(answer, platform, SystemStatus.OFF)
    if first_off is not None:
        return SystemPick(SWITCHED_OFF, first_off)
    return SystemPick(NO_SYSTEM)


def _platform_of(answer: IdAnswer, wanted: str) -> str | None:
    """The platform an answer stands for: its only one, or the one equal to *wanted*; ``None`` otherwise."""
    if len(answer.platforms) == 1:
        return answer.platforms[0]
    return wanted if wanted in answer.platforms else None


def _choose(answer: IdAnswer, platform: str | None, status: SystemStatus) -> str | None:
    """Among *answer*'s systems of *platform* with *status*, the one Tender's rule takes, or ``None``."""
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
    named under :data:`SWITCHED_OFF`. ``source`` is the source asked — or, where
    RomM's listing could not be read, the source that would have been — and
    ``None`` where there is none, and for a system an install record holds,
    which no source was asked for. ``unasked`` says why none was asked, in the vocabulary of
    :mod:`domain.emulator_sources` — or, where RomM's listing could not be read
    for the platform's ids, the reason that read failed with, and
    ``unasked_message`` its message.
    """

    state: str
    platform_slug: str
    platform_name: str
    system: str | None = None
    source: ArrangedSource | None = None
    unasked: str | None = None
    unasked_message: str | None = None

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
        if self.unasked is not None and self.unasked_message is not None:
            return FolderRefused(self.unasked, self.unasked_message)
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
