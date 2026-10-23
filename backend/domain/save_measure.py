"""What a local save file measures: its content hash, its size and its content size.

The content size is romm-gavel's (``SPEC.md``, "Inputs", at v2.0.0): a file's
size less the trailing run of the byte it ends with, when that byte is ``0xFF``
or ``0x00``, the two fill bytes. A file ending in any other byte has a content
size equal to its size; a file of fill only, like an empty one, has content
size 0. It is taken over the file's raw bytes, a zip save's included, while a
zip's content hash is RomM's per-entry one (:mod:`domain.save_hash`).

The pure half lives here: the trailing run a stream of chunks ends with, and the
content size that follows from it. Reading the file is the ``SaveFileStore``
adapter's. No I/O, stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass

FILL_BYTES = frozenset({0x00, 0xFF})


@dataclass(frozen=True, slots=True)
class SaveFileMeasure:
    """One read of a local save file: its content hash, size and content size."""

    content_hash: str
    size: int
    content_size: int


@dataclass(frozen=True, slots=True)
class TrailingRun:
    """The run of one repeated byte the bytes read so far end with.

    ``byte`` is ``None`` until a byte has been read.
    """

    byte: int | None = None
    length: int = 0

    def after(self, chunk: bytes) -> TrailingRun:
        """The run once *chunk* is read after the bytes this run ends."""
        if not chunk:
            return self
        last = chunk[-1]
        length = len(chunk) - len(chunk.rstrip(bytes((last,))))
        if length == len(chunk) and last == self.byte:
            return TrailingRun(last, self.length + length)
        return TrailingRun(last, length)


def content_size(size: int, trailing: TrailingRun) -> int:
    """The content size of a file of *size* bytes that ends with *trailing*."""
    if trailing.byte in FILL_BYTES:
        return size - trailing.length
    return size
