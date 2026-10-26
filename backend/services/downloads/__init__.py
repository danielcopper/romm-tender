"""ROM downloads — from the frontend's request to an install on disk.

Every consumer calls the façade, ``DownloadService`` (``service.py``). Its
parts share one state box (``_state.py``): the queue's entries and their frames
(``queue.py``), an admitted download's task (``transfer.py``) and what a
finished transfer becomes, a file kept as it is or a folder unpacked from a zip
(``unpack.py``). Only the composition root builds the parts, from the
re-exports below, and hands them to the façade.
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
