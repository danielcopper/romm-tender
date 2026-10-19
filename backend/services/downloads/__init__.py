"""ROM downloads — from the frontend's request to an install on disk.

The download engine and its queue (``service.py``) over what a finished
transfer becomes, a file kept as it is or a folder unpacked from a zip
(``unpack.py``). Consumers import the service; the unpacker is a sub-service the
service builds over its own seams.
"""

from services.downloads.service import DownloadService, DownloadServiceConfig

__all__ = ["DownloadService", "DownloadServiceConfig"]
