"""Request heads in, response heads out — against the bytes themselves."""

from __future__ import annotations

import pytest

from lib.http_messages import HttpParseError, build_response_head, parse_request_head


class TestParseRequestHead:
    def test_it_reads_a_plain_get(self):
        head = parse_request_head(b"GET /index.js HTTP/1.1\r\nHost: 127.0.0.1:27737\r\n")

        assert (head.method, head.target, head.version) == ("GET", "/index.js", "HTTP/1.1")
        assert head.header("host") == "127.0.0.1:27737"

    def test_field_names_are_matched_without_regard_to_case(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\nOrIgIn: https://steamloopback.host\r\n")

        assert head.header("origin") == "https://steamloopback.host"
        assert head.header("ORIGIN") == "https://steamloopback.host"

    def test_a_missing_field_answers_the_default(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\nHost: localhost:1\r\n")

        assert head.header("origin") == ""
        assert head.header("origin", "none") == "none"

    def test_a_repeated_field_is_folded_into_one_value(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\nHost: h\r\nAccept: a\r\nAccept: b\r\n")

        assert head.header("accept") == "a, b"

    def test_it_tolerates_bare_line_feeds(self):
        head = parse_request_head(b"GET / HTTP/1.1\nHost: localhost:9\n")

        assert head.header("host") == "localhost:9"

    def test_the_path_excludes_the_query(self):
        head = parse_request_head(b"GET /ws?token=abc&session=s1 HTTP/1.1\r\nHost: h\r\n")

        assert head.path == "/ws"

    def test_query_parameters_are_decoded(self):
        head = parse_request_head(b"GET /ws?token=a%20b&session=s1 HTTP/1.1\r\nHost: h\r\n")

        assert head.query == {"token": "a b", "session": "s1"}

    def test_a_repeated_query_parameter_answers_with_the_last(self):
        head = parse_request_head(b"GET /ws?token=first&token=second HTTP/1.1\r\nHost: h\r\n")

        assert head.query["token"] == "second"

    def test_no_query_is_an_empty_mapping(self):
        head = parse_request_head(b"GET /index.js HTTP/1.1\r\nHost: h\r\n")

        assert head.query == {}

    @pytest.mark.parametrize(
        ("raw", "why"),
        [
            (b"", "empty request head"),
            (b"GET\r\n", "malformed request line"),
            (b"GET /  HTTP/1.1\r\n", "malformed request line"),
            (b"GET / SPDY/3\r\n", "malformed request line"),
            (b"GET / HTTP/1.1\r\nHost 127.0.0.1\r\n", "malformed header line"),
            (b"GET / HTTP/1.1\r\n: nameless\r\n", "malformed header line"),
            (b"GET / HTTP/1.1\r\nHost : spaced\r\n", "malformed header line"),
        ],
    )
    def test_it_refuses_a_malformed_head(self, raw, why):
        with pytest.raises(HttpParseError, match=why):
            parse_request_head(raw)

    def test_it_refuses_obsolete_line_folding(self):
        """RFC 9112 §5.2 requires a server to reject a fold, not to guess at it."""
        with pytest.raises(HttpParseError, match="obsolete line folding"):
            parse_request_head(b"GET / HTTP/1.1\r\nHost: one\r\n  two\r\n")


class TestBuildResponseHead:
    def test_it_writes_the_status_line_and_fields(self):
        raw = build_response_head(200, [("Server", "romm-tender/1.0"), ("Content-Length", "3")])

        assert raw == b"HTTP/1.1 200 OK\r\nServer: romm-tender/1.0\r\nContent-Length: 3\r\n\r\n"

    def test_it_ends_with_the_blank_line(self):
        assert build_response_head(404).endswith(b"\r\n\r\n")

    @pytest.mark.parametrize(
        ("status", "phrase"),
        [(101, "Switching Protocols"), (401, "Unauthorized"), (421, "Misdirected Request"), (426, "Upgrade Required")],
    )
    def test_each_status_this_host_answers_with_carries_its_phrase(self, status, phrase):
        assert build_response_head(status).startswith(f"HTTP/1.1 {status} {phrase}".encode())

    def test_an_unlisted_status_still_produces_a_valid_status_line(self):
        assert build_response_head(599).startswith(b"HTTP/1.1 599 Unknown\r\n")

    def test_header_order_is_the_callers(self):
        raw = build_response_head(401, [("Server", "s"), ("Connection", "close")])

        assert raw.index(b"Server") < raw.index(b"Connection")
