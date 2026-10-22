"""The places Tender links firmware into: whether one may be used, what lies there, and how links are updated.

A place is the absolute path an emulator opens a firmware file at, as the
resolver names it. Tender puts a link there only where the name is free, never
over a file, a folder or a link that is not its own, and never under a root the
resolver only assumed (:mod:`domain.source_roots`); it creates no folder for a
place (:func:`place_refusal`).

A link is Tender's own when it points at a store file Tender has recorded,
wherever it lies, or where Tender's record of a link at that place names the
path it points at (:func:`is_own_link`). That record is what keeps a link Tender
could not remove with its store file its own (:class:`domain.firmware_store.FirmwareLink`).

Bringing the links up to date (:func:`plan_link_update`) places every link an
emulator wants where the name is free, places again a link of Tender's that
points at nothing where a store copy exists, removes such a link where none
does, records a link of Tender's found at a new place, and drops the record of a
place it may touch where no link of Tender's to the recorded file lies any more.
It downloads nothing.

Pure compute — the caller looks at the places and carries the plan out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from domain.firmware_store import FirmwareLink
    from domain.source_roots import SourceRoot, SourceRoots

# Why a place gets no link. ``root_assumed``: it lies under a root the resolver
# only assumed, or the source's roots could not be asked. ``folder_missing``: the folder it lies in is not there, and
# Tender creates none. ``unreadable``: the resolver could not look at it.
# ``no_place``: the answer names no place for the file.
REFUSED_ROOT_ASSUMED = "root_assumed"
REFUSED_FOLDER_MISSING = "folder_missing"
REFUSED_UNREADABLE = "unreadable"
REFUSED_NO_PLACE = "no_place"

# What lies at a place, as a row states it. ``linked``: Tender's link to this
# platform's store copy. ``linked_elsewhere``: Tender's link to another
# platform's store copy. ``not_linked``: free, or a link of Tender's that points
# at nothing, while a store copy waits to be linked there. ``occupied``: a file,
# a folder or a link that is not Tender's. ``free``: nothing, and no store copy
# to link.
LINK_LINKED = "linked"
LINK_ELSEWHERE = "linked_elsewhere"
LINK_NOT_LINKED = "not_linked"
LINK_OCCUPIED = "occupied"
LINK_FREE = "free"


@dataclass(frozen=True, slots=True)
class PlaceLook:
    """What one look at a place found: whether anything lies there, and where a link there points.

    ``link_target`` is the absolute path a symlink at the place names, ``None``
    for anything that is no symlink. ``is_dir`` is a folder at the place
    itself, not followed through a link. ``folder_exists`` is whether the folder
    the place lies in is there.
    """

    lexists: bool
    link_target: str | None = None
    is_dir: bool = False
    folder_exists: bool = True


@dataclass(frozen=True, slots=True)
class PlaceRefusal:
    """Why a place gets no link, and the folder the sentence names (``None`` where it names none)."""

    reason: str
    folder: str | None = None


@dataclass(frozen=True, slots=True)
class WantedLink:
    """One place an emulator wants a store file at.

    ``target`` is the store path of the file that fills the requirement there,
    ``None`` where this platform's store holds none. ``refusal`` is why the
    place may not be used, ``None`` where it may.
    """

    place: str
    target: str | None
    platform_slug: str
    file_name: str | None
    source: str
    refusal: PlaceRefusal | None = None


@dataclass(frozen=True, slots=True)
class LinkUpdatePlan:
    """What bringing one platform's links up to date does.

    ``place`` are links to create at a free place, ``replace`` links of
    Tender's that point at nothing to put back, ``remove`` such links with no
    store copy left; ``record`` the link records to write and ``drop`` the
    places whose record goes. ``left_in_place`` counts the places a store copy
    waits for and something not Tender's lies at.
    """

    place: tuple[WantedLink, ...] = ()
    replace: tuple[WantedLink, ...] = ()
    remove: tuple[str, ...] = ()
    record: tuple[WantedLink, ...] = ()
    drop: tuple[str, ...] = ()
    left_in_place: int = 0


@dataclass
class _Plan:
    place: list[WantedLink] = field(default_factory=list)
    replace: list[WantedLink] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    record: list[WantedLink] = field(default_factory=list)
    drop: list[str] = field(default_factory=list)
    left_in_place: int = 0

    def frozen(self) -> LinkUpdatePlan:
        return LinkUpdatePlan(
            place=tuple(self.place),
            replace=tuple(self.replace),
            remove=tuple(self.remove),
            record=tuple(self.record),
            drop=tuple(self.drop),
            left_in_place=self.left_in_place,
        )


def root_over(place: str, roots: SourceRoots | None) -> SourceRoot | None:
    """The deepest of *roots* the absolute *place* lies under, or ``None`` where it lies under none of them."""
    if roots is None:
        return None
    holding = [root for root in roots.roots if root.path is not None and _under(place, root.path)]
    return max(holding, key=lambda root: len(root.path or ""), default=None)


def place_refusal(
    place: str | None, look: PlaceLook | None, roots: SourceRoots | None, *, unreadable: bool
) -> PlaceRefusal | None:
    """Why *place* gets no link, or ``None`` where it may get one.

    The root it lies under is judged by where the resolver took it from: one
    it only assumed is used for nothing, and with no *roots* — the source's
    could not be asked — nothing is. A place under no root of the source —
    Cemu's ``data/Cemu`` — is the emulator's own and may be used. A folder that
    is not there is never created for a link.
    """
    if place is None:
        return PlaceRefusal(REFUSED_NO_PLACE)
    if unreadable:
        return PlaceRefusal(REFUSED_UNREADABLE)
    if roots is None:
        return PlaceRefusal(REFUSED_ROOT_ASSUMED, os.path.dirname(place))
    root = root_over(place, roots)
    if root is not None and root.assumed:
        return PlaceRefusal(REFUSED_ROOT_ASSUMED, root.path)
    if look is not None and not look.lexists and not look.folder_exists:
        return PlaceRefusal(REFUSED_FOLDER_MISSING, os.path.dirname(place))
    return None


def is_own_link(
    place: str, look: PlaceLook, store_targets: Mapping[str, str], records: Mapping[str, FirmwareLink]
) -> bool:
    """Is the link at *place* Tender's own?

    *store_targets* maps every recorded store file's path to its platform;
    *records* are Tender's link records by place.
    """
    target = look.link_target
    if target is None:
        return False
    if target in store_targets:
        return True
    record = records.get(place)
    return record is not None and record.target == target


def link_state(
    wanted: WantedLink,
    look: PlaceLook,
    *,
    store_targets: Mapping[str, str],
    present: frozenset[str],
    records: Mapping[str, FirmwareLink],
) -> tuple[str, str | None]:
    """What lies at *wanted*'s place as a row states it, and the platform whose store copy a link there points at.

    *present* are the store paths that are on disk. The platform is set for
    :data:`LINK_LINKED` and :data:`LINK_ELSEWHERE` only.
    """
    if not look.lexists:
        return (LINK_NOT_LINKED if wanted.target is not None else LINK_FREE), None
    if not is_own_link(wanted.place, look, store_targets, records):
        return LINK_OCCUPIED, None
    target = look.link_target or ""
    if target not in present:
        return (LINK_NOT_LINKED if wanted.target is not None else LINK_FREE), None
    owner = store_targets.get(target)
    if owner is None or owner == wanted.platform_slug:
        return LINK_LINKED, wanted.platform_slug
    return LINK_ELSEWHERE, owner


def plan_link_update(
    platform_slug: str,
    wanted: Iterable[WantedLink],
    looks: Mapping[str, PlaceLook],
    *,
    store_targets: Mapping[str, str],
    present: frozenset[str],
    records: Mapping[str, FirmwareLink],
    blocked: frozenset[str],
) -> LinkUpdatePlan:
    """Bring *platform_slug*'s links up to date over every place an emulator wants a store file at.

    *looks* hold a look at every wanted place and at the place of every record
    of the platform; *blocked* are the recorded places that may not be touched
    now, under a root the resolver only assumed or one that could not be judged.
    A place two emulators want is one place, and its first want decides.
    """
    plan = _Plan()
    seen: set[str] = set()
    for want in wanted:
        if want.place in seen or want.refusal is not None:
            seen.add(want.place)
            continue
        seen.add(want.place)
        _plan_wanted(plan, want, looks[want.place], store_targets, present, records)
    for place, record in records.items():
        if place in seen or record.platform_slug != platform_slug or place in blocked:
            continue
        _plan_recorded(plan, record, looks[place], present)
    return plan.frozen()


def _plan_wanted(
    plan: _Plan,
    want: WantedLink,
    look: PlaceLook,
    store_targets: Mapping[str, str],
    present: frozenset[str],
    records: Mapping[str, FirmwareLink],
) -> None:
    if not look.lexists:
        if want.target is not None:
            plan.place.append(want)
        return
    if not is_own_link(want.place, look, store_targets, records):
        if want.target is not None:
            plan.left_in_place += 1
        return
    target = look.link_target or ""
    if target in present:
        record = records.get(want.place)
        if record is None or record.target != target:
            plan.record.append(_found_link(want, target, store_targets))
        return
    if want.target is not None:
        plan.replace.append(want)
    else:
        plan.remove.append(want.place)
        if want.place in records:
            plan.drop.append(want.place)


def _plan_recorded(plan: _Plan, record: FirmwareLink, look: PlaceLook, present: frozenset[str]) -> None:
    if look.link_target != record.target:
        plan.drop.append(record.place)
        return
    if record.target not in present:
        plan.remove.append(record.place)
        plan.drop.append(record.place)


def _found_link(want: WantedLink, target: str, store_targets: Mapping[str, str]) -> WantedLink:
    """A link of Tender's found at *want*'s place with no record there, as the record it gets."""
    return WantedLink(
        place=want.place,
        target=target,
        platform_slug=store_targets.get(target, want.platform_slug),
        file_name=os.path.basename(target),
        source=want.source,
    )


def _under(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/") or root == "/"
