from contextlib import suppress
from threading import Lock

from ...zstd._cffi._common import (
    ZstdError,
    _check_int,
    _DictionaryType,
    _ffi,
    _lib,
)


class ZstdDict:
    """Represents a Zstandard dictionary.

  dict_content
    The content of a Zstandard dictionary as a bytes-like object.
  is_raw
    If true, perform no checks on *dict_content*, useful for some
    advanced cases. Otherwise, check that the content represents
    a Zstandard dictionary created by the zstd library or CLI.

The dictionary can be used for compression or decompression, and can be
shared by multiple ZstdCompressor or ZstdDecompressor objects."""

    def __init__(self, dict_content, *, is_raw=False):
        # All dictionaries must be at least 8 bytes
        if len(dict_content) < 8:
            raise ValueError(
                "Zstandard dictionary content too short "
                "(must have at least eight bytes)"
            )

        self._d_dict = _ffi.NULL
        self._dict_id = 0
        self._lock = Lock()

        # ZSTD_CDict dict
        self._c_dicts = {}

        self._dict_content = bytes(dict_content)

        # Get dict_id, 0 means "raw content" dictionary.
        self._dict_id = _lib.ZSTD_getDictID_fromDict(
            _ffi.from_buffer(self._dict_content), len(self._dict_content)
        )

        # Check validity for ordinary dictionary
        if not is_raw and self.dict_id == 0:
            raise ValueError("invalid Zstandard dictionary")

    def __repr__(self):
        return "<ZstdDict dict_id=%u dict_size=%d>" % (
            self._dict_id,
            len(self._dict_content),
        )

    def __len__(self):
        return len(self._dict_content)

    @property
    def dict_content(self):
        "The content of a Zstandard dictionary, as a bytes object."
        return self._dict_content

    @property
    def dict_id(self):
        """The Zstandard dictionary, an int between 0 and 2**32.

A non-zero value represents an ordinary Zstandard dictionary,
conforming to the standardised format.

A value of zero indicates a 'raw content' dictionary,
without any restrictions on format or content."""
        return self._dict_id

    @property
    def as_digested_dict(self):
        """Load as a digested dictionary to compressor.

Pass this attribute as zstd_dict argument:
compress(dat, zstd_dict=zd.as_digested_dict)

1. Some advanced compression parameters of compressor may be
   overridden by parameters of digested dictionary.
2. ZstdDict has a digested dictionaries cache for each compression
   level.  It's faster when loading again a digested dictionary with
   the same compression level.
3. No need to use this for decompression."""
        return self, _DictionaryType.DICT_TYPE_DIGESTED

    @property
    def as_undigested_dict(self):
        """Load as an undigested dictionary to compressor.

Pass this attribute as zstd_dict argument:
compress(dat, zstd_dict=zd.as_undigested_dict)

1. The advanced compression parameters of compressor will not be
   overridden.
2. Loading an undigested dictionary is costly. If load an undigested
   dictionary multiple times, consider reusing a compressor object.
3. No need to use this for decompression."""
        return self, _DictionaryType.DICT_TYPE_UNDIGESTED

    @property
    def as_prefix(self):
        """Load as a prefix to compressor/decompressor.

Pass this attribute as zstd_dict argument:
compress(dat, zstd_dict=zd.as_prefix)

1. Prefix is compatible with long distance matching, while
   dictionary is not.
2. It only works for the first frame, then the
   compressor/decompressor will return to no prefix state.
3. When decompressing, must use the same prefix as when compressing."""
        return self, _DictionaryType.DICT_TYPE_PREFIX

    def _get_cdict(self, compression_level):
        with self._lock:
            cdict = self._c_dicts.get(compression_level)
            if cdict is None:
                cdict = _lib.ZSTD_createCDict(
                    self._dict_content, len(self._dict_content), compression_level
                )
                if cdict == _ffi.NULL:
                    raise ZstdError(
                        "Failed to create a ZSTD_CDict instance from "
                        "Zstandard dictionary content."
                    )
                self._c_dicts[compression_level] = cdict

        return cdict

    def _get_ddict(self):
        with self._lock:
            if self._d_dict == _ffi.NULL:
                # Create ZSTD_DDict instance from dictionary content
                self._d_dict = _lib.ZSTD_createDDict(
                    self._dict_content, len(self._dict_content)
                )
                if self._d_dict == _ffi.NULL:
                    raise ZstdError(
                        "Failed to create a ZSTD_DDict instance from "
                        "Zstandard dictionary content."
                    )

            return self._d_dict

    def __del__(self):
        with suppress(AttributeError):
            for level, cdict in self._c_dicts.items():
                _lib.ZSTD_freeCDict(cdict)
                self._c_dicts[level] = _ffi.NULL

        with suppress(AttributeError):
            if self._d_dict != _ffi.NULL:
                _lib.ZSTD_freeDDict(self._d_dict)
                self._d_dict = _ffi.NULL


def _Py_parse_zstd_dict(zd, default_type):
    # Check ZstdDict
    if isinstance(zd, ZstdDict):
        return zd, default_type

    # Check (ZstdDict, type)
    if isinstance(zd, tuple) and len(zd) == 2:
        zd, type_ = zd
        if isinstance(zd, ZstdDict) and isinstance(type_, int):
            _check_int(type_)
            if type_ in (
                _DictionaryType.DICT_TYPE_DIGESTED,
                _DictionaryType.DICT_TYPE_UNDIGESTED,
                _DictionaryType.DICT_TYPE_PREFIX,
            ):
                return zd, type_

    # Wrong type
    raise TypeError("zstd_dict argument should be a ZstdDict object.")
