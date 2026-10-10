import enum
from io import BufferedIOBase, TextIOWrapper, _WrappedBuffer
from typing import (
    Any,
    Final,
    Iterable,
    Literal,
    Mapping,
    Protocol,
    Self,
    TypeAlias,
    final,
    overload,
    type_check_only,
)

from _typeshed import ReadableBuffer, StrOrBytesPath, SupportsWrite, WriteableBuffer

@type_check_only
class _streams_Reader(Protocol):
    def read(self, n: int, /) -> bytes: ...
    def seekable(self) -> bool: ...
    def seek(self, n: int, /) -> Any: ...

@type_check_only
class _streams_BaseStream(BufferedIOBase): ...

#
# __init__.py
#

__all__ = (
    # backports.zstd
    "COMPRESSION_LEVEL_DEFAULT",
    "compress",
    "CompressionParameter",
    "decompress",
    "DecompressionParameter",
    "finalize_dict",
    "get_frame_info",
    "Strategy",
    "train_dict",
    # backports.zstd._shutil
    "register_shutil",
    # backports.zstd._zstdfile
    "open",
    "ZstdFile",
    # backports.zstd._zstd
    "get_frame_size",
    "zstd_version",
    "zstd_version_info",
    "ZstdCompressor",
    "ZstdDecompressor",
    "ZstdDict",
    "ZstdError",
)

zstd_version_info: Final[tuple[int, int, int]]
COMPRESSION_LEVEL_DEFAULT: Final[int] = ...

class FrameInfo:
    __slots__ = ("decompressed_size", "dictionary_id")
    decompressed_size: int
    dictionary_id: int
    def __init__(self, decompressed_size: int, dictionary_id: int) -> None: ...

def get_frame_info(frame_buffer: ReadableBuffer) -> FrameInfo: ...
def train_dict(samples: Iterable[ReadableBuffer], dict_size: int) -> ZstdDict: ...
def finalize_dict(zstd_dict: ZstdDict, /, samples: Iterable[ReadableBuffer], dict_size: int, level: int) -> ZstdDict: ...
def compress(
    data: ReadableBuffer,
    level: int | None = None,
    options: Mapping[int, int] | None = None,
    zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
) -> bytes: ...
def decompress(
    data: ReadableBuffer, zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None, options: Mapping[int, int] | None = None
) -> bytes: ...

@final
class CompressionParameter(enum.IntEnum):
    compression_level = ...
    window_log = ...
    hash_log = ...
    chain_log = ...
    search_log = ...
    min_match = ...
    target_length = ...
    strategy = ...
    enable_long_distance_matching = ...
    ldm_hash_log = ...
    ldm_min_match = ...
    ldm_bucket_size_log = ...
    ldm_hash_rate_log = ...
    content_size_flag = ...
    checksum_flag = ...
    dict_id_flag = ...
    nb_workers = ...
    job_size = ...
    overlap_log = ...
    def bounds(self) -> tuple[int, int]: ...

@final
class DecompressionParameter(enum.IntEnum):
    window_log_max = ...
    def bounds(self) -> tuple[int, int]: ...

@final
class Strategy(enum.IntEnum):
    fast = ...
    dfast = ...
    greedy = ...
    lazy = ...
    lazy2 = ...
    btlazy2 = ...
    btopt = ...
    btultra = ...
    btultra2 = ...

#
# _shutil
#

def register_shutil(*, tar: bool = True, zip: bool = True) -> None: ...

#
# _zstdfile
#

_ReadBinaryMode: TypeAlias = Literal["r", "rb"]
_WriteBinaryMode: TypeAlias = Literal["w", "wb", "x", "xb", "a", "ab"]
_ReadTextMode: TypeAlias = Literal["rt"]
_WriteTextMode: TypeAlias = Literal["wt", "xt", "at"]

@type_check_only
class _FileBinaryRead(_streams_Reader, Protocol):
    def close(self) -> None: ...

@type_check_only
class _FileBinaryWrite(SupportsWrite[bytes], Protocol):
    def close(self) -> None: ...

class ZstdFile(_streams_BaseStream):
    FLUSH_BLOCK = ZstdCompressor.FLUSH_BLOCK
    FLUSH_FRAME = ZstdCompressor.FLUSH_FRAME

    @overload
    def __init__(
        self,
        file: StrOrBytesPath | _FileBinaryRead,
        /,
        mode: _ReadBinaryMode = "r",
        *,
        level: None = None,
        options: Mapping[int, int] | None = None,
        zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        file: StrOrBytesPath | _FileBinaryWrite,
        /,
        mode: _WriteBinaryMode,
        *,
        level: int | None = None,
        options: Mapping[int, int] | None = None,
        zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
    ) -> None: ...

    def write(self, data: ReadableBuffer, /) -> int: ...
    def flush(self, mode: _ZstdCompressorFlushBlock | _ZstdCompressorFlushFrame = 1) -> bytes: ...  # type: ignore[override]
    def read(self, size: int | None = -1) -> bytes: ...
    def read1(self, size: int | None = -1) -> bytes: ...
    def readinto(self, b: WriteableBuffer) -> int: ...
    def readinto1(self, b: WriteableBuffer) -> int: ...
    def readline(self, size: int | None = -1) -> bytes: ...
    def seek(self, offset: int, whence: int = 0) -> int: ...
    def peek(self, size: int = -1) -> bytes: ...
    @property
    def name(self) -> str | bytes: ...
    @property
    def mode(self) -> Literal["rb", "wb"]: ...

@overload
def open(
    file: StrOrBytesPath | _FileBinaryRead,
    /,
    mode: _ReadBinaryMode = "rb",
    *,
    level: None = None,
    options: Mapping[int, int] | None = None,
    zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> ZstdFile: ...
@overload
def open(
    file: StrOrBytesPath | _FileBinaryWrite,
    /,
    mode: _WriteBinaryMode,
    *,
    level: int | None = None,
    options: Mapping[int, int] | None = None,
    zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> ZstdFile: ...
@overload
def open(
    file: StrOrBytesPath | _WrappedBuffer,
    /,
    mode: _ReadTextMode,
    *,
    level: None = None,
    options: Mapping[int, int] | None = None,
    zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> TextIOWrapper: ...
@overload
def open(
    file: StrOrBytesPath | _WrappedBuffer,
    /,
    mode: _WriteTextMode,
    *,
    level: int | None = None,
    options: Mapping[int, int] | None = None,
    zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> TextIOWrapper: ...

#
# _zstd
#

_ZstdCompressorContinue: TypeAlias = Literal[0]
_ZstdCompressorFlushBlock: TypeAlias = Literal[1]
_ZstdCompressorFlushFrame: TypeAlias = Literal[2]

@final
class ZstdCompressor:
    CONTINUE: Final = 0
    FLUSH_BLOCK: Final = 1
    FLUSH_FRAME: Final = 2
    def __new__(
        cls,
        level: int | None = None,
        options: Mapping[int, int] | None = None,
        zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None,
    ) -> Self: ...
    def compress(
        self, /, data: ReadableBuffer, mode: _ZstdCompressorContinue | _ZstdCompressorFlushBlock | _ZstdCompressorFlushFrame = 0
    ) -> bytes: ...
    def flush(self, /, mode: _ZstdCompressorFlushBlock | _ZstdCompressorFlushFrame = 2) -> bytes: ...
    def set_pledged_input_size(self, size: int | None, /) -> None: ...
    @property
    def last_mode(self) -> _ZstdCompressorContinue | _ZstdCompressorFlushBlock | _ZstdCompressorFlushFrame: ...

@final
class ZstdDecompressor:
    def __new__(
        cls, zstd_dict: ZstdDict | tuple[ZstdDict, int] | None = None, options: Mapping[int, int] | None = None
    ) -> Self: ...
    def decompress(self, /, data: ReadableBuffer, max_length: int = -1) -> bytes: ...
    @property
    def eof(self) -> bool: ...
    @property
    def needs_input(self) -> bool: ...
    @property
    def unused_data(self) -> bytes: ...

@final
class ZstdDict:
    def __new__(cls, dict_content: ReadableBuffer, /, *, is_raw: bool = False) -> Self: ...
    def __len__(self, /) -> int: ...
    @property
    def as_digested_dict(self) -> tuple[Self, int]: ...
    @property
    def as_prefix(self) -> tuple[Self, int]: ...
    @property
    def as_undigested_dict(self) -> tuple[Self, int]: ...
    @property
    def dict_content(self) -> bytes: ...
    @property
    def dict_id(self) -> int: ...

class ZstdError(Exception): ...

def get_frame_size(frame_buffer: ReadableBuffer) -> int: ...

zstd_version: Final[str]
zstd_version_number: Final[int]
