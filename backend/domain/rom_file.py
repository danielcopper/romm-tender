"""Whether RomM holds a file for a ROM it lists.

RomM keeps a ROM whose file has left its disk, and lists a physical game that
never had one. Its ROM payload carries ``has_file_on_disk``, which is
``not is_physical and not missing_from_fs`` (RomM ``models/rom.py``, sent since
5.3.0), and it answers a content download of such a ROM with a 404 ("ROM … has
no file to download"). Neither ``missing_from_fs`` nor ``is_physical`` is read
on its own: the one field already says what both mean for a download.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.refusal import NamedDomainRefused

if TYPE_CHECKING:
    from collections.abc import Mapping


def has_file_on_romm(rom: Mapping[str, Any]) -> bool:
    """Whether the RomM-shaped *rom* has a file RomM can serve.

    Only an explicit ``false`` means no file. A payload without the key —
    a reconstructed row, a fake, a field RomM did not send — reads as having
    one, which is what every ROM read as before the field was read at all.
    """
    return rom.get("has_file_on_disk") is not False


# Holds for a physical game too, which never had a file a rescan could find.
NO_FILE_ON_ROMM_MESSAGE = "No file on RomM. If the file was moved or renamed, rescan the library in RomM, then sync."


class NoFileOnRomm(NamedDomainRefused):
    """RomM says the ROM has no file, so there is nothing to download or switch a shortcut onto."""

    reason = "no_file_on_romm"
