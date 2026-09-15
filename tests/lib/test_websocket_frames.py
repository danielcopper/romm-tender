"""The WebSocket codec against a table of bytes.

The vectors are hand-written from RFC 6455 §5.7's own examples plus the boundary
lengths the two length escapes sit on. They are byte strings rather than
assertions about behaviour, because that is what the codec's correctness
actually is: a frame either looks like the specification says or it does not.
"""

from __future__ import annotations

import pytest

from lib.websocket_frames import (
    MAX_CONTROL_PAYLOAD,
    OPCODE_BINARY,
    OPCODE_CLOSE,
    OPCODE_CONTINUATION,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    WebSocketProtocolError,
    accept_key,
    apply_mask,
    build_frame,
    close_frame,
    header_length,
    parse_frame_header,
    text_frame,
)

# (name, header bytes, fin, opcode, masked, payload length, mask)
_HEADER_VECTORS = [
    ("unmasked empty text", b"\x81\x00", True, OPCODE_TEXT, False, 0, b""),
    ("unmasked 5-byte text", b"\x81\x05", True, OPCODE_TEXT, False, 5, b""),
    ("masked 5-byte text", b"\x81\x85\x37\xfa\x21\x3d", True, OPCODE_TEXT, True, 5, b"\x37\xfa\x21\x3d"),
    ("non-final fragment", b"\x01\x03", False, OPCODE_TEXT, False, 3, b""),
    ("continuation", b"\x80\x02", True, OPCODE_CONTINUATION, False, 2, b""),
    ("125 — the last 7-bit length", b"\x81\x7d", True, OPCODE_TEXT, False, 125, b""),
    ("126 — first 16-bit length", b"\x81\x7e\x00\x7e", True, OPCODE_TEXT, False, 126, b""),
    ("65535 — last 16-bit length", b"\x81\x7e\xff\xff", True, OPCODE_TEXT, False, 65535, b""),
    ("65536 — first 64-bit length", b"\x81\x7f\x00\x00\x00\x00\x00\x01\x00\x00", True, OPCODE_TEXT, False, 65536, b""),
    ("ping", b"\x89\x00", True, OPCODE_PING, False, 0, b""),
    ("pong", b"\x8a\x00", True, OPCODE_PONG, False, 0, b""),
    ("close", b"\x88\x02", True, OPCODE_CLOSE, False, 2, b""),
]


class TestParseFrameHeader:
    @pytest.mark.parametrize(
        ("raw", "fin", "opcode", "masked", "length", "mask"),
        [pytest.param(*vector[1:], id=vector[0]) for vector in _HEADER_VECTORS],
    )
    def test_it_reads_each_vector(self, raw, fin, opcode, masked, length, mask):
        header = parse_frame_header(raw)

        assert (header.fin, header.opcode, header.masked) == (fin, opcode, masked)
        assert header.payload_length == length
        assert header.mask == mask

    @pytest.mark.parametrize(
        "raw",
        [pytest.param(vector[1], id=vector[0]) for vector in _HEADER_VECTORS],
    )
    def test_header_length_agrees_with_the_vector(self, raw):
        """The size read from two bytes is the size the whole vector has."""
        assert header_length(raw[:2]) == len(raw)

    def test_a_declared_length_is_known_before_the_payload_exists(self):
        """The cap's whole premise: length comes from the header, not the body."""
        header = parse_frame_header(b"\x81\x7f\x00\x00\x00\x00\x00\x01\x00\x00")

        assert header.payload_length == 65536

    def test_it_refuses_a_reserved_bit(self):
        with pytest.raises(WebSocketProtocolError, match="reserved bits"):
            parse_frame_header(b"\xc1\x00")

    def test_it_refuses_an_unknown_opcode(self):
        with pytest.raises(WebSocketProtocolError, match="unknown opcode"):
            parse_frame_header(b"\x83\x00")

    def test_it_refuses_a_fragmented_control_frame(self):
        with pytest.raises(WebSocketProtocolError, match="must not be fragmented"):
            parse_frame_header(b"\x09\x00")

    def test_it_refuses_an_oversized_control_frame(self):
        with pytest.raises(WebSocketProtocolError, match="at most 125"):
            parse_frame_header(b"\x89\x7e\x01\x00")

    def test_it_refuses_a_header_shorter_than_declared(self):
        with pytest.raises(WebSocketProtocolError, match="declares 4 bytes"):
            parse_frame_header(b"\x81\x7e")

    @pytest.mark.parametrize("raw", [b"", b"\x81"])
    def test_header_length_refuses_fewer_than_two_bytes(self, raw):
        with pytest.raises(WebSocketProtocolError, match="at least 2 bytes"):
            header_length(raw)


class TestBuildFrame:
    @pytest.mark.parametrize(
        ("raw", "fin", "opcode", "masked", "length", "mask"),
        [pytest.param(*vector[1:], id=vector[0]) for vector in _HEADER_VECTORS],
    )
    def test_it_writes_the_header_each_vector_declares(self, raw, fin, opcode, masked, length, mask):
        built = build_frame(opcode, b"\x00" * length, fin=fin, mask=mask)

        assert built[: len(raw)] == raw

    def test_the_rfc_example_round_trips(self):
        """RFC 6455 §5.7: a masked ``Hello`` on the wire, byte for byte."""
        wire = b"\x81\x85\x37\xfa\x21\x3d\x7f\x9f\x4d\x51\x58"

        assert build_frame(OPCODE_TEXT, b"Hello", mask=b"\x37\xfa\x21\x3d") == wire

        header = parse_frame_header(wire[:6])
        assert apply_mask(wire[6:], header.mask) == b"Hello"

    def test_text_frame_encodes_utf8(self):
        assert text_frame("héllo") == build_frame(OPCODE_TEXT, "héllo".encode())

    def test_it_refuses_an_oversized_control_payload(self):
        with pytest.raises(WebSocketProtocolError, match="at most 125"):
            build_frame(OPCODE_PING, b"x" * (MAX_CONTROL_PAYLOAD + 1))

    def test_it_refuses_a_mask_of_the_wrong_width(self):
        with pytest.raises(WebSocketProtocolError, match="a mask is 4 bytes"):
            build_frame(OPCODE_TEXT, b"hi", mask=b"\x01\x02")

    def test_a_binary_frame_is_encodable_even_though_the_host_refuses_one(self):
        """The codec is the format; refusing binary is the connection's rule."""
        assert parse_frame_header(build_frame(OPCODE_BINARY, b"\x00")[:2]).opcode == OPCODE_BINARY


class TestApplyMask:
    def test_it_is_its_own_inverse(self):
        masked = apply_mask(b"some payload", b"\x01\x02\x03\x04")

        assert apply_mask(masked, b"\x01\x02\x03\x04") == b"some payload"

    def test_an_empty_mask_is_the_identity(self):
        assert apply_mask(b"unmasked", b"") == b"unmasked"

    def test_it_refuses_a_mask_of_the_wrong_width(self):
        with pytest.raises(WebSocketProtocolError, match="a mask is 4 bytes"):
            apply_mask(b"x", b"\x01")


class TestCloseFrame:
    def test_it_carries_the_code_and_reason(self):
        frame = close_frame(1009, "too big")
        header = parse_frame_header(frame[:2])

        assert header.opcode == OPCODE_CLOSE
        assert frame[2:4] == b"\x03\xf1"
        assert frame[4:] == b"too big"

    def test_a_long_reason_is_truncated_rather_than_refused(self):
        """A close that broke the control-frame rule would close nothing."""
        frame = close_frame(1000, "x" * 500)

        assert parse_frame_header(frame[:2]).payload_length <= MAX_CONTROL_PAYLOAD

    def test_truncation_lands_on_a_character_boundary(self):
        """A half-written multi-byte character would make the reason undecodable."""
        frame = close_frame(1000, "ä" * 200)

        frame[4:].decode("utf-8")


class TestAcceptKey:
    def test_the_rfc_example(self):
        """RFC 6455 §1.3's worked handshake."""
        assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

    def test_a_different_key_answers_differently(self):
        assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") != accept_key("AAAAAAAAAAAAAAAAAAAAAA==")
