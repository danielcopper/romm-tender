"""Pure decoding of the platform names cache, and the names it is written from.

The cache maps each platform's ``fs_slug`` to the name it is
shown under (#2452 D10). The name the sync files a platform under, and the one
this cache holds, is the ``name`` RomM's platform listing gives it, never the
``custom_name`` a user set in RomM or the ``display_name`` that prefers it
(#2564 D2), with the platform's ``fs_slug`` added where #2452 D2 says
(:func:`listed_platform_names`). The cache is a JSON object stored as a single ``kv_config`` TEXT
value, rewritten from the run's listing by each sync so its readers get a
platform's name without asking RomM — among them the game-detail page's platform
name, a platform removal's answer, whose name the frontend uses to find that
platform's Steam collection, the next sync, which names the Steam collection of
a platform RomM no longer lists by it, a BIOS row a link of Tender's into
another platform's store folder serves, and the platform's system, whose name
its ``no_platform_system`` refusal, the pages that show the system, and a
download's queue entry and every frame and event it sends, ``download_failed``
included, carry. A key written before the cache was keyed by ``fs_slug`` is read as
the ``fs_slug`` it stands for until a sync rewrites it. Anything that turns the
stored string back into the ``fs_slug → name`` mapping belongs here; reading the value out of ``kv_config``
stays in the service.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# The ``kv_config`` key the cache is kept under.
PLATFORM_NAMES_KEY = "platform_names"


def decode_platform_names(raw: str | None) -> dict[str, str]:
    """Decode the cached ``fs_slug → name`` mapping.

    Returns ``{}`` when *raw* is absent, empty, not valid JSON, or decodes to
    anything other than an object — callers degrade to the slug or the
    ``fs_slug`` in every such case rather than surfacing a corrupt cache.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def platform_display_name(raw: str | None, platform_folder: str | None, slug: str | None) -> str:
    """The name a platform is shown under: the cached name for *platform_folder* in *raw*, else the slug.

    A game no sync has recorded an ``fs_slug`` for is shown under its slug; ``""``
    without a slug either.
    """
    fallback = slug or platform_folder or ""
    if not platform_folder:
        return fallback
    return decode_platform_names(raw).get(platform_folder, fallback)


def listed_fs_slug(platform: Mapping[str, Any]) -> str | None:
    """The ``fs_slug`` RomM's listing gives *platform*; ``None`` where it gives none, or a blank one."""
    return _text(platform.get("fs_slug"))


def listed_platform_names(platforms: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Every listed platform's name, keyed by its ``fs_slug``; one without an ``fs_slug`` or a name is left out.

    A platform whose ``fs_slug`` is not its slug, and whose ``name`` another platform
    of the listing shares, is named ``<name> (<fs_slug>)``; every other keeps its
    ``name``, so the platform whose ``fs_slug`` is its slug is not renamed when a
    second ``fs_slug`` appears (#2452 D2).
    """
    named = [
        (folder, _text(p.get("slug")), name)
        for p in platforms
        if (folder := listed_fs_slug(p)) is not None and (name := _text(p.get("name"))) is not None
    ]
    shared = Counter(name for _folder, _slug, name in named)
    return {
        folder: f"{name} ({folder})" if shared[name] > 1 and folder != slug else name for folder, slug, name in named
    }


def listed_platform_folders(platforms: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The ``fs_slug`` of every listed platform that has one."""
    return frozenset(folder for p in platforms if (folder := listed_fs_slug(p)) is not None)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
