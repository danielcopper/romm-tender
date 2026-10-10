from ...zstd._cffi._common import _ffi, _lib, _new_nonzero

unable_allocate_msg = "Unable to allocate output buffer."
OUTPUT_BUFFER_MAX_BLOCK_SIZE = 256 * 1024 * 1024
KB = 1024
MB = 1024 * 1024
BUFFER_BLOCK_SIZE = [
    32 * KB,
    64 * KB,
    256 * KB,
    1 * MB,
    4 * MB,
    8 * MB,
    16 * MB,
    16 * MB,
    32 * MB,
    32 * MB,
    32 * MB,
    32 * MB,
    64 * MB,
    64 * MB,
    128 * MB,
    128 * MB,
    OUTPUT_BUFFER_MAX_BLOCK_SIZE,
]


class BlocksOutputBuffer:
    def init_and_grow(self, max_length):
        # get block size
        if 0 <= max_length < BUFFER_BLOCK_SIZE[0]:
            block_size = max_length
        else:
            block_size = BUFFER_BLOCK_SIZE[0]

        # the first block
        b = _new_nonzero("char[]", block_size)
        if b == _ffi.NULL:
            raise MemoryError

        # create the list
        self.list = [b]

        # set variables
        self.allocated = block_size
        self.max_length = max_length

        return block_size, b

    def init_with_size(self, init_size):
        # the first block
        b = _new_nonzero("char[]", init_size)
        if b == _ffi.NULL:
            raise MemoryError

        # create the list
        self.list = [b]

        # set variables
        self.allocated = init_size
        self.max_length = -1

        return init_size, b

    def grow(self, avail_out):
        list_len = len(self.list)

        # ensure no gaps in the data
        if avail_out != 0:
            raise SystemError("avail_out is non-zero in _BlocksOutputBuffer_Grow().")

        # get block size
        if list_len < len(BUFFER_BLOCK_SIZE):
            block_size = BUFFER_BLOCK_SIZE[list_len]
        else:
            block_size = BUFFER_BLOCK_SIZE[-1]

        # check max_length
        if self.max_length >= 0:
            # if (rest == 0), should not grow the buffer.
            rest = self.max_length - self.allocated
            assert rest > 0

            # block_size of the last block
            if block_size > rest:
                block_size = rest

        # create the block
        b = _new_nonzero("char[]", block_size)
        if b == _ffi.NULL:
            raise MemoryError(unable_allocate_msg)
        self.list.append(b)

        # set variables
        self.allocated += block_size

        return block_size, b

    def finish(self, avail_out):
        list_len = len(self.list)

        # fast path for single block
        if (list_len == 1 and avail_out == 0) or (
            list_len == 2 and len(self.list[1]) == avail_out
        ):
            block = self.list[0]
            self.list.clear()
            return bytes(_ffi.buffer(block))

        # final bytes object
        result = _new_nonzero("char[]", self.allocated - avail_out)
        if result == _ffi.NULL:
            raise MemoryError(unable_allocate_msg)

        # memory copy
        if list_len > 0:
            posi = 0

            # blocks except the last one
            for block in self.list[:-1]:
                block_len = len(block)
                _ffi.memmove(result + posi, block, block_len)
                posi += block_len
            # the last block
            block = self.list[-1]
            _ffi.memmove(result + posi, block, len(block) - avail_out)
        else:
            assert len(result) == 0

        self.list.clear()
        return bytes(_ffi.buffer(result))
