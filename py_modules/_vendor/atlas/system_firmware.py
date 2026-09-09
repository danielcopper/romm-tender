"""Which systems cannot run without firmware — the half a ``.info`` cannot state.

A libretro core declares firmware one file at a time, with a boolean per file
(``firmwareN_opt``). The format has no way to say "one of these three" and no
way to say "this system does not start without one of them", so an author who
knows a PlayStation needs one regional BIOS has two lossy moves and the
deployed catalogue takes both: Beetle PSX marks the three region images
required and RetroArch labels all three "Missing, Required", SwanStation marks
all five optional and RetroArch labels none of them required.
:mod:`atlas.firmware` reports ``need`` straight from ``firmwareN_opt``, which is
faithful and — over an all-optional PlayStation — reads as "nothing required"
about a machine that will not boot.

**RetroArch labels; it does not refuse.** At the pinned revision ``a79435a``
``core_info_list_update_missing_firmware`` has two callers —
``menu/menu_displaylist.c:880`` and ``ui/drivers/ui_qt.cpp:1238`` — and both
build display rows rather than loading anything. The setting that would block a
load, ``check_firmware_before_loading``, is ``"false"`` in both configurations
RetroDECK ships; it is **not** a fact about the pinned source, where the name
does not appear at all, but a reading of the deployed 1.22.2 build and its two
config files. The dates run the opposite way to the obvious guess — the pin was
committed 2026-07-23 and the deployed build was built ``Nov 20 2025`` — so the
setting is absent from the *newer* source and present in the *older* build,
which reads as upstream having removed it. That makes the observed refusal the
**core's** rather than the frontend's, which is what gives this table its case:
``docs/research/system-firmware.md`` carries the readings.

The missing half is a fact about the **system**, not about the core, which is
the whole reason it cannot live on a rule card. mGBA, snes9x and gambatte
declare everything optional, and so does SwanStation — but SwanStation says it
about a machine observed refusing to start. Same declaration, and what could
tell them apart is the system behind it. (Whether those three are *right* is
not this module's to say. Of the three, only gambatte's ``systemname`` —
``Game Boy/Game Boy Color`` — is recorded here at all, as ``open``. mGBA's is
``Game Boy/Game Boy Color/Game Boy Advance``, a third distinct string with no
entry, and snes9x's has none either.) So the table is keyed by system, and its
verdicts are the three a reader actually needs: the system does not run without
firmware, it does, or nobody has established which.

**Open is a value, not an absence.** A system whose cores disagree is a system
someone should look at, and recording it as ``open`` is what makes it visible
without anyone pretending to know. Six of the seven shipped entries are exactly
that, and the tripwire beside this loader
(``tests/test_system_firmware_tripwire.py``) is what puts them there: it
recomputes the disagreements from the deployed catalogue and fails when it
finds one this table does not carry.

**How this reaches an answer.** :mod:`atlas.firmware` reads the table in one
place (``_stating_system_firmware``) and puts what it says on every core of a
firmware answer as ``system_firmware``, joining these ``systemname`` keys to
the system ids an answer speaks through
:func:`atlas.firmware.system_firmware_system`. A core whose system cannot run
without an image, and which the entry does not excuse, stops reporting
``requirements_met`` true while none of its declared images is in place — and
:data:`CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE` rides beside every **stated**
verdict so the second source is marked rather than implied. What does **not**
move is the declaration: every ``need`` on a requirement stays the value the
core's own ``.info`` carries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ._data import packaged_text

# Packaged-data schema version, strict for the reason every loader here is
# strict: a malformed build fails loudly rather than answering out of a table
# whose own account of itself does not add up.
SYSTEM_FIRMWARE_SCHEMA = 1

# The system does not start without firmware — the state a per-file optional
# flag cannot express and the reason this table exists.
VERDICT_CANNOT_RUN_WITHOUT = "cannot-run-without-firmware"
# It does start with none present. Recorded rather than left out, because
# "checked, and the answer is no" is knowledge and an absent entry is not.
VERDICT_RUNS_WITHOUT = "runs-without-firmware"
# Nobody has established which. A real value: it is how a system leaves the
# tripwire recorded and visible without a verdict being invented for it.
VERDICT_OPEN = "open"
SYSTEM_FIRMWARE_VERDICTS = (
    VERDICT_CANNOT_RUN_WITHOUT,
    VERDICT_RUNS_WITHOUT,
    VERDICT_OPEN,
)

# The repo's own evidence levels (CLAUDE.md): verified, derived, open. The
# finer markers the ``source`` prose uses — ``[V-live]``, ``[V-binary]``,
# ``[V-script]`` — say how a reading was made and all sit under ``[V]``.
#
# These bracket forms are **this repository's documentation notation**, not
# contract vocabulary: they are what a research page and a ``source`` string
# spell, and they stay inside the package.
EVIDENCE_VERIFIED = "[V]"
EVIDENCE_DERIVED = "[D]"
EVIDENCE_OPEN = "[O]"
EVIDENCE_LEVELS = (EVIDENCE_VERIFIED, EVIDENCE_DERIVED, EVIDENCE_OPEN)

# The same three levels as the **contract** publishes them. A client renders
# this value, and ``verified`` is readable where ``[V]`` is not; every other
# closed value the contract carries is a lowercase word, and this one is no
# exception. Keeping two spellings is the price of that boundary, and the map
# below is what makes it a boundary rather than a second source of truth: it
# is total over :data:`EVIDENCE_LEVELS`, so no marker can reach a client
# unspelled.
EVIDENCE_WORD_VERIFIED = "verified"
EVIDENCE_WORD_DERIVED = "derived"
EVIDENCE_WORD_OPEN = "open"
EVIDENCE_WORDS: "Mapping[str, str]" = MappingProxyType(
    {
        EVIDENCE_VERIFIED: EVIDENCE_WORD_VERIFIED,
        EVIDENCE_DERIVED: EVIDENCE_WORD_DERIVED,
        EVIDENCE_OPEN: EVIDENCE_WORD_OPEN,
    }
)

# The two words a *stated* verdict can rest on, and therefore the two the mark
# below can carry. ``open`` is missing on purpose rather than by oversight:
# the mark rides only an established verdict, and :func:`_system` refuses an
# established verdict that cites :data:`EVIDENCE_OPEN` — so ``[O]`` cannot
# reach the mark, and publishing it as a value a client may see would document
# a branch nothing can take.
STATED_EVIDENCE_WORDS = (EVIDENCE_WORD_VERIFIED, EVIDENCE_WORD_DERIVED)

# The mark an answer carries where this table makes an **established**
# statement: the system it spoke about, and the evidence level that statement
# rests on. Nothing else — the verdict is a field of the answer's own
# (:attr:`atlas.firmware.CoreFirmware.system_firmware`), and one fact stated
# twice is one fact that can disagree with itself.
#
# It rides ``cannot-run-without-firmware`` (both of the core-level shapes that
# verdict produces) and ``runs-without-firmware``, and it deliberately does
# **not** ride ``open``. A caveat here is a degradation with a stable code
# that a client acts on, not a general provenance note; ``open`` is not a
# degradation but the field's own value, and the field is serialized on every
# core, so the world knowledge is already marked where a reader meets it. A
# note that adds nothing to the field it sits beside devalues the notes that
# do carry something to act on.
#
# It lives here rather than beside the firmware caveat codes because it is
# about this table: the code exists so that a verdict drawn from packaged
# world knowledge is *marked* as that rather than implied, which is the
# repository's first rule applied to the one field that started crossing it.
CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE = "system-firmware-world-knowledge"


@dataclass(frozen=True, slots=True)
class CoreAlternative:
    """One core whose all-optional declaration is right, and why.

    A system that needs firmware can still have a core that runs without a
    file, because the core supplies its own substitute. That core's catalogue
    entry declares everything optional and is *correct* to, so it has to be
    nameable.

    **What it is for, precisely.** It is what keeps a system's requirement off
    a core that carries its own substitute: the answer reads it, and a core
    named here answers
    :data:`~atlas.firmware.SYSTEM_FIRMWARE_CORE_ALTERNATIVE` where its
    unexcused siblings answer
    :data:`~atlas.firmware.SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT`. It is *not*
    what makes the tripwire's derivation pass: that derivation compares whole
    systems and never consults this field, so removing it changes no verdict
    there and only leaves the staleness check with nothing to compare.

    Cores are named the way the catalogue names them — the ``.info`` stem
    without ``_libretro`` — which is what
    :func:`atlas.firmware._core_alternative_key` joins on, so a standalone
    emulator has no spelling here and can never be excused.

    ``reason`` carries the evidence rather than a bare exemption: what the core
    offers, and what was seen.
    """

    core: str
    reason: str


@dataclass(frozen=True, slots=True)
class SystemFirmware:
    """One system's verdict, the level it rests on, and what that level cites.

    ``alternatives`` is empty on every verdict but
    :data:`VERDICT_CANNOT_RUN_WITHOUT`, where alone the question "is this core
    understating?" can arise. It is not a completeness claim: it holds the
    cores whose substitute has been established, and a core absent from it is
    one nobody has looked at.
    """

    system: str
    verdict: str
    evidence: str
    source: str
    alternatives: tuple[CoreAlternative, ...]


def _expect_str(value: Any, where: str) -> str:
    # Blank-not-just-empty, because every string this table holds is prose a
    # person is meant to read. A reason written as `" "` passes a truthiness
    # check and says exactly as much as `""` does, which is the shape a bare
    # exemption would take once the empty one is refused.
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-blank string, got {value!r}")
    return value


def _alternatives(entry: Any, where: str) -> tuple[CoreAlternative, ...]:
    """The exempted cores of one entry, each with the reason it is exempt."""
    if not isinstance(entry, dict) or not entry:
        raise ValueError(
            f"{where}: cores_supplying_an_alternative must be a non-empty object mapping a core "
            f"key to its reason, got {entry!r}"
        )
    return tuple(
        CoreAlternative(
            core=_expect_str(core, f"{where}: core key"),
            reason=_expect_str(reason, f"{where}: {core!r} reason"),
        )
        for core, reason in entry.items()
    )


def _system(name: str, entry: Any) -> SystemFirmware:
    where = f"system_firmware {name!r}"
    _expect_str(name, "system_firmware: system key")
    required = {"verdict", "evidence", "source"}
    if not isinstance(entry, dict) or not required <= set(entry):
        raise ValueError(
            f"{where}: an entry states at least verdict, evidence and source, got {entry!r}"
        )
    surplus = set(entry) - required - {"cores_supplying_an_alternative"}
    if surplus:
        raise ValueError(f"{where}: unknown key(s) {sorted(surplus)}")
    verdict = _expect_str(entry["verdict"], f"{where}: verdict")
    if verdict not in SYSTEM_FIRMWARE_VERDICTS:
        raise ValueError(
            f"{where}: verdict must be one of {SYSTEM_FIRMWARE_VERDICTS}, got {verdict!r}"
        )
    evidence = _expect_str(entry["evidence"], f"{where}: evidence")
    if evidence not in EVIDENCE_LEVELS:
        raise ValueError(
            f"{where}: evidence must be one of {EVIDENCE_LEVELS}, got {evidence!r}"
        )
    # The two halves of one claim, so they are held together: a verdict that
    # states something cannot rest on an open question, and a question recorded
    # as open cannot cite a verified reading of the answer.
    if (verdict == VERDICT_OPEN) != (evidence == EVIDENCE_OPEN):
        raise ValueError(
            f"{where}: verdict {verdict!r} and evidence {evidence!r} disagree — "
            f"{VERDICT_OPEN!r} is the one verdict that carries {EVIDENCE_OPEN!r}, and the one "
            "that may"
        )
    # Key presence, not value truthiness: `"cores_supplying_an_alternative":
    # null` states the field on an entry that must not carry it, and reading it
    # through `.get()` let exactly that through on any verdict.
    states_alternatives = "cores_supplying_an_alternative" in entry
    if states_alternatives and verdict != VERDICT_CANNOT_RUN_WITHOUT:
        raise ValueError(
            f"{where}: only a {VERDICT_CANNOT_RUN_WITHOUT!r} entry names cores supplying an "
            f"alternative — over {verdict!r} there is no understatement to excuse"
        )
    return SystemFirmware(
        system=name,
        verdict=verdict,
        evidence=evidence,
        source=_expect_str(entry["source"], f"{where}: source"),
        alternatives=(
            _alternatives(entry["cores_supplying_an_alternative"], where)
            if states_alternatives
            else ()
        ),
    )


def load_system_firmware(text: str | None = None) -> dict[str, SystemFirmware]:
    """Load the packaged table (or *text* when supplied, for tests).

    Fail-closed throughout, because every refusal here is a claim a reader
    could otherwise act on: an unreadable schema, an entry missing a field, an
    unknown verdict or evidence level, the two disagreeing about whether the
    question is open, and an exemption recorded against a system nothing claims
    needs firmware each fail the load.

    Keys are libretro ``systemname`` strings verbatim, which is what the
    ``.info`` catalogue groups firmware by and therefore what the tripwire's
    derivation produces. They are deliberately not atlas's own system ids: one
    ``systemname`` can cover two catalogue systems (``Game Boy/Game Boy
    Color``). The answer that reads this table makes that translation at its
    own edge (:func:`atlas.firmware.system_firmware_system`), and it makes it
    through the map an answer's requirements already came through, so a key
    naming several machines reaches the one id that map rules for it and not
    its siblings — narrower than the entry rather than wider, because widening
    would state a verdict about a machine nobody recorded it for.
    """
    if text is None:
        text = packaged_text("system_firmware.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != SYSTEM_FIRMWARE_SCHEMA:
        raise ValueError(
            f"system_firmware: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {SYSTEM_FIRMWARE_SCHEMA})"
        )
    systems = raw.get("systems")
    if not isinstance(systems, dict) or not systems:
        raise ValueError("system_firmware: 'systems' must be a non-empty object")
    return {name: _system(name, entry) for name, entry in systems.items()}
