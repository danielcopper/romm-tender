"""The server device id a save operation may send to RomM.

A ``LiveDeviceId`` is an id this device's registration answered for during the
operation that holds it: registered, or touched without the server answering
that it is gone, a moment before. Only ``SyncEngine.live_device_id`` builds
one, and ``scripts/check_live_device_id.py`` fails on a construction anywhere
else; the places that take one are where the type checker refuses a stored id
or ``None``.

Why a save request must name a live device: RomM runs ``add_save``'s
write-time 409 gates, records the calling device's sync row on ``add_save``
and ``update_save``, and lists a save's ``device_syncs`` for this device, only
when the request names a device it knows (``endpoints/saves.py``, read at
5.3.0). Without one an upload skips the conflict backstop the save sync relies
on, and a listing carries no sync row for this device at all.
"""

from __future__ import annotations

from typing import NewType

LiveDeviceId = NewType("LiveDeviceId", str)
