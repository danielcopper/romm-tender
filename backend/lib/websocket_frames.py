"""WebSocket wire format — the pure half of RFC 6455.

Contract: turning bytes into frames and frames into bytes, and nothing else.
No socket, no connection state, no handshake policy — those belong to the host,
which owns the I/O. What lives here is decided by one question: could a table of
byte strings and expected results check it? Everything that answers yes is here,
so the format is pinned against vectors rather than against a running server.

The split is what makes the size cap checkable at the right moment. A frame
announces its payload length in its header, so :func:`parse_frame_header`
answers how long a payload will be *before* a single byte of it is read, and a
caller can refuse an oversized frame without ever buffering it. A codec that
returned whole frames could not offer that, because by the time it had one the
memory was already spent.
"""

from __future__ import annotations

import base64
import hashlib
import struct
from dataclasses import dataclass

# Opcodes this host speaks. Binary frames are deliberately absent from the
# dispatch path — every message is JSON text — but the opcode is named so an
# incoming binary frame is refused by name rather than falling through as
# "unknown".
OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA

_DATA_OPCODES = frozenset({OPCODE_CONTINUATION, OPCODE_TEXT, OPCODE_BINARY})
_CONTROL_OPCODES = frozenset({OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG})

# RFC 6455 close codes this host sends. ``GOING_AWAY`` is the shutdown answer;
# ``POLICY_VIOLATION`` carries a refusal the peer caused; ``MESSAGE_TOO_BIG`` is
# the size cap's own code, which is why an oversized frame is distinguishable
# from every other refusal in a browser's close event.
CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_INTERNAL_ERROR = 1011

# The handshake's magic string (RFC 6455 §1.3). It exists so a server cannot
# accidentally complete a handshake by echoing what it was sent.
_ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Payload lengths 126 and 127 are escapes into a 16- and a 64-bit field. Anything
# below is the length itself.
_LENGTH_ESCAPE_16 = 126
_LENGTH_ESCAPE_64 = 127

# A control frame must fit in one frame and in the 7-bit length field (RFC 6455
# §5.5), which is what keeps a close or a ping from being a memory question.
MAX_CONTROL_PAYLOAD = 125

_MASK_LENGTH = 4


class WebSocketProtocolError(ValueError):
    """Raised when bytes on the wire cannot be a valid frame.

    A :class:`ValueError` subclass so a caller that treats malformed input
    uniformly still catches it, while a caller that wants to answer with a
    specific close code can catch this type first.
    """


@dataclass(frozen=True)
class FrameHeader:
    """A frame's header, parsed — everything knowable before the payload is read.

    ``payload_length`` is what the sender *announced*. It is the value a size
    cap is checked against, and it is knowable while the payload is still on the
    network rather than in this process.
    """

    fin: bool
    opcode: int
    masked: bool
    payload_length: int
    mask: bytes

    @property
    def is_control(self) -> bool:
        """Is this a control frame (close, ping, pong) rather than data?"""
        return self.opcode in _CONTROL_OPCODES


def header_length(first_two: bytes) -> int:
    """Return the full header size in bytes, read from the first two.

    The first two bytes of every frame say how much more header there is: the
    length escape decides whether an extended length field follows and how wide
    it is, and the mask bit decides whether a four-byte key follows that. A
    reader uses this to ask the socket for exactly the rest of the header and
    not one byte more — which is what keeps the payload unread until its
    announced length has been judged.

    Raises :class:`WebSocketProtocolError` when fewer than two bytes are given.
    """
    if len(first_two) < 2:
        raise WebSocketProtocolError(f"a frame header needs at least 2 bytes, got {len(first_two)}")
    length_marker = first_two[1] & 0x7F
    masked = bool(first_two[1] & 0x80)
    size = 2
    if length_marker == _LENGTH_ESCAPE_16:
        size += 2
    elif length_marker == _LENGTH_ESCAPE_64:
        size += 8
    if masked:
        size += _MASK_LENGTH
    return size


def parse_frame_header(header: bytes) -> FrameHeader:
    """Parse a complete frame header — exactly :func:`header_length` bytes of it.

    Validates everything the format decides on its own, so a caller never has to
    re-check it: the reserved bits must be clear (this host negotiates no
    extension, so a set bit means the peer is speaking a protocol we did not
    agree to), the opcode must be one this host knows, and a control frame must
    be unfragmented and within :data:`MAX_CONTROL_PAYLOAD`.

    Raises :class:`WebSocketProtocolError` on any of those, and on a header
    shorter than the first two bytes declare.
    """
    expected = header_length(header)
    if len(header) != expected:
        raise WebSocketProtocolError(f"header declares {expected} bytes, got {len(header)}")

    first, second = header[0], header[1]
    if first & 0x70:
        raise WebSocketProtocolError("reserved bits set, but no extension was negotiated")

    opcode = first & 0x0F
    if opcode not in _DATA_OPCODES and opcode not in _CONTROL_OPCODES:
        raise WebSocketProtocolError(f"unknown opcode: {opcode:#x}")

    fin = bool(first & 0x80)
    masked = bool(second & 0x80)
    length_marker = second & 0x7F

    offset = 2
    if length_marker == _LENGTH_ESCAPE_16:
        payload_length = struct.unpack_from("!H", header, offset)[0]
        offset += 2
    elif length_marker == _LENGTH_ESCAPE_64:
        payload_length = struct.unpack_from("!Q", header, offset)[0]
        offset += 8
    else:
        payload_length = length_marker

    mask = header[offset : offset + _MASK_LENGTH] if masked else b""

    if opcode in _CONTROL_OPCODES:
        if not fin:
            raise WebSocketProtocolError("a control frame must not be fragmented")
        if payload_length > MAX_CONTROL_PAYLOAD:
            raise WebSocketProtocolError(f"a control frame payload must be at most {MAX_CONTROL_PAYLOAD} bytes")

    return FrameHeader(fin=fin, opcode=opcode, masked=masked, payload_length=payload_length, mask=mask)


def apply_mask(payload: bytes, mask: bytes) -> bytes:
    """XOR *payload* with the repeating four-byte *mask*; its own inverse.

    An empty mask returns the payload unchanged, so an unmasked frame needs no
    branch at the call site.
    """
    if not mask:
        return payload
    if len(mask) != _MASK_LENGTH:
        raise WebSocketProtocolError(f"a mask is {_MASK_LENGTH} bytes, got {len(mask)}")
    return bytes(byte ^ mask[index % _MASK_LENGTH] for index, byte in enumerate(payload))


def build_frame(opcode: int, payload: bytes, *, fin: bool = True, mask: bytes = b"") -> bytes:
    """Encode one frame.

    Server-to-client frames are unmasked, which is the default and what this
    host sends. *mask* exists for the other direction — a client frame must be
    masked (RFC 6455 §5.1) — and is what lets a test drive the parser with the
    bytes a browser would actually put on the wire.
    """
    if opcode in _CONTROL_OPCODES and len(payload) > MAX_CONTROL_PAYLOAD:
        raise WebSocketProtocolError(f"a control frame payload must be at most {MAX_CONTROL_PAYLOAD} bytes")
    if mask and len(mask) != _MASK_LENGTH:
        raise WebSocketProtocolError(f"a mask is {_MASK_LENGTH} bytes, got {len(mask)}")

    first = (0x80 if fin else 0x00) | opcode
    mask_bit = 0x80 if mask else 0x00
    length = len(payload)

    if length < _LENGTH_ESCAPE_16:
        head = struct.pack("!BB", first, mask_bit | length)
    elif length <= 0xFFFF:
        head = struct.pack("!BBH", first, mask_bit | _LENGTH_ESCAPE_16, length)
    else:
        head = struct.pack("!BBQ", first, mask_bit | _LENGTH_ESCAPE_64, length)

    if not mask:
        return head + payload
    return head + mask + apply_mask(payload, mask)


def text_frame(text: str) -> bytes:
    """Encode *text* as a single unmasked text frame."""
    return build_frame(OPCODE_TEXT, text.encode("utf-8"))


def close_frame(code: int, reason: str = "") -> bytes:
    """Encode a close frame carrying *code* and an optional UTF-8 *reason*.

    The reason is truncated to what a control frame can hold beside the two-byte
    code, on a character boundary — a close that itself violated the length rule
    would be answered with a protocol error instead of closing anything.
    """
    body = reason.encode("utf-8")[: MAX_CONTROL_PAYLOAD - 2]
    while body:
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            body = body[:-1]
            continue
        break
    return build_frame(OPCODE_CLOSE, struct.pack("!H", code) + body)


def accept_key(client_key: str) -> str:
    """Answer the ``Sec-WebSocket-Key`` a client offered (RFC 6455 §4.2.2).

    The digest is over the key *as the client spelled it* plus the protocol's
    magic string, which is what proves the server understood the handshake
    rather than echoed it.
    """
    # SHA-1 is what RFC 6455 §4.2.2 specifies. It is not used here as a security
    # primitive — the handshake proves protocol comprehension, not identity.
    digest = hashlib.sha1((client_key + _ACCEPT_GUID).encode("ascii"), usedforsecurity=False).digest()
    return base64.b64encode(digest).decode("ascii")
