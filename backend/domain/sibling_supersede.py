"""Which installed versions of a game give way when another version of it is installed.

At most one installed version per shortcut binding, whichever route — a
download or an adoption — produced it. Reading the group and acting on the
answer are the download service's concern; which sibling goes, and how, is
decided here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.rom import Rom
    from domain.rom_install import RomInstall


@dataclass(frozen=True)
class SiblingSupersede:
    """The siblings an install displaces: ``removed`` lose files and record, ``handed_over`` only the record."""

    removed: tuple[int, ...]
    handed_over: tuple[int, ...]


def supersede_siblings(
    shortcut_app_id: int | None,
    siblings: Iterable[tuple[Rom, RomInstall | None]],
    adopted_path: str | None,
) -> SiblingSupersede:
    """Split *siblings* — the other members of the group, each with its install — by what an install displaces.

    *shortcut_app_id* is the binding of the version being installed. A sibling
    with no install is not displaced, and neither is one bound to a different
    shortcut: a second shortcut of the same game, which still launches that
    sibling's files.
    Every other installed sibling is removed, unless its install is recorded at
    *adopted_path* — the content an adoption is about to record as its own.
    Removing that one would delete the very file being adopted, so it hands its
    record over and keeps its files. A download passes ``None``: it replaces what
    is at its path, so nothing there is kept.
    """
    removed: list[int] = []
    handed_over: list[int] = []
    for sibling, install in siblings:
        if install is None:
            continue
        if sibling.shortcut_app_id is not None and sibling.shortcut_app_id != shortcut_app_id:
            continue
        if adopted_path is not None and adopted_path in (install.file_path, install.rom_dir):
            handed_over.append(sibling.rom_id)
        else:
            removed.append(sibling.rom_id)
    return SiblingSupersede(removed=tuple(removed), handed_over=tuple(handed_over))
