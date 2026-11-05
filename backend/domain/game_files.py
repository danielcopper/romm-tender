"""Which of a ROM's files a download fetches from RomM, and how it asks for them.

RomM files each of a ROM's files under a category (RomM 5.4.1
``RomFileCategory``): a file directly in the game's folder is ``game``, a file
in a folder named after a category directly below it takes that category, and
any other nested file takes none. A download fetches the ``game`` files and the
files in no category; every other category — manuals, soundtracks, updates,
DLC and the rest — stays on the server. Where that would leave nothing, every
file is fetched. Which files those are, and the sizes Tender shows and checks,
are docs/architecture/backend-architecture.md, "DownloadService notes".

Pure: RomM's payload arrives as a value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from domain.rom_adoption import normalize_server_path, relative_server_path

# ``None`` is the category RomM gives a nested file outside every category
# folder, such as a PS3, Wii U or DOS game's own content.
FETCHED_CATEGORIES: frozenset[str | None] = frozenset({"game", None})

# RomM's own nginx takes 16 KiB per request line (``large_client_header_buffers``),
# and a proxy in front of it may take less.
MAX_REQUEST_LINE_BYTES = 8192


@dataclass(frozen=True)
class GameFile:
    """One of a ROM's files as the download reads it.

    *file_id* is RomM's id for the file, ``None`` where the payload states none.
    *rel_path* is where the file lies inside the game's own folder, and ``""``
    where the payload does not state enough to tell (``relative_server_path``).
    """

    file_id: int | None
    file_name: str
    rel_path: str
    size_bytes: int
    category: str | None


@dataclass(frozen=True)
class GameFiles:
    """A ROM's files, split into the ones a download fetches and the ones it leaves on the server.

    The two are disjoint and together are every file RomM lists.
    """

    fetched: tuple[GameFile, ...]
    left_out: tuple[GameFile, ...]

    @property
    def fetched_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.fetched)

    @property
    def file_ids(self) -> tuple[int, ...] | None:
        """The ids the content request names, or ``None`` where it names none.

        ``None`` both where nothing is left out, so the request asks for the
        whole game, and where a fetched file has no id, so it cannot be named.
        """
        if not self.left_out:
            return None
        ids = tuple(entry.file_id for entry in self.fetched)
        if any(file_id is None for file_id in ids):
            return None
        return tuple(file_id for file_id in ids if file_id is not None)


def game_files(rom_detail: dict[str, Any]) -> GameFiles:
    """Split RomM's ``files`` of *rom_detail* into what a download fetches and what it leaves out."""
    rom_root = normalize_server_path(rom_detail.get("full_path"))
    entries = tuple(_game_file(entry, rom_root) for entry in rom_detail.get("files") or [])
    fetched = tuple(entry for entry in entries if entry.category in FETCHED_CATEGORIES)
    if not fetched:
        return GameFiles(fetched=entries, left_out=())
    return GameFiles(fetched=fetched, left_out=tuple(entry for entry in entries if entry not in fetched))


def download_size_bytes(rom_detail: dict[str, Any]) -> int:
    """What a download of *rom_detail* fetches, in bytes: the sum of the files it fetches.

    Where it leaves nothing out it fetches the whole game, whose size RomM
    states itself (``fs_size_bytes``); a payload that states none is summed.
    """
    files = game_files(rom_detail)
    if files.left_out:
        return files.fetched_bytes
    return whole_game_bytes(rom_detail)


def whole_game_bytes(rom_detail: dict[str, Any]) -> int:
    """RomM's size of the whole game, or the sum of every file it lists where it states none."""
    stated = int(rom_detail.get("fs_size_bytes") or 0)
    if stated:
        return stated
    files = game_files(rom_detail)
    return files.fetched_bytes + sum(entry.size_bytes for entry in files.left_out)


def _game_file(entry: dict[str, Any], rom_root: str) -> GameFile:
    file_id = entry.get("id")
    name = entry.get("file_name") or ""
    return GameFile(
        file_id=file_id if isinstance(file_id, int) and not isinstance(file_id, bool) else None,
        file_name=name,
        rel_path=relative_server_path(rom_root, entry.get("file_path"), name) if name else "",
        size_bytes=int(entry.get("file_size_bytes") or 0),
        category=entry.get("category"),
    )


def file_ids_query(file_ids: tuple[int, ...]) -> str:
    """The query string that asks RomM's content endpoint for exactly *file_ids*."""
    return "?file_ids=" + ",".join(str(file_id) for file_id in file_ids)


def request_line_fits(url: str) -> bool:
    """Whether a GET of *url* stays within ``MAX_REQUEST_LINE_BYTES``.

    The whole URL is counted, scheme and host included: a request through a
    proxy carries it so, and one sent directly carries less.
    """
    return len(f"GET {url} HTTP/1.1".encode()) <= MAX_REQUEST_LINE_BYTES


def is_top_level(entry: GameFile) -> bool:
    """Whether *entry* lies directly in the game's folder."""
    return bool(entry.rel_path) and os.path.dirname(entry.rel_path) == ""
