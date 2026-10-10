from contextlib import suppress
from threading import Lock

from ...zstd._cffi._blocks_output_buffer import BlocksOutputBuffer
from ...zstd._cffi._common import (
    _PARAMETER_TYPES,
    ZstdError,
    _check_int,
    _DictionaryType,
    _ErrorType,
    _ffi,
    _lib,
    _nbytes,
    _new_nonzero,
    _set_parameter_error,
    _set_zstd_error,
)
from ...zstd._cffi.buffer import (
    _OutputBuffer_Finish,
    _OutputBuffer_Grow,
    _OutputBuffer_InitAndGrow,
    _OutputBuffer_InitWithSize,
)
from ...zstd._cffi.zstddict import _Py_parse_zstd_dict


def _zstd_contentsize_converter(size):
    if size is None:
        return _lib.ZSTD_CONTENTSIZE_UNKNOWN

    if size < 0 or size >= _lib.ZSTD_CONTENTSIZE_ERROR:
        raise ValueError(
            "size argument should be a positive int less "
            "than %u" % _lib.ZSTD_CONTENTSIZE_ERROR
        )

    return size


class ZstdCompressor:
    """Create a compressor object for compressing data incrementally.

  level
    The compression level to use. Defaults to COMPRESSION_LEVEL_DEFAULT.
  options
    A dict object that contains advanced compression parameters.
  zstd_dict
    A ZstdDict object, a pre-trained Zstandard dictionary.

Thread-safe at method level.  For one-shot compression, use the
compress() function instead."""

    CONTINUE = _lib.ZSTD_e_continue
    FLUSH_BLOCK = _lib.ZSTD_e_flush
    FLUSH_FRAME = _lib.ZSTD_e_end

    def __init__(self, level=None, options=None, zstd_dict=None):
        self._compression_level = _lib.ZSTD_defaultCLevel()
        self._use_multithread = False
        self._lock = Lock()

        # Compression context
        self._cctx = _lib.ZSTD_createCCtx()
        if self._cctx == _ffi.NULL:
            raise ZstdError("Unable to create ZSTD_CCtx instance.")

        # Last mode
        self._last_mode = _lib.ZSTD_e_end

        if level is not None and options is not None:
            raise TypeError("Only one of level or options should be used.")

        # Set compression level
        if level is not None:
            if not isinstance(level, int):
                raise TypeError("invalid type for level, expected int")
            try:
                _check_int(level)
            except OverflowError:
                raise ValueError(
                    "illegal compression level; the valid range is [%d, %d]"
                    % (_lib.ZSTD_minCLevel(), _lib.ZSTD_maxCLevel())
                )
            self._zstd_set_c_level(level)

        # Set options dictionary
        if options is not None:
            self._zstd_set_c_parameters(options)

        # Load Zstandard dictionary to compression context
        if zstd_dict is not None:
            self._zstd_load_c_dict(zstd_dict)

    @property
    def last_mode(self):
        """The last mode used to this compressor object, its value can be .CONTINUE,
.FLUSH_BLOCK, .FLUSH_FRAME. Initialized to .FLUSH_FRAME.

It can be used to get the current state of a compressor, such as, data
flushed, or a frame ended."""
        return self._last_mode

    def _zstd_set_c_level(self, level):
        # Set integer compression level
        min_level = _lib.ZSTD_minCLevel()
        max_level = _lib.ZSTD_maxCLevel()
        if level < min_level or level > max_level:
            raise ValueError(
                "illegal compression level %d; the valid range is [%d, %d]"
                % (level, min_level, max_level)
            )

        # Save for generating ZSTD_CDICT
        self._compression_level = level

        # Set compressionLevel to compression context
        zstd_ret = _lib.ZSTD_CCtx_setParameter(
            self._cctx, _lib.ZSTD_c_compressionLevel, level
        )

        # Check error
        if _lib.ZSTD_isError(zstd_ret):
            _set_zstd_error(_ErrorType.ERR_SET_C_LEVEL, zstd_ret)

    def _zstd_set_c_parameters(self, options):
        if not isinstance(options, dict):
            raise TypeError(
                "ZstdCompressor() argument 'options' must be dict, not %s"
                % options.__class__.__name__
            )

        for key, value in options.items():
            # Check key type
            if isinstance(key, _PARAMETER_TYPES["decompression"]):
                raise TypeError(
                    "compression options dictionary key must not be a "
                    "DecompressionParameter attribute"
                )

            _check_int(key)
            _check_int(value)

            if key == _lib.ZSTD_c_compressionLevel:
                self._zstd_set_c_level(value)
                continue
            if key == _lib.ZSTD_c_nbWorkers:
                # From the zstd library docs:
                # 1. When nbWorkers >= 1, triggers asynchronous mode when
                #    used with ZSTD_compressStream2().
                # 2, Default value is `0`, aka "single-threaded mode" : no
                #    worker is spawned, compression is performed inside
                #    caller's thread, all invocations are blocking.
                if value:
                    self._use_multithread = True

            # Set parameter to compression context */
            try:
                zstd_ret = _lib.ZSTD_CCtx_setParameter(self._cctx, key, value)
            except OverflowError:
                _set_parameter_error(True, key, value)
            if _lib.ZSTD_isError(zstd_ret):
                _set_parameter_error(True, key, value)

    def _zstd_load_c_dict(self, dict_):
        # When compressing, use undigested dictionary by default.
        zd, type_ = _Py_parse_zstd_dict(dict_, _DictionaryType.DICT_TYPE_UNDIGESTED)
        with self._lock:
            ret = self._zstd_load_impl(zd, type_)
        return ret

    def _zstd_load_impl(self, zd, type_):
        if type_ == _DictionaryType.DICT_TYPE_DIGESTED:
            # Get ZSTD_CDict
            c_dict = zd._get_cdict(self._compression_level)
            # Reference a prepared dictionary.
            # It overrides some compression context's parameters.
            zstd_ret = _lib.ZSTD_CCtx_refCDict(self._cctx, c_dict)
        elif type_ == _DictionaryType.DICT_TYPE_UNDIGESTED:
            # Load a dictionary.
            # It doesn't override compression context's parameters.
            zstd_ret = _lib.ZSTD_CCtx_loadDictionary(
                self._cctx, _ffi.from_buffer(zd._dict_content), len(zd._dict_content)
            )
        elif type_ == _DictionaryType.DICT_TYPE_PREFIX:
            # Load a prefix
            zstd_ret = _lib.ZSTD_CCtx_refPrefix(
                self._cctx, zd._dict_content, len(zd._dict_content)
            )
        else:
            raise SystemError("Unreachable")

        # Check error
        if _lib.ZSTD_isError(zstd_ret):
            _set_zstd_error(_ErrorType.ERR_LOAD_C_DICT, zstd_ret)

    def __del__(self):
        with suppress(AttributeError):
            _lib.ZSTD_freeCCtx(self._cctx)
            self._cctx = _ffi.NULL

    def compress(self, data, mode=_lib.ZSTD_e_continue):
        """Provide data to the compressor object.

  mode
    Can be these 3 values ZstdCompressor.CONTINUE,
    ZstdCompressor.FLUSH_BLOCK, ZstdCompressor.FLUSH_FRAME

Return a chunk of compressed data if possible, or b'' otherwise.
When you have finished providing data to the compressor, call the
flush() method to finish the compression process."""
        if (
            mode != _lib.ZSTD_e_continue
            and mode != _lib.ZSTD_e_flush
            and mode != _lib.ZSTD_e_end
        ):
            raise ValueError(
                "mode argument wrong value, it should be one of "
                "ZstdCompressor.CONTINUE, ZstdCompressor.FLUSH_BLOCK, "
                "ZstdCompressor.FLUSH_FRAME."
            )

        # Thread-safe code
        with self._lock:
            try:
                # Compress
                if self._use_multithread and mode == _lib.ZSTD_e_continue:
                    ret = self._compress_mt_continue_lock_held(data)
                else:
                    ret = self._compress_lock_held(data, mode)
                self._last_mode = mode
                return ret
            except:
                self._last_mode = _lib.ZSTD_e_end
                _lib.ZSTD_CCtx_reset(self._cctx, _lib.ZSTD_reset_session_only)
                raise

    def _compress_lock_held(self, data, end_directive):
        in_ = _new_nonzero("ZSTD_inBuffer *")
        if in_ == _ffi.NULL:
            raise MemoryError
        out = _new_nonzero("ZSTD_outBuffer *")
        if out == _ffi.NULL:
            raise MemoryError
        buffer = BlocksOutputBuffer()

        # Prepare input & output buffers
        if data:
            in_.src = _ffi.from_buffer(data)
            in_.size = _nbytes(data)
            in_.pos = 0
        else:
            in_.src = _ffi.NULL
            in_.size = 0
            in_.pos = 0

        # Calculate output buffer's size
        output_buffer_size = _lib.ZSTD_compressBound(in_.size)
        _OutputBuffer_InitWithSize(buffer, out, -1, output_buffer_size)

        # Zstandard stream compress
        while True:
            zstd_ret = _lib.ZSTD_compressStream2(self._cctx, out, in_, end_directive)

            # Check error
            if _lib.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

            # Finished
            if zstd_ret == 0:
                break

            # Output buffer should be exhausted, grow the buffer.
            if out.pos == out.size:
                _OutputBuffer_Grow(buffer, out)

        # Return a bytes object
        return _OutputBuffer_Finish(buffer, out)

    def _compress_mt_continue_lock_held(self, data):
        in_ = _new_nonzero("ZSTD_inBuffer *")
        if in_ == _ffi.NULL:
            raise MemoryError
        out = _new_nonzero("ZSTD_outBuffer *")
        if out == _ffi.NULL:
            raise MemoryError
        buffer = BlocksOutputBuffer()

        # Prepare input & output buffers
        in_.src = _ffi.from_buffer(data)
        in_.size = _nbytes(data)
        in_.pos = 0

        _OutputBuffer_InitAndGrow(buffer, out, -1)

        # Zstandard stream compress
        while True:
            while True:
                zstd_ret = _lib.ZSTD_compressStream2(
                    self._cctx, out, in_, _lib.ZSTD_e_continue
                )
                if (
                    out.pos != out.size
                    and in_.pos != in_.size
                    and not _lib.ZSTD_isError(zstd_ret)
                ):
                    continue
                break

            # Check error
            if _lib.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

            # Like compress_lock_held(), output as much as possible.
            if out.pos == out.size:
                _OutputBuffer_Grow(buffer, out)
            elif in_.pos == in_.size:
                # Finished
                break

        # Return a bytes object
        return _OutputBuffer_Finish(buffer, out)

    def flush(self, mode=_lib.ZSTD_e_end):
        """Finish the compression process.

  mode
    Can be these 2 values ZstdCompressor.FLUSH_FRAME,
    ZstdCompressor.FLUSH_BLOCK

Flush any remaining data left in internal buffers.  Since Zstandard
data consists of one or more independent frames, the compressor
object can still be used after this method is called."""
        # Check mode value
        if mode != _lib.ZSTD_e_end and mode != _lib.ZSTD_e_flush:
            raise ValueError(
                "mode argument wrong value, it should be "
                "ZstdCompressor.FLUSH_FRAME or "
                "ZstdCompressor.FLUSH_BLOCK."
            )

        # Thread-safe code
        with self._lock:
            try:
                ret = self._compress_lock_held(None, mode)
                self._last_mode = mode
                return ret
            except:
                self._last_mode = _lib.ZSTD_e_end
                # Resetting cctx's session never fail
                _lib.ZSTD_CCtx_reset(self._cctx, _lib.ZSTD_reset_session_only)
                raise

    def set_pledged_input_size(self, size, /):
        """Set the uncompressed content size to be written into the frame header.

  size
    The size of the uncompressed data to be provided to the compressor.

This method can be used to ensure the header of the frame about to
be written includes the size of the data, unless the
CompressionParameter.content_size_flag is set to False.
If last_mode != FLUSH_FRAME, then a RuntimeError is raised.

It is important to ensure that the pledged data size matches the
actual data size.  If they do not match the compressed output data
may be corrupted and the final chunk written may be lost."""
        size = _zstd_contentsize_converter(size)

        # Thread-safe code
        with self._lock:
            # Check the current mode
            if self._last_mode != _lib.ZSTD_e_end:
                raise ValueError(
                    "set_pledged_input_size() method must be called "
                    "when last_mode == FLUSH_FRAME"
                )

            # Set pledged content size
            zstd_ret = _lib.ZSTD_CCtx_setPledgedSrcSize(self._cctx, size)

        if _lib.ZSTD_isError(zstd_ret):
            _set_zstd_error(_ErrorType.ERR_SET_PLEDGED_INPUT_SIZE, zstd_ret)
