def _OutputBuffer_InitAndGrow(buffer, ob, max_length):
    res, ob.dst = buffer.init_and_grow(max_length)
    ob.size = res
    ob.pos = 0


def _OutputBuffer_InitWithSize(buffer, ob, max_length, init_size):
    # Get block size
    if 0 <= max_length < init_size:
        block_size = max_length
    else:
        block_size = init_size

    res, ob.dst = buffer.init_with_size(block_size)

    # Set max_length, InitWithSize doesn't do this
    buffer.max_length = max_length
    ob.size = res
    ob.pos = 0


def _OutputBuffer_Grow(buffer, ob):
    res, ob.dst = buffer.grow(0)
    ob.size = res
    ob.pos = 0


def _OutputBuffer_Finish(buffer, ob):
    return buffer.finish(ob.size - ob.pos)


def _OutputBuffer_ReachedMaxLength(buffer, ob):
    return buffer.allocated == buffer.max_length
