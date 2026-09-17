"""The record that says an injection took the Steam UI down, and what follows.

Contract: whether this process may evaluate anything into Steam, and the one
piece of state that answers it. It performs no injection and reads no page — it
is handed a fingerprint and told afterwards what happened.

**Why a record and not a counter.** The issue this replaces asked for three
crashes within a minute to stop injecting. That cannot be observed. What was
measured on the device is that the crash takes every other page target at once
while ``SharedJSContext`` survives and the debugger keeps answering, and that
nothing recovers within 45 s. The rest follows rather than being measured: the
marker this cut plants lives on that surviving target — it did not exist in that
provocation — so the injector would find it, read "already injected", and never
try a second time. Within one Steam session the crash therefore happens at most
once, and counting inside a session counts to one for ever.

What is observable is the OTHER side of it: write a record before evaluating,
clear it once the interface is still there some seconds later, and an open record
found at the next start is a crash that has already happened. That moves the
count from "per minute" to "per start", which is the unit the user experiences.

**Two consecutive failures, not three.** One can be anything — a Steam update
mid-start, a machine put to sleep. Two is evidence. Three dead Steam starts is
too much to ask of someone who has no reason to suspect this program.

**It starts trying again on its own.** The record carries a fingerprint of the
three things that could have repaired the fault — this program's version, the
bundles' bytes, Steam's build — and a change in any of them drops the count. The
user updates something and it works again, with no file to find and nothing to
delete.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

WATCHDOG_FILENAME = "injection-guard.json"

# Two starts that ended with an open record stop the injection. See the module
# docstring for why it is not three.
FAILURE_LIMIT = 2

# The switch, and the two words it takes. It is spelled here rather than beside
# the injector because this module writes the sentence that names it, and a
# second spelling of an environment variable is a switch that silently does
# nothing.
INJECT_ENV = "TENDER_INJECT"
INJECT_OFF = "off"
INJECT_FORCE = "force"


@dataclass(frozen=True)
class Fingerprint:
    """The three readings whose change means the fault may be repaired."""

    tender: str
    bundle: str
    steam: str


@dataclass(frozen=True)
class Verdict:
    """What the record says about injecting now, and the sentence for the log."""

    may_inject: bool
    failures: int
    line: str


class CrashWatchdog:
    """The open-record file, and the decision taken from it."""

    def __init__(self, path: str, *, limit: int = FAILURE_LIMIT, override: str = "") -> None:
        self._path = path
        self._limit = limit
        self._override = override
        self._failures = 0
        self._fingerprint: Fingerprint | None = None
        self._armed = False

    @property
    def path(self) -> str:
        """Where the record lives."""
        return self._path

    @property
    def armed(self) -> bool:
        """Is there a record this process opened and has not yet answered for?

        Exactly one of :meth:`survived`, :meth:`inconclusive` and
        :meth:`stays_open` answers for an :meth:`arm`. This is how a shutdown
        tells "nobody has looked yet" — which it must close, or the next start
        reads it as a crash — from "the reading was taken", which it must leave
        exactly as the reading left it.
        """
        return self._armed

    def judge(self, now: Fingerprint) -> Verdict:
        """Resolve what the last attempt left behind, and answer whether to inject.

        Reads the record, turns an open one into a failure, drops the count when
        the fingerprint has moved, and writes the resolution back before
        answering — so a process that dies between this call and the injection
        cannot count the same record twice.
        """
        stored = self._read()
        counted = stored.get("failures")
        self._failures = counted if isinstance(counted, int) else 0
        previous = _fingerprint_from(stored.get("fingerprint"))
        self._fingerprint = now

        if previous is not None and previous != now:
            dropped, self._failures = self._failures, 0
            self._write(open_record=False)
            return Verdict(True, 0, _resumed_line(previous, now, dropped))

        if stored.get("open"):
            self._failures += 1
        self._write(open_record=False)
        return self._answer(now)

    def arm(self, fingerprint: Fingerprint) -> None:
        """Open a record, before anything is evaluated into Steam."""
        self._fingerprint = fingerprint
        self._armed = True
        self._write(open_record=True)

    def survived(self) -> None:
        """Close the record: the interface was still there afterwards."""
        self._failures = 0
        self._armed = False
        self._write(open_record=False)

    def inconclusive(self) -> None:
        """Close the record without counting it — nothing was established.

        The crash signature is specific: the debugger keeps answering and
        ``SharedJSContext`` survives while everything else goes. Where the
        debugger stopped answering, where nothing but the renderer was open to
        begin with, or where this process is shutting down, the interface was not
        observed at all — and an unobserved attempt recorded as a failure would
        stop the injection over a user closing Steam.
        """
        self._armed = False
        self._write(open_record=False)

    def stays_open(self) -> None:
        """Leave the record open — the interface did not come back.

        Writes nothing, because the record already says so. What it does is mark
        the reading as TAKEN, so a shutdown afterwards does not close what this
        reading deliberately left open for the next start to find.
        """
        self._armed = False

    # -- the file --------------------------------------------------------------

    def _answer(self, now: Fingerprint) -> Verdict:
        """Build the verdict, with the sentence that belongs in the log."""
        if self._failures < self._limit:
            if self._failures:
                return Verdict(
                    True,
                    self._failures,
                    f"the last injection left Steam's interface gone ({self._failures} of {self._limit}); "
                    f"loading the panel again",
                )
            return Verdict(True, 0, "")
        if self._override == INJECT_FORCE:
            return Verdict(
                True,
                self._failures,
                f"{self._failures} attempts in a row ended with Steam's interface gone; loading the panel anyway "
                f"because {INJECT_ENV}={INJECT_FORCE} is set",
            )
        return Verdict(
            False,
            self._failures,
            f"not loading the panel into Steam: {self._failures} attempts in a row ended with Steam's interface "
            f"gone. Tender version {now.tender}, Steam build {now.steam or 'unknown'}. It will try again by itself "
            f"when any of those changes; to try now, start the backend with {INJECT_ENV}={INJECT_FORCE}.",
        )

    def _read(self) -> dict[str, object]:
        """The record as stored, or an empty one when there is nothing readable."""
        try:
            with open(self._path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return {}
        return stored if isinstance(stored, dict) else {}

    def _write(self, *, open_record: bool) -> None:
        """Replace the record. Written through a temporary file, like the port note."""
        payload = {
            "fingerprint": asdict(self._fingerprint) if self._fingerprint is not None else None,
            "failures": self._failures,
            "open": open_record,
        }
        temporary = f"{self._path}.{os.getpid()}.tmp"
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(temporary, self._path)
        except OSError:
            # A record that cannot be written leaves the guard unable to count,
            # which is the lenient direction and the right one: this file exists
            # to stop a crash loop, and refusing to load the panel because a
            # state directory is read-only would be a fault of its own.
            return


def _fingerprint_from(stored: object) -> Fingerprint | None:
    """Read a fingerprint back out of the record, or ``None`` if it is not one."""
    if not isinstance(stored, dict):
        return None
    values = [stored.get(field) for field in ("tender", "bundle", "steam")]
    if not all(isinstance(value, str) for value in values):
        return None
    tender, bundle, steam = values
    return Fingerprint(tender=str(tender), bundle=str(bundle), steam=str(steam))


def _resumed_line(previous: Fingerprint, now: Fingerprint, dropped: int) -> str:
    """What to say when the fingerprint moved; nothing when there was nothing to drop."""
    if not dropped:
        return ""
    return (
        f"{_what_moved(previous, now)} changed since the last attempt, so the {dropped} failed start(s) are forgotten"
    )


def _what_moved(previous: Fingerprint, now: Fingerprint) -> str:
    """Name the parts of the fingerprint that differ, for the log."""
    moved = [
        name
        for name, before, after in (
            ("Tender's version", previous.tender, now.tender),
            ("the panel bundles", previous.bundle, now.bundle),
            ("Steam's build", previous.steam, now.steam),
        )
        if before != after
    ]
    return ", ".join(moved)
