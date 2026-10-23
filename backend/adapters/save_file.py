"""Filesystem adapter for local save file operations.

Owns the raw POSIX, ``open()``, ``tempfile``, and ``hashlib``-on-file
calls used by SaveService and its sub-services when reading, writing,
backing up, hashing, and removing local save files under the RetroDECK
saves directory. Path construction, platform-specific extension lookup,
and slot/sync policy remain a service or domain concern; this adapter
exposes only the I/O seams declared by
``services.protocols.SaveFileStore``.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
import zipfile
import zlib
from typing import TYPE_CHECKING

from adapters.descriptor_paths import claim_source, ensure_directory, rename_claimed
from domain.save_hash import combine_zip_entry_hashes
from domain.save_measure import SaveFileMeasure, TrailingRun, content_size

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterator
    from typing import BinaryIO

    from models.prune import MutationOutcome, SourceClaim

_MD5_CHUNK_SIZE = 8192

# Zip-decode failures a positive ``zipfile.is_zipfile`` sniff can still hit once
# the archive is actually read (the sniff inspects the End-Of-Central-Directory
# record and, from CPython 3.14 (gh-72680), the signature of the first
# central-directory entry — nothing past it): a corrupt / truncated central
# directory or a bad entry CRC (``BadZipFile``), a compressed stream ``zlib``
# cannot inflate (``zlib.error``), or an entry this runtime cannot decode — an
# encrypted member or a compression method the stdlib lacks (``RuntimeError``;
# the unknown-method ``NotImplementedError`` is a ``RuntimeError`` subclass, e.g.
# a save zipped with zstd, which ``zipfile`` only learns to read in 3.14).
# ``OSError`` is deliberately excluded so a genuine I/O fault (a vanished /
# unreadable file) still surfaces, matching the non-zip branch and
# ``checksum_md5``; ``LargeZipFile`` is excluded as an unreachable write-time
# ZIP64 guard.
_ZIP_READ_ERRORS = (zipfile.BadZipFile, zlib.error, RuntimeError)


class SaveFileAdapter:
    """Synchronous filesystem operations for local save files.

    Implements the ``SaveFileStore`` Protocol. Methods are
    synchronous — services that call from an async context offload via
    ``loop.run_in_executor``.
    """

    def __init__(self, *, logger: logging.Logger) -> None:
        self._logger = logger
        # Per-run measure memo, live only inside ``hash_memo_scope``.
        # ``None`` outside a scope so non-sync callers never populate a
        # process-lifetime cache; a fresh dict per outermost scope bounds it to
        # one sync run. Keyed ``(path, mtime_ns, size)`` so a save overwritten
        # mid-run (a download) re-reads on its new stat instead of returning a
        # stale measure. ``_hash_memo_depth`` makes nested scopes share one memo.
        self._hash_memo: dict[tuple[str, int, int], SaveFileMeasure] | None = None
        self._hash_memo_depth = 0

    def exists(self, path: str) -> bool:
        """Return True when *path* refers to an existing file or directory."""
        return os.path.exists(path)

    def is_file(self, path: str) -> bool:
        """Return True when *path* exists and is a regular file."""
        return os.path.isfile(path)

    def is_dir(self, path: str) -> bool:
        """Return True when *path* exists and is a directory."""
        return os.path.isdir(path)

    def is_symlink(self, path: str) -> bool:
        return os.path.islink(path)

    def canonical_path(self, path: str) -> str:
        return os.path.realpath(path)

    def is_within(self, path: str, root: str) -> bool:
        try:
            return os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == os.path.realpath(root)
        except ValueError:
            return False

    def make_dirs(self, path: str) -> None:
        """Create *path* and any missing parents. Idempotent."""
        os.makedirs(path, exist_ok=True)

    def remove_file(self, path: str) -> None:
        """Delete *path*. Idempotent: a missing file is not an error."""
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)

    def listdir(self, directory: str) -> list[str]:
        """Return the entry names in *directory*; empty list if it does not exist."""
        try:
            return os.listdir(directory)
        except FileNotFoundError:
            return []

    def rename(self, src: str, dst: str) -> None:
        """Atomically rename *src* to *dst*, replacing any existing file at *dst*."""
        os.replace(src, dst)

    def move(self, src: str, dst: str) -> None:
        """Move *src* to *dst*; ``shutil.move`` degrades to copy + delete across filesystems."""
        shutil.move(src, dst)

    def claim_source(self, path: str, safe_root: str) -> SourceClaim:
        return claim_source(path, safe_root)

    def ensure_directory(self, path: str, safe_root: str) -> None:
        ensure_directory(path, safe_root)

    def rename_claimed(self, src: str, dst: str, safe_root: str, claim: SourceClaim) -> MutationOutcome:
        return rename_claimed(src, dst, safe_root, claim)

    def get_mtime(self, path: str) -> float:
        """Return the mtime of *path* as a Unix timestamp."""
        return os.path.getmtime(path)

    def get_size(self, path: str) -> int:
        """Return the size of *path* in bytes."""
        return os.path.getsize(path)

    def checksum_md5(self, path: str) -> str:
        """Return the hex-encoded MD5 digest of *path*'s contents.

        Streams the file in fixed-size chunks so memory use stays
        bounded for large save files. Non-security use: MD5 here is the
        drift baseline that ``compute_sync_action`` compares against
        ``last_sync_hash`` to decide whether a local save changed since
        the last sync. ``usedforsecurity=False`` documents this and
        silences Sonar S4790.
        """
        h = hashlib.md5(usedforsecurity=False)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_MD5_CHUNK_SIZE), b""):
                h.update(chunk)
        return h.hexdigest()

    def content_hash(self, path: str) -> str:
        """Return RomM's content hash for *path* — zip-aware, MD5-based.

        Matches the server's ``compute_content_hash`` so a save's local and
        server hashes agree and save-sync converges. Dispatch mirrors RomM
        exactly: a file that *is* a zip archive (``zipfile.is_zipfile`` — a
        content sniff, not the extension) is hashed per entry and combined via
        :func:`domain.save_hash.combine_zip_entry_hashes`; any other file is the
        plain MD5 of its bytes. Directory entries inside the archive are
        skipped. A positive sniff over a file that cannot be read as a zip falls
        back to the plain MD5 rather than raising (see :meth:`_measure`).
        Non-security use, like ``checksum_md5``. It is :meth:`measure`'s hash,
        memoized the same way.
        """
        return self.measure(path).content_hash

    def measure(self, path: str) -> SaveFileMeasure:
        """Return *path*'s content hash, size and content size, read in one open.

        The hash is :meth:`content_hash`'s; the size and the content size are
        taken over the file's raw bytes, a zip's included
        (:mod:`domain.save_measure`).

        Inside a :meth:`hash_memo_scope` the measure is memoized by the file's
        ``(path, mtime_ns, size)`` so one sync run's repeated reads of the same
        save (negotiate inventory, the newest-wins matrix, the post-op baseline
        write) read it once. Outside a scope every call reads the file.
        """
        memo = self._hash_memo
        if memo is None:
            return self._measure(path)
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        cached = memo.get(key)
        if cached is not None:
            return cached
        measured = self._measure(path)
        memo[key] = measured
        return measured

    def _measure(self, path: str) -> SaveFileMeasure:
        """Read *path* once and measure it (no memo).

        A file that ``zipfile.is_zipfile`` accepts but that cannot be read as a
        zip (``_ZIP_READ_ERRORS``) is hashed as the plain MD5 of its bytes.
        RomM's server degrades the same file to ``content_hash=None``, so the
        kernel's truthiness guards reject a ``None``-side identity match and the
        fallback can never manufacture a false byte-identical match — it only
        keeps the local drift baseline working.
        """
        with open(path, "rb") as f:
            is_zip = zipfile.is_zipfile(f)
            f.seek(0)
            raw_md5, size, trailing = _scan(f)
            if not is_zip:
                return SaveFileMeasure(raw_md5, size, content_size(size, trailing))
            f.seek(0)
            try:
                with zipfile.ZipFile(f, "r") as zf:
                    entries = [
                        (name, hashlib.md5(zf.read(name), usedforsecurity=False).hexdigest())
                        for name in zf.namelist()
                        if not name.endswith("/")
                    ]
            except _ZIP_READ_ERRORS as exc:
                self._logger.debug("content_hash: %s sniffed as zip but unreadable (%s); MD5 fallback", path, exc)
                return SaveFileMeasure(raw_md5, size, content_size(size, trailing))
        return SaveFileMeasure(combine_zip_entry_hashes(entries), size, content_size(size, trailing))

    @contextlib.contextmanager
    def hash_memo_scope(self) -> Iterator[None]:
        """Bound a per-run :meth:`measure` memo to a single sync run.

        Within the scope, :meth:`measure` (and so ``content_hash``) caches each
        save's measure keyed by ``(path, mtime_ns, size)`` so the several passes
        of one run that hash the same file read it once. The memo is discarded
        when the outermost scope exits, so it is never a process-lifetime cache;
        a file overwritten mid-run gets a new stat key and is read again. Reentrant — nested scopes
        share one memo and only the outermost clears it — so the engine can open
        it at both the sweep and per-ROM levels. The engine opens exactly one
        (outermost) scope per device-gated save-sync run, so within-scope hashing
        is single-threaded; callers outside a scope hash directly and never touch
        the memo.
        """
        self._hash_memo_depth += 1
        if self._hash_memo is None:
            self._hash_memo = {}
        try:
            yield
        finally:
            self._hash_memo_depth -= 1
            if self._hash_memo_depth == 0:
                self._hash_memo = None

    def make_temp_path(self, suffix: str = "") -> str:
        """Return a fresh, unique path safe to write to.

        Backed by ``tempfile.mkstemp`` so the file is created atomically
        (``O_EXCL``) before the fd is closed. The caller owns the file
        and is responsible for removing it.
        """
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return path

    def read_bytes(self, path: str) -> bytes:
        """Return the contents of *path* as raw bytes."""
        with open(path, "rb") as f:
            return f.read()


def _scan(f: BinaryIO) -> tuple[str, int, TrailingRun]:
    """Stream *f* once: the MD5 of its bytes, their count, and the run they end with."""
    h = hashlib.md5(usedforsecurity=False)
    size = 0
    trailing = TrailingRun()
    for chunk in iter(lambda: f.read(_MD5_CHUNK_SIZE), b""):
        h.update(chunk)
        size += len(chunk)
        trailing = trailing.after(chunk)
    return h.hexdigest(), size, trailing
