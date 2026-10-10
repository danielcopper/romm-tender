import enum

from ...zstd._zstd_cffi import ffi as _ffi
from ...zstd._zstd_cffi import lib as _lib

_new_nonzero = _ffi.new_allocator(should_clear_after_alloc=False)


class ZstdError(Exception):
    "An error occurred in the zstd library."


def _nbytes(dat, /):
    if isinstance(dat, (bytes, bytearray)):
        return len(dat)
    with memoryview(dat) as mv:
        return mv.nbytes


class _ErrorType(enum.IntEnum):
    ERR_DECOMPRESS = 0
    ERR_COMPRESS = enum.auto()
    ERR_SET_PLEDGED_INPUT_SIZE = enum.auto()
    ERR_LOAD_D_DICT = enum.auto()
    ERR_LOAD_C_DICT = enum.auto()
    ERR_GET_C_BOUNDS = enum.auto()
    ERR_GET_D_BOUNDS = enum.auto()
    ERR_SET_C_LEVEL = enum.auto()
    ERR_TRAIN_DICT = enum.auto()
    ERR_FINALIZE_DICT = enum.auto()


class _DictionaryType(enum.IntEnum):
    DICT_TYPE_DIGESTED = 0
    DICT_TYPE_UNDIGESTED = enum.auto()
    DICT_TYPE_PREFIX = enum.auto()


_ErrorTypeMessages = {
    _ErrorType.ERR_DECOMPRESS: "Unable to decompress Zstandard data: %s",
    _ErrorType.ERR_COMPRESS: "Unable to compress Zstandard data: %s",
    _ErrorType.ERR_SET_PLEDGED_INPUT_SIZE: "Unable to set pledged uncompressed content size: %s",
    _ErrorType.ERR_LOAD_D_DICT: "Unable to load Zstandard dictionary or prefix for decompression: %s",
    _ErrorType.ERR_LOAD_C_DICT: "Unable to load Zstandard dictionary or prefix for compression: %s",
    _ErrorType.ERR_GET_C_BOUNDS: "Unable to get zstd compression parameter bounds: %s",
    _ErrorType.ERR_GET_D_BOUNDS: "Unable to get zstd decompression parameter bounds: %s",
    _ErrorType.ERR_SET_C_LEVEL: "Unable to set zstd compression level: %s",
    _ErrorType.ERR_TRAIN_DICT: "Unable to train the Zstandard dictionary: %s",
    _ErrorType.ERR_FINALIZE_DICT: "Unable to finalize the Zstandard dictionary: %s",
}


def _set_zstd_error(type_, zstd_ret):
    raise ZstdError(
        _ErrorTypeMessages[type_]
        % _ffi.string(_lib.ZSTD_getErrorName(zstd_ret)).decode()
    )


_CpItems = {
    _lib.ZSTD_c_compressionLevel: "compression_level",
    _lib.ZSTD_c_windowLog: "window_log",
    _lib.ZSTD_c_hashLog: "hash_log",
    _lib.ZSTD_c_chainLog: "chain_log",
    _lib.ZSTD_c_searchLog: "search_log",
    _lib.ZSTD_c_minMatch: "min_match",
    _lib.ZSTD_c_targetLength: "target_length",
    _lib.ZSTD_c_strategy: "strategy",
    _lib.ZSTD_c_enableLongDistanceMatching: "enable_long_distance_matching",
    _lib.ZSTD_c_ldmHashLog: "ldm_hash_log",
    _lib.ZSTD_c_ldmMinMatch: "ldm_min_match",
    _lib.ZSTD_c_ldmBucketSizeLog: "ldm_bucket_size_log",
    _lib.ZSTD_c_ldmHashRateLog: "ldm_hash_rate_log",
    _lib.ZSTD_c_contentSizeFlag: "content_size_flag",
    _lib.ZSTD_c_checksumFlag: "checksum_flag",
    _lib.ZSTD_c_dictIDFlag: "dict_id_flag",
    _lib.ZSTD_c_nbWorkers: "nb_workers",
    _lib.ZSTD_c_jobSize: "job_size",
    _lib.ZSTD_c_overlapLog: "overlap_log",
}
_DpItems = {
    _lib.ZSTD_d_windowLogMax: "window_log_max",
}


def _set_parameter_error(is_compress, key, value):
    if is_compress:
        items = _CpItems
        type_ = "compression"
    else:
        items = _DpItems
        type_ = "decompression"

    # Find parameter's name
    try:
        name = items[key]
    # Unknown parameter
    except KeyError:
        name = "unknown parameter (key %d)" % key

    # Get parameter bounds
    try:
        if is_compress:
            bounds = _lib.ZSTD_cParam_getBounds(key)
        else:
            bounds = _lib.ZSTD_dParam_getBounds(key)
    except OverflowError:
        raise ValueError("invalid %s parameter '%s'" % (type_, name)) from None
    if _lib.ZSTD_isError(bounds.error):
        raise ValueError("invalid %s parameter '%s'" % (type_, name))

    # Error message
    raise ValueError(
        "%s parameter '%s' received an illegal value %d; "
        "the valid range is [%d, %d]"
        % (type_, name, value, bounds.lowerBound, bounds.upperBound)
    )


_PARAMETER_TYPES = {}


def set_parameter_types(c_parameter_type, d_parameter_type):
    """Set CompressionParameter and DecompressionParameter types for validity check.

  c_parameter_type
    CompressionParameter IntEnum type object
  d_parameter_type
    DecompressionParameter IntEnum type object"""
    _PARAMETER_TYPES["compression"] = c_parameter_type
    _PARAMETER_TYPES["decompression"] = d_parameter_type


def _check_int(value):
    if not isinstance(value, int):
        raise TypeError(
            f"'{value.__class__.__name__}' object cannot be interpreted as an integer"
        )
    if value > 2147483647 or value < -2147483648:
        raise OverflowError("Python int too large to convert to C int")


def _clinic_check(fname, args, pos, expected):
    got = type(args[pos])
    if got != expected:
        raise TypeError(
            f"{fname}() argument {pos + 1} must be {expected.__name__}, not {got.__name__}"
        )
