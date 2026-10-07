"""The SGDB artwork cache fake, read on one thread while another thread writes to it.

``SteamGridService`` downloads a picked game's four asset types on four executor
threads at once, so one thread's download lands in ``files`` while another is
still answering ``exists`` for its own asset. Each test here holds a read at a
known point inside the fake, lets a write from a second thread land there, and
only then lets the read go on — so the interleaving is the same on every run.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, SupportsIndex

import pytest

from fakes.fake_sgdb_artwork_cache import FakeSgdbArtworkCache

if TYPE_CHECKING:
    from collections.abc import Callable

_WAIT_SECONDS = 5.0
_ART = "/runtime/artwork"
_HERO = f"{_ART}/42_hero.png"
_STAGED = frozenset({"/elsewhere/a.png", _HERO, "/elsewhere/b.png"})


def _add_a_download(cache: FakeSgdbArtworkCache) -> None:
    cache.files["/elsewhere/c.png"] = b"c"


def _remove_a_file(cache: FakeSgdbArtworkCache) -> None:
    cache.remove_file("/elsewhere/b.png")


class TestAWriteFromAnotherThreadMidRead:
    @pytest.mark.parametrize(
        ("read", "answer"),
        [
            pytest.param(lambda cache: cache.exists(f"{_ART}/42_logo.png"), False, id="exists"),
            pytest.param(lambda cache: cache.is_dir("/runtime"), True, id="is_dir"),
            pytest.param(lambda cache: cache.listdir(_ART), ["42_hero.png"], id="listdir"),
        ],
    )
    @pytest.mark.parametrize(
        ("write", "files_after"),
        [
            pytest.param(_add_a_download, _STAGED | {"/elsewhere/c.png"}, id="add"),
            pytest.param(_remove_a_file, _STAGED - {"/elsewhere/b.png"}, id="remove"),
        ],
    )
    def test_the_read_answers_and_the_write_lands(
        self,
        read: Callable[[FakeSgdbArtworkCache], object],
        answer: object,
        write: Callable[[FakeSgdbArtworkCache], None],
        files_after: frozenset[str],
    ) -> None:
        reached = threading.Event()
        written = threading.Event()

        class _PausingPath(str):
            """A stored path whose first prefix comparison waits for the other thread's write."""

            __slots__ = ()

            def startswith(
                self,
                prefix: str | tuple[str, ...],
                start: SupportsIndex | None = None,
                end: SupportsIndex | None = None,
                /,
            ) -> bool:
                if not reached.is_set():
                    reached.set()
                    written.wait(_WAIT_SECONDS)
                return str.startswith(self, prefix, start, end)

        cache = FakeSgdbArtworkCache(
            cache_root="/runtime",
            files={_PausingPath("/elsewhere/a.png"): b"a", _HERO: b"hero", "/elsewhere/b.png": b"b"},
        )

        def writer() -> None:
            reached.wait(_WAIT_SECONDS)
            write(cache)
            written.set()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            result = read(cache)
        finally:
            thread.join(_WAIT_SECONDS)

        assert reached.is_set(), "the read never compared a stored path, so no write landed mid-read"
        assert result == answer
        assert set(cache.files) == files_after


class TestAFileRemovedMidReadBytes:
    def test_reads_as_missing_or_as_its_bytes_never_as_a_key_error(self) -> None:
        cache = FakeSgdbArtworkCache(cache_root="/runtime", files={_HERO: b"hero"})
        lookups = 0

        class _RemovingPath(str):
            """A path whose second lookup first has another thread remove the file it names."""

            __slots__ = ()

            def __hash__(self) -> int:
                nonlocal lookups
                lookups += 1
                if lookups == 2:
                    thread = threading.Thread(target=cache.remove_file, args=(_HERO,))
                    thread.start()
                    thread.join(_WAIT_SECONDS)
                return str.__hash__(self)

        try:
            result: bytes | None = cache.read_bytes(_RemovingPath(_HERO))
        except FileNotFoundError:
            result = None

        assert result in (b"hero", None)
