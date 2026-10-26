"""ROM downloads — from the frontend's request to an install on disk.

The endpoints and the other services call the façade, ``DownloadService``
(``service.py``). The queue holds the entries and sends their frames
(``queue.py``); the façade and an admitted download's task (``transfer.py``)
share the downloads' claims, tasks, reservations and control tokens in one state
box (``_state.py``). What a finished transfer becomes, a file kept as it is or a
folder unpacked from a zip, is the unpacker's (``unpack.py``), which holds no
state. Only the composition root builds the parts, from the re-exports below,
and hands them to the façade.
"""

from services.downloads._state import DownloadsStateBox
from services.downloads.queue import DownloadQueue, DownloadQueueConfig
from services.downloads.service import DownloadService, DownloadServiceConfig
from services.downloads.transfer import DownloadTransfer, DownloadTransferConfig
from services.downloads.unpack import DownloadUnpacker, DownloadUnpackerConfig

__all__ = [
    "DownloadQueue",
    "DownloadQueueConfig",
    "DownloadService",
    "DownloadServiceConfig",
    "DownloadTransfer",
    "DownloadTransferConfig",
    "DownloadUnpacker",
    "DownloadUnpackerConfig",
    "DownloadsStateBox",
]
