from ...zstd._cffi._common import (
    ZstdError,
    _check_int,
    _clinic_check,
    _ErrorType,
    _ffi,
    _lib,
    _nbytes,
    _new_nonzero,
    _set_zstd_error,
    set_parameter_types,
)
from ...zstd._cffi.compressor import ZstdCompressor
from ...zstd._cffi.decompressor import ZstdDecompressor
from ...zstd._cffi.zstddict import ZstdDict

zstd_version = _ffi.string(_lib.ZSTD_versionString()).decode()
zstd_version_number = _lib.ZSTD_versionNumber()
ZSTD_CLEVEL_DEFAULT = _lib.ZSTD_defaultCLevel()
ZSTD_DStreamOutSize = _lib.ZSTD_DStreamOutSize()

ZSTD_c_compressionLevel = _lib.ZSTD_c_compressionLevel
ZSTD_c_windowLog = _lib.ZSTD_c_windowLog
ZSTD_c_hashLog = _lib.ZSTD_c_hashLog
ZSTD_c_chainLog = _lib.ZSTD_c_chainLog
ZSTD_c_searchLog = _lib.ZSTD_c_searchLog
ZSTD_c_minMatch = _lib.ZSTD_c_minMatch
ZSTD_c_targetLength = _lib.ZSTD_c_targetLength
ZSTD_c_strategy = _lib.ZSTD_c_strategy
ZSTD_c_enableLongDistanceMatching = _lib.ZSTD_c_enableLongDistanceMatching
ZSTD_c_ldmHashLog = _lib.ZSTD_c_ldmHashLog
ZSTD_c_ldmMinMatch = _lib.ZSTD_c_ldmMinMatch
ZSTD_c_ldmBucketSizeLog = _lib.ZSTD_c_ldmBucketSizeLog
ZSTD_c_ldmHashRateLog = _lib.ZSTD_c_ldmHashRateLog
ZSTD_c_contentSizeFlag = _lib.ZSTD_c_contentSizeFlag
ZSTD_c_checksumFlag = _lib.ZSTD_c_checksumFlag
ZSTD_c_dictIDFlag = _lib.ZSTD_c_dictIDFlag
ZSTD_c_nbWorkers = _lib.ZSTD_c_nbWorkers
ZSTD_c_jobSize = _lib.ZSTD_c_jobSize
ZSTD_c_overlapLog = _lib.ZSTD_c_overlapLog

ZSTD_d_windowLogMax = _lib.ZSTD_d_windowLogMax

ZSTD_fast = _lib.ZSTD_fast
ZSTD_dfast = _lib.ZSTD_dfast
ZSTD_greedy = _lib.ZSTD_greedy
ZSTD_lazy = _lib.ZSTD_lazy
ZSTD_lazy2 = _lib.ZSTD_lazy2
ZSTD_btlazy2 = _lib.ZSTD_btlazy2
ZSTD_btopt = _lib.ZSTD_btopt
ZSTD_btultra = _lib.ZSTD_btultra
ZSTD_btultra2 = _lib.ZSTD_btultra2


def get_param_bounds(parameter, is_compress):
    """Get CompressionParameter/DecompressionParameter bounds.

  parameter
    The parameter to get bounds.
  is_compress
    True for CompressionParameter, False for DecompressionParameter."""
    if is_compress:
        bound = _lib.ZSTD_cParam_getBounds(parameter)
        if _lib.ZSTD_isError(bound.error):
            _set_zstd_error(_ErrorType.ERR_GET_C_BOUNDS, bound.error)
    else:
        bound = _lib.ZSTD_dParam_getBounds(parameter)
        if _lib.ZSTD_isError(bound.error):
            _set_zstd_error(_ErrorType.ERR_GET_D_BOUNDS, bound.error)
    return bound.lowerBound, bound.upperBound


def get_frame_info(frame_buffer):
    """Get Zstandard frame infomation from a frame header.

  frame_buffer
    A bytes-like object, containing the header of a Zstandard frame."""
    decompressed_size = _lib.ZSTD_getFrameContentSize(
        _ffi.from_buffer(frame_buffer), len(frame_buffer)
    )
    if decompressed_size == _lib.ZSTD_CONTENTSIZE_ERROR:
        raise ZstdError(
            "Error when getting information from the header of "
            "a Zstandard frame. Ensure the frame_buffer argument "
            "starts from the beginning of a frame, and its length "
            "is not less than the frame header (6~18 bytes)."
        )
    dict_id = _lib.ZSTD_getDictID_fromFrame(
        _ffi.from_buffer(frame_buffer), len(frame_buffer)
    )
    if decompressed_size == _lib.ZSTD_CONTENTSIZE_UNKNOWN:
        return None, dict_id
    return decompressed_size, dict_id


def get_frame_size(frame_buffer):
    """Get the size of a Zstandard frame, including the header and optional checksum.

  frame_buffer
    A bytes-like object, it should start from the beginning of a frame,
    and contains at least one complete frame."""
    frame_size = _lib.ZSTD_findFrameCompressedSize(
        _ffi.from_buffer(frame_buffer), len(frame_buffer)
    )
    if _lib.ZSTD_isError(frame_size):
        raise ZstdError(
            "Error when finding the compressed size of a Zstandard frame. "
            "Ensure the frame_buffer argument starts from the "
            "beginning of a frame, and its length is not less than this "
            "complete frame. Zstd error message: %s."
            % _ffi.string(_lib.ZSTD_getErrorName(frame_size)).decode()
        )
    return frame_size


def _calculate_samples_stats(samples_bytes, samples_sizes):
    chunks_number = len(samples_sizes)
    # Prepare chunk_sizes
    chunk_sizes = _new_nonzero("size_t[]", chunks_number)
    sizes_sum = _nbytes(samples_bytes)
    for i, size in enumerate(samples_sizes):
        try:
            chunk_sizes[i] = size
        except OverflowError:
            sizes_sum = -1
            break
        sizes_sum -= size
    if sizes_sum != 0:
        raise ValueError(
            "The samples size tuple doesn't match the concatenation's size."
        )
    return chunk_sizes, chunks_number


def _clinic_train_dict(*args):
    _clinic_check("train_dict", args, 0, bytes)
    _clinic_check("train_dict", args, 1, tuple)


def train_dict(samples_bytes, samples_sizes, dict_size):
    """Train a Zstandard dictionary on sample data.

  samples_bytes
    Concatenation of samples.
  samples_sizes
    Tuple of samples' sizes.
  dict_size
    The size of the dictionary."""
    _clinic_train_dict(samples_bytes, samples_sizes, dict_size)

    # Check arguments
    _check_int(dict_size)
    if dict_size <= 0:
        raise ValueError("dict_size argument should be positive number.")

    # Check that the samples are valid and get their sizes
    chunk_sizes, chunks_number = _calculate_samples_stats(samples_bytes, samples_sizes)

    # Allocate dict buffer
    dst_dict_bytes = _new_nonzero("char[]", dict_size)
    if dst_dict_bytes == _ffi.NULL:
        raise MemoryError

    # Train the dictionary
    zstd_ret = _lib.ZDICT_trainFromBuffer(
        dst_dict_bytes,
        dict_size,
        _ffi.from_buffer(samples_bytes),
        chunk_sizes,
        chunks_number,
    )
    if _lib.ZDICT_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_TRAIN_DICT, zstd_ret)

    # Resize dict_buffer
    return _ffi.buffer(dst_dict_bytes)[:zstd_ret]


def _clinic_finalize_dict(*args):
    _clinic_check("finalize_dict", args, 0, bytes)
    _clinic_check("finalize_dict", args, 1, bytes)
    _clinic_check("finalize_dict", args, 2, tuple)


def finalize_dict(
    custom_dict_bytes, samples_bytes, samples_sizes, dict_size, compression_level
):
    """Finalize a Zstandard dictionary.

  custom_dict_bytes
    Custom dictionary content.
  samples_bytes
    Concatenation of samples.
  samples_sizes
    Tuple of samples' sizes.
  dict_size
    The size of the dictionary.
  compression_level
    Optimize for a specific Zstandard compression level, 0 means default."""
    _clinic_finalize_dict(
        custom_dict_bytes, samples_bytes, samples_sizes, dict_size, compression_level
    )

    # Check arguments
    _check_int(dict_size)
    if dict_size <= 0:
        raise ValueError("dict_size argument should be positive number.")

    # Check that the samples are valid and get their sizes
    chunk_sizes, chunks_number = _calculate_samples_stats(samples_bytes, samples_sizes)

    # Allocate dict buffer
    dst_dict_bytes = _new_nonzero("char[]", dict_size)
    if dst_dict_bytes == _ffi.NULL:
        raise MemoryError

    # Parameters
    params = _new_nonzero("ZDICT_params_t *")
    if params == _ffi.NULL:
        raise MemoryError
    # Optimize for a specific Zstandard compression level, 0 means default.
    params.compressionLevel = compression_level
    # Write log to stderr, 0 = none.
    params.notificationLevel = 0
    # Force dictID value, 0 means auto mode (32-bits random value).
    params.dictID = 0

    # Finalize the dictionary
    zstd_ret = _lib.ZDICT_finalizeDictionary(
        dst_dict_bytes,
        dict_size,
        _ffi.from_buffer(custom_dict_bytes),
        _nbytes(custom_dict_bytes),
        _ffi.from_buffer(samples_bytes),
        chunk_sizes,
        chunks_number,
        params[0],
    )

    # Check Zstandard dict error
    if _lib.ZDICT_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_FINALIZE_DICT, zstd_ret)

    # Resize dict_buffer
    return _ffi.buffer(dst_dict_bytes)[:zstd_ret]
