"""Pure helpers for SteamGridDB asset-type maps, endpoint paths, image choice, and app-id math.

Owns the stateless compute SteamGridService needs to translate
Tender's internal asset-type vocabulary into SteamGridDB API endpoints,
to choose one image among an asset's candidates, to name the artwork
cache's files, and to convert unsigned Steam app IDs into the signed int32
form ``shortcuts.vdf`` records use.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

# Discriminant returned by ``classify_resolution`` describing which
# sgdb_id (if any) should win. RomM is the source of truth.
ResolutionDecision = Literal["use_state", "use_romm", "unresolved"]

# Internal singular asset-type names mapped to the SGDB endpoint segment.
# The asset-type strings on the right are what the SGDB HTTP API exposes
# (``/heroes/game/{id}``, ``/logos/game/{id}``, ``/grids/game/{id}``,
# ``/icons/game/{id}``).
_ASSET_TYPE_TO_ENDPOINT: dict[str, str] = {
    "hero": "heroes",
    "logo": "logos",
    "grid": "grids",
    "icon": "icons",
}

# Numeric asset-type codes the frontend passes to the `get_sgdb_artwork_base64`
# endpoint. Keep in sync with the frontend's encoding.
_ASSET_TYPE_NUM_TO_NAME: dict[int, str] = {
    1: "hero",
    2: "logo",
    3: "grid",
    4: "icon",
}


def asset_type_name(type_num: int) -> str | None:
    """Return the singular asset-type name for a numeric code, or ``None``."""
    return _ASSET_TYPE_NUM_TO_NAME.get(type_num)


def asset_type_endpoint(asset_type: str) -> str | None:
    """Return the SGDB endpoint segment for a singular asset-type name, or ``None``."""
    return _ASSET_TYPE_TO_ENDPOINT.get(asset_type)


# The query each asset type is asked with; why, under "Which image" in
# docs/architecture/steam-non-steam-shortcuts.md.
_ASSET_TYPE_QUERY: dict[str, str] = {
    "grid": "?dimensions=460x215,920x430&types=static",
    "logo": "?types=static",
}

# A wide grid's sizes in the order they are preferred, and the widths a logo is
# preferred within; the reasons are the same doc section's.
_WIDE_GRID_SIZES: tuple[tuple[int, int], ...] = ((920, 430), (460, 215))
_LOGO_WIDTHS = range(640, 2048 + 1)


@dataclass(frozen=True, slots=True)
class SgdbCandidate:
    """One image SteamGridDB lists for an asset: its id, where it is, and its stated size."""

    image_id: int
    url: str
    width: int | None
    height: int | None


def sgdb_endpoint_path(asset_type: str, sgdb_game_id: int) -> str | None:
    """Build the SGDB API path for fetching artwork of a given type.

    Returns ``None`` when *asset_type* is not a recognised name. The path
    carries the asset type's query string, so callers do not need to
    special-case it.
    """
    endpoint = asset_type_endpoint(asset_type)
    if endpoint is None:
        return None
    return f"/{endpoint}/game/{sgdb_game_id}" + _ASSET_TYPE_QUERY.get(asset_type, "")


def choose_candidate(asset_type: str, data: object) -> SgdbCandidate | None:
    """Choose the image to use among *data*, an asset response's ``data`` list, by stated size.

    A candidate is an entry with a non-empty ``url`` and an integer ``id``,
    which names its cache file. The wide grid prefers 920x430, then 460x215;
    the logo prefers the first candidate between 640 and 2048 pixels wide.
    Either falls back to the first candidate, and every other asset type takes
    the first. ``None`` when *data* holds no candidate.
    """
    candidates = _candidates(data)
    if not candidates:
        return None
    preferred = None
    if asset_type == "grid":
        preferred = _preferred_wide_grid(candidates)
    elif asset_type == "logo":
        preferred = _preferred_logo(candidates)
    return preferred or candidates[0]


def _preferred_wide_grid(candidates: list[SgdbCandidate]) -> SgdbCandidate | None:
    for size in _WIDE_GRID_SIZES:
        for candidate in candidates:
            if (candidate.width, candidate.height) == size:
                return candidate
    return None


def _preferred_logo(candidates: list[SgdbCandidate]) -> SgdbCandidate | None:
    return next((candidate for candidate in candidates if candidate.width in _LOGO_WIDTHS), None)


def _candidates(data: object) -> list[SgdbCandidate]:
    if not isinstance(data, list):
        return []
    candidates: list[SgdbCandidate] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        image_id = _int_or_none(entry.get("id"))
        if not isinstance(url, str) or not url or image_id is None:
            continue
        width, height = _int_or_none(entry.get("width")), _int_or_none(entry.get("height"))
        candidates.append(SgdbCandidate(image_id=image_id, url=url, width=width, height=height))
    return candidates


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def cached_artwork_name(rom_id: int, asset_type: str, image_id: int | None) -> str:
    """The artwork cache's file name for *rom_id*'s *asset_type*.

    The name carries the SteamGridDB image id it was downloaded as
    (``{rom_id}_grid_{image_id}.png``), so what the cache holds is known as
    long as the file is there. Raises ``ValueError`` without an image id.
    """
    if image_id is None:
        raise ValueError(f"a cached {asset_type} is named by its image id")
    return f"{rom_id}_{asset_type}_{image_id}.png"


def cached_image_id(name: str, rom_id: int, asset_type: str) -> int | None:
    """The SteamGridDB image id *name* says it caches for *rom_id*'s *asset_type*, else ``None``.

    ``None`` for a name without one, which a cache an older release wrote may
    still hold.
    """
    match = re.fullmatch(rf"{rom_id}_{asset_type}_(\d+)\.png", name)
    return int(match.group(1)) if match else None


def is_cached_artwork(name: str, rom_id: int, asset_type: str) -> bool:
    """Whether *name* is a cache file of *rom_id*'s *asset_type*, under any name it has been given."""
    return name == f"{rom_id}_{asset_type}.png" or cached_image_id(name, rom_id, asset_type) is not None


_CACHE_FILE = re.compile(rf"(\d+)_({'|'.join(sorted(_ASSET_TYPE_TO_ENDPOINT))})(?:_\d+)?\.png")


def cached_slot_of(name: str) -> tuple[int, str] | None:
    """The ROM and asset type whose image *name* caches, else ``None``.

    Counts the same names :func:`is_cached_artwork` does.
    """
    match = _CACHE_FILE.fullmatch(name)
    if match is None:
        return None
    rom_id, asset_type = int(match.group(1)), match.group(2)
    return (rom_id, asset_type) if is_cached_artwork(name, rom_id, asset_type) else None


def to_signed_app_id(app_id: int) -> int:
    """Convert an unsigned Steam shortcut app ID to its signed int32 form."""
    return struct.unpack("i", struct.pack("I", app_id & 0xFFFFFFFF))[0]


def to_unsigned_app_id(app_id: int) -> int:
    """Convert a signed int32 Steam shortcut app ID back to its unsigned form.

    The inverse of :func:`to_signed_app_id`, and the direction anything reading
    ``shortcuts.vdf`` needs: the file stores the id signed, while every id the
    frontend handles — ``AddShortcut``'s return, ``collectionStore``'s keys, the
    argument every ``SteamClient.Apps.Set*`` takes — is unsigned. A negative id
    would therefore name no shortcut; that it fails silently rather than
    throwing is inferred from those APIs returning ``void`` with no success
    signal, and has not been measured.
    """
    return struct.unpack("I", struct.pack("i", app_id))[0]


def build_autocomplete_path(term: str) -> str:
    """Build the SGDB autocomplete search path for a free-text *term*.

    The term is percent-encoded so spaces and other reserved characters
    survive into the query path.
    """
    return f"/search/autocomplete/{quote(term)}"


def parse_autocomplete_results(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalise an SGDB autocomplete response into candidate dicts.

    From the SGDB shape ``{"success": true, "data": [{"id", "name",
    "release_date", ...}]}`` produce ``[{"id": int, "name": str,
    "release_year": int | None}]``. ``release_date`` is a unix timestamp
    in seconds and is converted to a calendar year (UTC). A missing,
    falsy, or malformed payload — or one with ``success: false`` —
    yields an empty list. Individual entries lacking a usable ``id`` or
    ``name`` are skipped.
    """
    if not payload or not payload.get("success"):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    results: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        game_id = entry.get("id")
        name = entry.get("name")
        if not isinstance(game_id, int) or not isinstance(name, str):
            continue
        results.append(
            {
                "id": game_id,
                "name": name,
                "release_year": _release_year(entry.get("release_date")),
            }
        )
    return results


def first_grid_url(payload: dict[str, Any] | None) -> str | None:
    """Return a thumbnail URL for the first grid in a ``/grids/...`` response.

    Prefers the ``thumb`` field, falling back to ``url``. Returns
    ``None`` for a missing/failed payload, an empty ``data`` list, or a
    first entry carrying neither field.
    """
    if not payload or not payload.get("success"):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    thumb = first.get("thumb")
    if isinstance(thumb, str) and thumb:
        return thumb
    url = first.get("url")
    if isinstance(url, str) and url:
        return url
    return None


def effective_sgdb_id(romm_sgdb_id: int | None, chosen_sgdb_id: int | None) -> int | None:
    """The SteamGridDB game a ROM's artwork comes from: RomM's id where RomM gives one, else the one the user chose.

    ``None`` when neither exists.
    """
    return romm_sgdb_id or chosen_sgdb_id or None


def classify_resolution(state_id: int | None, romm_id: int | None) -> ResolutionDecision:
    """Decide which sgdb_id wins given the state and RomM values.

    RomM is the source of truth:

    - RomM set → ``"use_romm"`` (regardless of the stored state id)
    - RomM absent, state set → ``"use_state"``
    - both absent → ``"unresolved"``
    """
    if romm_id is not None:
        return "use_romm"
    if state_id is not None:
        return "use_state"
    return "unresolved"


def _release_year(release_date: object) -> int | None:
    """Convert a unix-seconds timestamp to a UTC calendar year, or ``None``."""
    if not isinstance(release_date, int | float) or isinstance(release_date, bool):
        return None
    try:
        return datetime.fromtimestamp(release_date, tz=UTC).year
    except (OverflowError, OSError, ValueError):
        return None
