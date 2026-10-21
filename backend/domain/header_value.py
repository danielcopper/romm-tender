"""Whether a credential can be carried by an HTTP header at all."""

from __future__ import annotations


def no_header_can_carry(value: str) -> bool:
    """Whether *value* holds a CR, an LF or a character past latin-1.

    ``http.client.putheader`` raises on a CR or LF it does not read as a folded
    line, with the whole value in its exception text, and on a character past
    latin-1, which it cannot encode. Every CR and LF is refused here, folded or
    not, before the credential is put on a request.
    """
    return any(ch in "\r\n" or ch > "\xff" for ch in value)
