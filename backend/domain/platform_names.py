"""Pure decoding of the ``platform_slug → display_name`` cache.

The cache is a JSON object stored as a single ``kv_config`` TEXT value, refreshed
each sync so two readers get a platform's name without asking RomM: the game-detail
page's platform name, and a platform removal's answer, whose name the frontend uses
to find that platform's Steam collection. Anything that turns the stored
string back into the ``slug → name`` mapping belongs here; reading the value out
of ``kv_config`` stays in the service.
"""

from __future__ import annotations

import json


def decode_platform_names(raw: str | None) -> dict[str, str]:
    """Decode the cached ``platform_slug → display_name`` mapping.

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
