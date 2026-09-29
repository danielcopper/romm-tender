"""Which virtual types a set of RomM virtual-collection ids needs listed.

RomM builds a virtual collection's id as the URL-safe base64 of the JSON
``{"name": ..., "type": ...}`` and reads it back the same way
(``VirtualCollection.id`` / ``VirtualCollection.from_id`` in RomM's
``backend/models/collection.py``, read at 5.3.0 and 5.3.1).
"""

from __future__ import annotations

import base64
import json


def virtual_type_of(collection_id: str) -> str | None:
    """The ``type`` encoded in *collection_id*, or ``None`` when the id does not decode to one."""
    try:
        decoded = json.loads(base64.urlsafe_b64decode(collection_id.encode()).decode())
    except ValueError:  # binascii.Error, UnicodeDecodeError and JSONDecodeError all derive from it
        return None
    if not isinstance(decoded, dict):
        return None
    virtual_type = decoded.get("type")
    return virtual_type if isinstance(virtual_type, str) else None


def virtual_types_to_list(enabled_ids: set[str], supported: tuple[str, ...]) -> tuple[str, ...]:
    """The *supported* types, in their order, that hold one of *enabled_ids*.

    Every supported type when any id does not decode: a type left unlisted
    would build no unit for an enabled collection of it.
    """
    types = {virtual_type_of(collection_id) for collection_id in enabled_ids}
    if None in types:
        return supported
    return tuple(virtual_type for virtual_type in supported if virtual_type in types)
