"""The verdict over a one-of group, and the console regions it is judged in.

A one-of group (:class:`domain.firmware_wants.FirmwareGroup`) is ONE requirement:
a launch opens exactly one of its options, and which one is decided by the
console region of the disc being booted. So the group is judged region by
region, and nothing here knows which console or which emulator it is judging —
every group the resolver states for any emulator is read the same way.

Each region the group speaks about lands in exactly one of three sets, in this
order of precedence:

- **covered** — an option serving it is in place (``satisfied is True``);
- **missing** — the option serving it is shown not to be, or the resolver stated
  that nothing boots for it;
- **unchecked** — nothing established either: an option serving it is there and
  could not be read, or the resolver named it as resting on a read that settled
  nothing.

The state follows, as one of :data:`GROUP_STATES`. ``met`` only where every
region is covered — never green over a region nobody checked. ``partial`` where
something is covered and a region is shown not to be. ``unknown`` where nothing
shown missing stands beside a covered region that is not the whole story, or
where an option is there and unread. ``unmet`` where no option is in place, or
might be — a region stated uncovered stays red whatever else is unchecked.

The game page asks the same question narrowed to the game's own regions
(:func:`judge_group_for_game`), mapped from RomM's region names by
:func:`console_regions_of`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.firmware_wants import FirmwareGroup, FirmwarePlacement

GROUP_MET = "met"
GROUP_PARTIAL = "partial"
GROUP_UNMET = "unmet"
GROUP_UNKNOWN = "unknown"

GROUP_STATES = (GROUP_MET, GROUP_PARTIAL, GROUP_UNMET, GROUP_UNKNOWN)

# The console regions a RomM region name maps onto, in the resolver's own
# spelling of them. Only these three are mapped FROM a game's regions; a group
# may still speak about any region the resolver names, and is judged over it the
# same way.
REGION_NTSC_J = "ntsc-j"
REGION_NTSC_U = "ntsc-u"
REGION_PAL = "pal"

# RomM's region names (the No-Intro / Redump filename tags it parses), folded
# to lower case. A name not listed — World, Asia, Korea, Brazil, anything
# unknown — maps to no console region, and the game is then answered by the
# platform's verdict rather than by a guess about which console it was sold for.
_ROMM_REGIONS: dict[str, str] = {
    "usa": REGION_NTSC_U,
    "canada": REGION_NTSC_U,
    "japan": REGION_NTSC_J,
    "europe": REGION_PAL,
    "australia": REGION_PAL,
    "austria": REGION_PAL,
    "belgium": REGION_PAL,
    "croatia": REGION_PAL,
    "denmark": REGION_PAL,
    "england": REGION_PAL,
    "finland": REGION_PAL,
    "france": REGION_PAL,
    "germany": REGION_PAL,
    "greece": REGION_PAL,
    "holland": REGION_PAL,
    "ireland": REGION_PAL,
    "italy": REGION_PAL,
    "netherlands": REGION_PAL,
    "norway": REGION_PAL,
    "poland": REGION_PAL,
    "portugal": REGION_PAL,
    "russia": REGION_PAL,
    "scandinavia": REGION_PAL,
    "spain": REGION_PAL,
    "sweden": REGION_PAL,
    "switzerland": REGION_PAL,
    "uk": REGION_PAL,
    "united kingdom": REGION_PAL,
}


@dataclass(frozen=True)
class GroupVerdict:
    """What one group's options establish, region by region, and the state over it.

    ``game_regions`` is empty for the platform's verdict and holds the game's
    console regions where the verdict was narrowed to one game; the three
    region sets are then the game's regions alone.
    """

    state: str
    covered: tuple[str, ...]
    missing: tuple[str, ...]
    unchecked: tuple[str, ...]
    game_regions: tuple[str, ...] = ()


def judge_group(group: FirmwareGroup) -> GroupVerdict:
    """The platform's verdict over *group* — every region it speaks about."""
    covered = _regions_where(group, True)
    missing = tuple(
        region
        for region in dict.fromkeys((*_regions_where(group, False), *group.absent_regions))
        if region not in covered
    )
    unchecked = tuple(region for region in group.regions if region not in covered and region not in missing)
    return GroupVerdict(
        state=_state(group, covered, missing, unchecked), covered=covered, missing=missing, unchecked=unchecked
    )


def judge_group_for_game(group: FirmwareGroup, game_regions: tuple[str, ...]) -> GroupVerdict:
    """*group*'s verdict for a game sold in *game_regions*, which must not be empty.

    The game is covered where ONE of its regions is — a disc carries one region,
    and a release listed under several boots wherever any of them is served. A
    region the group says nothing about at all is unchecked, never missing:
    nothing was stated for it either way.
    """
    platform = judge_group(group)
    covered = tuple(region for region in game_regions if region in platform.covered)
    if covered:
        return GroupVerdict(state=GROUP_MET, covered=covered, missing=(), unchecked=(), game_regions=game_regions)
    missing = tuple(region for region in game_regions if region in platform.missing)
    unchecked = tuple(region for region in game_regions if region not in platform.missing)
    return GroupVerdict(
        state=GROUP_UNKNOWN if unchecked else GROUP_UNMET,
        covered=(),
        missing=missing,
        unchecked=unchecked,
        game_regions=game_regions,
    )


def judge_groups(groups: Iterable[FirmwareGroup], game_regions: tuple[str, ...] = ()) -> tuple[GroupVerdict, ...]:
    """Every group's verdict — narrowed to the game where *game_regions* names any."""
    if game_regions:
        return tuple(judge_group_for_game(group, game_regions) for group in groups)
    return tuple(judge_group(group) for group in groups)


def console_regions_of(rom_regions: Iterable[str]) -> tuple[str, ...]:
    """The console regions RomM's region names for one game map onto, without repeats.

    Case-insensitive, because RomM keeps the spelling of the filename it parsed.
    Empty where no name maps — the game's own region is then unknown, which is
    the caller's cue to answer with the platform's verdict.
    """
    mapped = (_ROMM_REGIONS.get(region.strip().casefold()) for region in rom_regions)
    return tuple(dict.fromkeys(region for region in mapped if region is not None))


def fetched_as_required(
    placement: FirmwarePlacement | None, emulator: str | None, groups: tuple[FirmwareGroup, ...]
) -> bool:
    """Does "Download required" fetch this file for a launch with *emulator*?

    The one rule both the button's count and the download itself apply. A file
    the emulator requires on its own is fetched; so is an option of one of its
    groups that serves a region nothing in place covers yet — and nothing of a
    group whose regions are all covered. *emulator* ``None`` is a pick that
    could not be identified, which falls back to "any emulator requires it" and
    owns no group.
    """
    if placement is None:
        return False
    if emulator is None:
        required = placement.required_by_any
    else:
        required = any(want.emulator == emulator and want.required for want in placement.wants)
    return required or any(placement.file_name in _uncovered_options(group) for group in groups)


def _uncovered_options(group: FirmwareGroup) -> frozenset[str]:
    """The option files serving a region of *group* that nothing in place covers."""
    covered = set(judge_group(group).covered)
    return frozenset(option.file_name for option in group.options if not covered.issuperset(option.regions))


def _regions_where(group: FirmwareGroup, satisfied: bool | None) -> tuple[str, ...]:
    """The regions served by an option of *group* whose verdict is *satisfied*."""
    return tuple(
        dict.fromkeys(region for option in group.options if option.satisfied is satisfied for region in option.regions)
    )


def _state(group: FirmwareGroup, covered: tuple[str, ...], missing: tuple[str, ...], unchecked: tuple[str, ...]) -> str:
    """The state the three region sets amount to — see the module docstring for the order."""
    if covered:
        if missing:
            return GROUP_PARTIAL
        return GROUP_UNKNOWN if unchecked else GROUP_MET
    if any(option.satisfied is None for option in group.options):
        return GROUP_UNKNOWN
    return GROUP_UNMET
