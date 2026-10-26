"""Pure decoding of the ``platform_slug → name`` cache, and the names it is written from.

The name the sync files a platform under, and the one this cache holds, is the
``name`` RomM's platform listing gives it (:func:`listed_platform_names`), never
the ``custom_name`` a user set in RomM or the ``display_name`` that prefers it
(#2564 D2). The cache is a JSON object stored as a single ``kv_config`` TEXT
value, rewritten from the run's listing by each sync so its readers get a
platform's name without asking RomM — among them the game-detail page's platform
name, a platform removal's answer, whose name the frontend uses to find that
platform's Steam collection, the next sync, which names the Steam collection of
a platform RomM no longer lists by it, a BIOS row a link of Tender's into
another platform's store folder serves, and the platform answer a download is
admitted with, whose name the download queue shows. Anything that turns the
stored string back into the ``slug → name`` mapping belongs here; reading the
value out of ``kv_config`` stays in the service.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def decode_platform_names(raw: str | None) -> dict[str, str]:
    """Decode the cached ``platform_slug → name`` mapping.

    Returns ``{}`` when *raw* is absent, empty, not valid JSON, or decodes to
    anything other than an object — callers degrade to the slug in every such
    case rather than surfacing a corrupt cache.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def listed_platform_names(platforms: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Every listed platform's ``name``, keyed by its slug; a platform without a slug or a name is left out."""
    return {
        slug: name
        for p in platforms
        if (slug := _text(p.get("slug"))) is not None and (name := _text(p.get("name"))) is not None
    }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
