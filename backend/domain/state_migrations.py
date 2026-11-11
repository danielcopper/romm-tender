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

# The source every pick stored before sources were recorded came from.
PICKS_FROM_BEFORE_SOURCES = "retrodeck"

# The version from which the per-platform sync toggles are kept by the
# platform's ``fs_slug`` rather than by RomM's platform id.
SWITCHES_BY_FOLDER_VERSION = 15

# Where the toggles kept by RomM's platform id wait for a read of RomM's
# listing to move them onto the platforms' ``fs_slug``.
SWITCHES_BY_ID_SETTING = "enabled_platform_ids"

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
    "source"}``. A bare label from before becomes RetroDECK's, the only source
    Tender started through (#2195 D20); a value that is no label is dropped, as
    it named no pick. The step stays in every later release and is safe to run
    again: it converts only a file older than 14.

    Version 15 keeps the per-platform sync toggles by ``fs_slug``. The map kept
    by RomM's platform id moves to ``enabled_platform_ids`` — an empty one too,
    since it meant every platform on — until a read of RomM's listing moves it
    onto the platforms' ``fs_slug`` (#2452 D8, D24); a value that is no map
    names no toggle and is dropped. The new map starts empty.
    """
    migrated = dict(data)
    if migrated["version"] < PLATFORM_PICK_SOURCE_VERSION:
        picks = migrated.get("platform_cores")
        if isinstance(picks, dict):
            migrated["platform_cores"] = {
                slug: {"label": label, "source": PICKS_FROM_BEFORE_SOURCES}
                for slug, label in picks.items()
                if isinstance(label, str) and label
            }
        migrated["version"] = PLATFORM_PICK_SOURCE_VERSION
    if migrated["version"] < SWITCHES_BY_FOLDER_VERSION:
        by_id = migrated.get("enabled_platforms")
        if isinstance(by_id, dict):
            migrated[SWITCHES_BY_ID_SETTING] = by_id
        migrated["enabled_platforms"] = {}
        migrated["version"] = SWITCHES_BY_FOLDER_VERSION
    return migrated
