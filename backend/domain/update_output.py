"""What an update that failed printed, as the journal keeps it: everything about it that is pure.

Contract: the unit whose journal holds a version's own start, how one entry of
``journalctl --output=json`` is read, which run of a unit belongs to a failure,
and what the panel is shown of a run — its tail, each line cut short, and the
admission token hidden. Running ``journalctl`` stays in the adapter; which
failure is asked about stays in the service.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# The unit this program runs as (``UNIT_NAME`` in ``install.sh``, which
# ``tests/scripts/test_install_sh.py`` holds equal to this spelling).
SERVICE_UNIT = "romm-tender"

# A section shows the last this many lines of its run, since a failure's reason
# is at the end, and cuts every line at this many characters.
MAX_LINES = 300
MAX_LINE_CHARS = 500

# How far either side of the installer's record its run is looked for: wider
# than any one run of the installer, which stops waiting for a new version
# within minutes.
RUN_SPAN_SECONDS = 3600

# The query parameter the admission token travels in (``TOKEN_PARAM`` in
# ``host/access.py``, which this layer may not import;
# ``tests/domain/test_update_output.py`` holds the two equal). Every start logs
# the address the panel is loaded from, token included, to its journal.
_TOKEN_PARAM = "token"
_TOKEN = re.compile(rf"\b({_TOKEN_PARAM}=)[^\s&#]+")
_HIDDEN = r"\1[hidden]"

# ``journalctl`` names the run a line belongs to under the first key for what
# the unit's own process printed, and under the second for what the user
# manager said about the unit. A line with neither — the manager failing to
# open a collected unit's file, for one — belongs to no run.
_INVOCATION_KEYS = ("_SYSTEMD_INVOCATION_ID", "USER_INVOCATION_ID")


class OutputGap(StrEnum):
    """Why there is no installer output to show, where the journal was read and holds none."""

    # The journal keeps less than reaches back to the failure.
    ROTATED = "rotated"
    # The journal reaches back to it and holds no run of the installer's unit
    # there: the installer ran by hand, and printed to its terminal.
    TERMINAL = "terminal"


@dataclass(frozen=True)
class JournalEntry:
    """One journal line: when it was written, in epoch seconds, the run it belongs to, and what it says."""

    at: float
    invocation: str | None
    message: str


@dataclass(frozen=True)
class OutputSection:
    """The lines of one run the panel is shown, and how many before them it is not."""

    lines: tuple[str, ...]
    earlier: int

    def to_wire(self) -> dict[str, object]:
        """The JSON shape a section is answered in."""
        return {"lines": list(self.lines), "earlier": self.earlier}


def decode_journal_entry(raw: str) -> JournalEntry | None:
    """One line of ``journalctl --output=json``, or ``None`` where it holds no timestamp.

    ``MESSAGE`` arrives as a list of byte values where it holds bytes that are
    not printable UTF-8 — ``journalctl(1)``, ``--output=json`` — and is decoded
    with the bad ones replaced. A line without one reads as an empty message.
    """
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    try:
        at = int(decoded["__REALTIME_TIMESTAMP"]) / 1_000_000
    except (KeyError, ValueError, TypeError):
        return None
    invocation = next((value for key in _INVOCATION_KEYS if isinstance(value := decoded.get(key), str)), None)
    return JournalEntry(at=at, invocation=invocation or None, message=_message(decoded.get("MESSAGE")))


def _message(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(byte, int) and 0 <= byte < 256 for byte in value):
        return bytes(value).decode("utf-8", errors="replace")
    return ""


def utc_stamp_seconds(stamp: str) -> float | None:
    """*stamp* — ISO-8601 UTC to the second, as the installer and this program write them — in epoch seconds."""
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def journal_runs(entries: Iterable[JournalEntry]) -> list[tuple[JournalEntry, ...]]:
    """*entries* grouped by the run they belong to, in the order each run first appears; lines of no run left out."""
    runs: dict[str, list[JournalEntry]] = {}
    for entry in entries:
        if entry.invocation is not None:
            runs.setdefault(entry.invocation, []).append(entry)
    return [tuple(run) for run in runs.values()]


def run_around(entries: Iterable[JournalEntry], at: float) -> tuple[JournalEntry, ...] | None:
    """The run that was going on at *at* — a record's stamp, cut to the second — or ``None``.

    A run began no later than the second *at* names and wrote its last line no
    earlier: the installer writes its record partway through, and goes on
    printing after it.
    """
    return next((run for run in journal_runs(entries) if run[0].at < at + 1 and run[-1].at >= at), None)


def first_run_from(entries: Iterable[JournalEntry], at: float) -> tuple[JournalEntry, ...] | None:
    """The first run that began at or after *at*, or ``None``."""
    return next((run for run in journal_runs(entries) if run[0].at >= at), None)


def runs_other_than(entries: Iterable[JournalEntry], invocation: str | None) -> tuple[JournalEntry, ...]:
    """Every line of *entries* that belongs to a run other than *invocation*."""
    return tuple(entry for entry in entries if entry.invocation is not None and entry.invocation != invocation)


def hide_token(line: str) -> str:
    """*line* with the value of every admission-token parameter replaced by ``[hidden]``."""
    return _TOKEN.sub(_HIDDEN, line)


def output_section(entries: Sequence[JournalEntry]) -> OutputSection:
    """What the panel is shown of *entries*: the token hidden, each line cut, the last :data:`MAX_LINES` of them."""
    lines = [_cut(hide_token(line)) for entry in entries for line in entry.message.splitlines() or [""]]
    shown = lines[-MAX_LINES:]
    return OutputSection(lines=tuple(shown), earlier=len(lines) - len(shown))


def _cut(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else f"{line[:MAX_LINE_CHARS]}…"
