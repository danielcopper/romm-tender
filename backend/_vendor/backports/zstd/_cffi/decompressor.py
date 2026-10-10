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
    _new_nonzero,
    _set_parameter_error,
    _set_zstd_error,
)
from ...zstd._cffi.buffer import (
    _OutputBuffer_Finish,
    _OutputBuffer_Grow,
    _OutputBuffer_InitAndGrow,
    _OutputBuffer_ReachedMaxLength,
)
from ...zstd._cffi.zstddict import _Py_parse_zstd_dict


class ZstdDecompressor:
    """Create a decompressor object for decompressing data incrementally.

  zstd_dict
    A ZstdDict object, a pre-trained Zstandard dictionary.
  options
    A dict object that contains advanced decompression parameters.

Thread-safe at method level.  For one-shot decompression, use the
decompress() function instead."""

    def __init__(self, zstd_dict=None, options=None):
        self._input_buffer = _ffi.NULL
        self._input_buffer_size = 0
        self._in_begin = -1
        self._in_end = -1
        self._unused_data = _ffi.NULL
        self._eof = False
        self._lock = Lock()

        # needs_input flag
        self._needs_input = True

        # Decompression context
        self._dctx = _lib.ZSTD_createDCtx()
        if self._dctx == _ffi.NULL:
            raise ZstdError("Unable to create ZSTD_DCtx instance.")

        # Load Zstandard dictionary to decompression context
        if zstd_dict is not None:
            self._zstd_load_d_dict(zstd_dict)

        # Set options dictionary
        if options is not None:
            self._zstd_set_d_parameters(options)

    def _zstd_load_d_dict(self, dict_):
        # When decompressing, use digested dictionary by default.
        zd, type_ = _Py_parse_zstd_dict(dict_, _DictionaryType.DICT_TYPE_DIGESTED)
        with self._lock:
            self._zstd_load_impl(zd, type_)

    def _zstd_load_impl(self, zd, type_):
        if type_ == _DictionaryType.DICT_TYPE_DIGESTED:
            # Get ZSTD_DDict
            d_dict = zd._get_ddict()
            # Reference a prepared dictionary
            zstd_ret = _lib.ZSTD_DCtx_refDDict(self._dctx, d_dict)
        elif type_ == _DictionaryType.DICT_TYPE_UNDIGESTED:
            # Load a dictionary
            zstd_ret = _lib.ZSTD_DCtx_loadDictionary(
                self._dctx, _ffi.from_buffer(zd._dict_content), len(zd._dict_content)
            )
        elif type_ == _DictionaryType.DICT_TYPE_PREFIX:
            # Load a prefix
            zstd_ret = _lib.ZSTD_DCtx_refPrefix(
                self._dctx, _ffi.from_buffer(zd._dict_content), len(zd._dict_content)
            )
        else:
            raise SystemError("Unreachable")

        # Check error
        if _lib.ZSTD_isError(zstd_ret):
            _set_zstd_error(_ErrorType.ERR_LOAD_D_DICT, zstd_ret)

    def _zstd_set_d_parameters(self, options):
        if not isinstance(options, dict):
            raise TypeError(
                "ZstdDecompressor() argument 'options' must be dict, not %s"
                % options.__class__.__name__
            )

        for key, value in options.items():
            # Check key type
            if isinstance(key, _PARAMETER_TYPES["compression"]):
                raise TypeError(
                    "decompression options dictionary key must not be a "
                    "CompressionParameter attribute"
                )

            _check_int(key)
            _check_int(value)

            # Set parameter to compression context
            try:
                zstd_ret = _lib.ZSTD_DCtx_setParameter(self._dctx, key, value)
            except OverflowError:
                _set_parameter_error(False, key, value)

            # Check error
            if _lib.ZSTD_isError(zstd_ret):
                _set_parameter_error(False, key, value)

    def __del__(self):
        with suppress(AttributeError):
            _lib.ZSTD_freeDCtx(self._dctx)
            self._dctx = _ffi.NULL

    @property
    def unused_data(self):
        """A bytes object of un-consumed input data.

When ZstdDecompressor object stops after a frame is
decompressed, unused input data after the frame.  Otherwise this
will be b''."""
        with self._lock:
            if not self._eof:
                return b""
            if self._unused_data == _ffi.NULL:
                if self._input_buffer == _ffi.NULL:
                    self._unused_data = b""
                else:
                    self._unused_data = _ffi.buffer(self._input_buffer)[
                        self._in_begin : self._in_end
                    ]
            return self._unused_data

    @property
    def eof(self):
        """True means the end of the first frame has been reached. If decompress data
after that, an EOFError exception will be raised."""
        return self._eof

    @property
    def needs_input(self):
        """If the max_length output limit in .decompress() method has been reached,
and the decompressor has (or may has) unconsumed input data, it will be set
to False. In this case, passing b'' to the .decompress() method may output
further data."""
        return self._needs_input

    def decompress(self, data, max_length=-1):
        """Decompress *data*, returning uncompressed bytes if possible, or b'' otherwise.

  data
    A bytes-like object, Zstandard data to be decompressed.
  max_length
    Maximum size of returned data. When it is negative, the size of
    output buffer is unlimited. When it is nonnegative, returns at
    most max_length bytes of decompressed data.

If *max_length* is nonnegative, returns at most *max_length* bytes
of decompressed data.  If this limit is reached and further output
can be produced, *self.needs_input* will be set to ``False``.  In
this case, the next call to *decompress()* may provide *data* as b''
to obtain more of the output.

If all of the input data was decompressed and returned (either
because this was less than *max_length* bytes, or because
*max_length* was negative), *self.needs_input* will be set to True.

Attempting to decompress data after the end of a frame is reached
raises an EOFError.  Any data found after the end of the frame is
ignored and saved in the self.unused_data attribute."""
        with self._lock:
            return self._stream_decompress_lock_held(data, max_length)

    def _stream_decompress_lock_held(self, data, max_length):
        in_ = _new_nonzero("ZSTD_inBuffer *")
        if in_ == _ffi.NULL:
            raise MemoryError

        try:
            # Check .eof flag
            if self._eof:
                raise EOFError("Already at the end of a Zstandard frame.")

            # Prepare input buffer w/wo unconsumed data
            if self._in_begin == self._in_end:
                # No unconsumed data
                use_input_buffer = False
                in_.src = _ffi.from_buffer(data)
                in_.size = len(data)
                in_.pos = 0
            elif len(data) == 0:
                # Has unconsumed data, fast path for b''
                use_input_buffer = True
                in_.src = self._input_buffer + self._in_begin
                in_.size = self._in_end - self._in_begin
                in_.pos = 0
            else:
                # Has unconsumed data
                use_input_buffer = True

                # Unconsumed data size in input_buffer
                used_now = self._in_end - self._in_begin

                # Number of bytes we can append to input buffer
                avail_now = self._input_buffer_size - self._in_end

                # Number of bytes we can append if we move existing contents to
                # beginning of buffer
                avail_total = self._input_buffer_size - used_now

                if avail_total < len(data):
                    new_size = used_now + len(data)

                    # Allocate with new size
                    tmp = _new_nonzero("char[]", new_size)
                    if tmp == _ffi.NULL:
                        raise MemoryError

                    # Copy unconsumed data to the beginning of new buffer
                    _ffi.memmove(tmp, self._input_buffer + self._in_begin, used_now)

                    # Switch to new buffer
                    self._input_buffer = tmp
                    self._input_buffer_size = new_size

                    # Set begin & end position
                    self._in_begin = 0
                    self._in_end = used_now

                elif avail_now < len(data):
                    # Move unconsumed data to the beginning.
                    _ffi.memmove(
                        self._input_buffer,
                        self._input_buffer + self._in_begin,
                        used_now,
                    )

                    # Set begin & end position
                    self._in_begin = 0
                    self._in_end = used_now

                # Copy data to input buffer
                _ffi.memmove(
                    self._input_buffer + self._in_end, _ffi.from_buffer(data), len(data)
                )
                self._in_end += len(data)

                in_.src = self._input_buffer + self._in_begin
                in_.size = used_now + len(data)
                in_.pos = 0

            # Decompress
            ret = self._decompress_lock_held(in_, max_length)

            # Unconsumed input data
            if in_.pos == in_.size:
                if len(ret) == max_length or self._eof:
                    self._needs_input = False
                else:
                    self._needs_input = True

                if use_input_buffer:
                    # Clear input_buffer
                    self._in_begin = 0
                    self._in_end = 0

            else:
                data_size = in_.size - in_.pos

                self._needs_input = False

                if not use_input_buffer:
                    # Discard buffer if it's too small
                    # (resizing it may needlessly copy the current contents)
                    if (
                        self._input_buffer != _ffi.NULL
                        and self._input_buffer_size < data_size
                    ):
                        self._input_buffer = _ffi.NULL
                        self._input_buffer_size = 0

                    # Allocate if necessary
                    if self._input_buffer == _ffi.NULL:
                        self._input_buffer = _new_nonzero("char[]", data_size)
                        if self._input_buffer == _ffi.NULL:
                            raise MemoryError
                        self._input_buffer_size = data_size

                    # Copy unconsumed data
                    _ffi.memmove(self._input_buffer, in_.src + in_.pos, data_size)
                    self._in_begin = 0
                    self._in_end = data_size

                else:
                    # Use input buffer
                    self._in_begin += in_.pos

            return ret

        except:
            # Reset decompressor's states/session
            self._decompressor_reset_session_lock_held()
            raise

    def _decompress_lock_held(self, in_, max_length):
        out = _new_nonzero("ZSTD_outBuffer *")
        if out == _ffi.NULL:
            raise MemoryError
        buffer = BlocksOutputBuffer()

        # Initialize the output buffer
        _OutputBuffer_InitAndGrow(buffer, out, max_length)

        while True:
            # Decompress
            zstd_ret = _lib.ZSTD_decompressStream(self._dctx, out, in_)

            # Check error
            if _lib.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_DECOMPRESS, zstd_ret)

            # Set .eof flag
            if zstd_ret == 0:
                # Stop when a frame is decompressed
                self._eof = 1
                break

            # Need to check out before in. Maybe zstd's internal buffer still has
            # a few bytes that can be output, grow the buffer and continue.
            if out.pos == out.size:
                # Output buffer exhausted

                # Output buffer reached max_length
                if _OutputBuffer_ReachedMaxLength(buffer, out):
                    break

                # Grow output buffer
                _OutputBuffer_Grow(buffer, out)

            elif in_.pos == in_.size:
                # Finished
                break

        # Return a bytes object
        return _OutputBuffer_Finish(buffer, out)

    def _decompressor_reset_session_lock_held(self):
        # Reset variables
        self._in_begin = 0
        self._in_end = 0

        self._unused_data = _ffi.NULL

        # Reset variables in one operation
        self._needs_input = 1
        self._eof = 0

        # Resetting session is guaranteed to never fail
        _lib.ZSTD_DCtx_reset(self._dctx, _lib.ZSTD_reset_session_only)
