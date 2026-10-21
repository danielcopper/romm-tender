"""Whether a credential can be carried by an HTTP header at all."""

from __future__ import annotations


def no_header_can_carry(value: str) -> bool:
    """Whether *value* holds a CR, an LF or a character past latin-1.

    ``http.client.putheader`` raises on such a value, and for a CR or LF its
    exception text holds the whole header line — so a credential like this is
    refused before it is put on a request rather than left to fail there.
    """
    return any(ch in "\r\n" or ch > "\xff" for ch in value)
