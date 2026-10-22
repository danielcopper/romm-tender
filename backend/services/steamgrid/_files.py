"""The SteamGridDB artwork cache: which image each ROM's slot is cached as, and the downloads that fill it.

One instance serves the whole package, because it keeps an index of the
cache's names that every download and removal keeps current; a second
instance would answer from an index the first one's downloads never reach.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from domain.sgdb_artwork import (
    cached_artwork_name,
    cached_image_id,
    cached_slot_of,
    choose_candidate,
    sgdb_endpoint_path,
)
from lib.errors import SgdbApiError, SgdbKeyUnsendableError, SgdbRateLimitedError
from models.shortcut_artwork import SlotAnswer

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from domain.sgdb_artwork import SgdbCandidate
    from services.protocols import SgdbArtworkCache, SteamGridDbApi


@dataclass(frozen=True)
class CachedArtwork:
    """A file the artwork cache holds, and the SteamGridDB image id its name carries."""

    path: str
    image_id: int


class NoArtwork(Enum):
    """Why a download answered no file: SteamGridDB's list is empty, or the answer or download did not succeed."""

    NONE = "none"
    FAILED = "failed"


@dataclass(frozen=True)
class SgdbArtworkFilesConfig:
    """Wiring for :class:`SgdbArtworkFiles`: the SteamGridDB transport, the cache's file seams and the logger.

    ``stored`` is told of every download that replaced a slot's cache file,
    with the file it replaced, while that file is still there.
    """

    sgdb_api: SteamGridDbApi
    cache: SgdbArtworkCache
    logger: logging.Logger
    stored: Callable[[int, str, CachedArtwork | None, CachedArtwork], None] | None = None


class SgdbArtworkFiles:
    """The artwork cache of every ROM's SteamGridDB slots, named by image id (``{rom_id}_{asset}_{image_id}.png``)."""

    def __init__(self, *, config: SgdbArtworkFilesConfig) -> None:
        self._sgdb_api = config.sgdb_api
        self._cache = config.cache
        self._logger = config.logger
        self._stored = config.stored
        # Per ROM and asset type, the names in the artwork cache: listed once,
        # then kept by this object's own downloads and removals, so a lookup
        # does not list the whole cache. Lookups run on executor threads and
        # nothing here takes a lock, so each entry is replaced rather than
        # changed: a reader holds a frozen snapshot. A note two threads race to
        # lose costs one download more, nothing else.
        self._slot_cache_names: dict[tuple[int, str], frozenset[str]] | None = None

    def download(self, sgdb_game_id, rom_id, asset_type) -> CachedArtwork | NoArtwork:
        """The cached file of *rom_id*'s *asset_type*, downloaded first when it is not cached.

        The image is chosen among SteamGridDB's candidates by ``choose_candidate``.
        Answers ``NONE`` when SteamGridDB's list is empty, and ``FAILED`` for an
        unsuccessful answer, a list with no usable entry or a failed download;
        lets the API's errors through. A downloaded image replaces every other
        cache file of that slot.
        """
        cached = self.cached(rom_id, asset_type)
        if cached is not None:
            return cached
        return self.fetch(sgdb_game_id, rom_id, asset_type)

    def fetch(self, sgdb_game_id, rom_id, asset_type) -> CachedArtwork | NoArtwork:
        """:meth:`download` without its cache check.

        It drops the slot's other cache files only once the download succeeded.
        """
        path = sgdb_endpoint_path(asset_type, sgdb_game_id)
        if path is None:
            return NoArtwork.FAILED
        result = self._sgdb_api.request(path)
        if not result or not result.get("success"):
            return NoArtwork.FAILED
        if not result.get("data"):
            return NoArtwork.NONE
        candidate = choose_candidate(asset_type, result["data"])
        if candidate is None:
            return NoArtwork.FAILED
        return self.store(rom_id, asset_type, candidate)

    def store(self, rom_id, asset_type, candidate: SgdbCandidate) -> CachedArtwork | NoArtwork:
        """Download *candidate* as *rom_id*'s *asset_type*; ``FAILED`` when the download did not succeed.

        Once it has, ``stored`` is told and the slot's other cache files go.
        Lets a 429 through.
        """
        target = os.path.join(self._cache.cache_dir(), cached_artwork_name(rom_id, asset_type, candidate.image_id))
        previous = self.cached(rom_id, asset_type)
        if not self._sgdb_api.download_image(candidate.url, target):
            return NoArtwork.FAILED
        stored = CachedArtwork(path=target, image_id=candidate.image_id)
        self._note_cache_name(rom_id, asset_type, os.path.basename(target), held=True)
        if self._stored is not None:
            self._stored(int(rom_id), asset_type, previous, stored)
        self.drop(rom_id, asset_type, keep=os.path.basename(target))
        return stored

    def fetch_or_answer(
        self, rom_id: int, asset_type: str, sgdb_id: int, anew: bool = False
    ) -> CachedArtwork | NoArtwork | SlotAnswer:
        """*rom_id*'s cached *asset_type*, downloaded first when needed or *anew*, or what a 429, 401 or 403 answers.

        SteamGridDB answers 404 ("Game not found.") for a game id it does not
        know, as its own client library, npm's steamgriddb, documents; that is
        SteamGridDB having none.
        """
        download = self.fetch if anew else self.download
        try:
            return download(sgdb_id, rom_id, asset_type)
        except SgdbRateLimitedError:
            return SlotAnswer.RATE_LIMITED
        except SgdbApiError as e:
            if e.status_code == 404:
                return NoArtwork.NONE
            if e.status_code in (401, 403) or isinstance(e, SgdbKeyUnsendableError):
                return SlotAnswer.KEY_REFUSED
            self._logger.warning(f"SGDB {asset_type} fetch failed for rom_id={rom_id}: HTTP {e.status_code}")
        except Exception as e:
            self._logger.warning(f"SGDB {asset_type} fetch failed for rom_id={rom_id}: {e}")
        return NoArtwork.FAILED

    def cached(self, rom_id, asset_type) -> CachedArtwork | None:
        """The cache file of *rom_id*'s *asset_type*, or ``None`` when the cache holds none.

        A file whose name carries no image id is not counted, so it is
        downloaded again and replaced; nor is a name the cache no longer holds.
        """
        art_dir = self._cache.cache_dir()
        for name in self._cache_names(rom_id, asset_type):
            image_id = cached_image_id(name, rom_id, asset_type)
            if image_id is None:
                continue
            path = os.path.join(art_dir, name)
            if self._cache.exists(path):
                return CachedArtwork(path=path, image_id=image_id)
            self._note_cache_name(rom_id, asset_type, name, held=False)
        return None

    def remove_image(self, rom_id: int, asset_type: str, image_id: int) -> None:
        """Remove *rom_id*'s cached *asset_type* named by *image_id*, and no other file of the slot (sync)."""
        name = cached_artwork_name(rom_id, asset_type, image_id)
        try:
            self._cache.remove_file(os.path.join(self._cache.cache_dir(), name))
        except FileNotFoundError:
            pass
        except OSError as e:
            self._logger.warning(f"Failed to clear cached artwork {name}: {e}")
            return
        self._note_cache_name(rom_id, asset_type, name, held=False)

    def drop(self, rom_id, asset_type, *, keep: str | None = None) -> None:
        """Remove every cache file of *rom_id*'s *asset_type* but *keep* (sync)."""
        art_dir = self._cache.cache_dir()
        for name in self._cache_names(rom_id, asset_type):
            if name == keep:
                continue
            try:
                self._cache.remove_file(os.path.join(art_dir, name))
            except OSError as e:
                self._logger.warning(f"Failed to clear cached artwork {name}: {e}")
                continue
            self._note_cache_name(rom_id, asset_type, name, held=False)

    def _cache_names(self, rom_id, asset_type) -> list[str]:
        """The names *rom_id*'s *asset_type* may be cached under, sorted; the cache is listed only the first time."""
        rom_id = int(rom_id)
        names = self._slot_cache_names
        if names is None:
            listed: dict[tuple[int, str], set[str]] = {}
            for name in self._cache.listdir(self._cache.cache_dir()):
                slot = cached_slot_of(name)
                if slot is not None:
                    listed.setdefault(slot, set()).add(name)
            names = self._slot_cache_names = {slot: frozenset(held) for slot, held in listed.items()}
        return sorted(names.get((rom_id, asset_type), ()))

    def _note_cache_name(self, rom_id, asset_type, name: str, *, held: bool) -> None:
        """Note that the cache now holds *name* for *rom_id*'s *asset_type*, or no longer does."""
        names = self._slot_cache_names
        if names is None:
            return
        key = (int(rom_id), asset_type)
        before = names.get(key, frozenset())
        names[key] = before | {name} if held else before - {name}
