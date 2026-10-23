"""The content size: a file's size less the trailing run of a fill byte it ends with."""

from __future__ import annotations

import pytest

from domain.save_measure import TrailingRun, content_size


def _content_size(*chunks: bytes) -> int:
    trailing = TrailingRun()
    for chunk in chunks:
        trailing = trailing.after(chunk)
    return content_size(sum(len(chunk) for chunk in chunks), trailing)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", 0),
        (b"\xff" * 8, 0),
        (b"\x00" * 8, 0),
        (b"save" + b"\xff" * 12, 4),
        (b"save" + b"\x00" * 12, 4),
        (b"save", 4),
        (b"save" + b"\xff" * 3 + b"!", 8),
        (b"\x00" * 4 + b"save" + b"\xff" * 4, 8),
        (b"save" + b"\x00" * 2 + b"\xff" * 2, 6),
    ],
    ids=[
        "empty",
        "only-0xff",
        "only-0x00",
        "0xff-fill",
        "0x00-fill",
        "no-fill",
        "fill-inside-not-at-the-end",
        "leading-fill-counts",
        "only-the-last-bytes-run",
    ],
)
def test_the_content_size_drops_only_the_trailing_run_of_a_fill_byte(data: bytes, expected: int) -> None:
    assert _content_size(data) == expected


def test_a_run_of_any_other_byte_is_content() -> None:
    assert _content_size(b"save" + b"\x01" * 12) == 16


def test_a_run_crossing_chunks_is_counted_whole() -> None:
    assert _content_size(b"save\xff\xff", b"\xff\xff\xff", b"", b"\xff") == 4


def test_a_chunk_ending_in_another_byte_ends_the_run() -> None:
    assert _content_size(b"\xff\xff\xff", b"ab\xff") == 5


def test_a_whole_chunk_of_a_different_fill_byte_starts_a_new_run() -> None:
    assert _content_size(b"save\x00\x00", b"\xff\xff") == 6
