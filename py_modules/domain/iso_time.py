"""Timestamp helpers — ISO-8601 parsing and rendering, pure compute, stdlib only."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to an aware datetime, or None on failure.

    Handles a trailing "Z" defensively (older datetime.fromisoformat versions
    reject it). Returns None for empty/None input or any parse failure — the
    caller decides how to interpret that.
    """
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


def parse_iso_to_epoch(value: str | None) -> float | None:
    """Parse an ISO-8601 timestamp to epoch seconds (UTC), or None on failure."""
    dt = parse_iso(value)
    return dt.timestamp() if dt is not None else None


def epoch_to_iso(epoch: float) -> str:
    """Render epoch seconds as a UTC ISO-8601 string — round-trip inverse of parse_iso_to_epoch."""
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def epoch_to_local_stamp(epoch: float) -> str:
    """Render epoch seconds as a date and time a person reads, in the machine's own zone.

    For text a user opens rather than anything that parses it back, so it drops
    the seconds and the offset that make :func:`epoch_to_iso` a round-trip. The
    zone is the machine's because the alternative — UTC — is an hour or two off
    the clock the reader just looked at, with nothing on the line saying so.
    """
    moment = datetime.fromtimestamp(epoch)
    return f"{moment.day} {moment:%B %Y at %H:%M}"
