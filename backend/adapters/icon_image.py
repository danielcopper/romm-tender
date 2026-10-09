"""Shortcut icons at no more than :data:`ICON_SIZE` on their longer side, and Tender's logo.

Implements ``services.protocols.IconDownscaleFn`` and
``services.protocols.TenderLogoFn``. Why every icon is downscaled, and why to
that size, is docs/architecture/steam-non-steam-shortcuts.md, "Shortcut icons".

The vendored ``png`` is imported on the first downscale, not at module scope: a
copy that will not load under the device's Python then costs the downscale and
nothing else (``.claude/rules/vendored-assets.md``).
"""

from __future__ import annotations

import io
import os
import struct
from typing import TYPE_CHECKING, Any

from domain.shortcut_icon import LOGO_ICON_NAME

if TYPE_CHECKING:
    import logging
    from types import ModuleType

ICON_SIZE = 64

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ICO_HEADER = b"\x00\x00\x01\x00"
_ICO_ENTRY = struct.Struct("<BBBBHHII")


class IconImages:
    """Downscales SteamGridDB's icons and reads the logo shipped under *code_dir*'s ``defaults/``."""

    def __init__(self, *, code_dir: str, logger: logging.Logger) -> None:
        self._code_dir = code_dir
        self._logger = logger
        self._png: ModuleType | None = None
        self._png_missing = False

    def downscale(self, data: bytes) -> bytes | None:
        """Answer *data* as an icon no larger than :data:`ICON_SIZE` on its longer side; ``None`` when it is none.

        A PNG larger than that is area-averaged down to it, keeping its aspect
        ratio, and answered as an 8-bit RGBA PNG; one at or below it is answered
        unchanged, since scaling it would only grow it. From an ``.ico`` the PNG
        entry closest to the size at or above it is taken (the largest one when
        none is that large) and treated the same way; an ``.ico`` with no PNG
        entry is answered unchanged. ``None`` for anything else, and for a PNG
        that does not decode. Where the vendored ``png`` cannot be loaded every
        icon is answered unchanged, and that is logged once. Never raises.
        """
        payload = _png_payload(data)
        if payload is None:
            return data if _is_ico(data) else None
        png = self._png_module()
        if png is None:
            return data
        try:
            width, height, rows, _info = png.Reader(bytes=payload).asRGBA8()
            # pypng reads the rows only when they are iterated, so a file whose
            # header reads but whose image data is broken fails only here.
            source = [bytes(row) for row in rows]
            if max(width, height) <= ICON_SIZE:
                return payload
            target_w, target_h = _fit(width, height)
            pixels = _area_average(source, width, height, target_w, target_h)
            out = io.BytesIO()
            # ``greyscale`` must be passed: pypng's default is a sentinel class it reads as
            # true, and its signature types the parameter as that class, so the options
            # go in as a dict the type checker does not hold to it.
            options: dict[str, Any] = {"greyscale": False, "alpha": True, "bitdepth": 8, "compression": 9}
            png.Writer(target_w, target_h, **options).write(out, pixels)
        except Exception:
            return None
        return out.getvalue()

    def tender_logo(self) -> bytes | None:
        """Read the logo shipped at ``defaults/``; ``None``, with a warning, when it cannot be read."""
        try:
            path = os.path.join(self._code_dir, "defaults", LOGO_ICON_NAME)
            with open(path, "rb") as handle:
                return handle.read()
        except OSError as e:
            self._logger.warning(f"Shortcut icons: cannot read Tender's logo at {path}: {e}")
            return None

    def _png_module(self) -> ModuleType | None:
        if self._png is None and not self._png_missing:
            try:
                from _vendor import png
            except Exception as e:
                self._png_missing = True
                self._logger.warning(f"Shortcut icons: the vendored png did not load, icons are written full size: {e}")
                return None
            self._png = png
        return self._png


def _is_ico(data: bytes) -> bool:
    return data.startswith(_ICO_HEADER) and len(data) >= 6


def _png_payload(data: bytes) -> bytes | None:
    """The PNG inside *data*: *data* itself, an ``.ico``'s best PNG entry, or ``None``."""
    if data.startswith(_PNG_SIGNATURE):
        return data
    if not _is_ico(data):
        return None
    count = struct.unpack_from("<H", data, 4)[0]
    entries: list[tuple[int, bytes]] = []
    for index in range(count):
        start = 6 + index * _ICO_ENTRY.size
        if start + _ICO_ENTRY.size > len(data):
            break
        width, height, _colours, _reserved, _planes, _bpp, size, offset = _ICO_ENTRY.unpack_from(data, start)
        image = data[offset : offset + size]
        if len(image) == size and image.startswith(_PNG_SIGNATURE):
            # An .ico stores 256 as 0 in its one-byte size fields.
            entries.append((max(width or 256, height or 256), image))
    if not entries:
        return None
    large_enough = [entry for entry in entries if entry[0] >= ICON_SIZE]
    if large_enough:
        return min(large_enough, key=lambda entry: entry[0])[1]
    return max(entries, key=lambda entry: entry[0])[1]


def _fit(width: int, height: int) -> tuple[int, int]:
    """The size *width* x *height* takes inside an :data:`ICON_SIZE` square, aspect kept."""
    if width >= height:
        return ICON_SIZE, max(1, round(height * ICON_SIZE / width))
    return max(1, round(width * ICON_SIZE / height)), ICON_SIZE


def _area_average(rows: list[bytes], width: int, height: int, target_w: int, target_h: int) -> list[Any]:
    """Average each target pixel's source block, weighting colour by alpha.

    Weighting by alpha keeps a transparent pixel's colour — usually black — from
    darkening the edge of the shape around it.
    """
    out: list[Any] = []
    for ty in range(target_h):
        y0 = ty * height // target_h
        y1 = max(y0 + 1, (ty + 1) * height // target_h)
        line = bytearray(target_w * 4)
        for tx in range(target_w):
            x0 = tx * width // target_w
            x1 = max(x0 + 1, (tx + 1) * width // target_w)
            red = green = blue = alpha = count = 0
            for y in range(y0, y1):
                row = rows[y]
                for x in range(x0 * 4, x1 * 4, 4):
                    weight = row[x + 3]
                    red += row[x] * weight
                    green += row[x + 1] * weight
                    blue += row[x + 2] * weight
                    alpha += weight
                    count += 1
            if alpha:
                line[tx * 4 : tx * 4 + 4] = bytes((red // alpha, green // alpha, blue // alpha, alpha // count))
        out.append(line)
    return out
