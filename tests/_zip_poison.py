"""The corrupt-central-directory zip poison, shared by the tests that feed it to the save-file fallback.

Import as ``from _zip_poison import corrupt_central_dir_zip_bytes`` — ``tests/`` is on the path via the root
conftest, the same way ``_factories`` is reached.
"""

import io
import struct
import zipfile


def corrupt_central_dir_zip_bytes() -> bytes:
    """A real two-entry zip with its SECOND central-directory entry's signature clobbered.

    The End-Of-Central-Directory record and the first entry stay intact, so ``is_zipfile`` still sniffs it as a zip —
    what the sniff reads is the comment on ``_ZIP_READ_ERRORS`` in ``adapters/save_file.py``, and it is why the second
    entry is the one clobbered — but ``ZipFile`` reads every entry on open and raises ``BadZipFile``. The dominant
    real-world poison: a corrupt / truncated archive.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("battery.srm", b"battery-bytes")
        zf.writestr("rtc.bin", b"rtc-bytes")
    data = bytearray(buf.getvalue())
    cd_offset = struct.unpack("<I", data[-22:][16:20])[0]  # EOCD → central-dir offset
    second = data.find(b"PK\x01\x02", cd_offset + 4)
    data[second : second + 4] = b"\x00\x00\x00\x00"  # kill the second entry's PK\x01\x02 magic
    return bytes(data)
