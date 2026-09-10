"""Version string parsing and the comparisons drawn from it."""

from __future__ import annotations

import re

_VERSION_RE = re.compile(
    r"^(\d+(?:\.\d+)*)(?:-(alpha|beta)(?:\.\d+)?)?$",
    re.IGNORECASE,
)


def _parse_version(version_str: str | None) -> tuple[tuple[int, ...], bool] | None:
    """Parse *version_str* into ``(core_tuple, has_prerelease)``.

    Returns ``None`` when *version_str* is not a supported version shape — a
    dot-separated numeric core with an optional ``-alpha`` / ``-beta``
    pre-release suffix, which both RomM's server versions and this plugin's own
    releases are spelled in.
    """
    if not isinstance(version_str, str):
        return None
    match = _VERSION_RE.match(version_str)
    if match is None:
        return None
    try:
        core = tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None
    has_prerelease = match.group(2) is not None
    return core, has_prerelease


def is_newer_version(candidate: str | None, current: str | None) -> bool:
    """Return True when *candidate* is strictly a later version than *current*.

    Both take the same shapes :func:`meets_min_version` accepts, and a
    pre-release ranks below its own release, so ``0.33.0-beta`` is not newer
    than ``0.33.0`` while ``0.34.0-beta`` is. Equality is never "newer" — the
    running version answers False against itself.

    Returns ``False`` whenever either side cannot be parsed. That is the silent
    direction on purpose: the caller offers an update on a True, so an
    unreadable version says nothing rather than announcing one.
    """
    parsed_candidate = _parse_version(candidate)
    parsed_current = _parse_version(current)
    if parsed_candidate is None or parsed_current is None:
        return False
    candidate_core, candidate_prerelease = parsed_candidate
    current_core, current_prerelease = parsed_current
    if candidate_core != current_core:
        return candidate_core > current_core
    return current_prerelease and not candidate_prerelease


def meets_min_version(version_str: str | None, minimum: tuple[int, ...]) -> bool:
    """Return True when *version_str* is SemVer-compatible with ``>= minimum``.

    *version_str* is a dot-separated numeric string such as ``"4.8.1"``, optionally
    followed by a RomM pre-release suffix ``-alpha`` or ``-beta`` (case-insensitive)
    with an optional ``.N`` build number (e.g. ``"5.0.0-alpha.1"``, ``"4.9.0-beta"``).

    Pre-releases rank **below** their own release: at floor ``(4, 9, 0)``,
    ``4.9.0-beta.3`` is rejected while ``4.9.1-beta`` passes because its numeric
    core is genuinely above the floor. When the numeric core is below *minimum*,
    the result is ``False`` regardless of suffix.

    Returns ``False`` for any input that cannot be parsed (empty string, non-numeric
    parts, unsupported pre-release tags, ``None``, or any non-``str`` type). The input
    is server-controlled, so a numeric or structured value is rejected by the
    ``isinstance`` guard in :func:`_parse_version` rather than raising. Non-numeric
    sentinel strings like ``"development"`` also return ``False`` — callers that want
    to bypass the check for development builds must test for them before invoking this
    function.
    """
    parsed = _parse_version(version_str)
    if parsed is None:
        return False
    core, has_prerelease = parsed
    if core < minimum:
        return False
    if core > minimum:
        return True
    return not has_prerelease
