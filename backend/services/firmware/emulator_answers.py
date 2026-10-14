"""What the resolver says about each emulator of a platform, as the BIOS payloads carry it.

The launching emulator's answer — its verdict, the region rule over its groups
and the words a surface puts to them — and one tab per emulator the resolver
answered for (#2190 D15). Split out of :mod:`services.firmware.status`, which
builds the rows and aggregates these ride beside.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from domain.bios_status import collect_firmware_status, game_region_level, region_decides
from domain.firmware_wants import LOCATING_UNESTABLISHED

if TYPE_CHECKING:
    from domain.bios_status import BiosFileEntry
    from domain.emulator_commands import EmulatorOption
    from domain.firmware_groups import GroupVerdict
    from domain.firmware_wants import CoreFirmwareVerdict, FirmwareCatalogue


@dataclass(frozen=True)
class EmulatorAnswer:
    """What the resolver says about the launching emulator, as the aggregates read it.

    ``complete`` is whether that emulator stated what it wants
    (:meth:`FirmwareCatalogue.reading_complete_for`) — not whether it has a
    verdict, which ``verdict`` carries either way, ``verdict`` being its entry
    (``None`` where the answer holds none, which counts as not read),
    ``region_decides`` the region rule over its own groups
    (:func:`domain.bios_status.region_decides`), and ``groups`` the verdicts
    over those groups a surface words — narrowed to a game's regions where the
    caller had one, in which case ``game_level`` is the level that region gives
    (:func:`domain.bios_status.game_region_level`).
    """

    complete: bool
    verdict: CoreFirmwareVerdict | None
    region_decides: bool
    groups: tuple[GroupVerdict, ...]
    game_level: str | None = None


def emulator_answer(
    catalogue: FirmwareCatalogue, identity: str | None, groups: tuple[GroupVerdict, ...]
) -> EmulatorAnswer:
    """The launching emulator's answer out of one catalogue."""
    verdict = catalogue.verdict_for(identity)
    stated = catalogue.groups_for(identity)
    return EmulatorAnswer(
        complete=catalogue.reading_complete_for(identity),
        verdict=verdict,
        region_decides=region_decides(verdict, stated),
        groups=groups,
        game_level=game_region_level(verdict, stated, groups),
    )


# The payload keys :func:`emulator_fields` answers, which the platform entry
# copies across with the counts.
EMULATOR_FIELDS = (
    "emulator_read",
    "locating",
    "emulator_declaration",
    "console_needs_image",
    "refused",
    "emulator_notes",
    "content_search",
)


def emulator_fields(answer: EmulatorAnswer) -> dict[str, Any]:
    """The resolver's own words about the launching emulator, for a surface to word.

    ``emulator_read`` is whether the emulator stated what it wants; the
    verdict, which can stand where it did not, reaches a surface as
    ``bios_level``. ``console_needs_image`` is the resolver's
    ``system_firmware`` saying the console does not start without an image this
    emulator does not carry, under its world-knowledge mark — which image is in
    place is the rows' verdicts, never this. ``refused`` is what
    keeps a verdict open that a sentence names; ``emulator_notes`` the
    statements about the emulator a surface words. ``content_search``
    rides beside them, from the rows as well
    (:func:`domain.bios_status.classify_content_search`).
    """
    verdict = answer.verdict
    if verdict is None:
        return {
            "emulator_read": False,
            "locating": LOCATING_UNESTABLISHED,
            "emulator_declaration": None,
            "console_needs_image": False,
            "refused": [],
            "emulator_notes": [],
        }
    return {
        "emulator_read": answer.complete,
        "locating": verdict.locating,
        "emulator_declaration": verdict.declaration,
        "console_needs_image": verdict.system_needs_an_image and verdict.world_knowledge,
        "refused": [
            {"declared": refused.declared, "required": refused.required, "reason": refused.reason}
            for refused in verdict.refused
        ],
        "emulator_notes": [
            {
                "kind": note.kind,
                "setting": note.setting,
                "tied": note.tied,
                "dir": note.dir,
                "candidates": note.candidates,
                "regions": list(note.regions),
            }
            for note in verdict.notes
        ],
    }


def emulator_tabs(
    catalogue: FirmwareCatalogue,
    launching: str | None,
    launching_label: str | None,
    options: list[EmulatorOption],
    others: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """One tab per emulator the resolver answered for, the launching one first (#2190 D15).

    Each tab carries that emulator's own verdict (``requirements_met``) and its
    label; the launching emulator's tab carries no rows of its own, because the
    payload's ``files`` are its rows. Every other tab carries its emulator's own
    requirements exactly as the resolver answers them — places, needs and
    per-file verdicts, read for that emulator — in the order the resolver gave
    the emulators. A tab changes what a surface shows and never which emulator
    launches.
    """
    labels: dict[str, str] = {}
    for option in options:
        if option.emulator is not None:
            labels.setdefault(option.emulator, option.label)

    def verdict_of(emulator: str | None) -> bool | None:
        verdict = catalogue.verdict_for(emulator)
        return verdict.requirements_met if verdict is not None else None

    tabs: list[dict[str, Any]] = [
        {
            "emulator": launching,
            "label": launching_label or labels.get(launching or "") or launching,
            "launching": True,
            "requirements_met": verdict_of(launching),
        }
    ]
    for emulator, items in others.items():
        placements: dict[str, Any] = {}
        for placement in catalogue.placements:
            if placement.emulator == emulator:
                placements.setdefault(placement.file_name, placement)
        entries = collect_firmware_status(
            items, placements, catalogue.reading_complete_for(emulator), emulator, catalogue.groups
        )
        rows = sorted((row_payload(entry) for entry in entries), key=lambda row: row["declared_path"].lower())
        tabs.append(
            {
                "emulator": emulator,
                "label": labels.get(emulator, emulator),
                "launching": False,
                "requirements_met": verdict_of(emulator),
                "rows": rows,
            }
        )
    return tabs


def row_payload(entry: BiosFileEntry) -> dict[str, Any]:
    """One row of a game page or an emulator's tab on the wire.

    Every field of the entry but ``cores``: the per-emulator entries are what
    the domain reads a row's own ``one_of`` and need off, and no surface reads
    them since each emulator has a tab of its own (#2190 D15).
    """
    row = asdict(entry)
    del row["cores"]
    return row
