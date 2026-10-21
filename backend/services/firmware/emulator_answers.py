"""What the resolver says about each emulator of a platform, as the BIOS payloads carry it.

The launching emulator's answer — its verdict, the region rule over its groups
and the words a surface puts to them — and one entry per emulator the resolver
answered for, which a BIOS table's emulator selector offers. Split
out of :mod:`services.firmware.status`, which builds the rows and aggregates
these ride beside.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from domain.bios_status import game_region_level, region_decides
from domain.firmware_wants import LOCATING_UNESTABLISHED
from services.firmware.rows import required_folders

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
    (:func:`domain.bios_status.game_region_level`). ``folders`` are the
    verdicts over the folders the emulator requires and lists, each one
    requirement in the counts.
    """

    complete: bool
    verdict: CoreFirmwareVerdict | None
    region_decides: bool
    groups: tuple[GroupVerdict, ...]
    game_level: str | None = None
    folders: tuple[bool | None, ...] = ()


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
        folders=required_folders(catalogue, identity),
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
    others: dict[str, list[BiosFileEntry]],
) -> list[dict[str, Any]]:
    """One entry per emulator the resolver answered for, the launching one first.

    Each entry carries that emulator's own verdict (``requirements_met``) and its
    label; the launching emulator's entry carries no rows of its own, because the
    payload's ``files`` are its rows. Every other entry carries its emulator's own
    requirements exactly as the resolver answers them — places, needs and
    per-file verdicts, read for that emulator — in the order the resolver gave
    the emulators. Choosing an entry changes what a surface shows and never which
    emulator launches.

    An entry is named by every distinct label *options* give its identity,
    joined " / " in their own order — RetroDECK lists ``pcsx2_libretro.so`` as
    both LRPS2 and PCSX2 — so it reads the same whichever of them is the pick.
    ``installed`` is ``False`` for an emulator *options* mark not
    installed (``not_installed``), the same reading the emulator picker shows.

    A launching emulator with no identity has no entry: nothing could be asked
    about it, and an entry under a made-up name would read as an emulator.
    """
    not_installed = {
        option.emulator for option in options if option.emulator is not None and option.reason == "not_installed"
    }
    labels = _labels_by_emulator(options)

    def label_of(emulator: str | None) -> str | None:
        names = labels.get(emulator or "")
        return " / ".join(names) if names else None

    def verdict_of(emulator: str | None) -> bool | None:
        verdict = catalogue.verdict_for(emulator)
        return verdict.requirements_met if verdict is not None else None

    tabs: list[dict[str, Any]] = []
    if launching is not None:
        tabs.append(
            {
                "emulator": launching,
                "label": label_of(launching) or launching_label or launching,
                "launching": True,
                "requirements_met": verdict_of(launching),
                "installed": launching not in not_installed,
            }
        )
    for emulator, items in others.items():
        tabs.append(
            {
                "emulator": emulator,
                "label": label_of(emulator) or emulator,
                "launching": False,
                "requirements_met": verdict_of(emulator),
                "installed": emulator not in not_installed,
                "rows": sorted((row_payload(entry) for entry in items), key=lambda row: row["declared_path"].lower()),
            }
        )
    return tabs


def _labels_by_emulator(options: list[EmulatorOption]) -> dict[str, list[str]]:
    """Every distinct label *options* give each identity, in their own order."""
    labels: dict[str, list[str]] = {}
    for option in options:
        if option.emulator is not None:
            names = labels.setdefault(option.emulator, [])
            if option.label not in names:
                names.append(option.label)
    return labels


def row_payload(entry: BiosFileEntry) -> dict[str, Any]:
    """One row of a game page or of another emulator's entry on the wire.

    Every field of the entry but ``cores``: the per-emulator entries are what
    the domain reads a row's own ``one_of`` and need off, and no surface reads
    them since each emulator has an entry of its own.
    """
    row = asdict(entry)
    del row["cores"]
    return row
