"""Tests for adapters/icon_image.py — shortcut icons scaled to no more than 64x64."""

from __future__ import annotations

import io
import struct

import pytest
from _vendor import png

from adapters.icon_image import ICON_SIZE, downscale_icon, read_tender_logo


def _png(width, height, pixel=(200, 30, 40, 255), **writer_options):
    """An RGBA PNG of one colour, or one written with *writer_options* from 8-bit RGBA rows."""
    rows = [list(pixel) * width for _ in range(height)]
    return _written(width, height, rows, greyscale=False, alpha=True, bitdepth=8, **writer_options)


def _decode(data):
    width, height, rows, _info = png.Reader(bytes=data).asRGBA8()
    return width, height, [bytes(row) for row in rows]


def _ico(*images):
    """An .ico whose entries carry *images* — ``(size, payload)`` pairs, 256 stored as 0."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payloads = b"", b""
    for size, payload in images:
        stored = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(payload), offset)
        payloads += payload
        offset += len(payload)
    return header + entries + payloads


class TestALargerPng:
    def test_comes_back_at_icon_size_in_rgba(self):
        width, height, rows = _decode(downscale_icon(_png(256, 256)))
        assert (width, height) == (ICON_SIZE, ICON_SIZE)
        assert rows[0][:4] == bytes((200, 30, 40, 255))

    def test_keeps_its_aspect_ratio(self):
        assert _decode(downscale_icon(_png(200, 100)))[:2] == (64, 32)
        assert _decode(downscale_icon(_png(100, 400)))[:2] == (16, 64)

    def test_a_transparent_neighbour_does_not_darken_the_colour(self):
        # Each 2x2 block holds one opaque red pixel and three transparent black ones.
        rows = [[255, 0, 0, 255, 0, 0, 0, 0] * 64 if y % 2 == 0 else [0, 0, 0, 0] * 128 for y in range(128)]
        _w, _h, scaled = _decode(downscale_icon(_written(128, 128, rows, greyscale=False, alpha=True, bitdepth=8)))

        assert scaled[10][40:44] == bytes((255, 0, 0, 63))

    def test_a_wholly_transparent_area_stays_transparent(self):
        _w, _h, rows = _decode(downscale_icon(_png(128, 128, pixel=(10, 20, 30, 0))))
        assert set(rows[5]) == {0}

    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(
                lambda: _written(128, 128, [[1, 0] * 64] * 128, greyscale=True, bitdepth=1), id="greyscale-1bit"
            ),
            pytest.param(
                lambda: _written(128, 128, [[3, 0] * 64] * 128, greyscale=True, bitdepth=2), id="greyscale-2bit"
            ),
            pytest.param(
                lambda: _written(128, 128, [[200, 255] * 128] * 128, greyscale=True, alpha=True, bitdepth=8),
                id="greyscale-alpha-8bit",
            ),
            pytest.param(
                lambda: _written(128, 128, [[10, 20, 30] * 128] * 128, greyscale=False, bitdepth=8), id="rgb-8bit"
            ),
            pytest.param(
                lambda: _written(128, 128, [[0, 1] * 64] * 128, palette=[(255, 0, 0, 255), (0, 0, 255, 0)], bitdepth=4),
                id="palette-4bit-transparent",
            ),
            pytest.param(
                lambda: _written(128, 128, [[65535, 0, 0] * 128] * 128, greyscale=False, bitdepth=16),
                id="rgb-16bit",
            ),
            pytest.param(lambda: _png(128, 128, interlace=True), id="interlaced"),
        ],
    )
    def test_every_png_form_decodes(self, make):
        assert _decode(downscale_icon(make()))[:2] == (ICON_SIZE, ICON_SIZE)


def _written(width, height, rows, **options):
    out = io.BytesIO()
    png.Writer(width, height, **options).write(out, rows)
    return out.getvalue()


class TestAPngAtOrBelowIconSize:
    @pytest.mark.parametrize("size", [ICON_SIZE, 32, 1])
    def test_comes_back_unchanged(self, size):
        data = _png(size, size)
        assert downscale_icon(data) == data


class TestAnIco:
    def test_gives_the_entry_closest_to_icon_size_at_or_above_it(self):
        exact = _png(64, 64, pixel=(1, 2, 3, 255))
        data = _ico((32, _png(32, 32)), (64, exact), (256, _png(256, 256)))
        assert downscale_icon(data) == exact

    def test_scales_down_the_smallest_entry_above_icon_size(self):
        data = _ico((32, _png(32, 32)), (128, _png(128, 128, pixel=(9, 8, 7, 255))), (256, _png(256, 256)))
        width, height, rows = _decode(downscale_icon(data))
        assert (width, height) == (ICON_SIZE, ICON_SIZE)
        assert rows[0][:4] == bytes((9, 8, 7, 255))

    def test_gives_its_largest_entry_when_none_reaches_icon_size(self):
        largest = _png(48, 48)
        assert downscale_icon(_ico((16, _png(16, 16)), (48, largest))) == largest

    def test_with_no_png_entry_comes_back_unchanged(self):
        bitmap = b"\x28\x00\x00\x00" + b"\x00" * 60
        data = _ico((32, bitmap))
        assert downscale_icon(data) == data

    def test_ignores_an_entry_that_points_past_the_file(self):
        data = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 64, 64, 0, 0, 1, 32, 500, 9999)
        assert downscale_icon(data) == data

    def test_ignores_entries_its_count_claims_but_the_file_does_not_hold(self):
        data = struct.pack("<HHH", 0, 1, 5)
        assert downscale_icon(data) == data

    def test_with_a_header_and_nothing_else_comes_back_unchanged(self):
        assert downscale_icon(b"\x00\x00\x01\x00") == b"\x00\x00\x01\x00"


class TestWhatItCannotRead:
    def test_another_format_comes_back_unchanged(self):
        assert downscale_icon(b"GIF89a....") == b"GIF89a...."

    def test_a_png_that_does_not_decode_comes_back_unchanged(self):
        broken = _png(256, 256)[:200]
        assert downscale_icon(broken) == broken


class TestReadTenderLogo:
    def test_reads_the_logo_shipped_under_defaults(self, tmp_path):
        (tmp_path / "defaults").mkdir()
        (tmp_path / "defaults" / "tender-icon.png").write_bytes(b"logo")

        assert read_tender_logo(str(tmp_path)) == b"logo"

    def test_answers_none_where_there_is_no_logo(self, tmp_path):
        assert read_tender_logo(str(tmp_path)) is None

    def test_the_shipped_logo_is_already_at_icon_size(self):
        import pathlib

        repo = pathlib.Path(__file__).resolve().parents[2]
        logo = read_tender_logo(str(repo))

        assert logo is not None
        assert _decode(logo)[:2] == (ICON_SIZE, ICON_SIZE)
        assert downscale_icon(logo) == logo
