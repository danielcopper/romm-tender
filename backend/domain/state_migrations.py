"""Pure schema-migration functions for Tender's state files.

Each function accepts a raw value (as loaded from disk) and answers about it or
returns it promoted to the current schema version. No I/O — reading and writing
is the caller's responsibility.
"""

from __future__ import annotations

from typing import Any

# The oldest settings version this release reads; an older file is not
# converted but replaced by the defaults.
OLDEST_SETTINGS_VERSION = 13

# The version from which a per-platform emulator pick is stored as its label and
# its emulator source rather than as a bare label.
PLATFORM_PICK_SOURCE_VERSION = 14

_JSON_KINDS: dict[type, str] = {
    dict: "object",
    list: "array",
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
    type(None): "null",
}


def _json_kind(value: object) -> str:
    return _JSON_KINDS.get(type(value), type(value).__name__)


def unreadable_settings(data: object) -> str | None:
    """What makes *data* a settings file this release does not read, or ``None`` when it reads it.

    A file it reads is a JSON object whose ``version`` is a whole number no
    older than :data:`OLDEST_SETTINGS_VERSION`. The answer names what was found
    instead, for a log line: a version that is a whole number by its value, anything
    else by its JSON kind alone, so no text from the file reaches the log.
    """
    if not isinstance(data, dict):
        return f"it holds a JSON {_json_kind(data)}, not an object"
    if "version" not in data:
        return "it has no version"
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        return f"its version is a JSON {_json_kind(version)}, not a whole number"
    if version < OLDEST_SETTINGS_VERSION:
        return f"its version {int(version)} is older than {OLDEST_SETTINGS_VERSION}"
    return None


def migrate_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Bring *data* from any settings schema this release reads to the current version.

    *data* is one :func:`unreadable_settings` has no objection to. Value
    semantics — the caller's dict is never mutated.

    Version 14 stores each per-platform emulator pick as ``{"label",
    "source"}``. A bare label from before carries ``None`` for its source,
    since nothing recorded which catalogue it came from; a value that is no
    label is dropped, as it named no pick. The step stays in every later release
    and is safe to run again: it converts only a file older than 14.
    """
    migrated = dict(data)
    if migrated["version"] < PLATFORM_PICK_SOURCE_VERSION:
        picks = migrated.get("platform_cores")
        if isinstance(picks, dict):
            migrated["platform_cores"] = {
                slug: {"label": label, "source": None}
                for slug, label in picks.items()
                if isinstance(label, str) and label
            }
        migrated["version"] = PLATFORM_PICK_SOURCE_VERSION
    return migrated
