"""What one download asks RomM for: the files it fetches, and whether it names them.

Shared by the start of a download, which weighs the fetch, and by the transfer,
which makes it, so both read one answer from the same ROM detail (#2223 D27,
D30).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.game_files import GameFile, GameFiles, download_size_bytes, game_files, request_line_fits, whole_game_bytes
from domain.rom_files import is_multi_file_download

if TYPE_CHECKING:
    from services.protocols import RommRomReader


@dataclass(frozen=True)
class DownloadFetch:
    """One download's request to RomM and what it brings.

    *file_ids* is what the request names, ``None`` for the whole game. Where the
    files a download leaves out cannot be left out by the request — the list
    would make the request line too long, or a fetched file has no id — the
    whole game is fetched (*whole*) and the files are removed once it is
    unpacked (``DownloadUnpacker.finish_multi``); *size_bytes*, what the
    request brings, is then the whole game's.
    """

    files: GameFiles
    file_ids: tuple[int, ...] | None
    whole: bool
    zipped: bool
    size_bytes: int

    @property
    def single_file(self) -> GameFile | None:
        """The one file RomM serves as itself, where it serves one and the payload names it."""
        if self.zipped or len(self.files.fetched) != 1:
            return None
        return self.files.fetched[0]


def plan_fetch(rom_detail: dict[str, Any], file_name: str, romm_api: RommRomReader) -> DownloadFetch:
    """The fetch a download of *rom_detail* under *file_name* makes."""
    files = game_files(rom_detail)
    zipped = is_multi_file_download(rom_detail)
    size = download_size_bytes(rom_detail)
    if not files.left_out:
        return DownloadFetch(files=files, file_ids=None, whole=False, zipped=zipped, size_bytes=size)
    file_ids = files.file_ids
    if file_ids is not None and request_line_fits(
        romm_api.rom_content_url(int(rom_detail.get("id") or 0), file_name, file_ids)
    ):
        return DownloadFetch(files=files, file_ids=file_ids, whole=False, zipped=zipped, size_bytes=size)
    every_file = files.fetched + files.left_out
    return DownloadFetch(
        files=files,
        file_ids=None,
        whole=True,
        zipped=len(every_file) > 1,
        size_bytes=whole_game_bytes(rom_detail),
    )
