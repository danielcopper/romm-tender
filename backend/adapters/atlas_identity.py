"""Which emulator an atlas answer is about — the one place that decides.

The resolver answers two questions this plugin joins: which emulators launch a
system (a catalogue entry) and what each of them wants (a firmware core). Both
answers state the emulator under ``emulator``, in the spelling its launch command
uses — ``dolphin_libretro.so`` for a libretro entry, ``DOLPHIN`` for the
standalone one beside it — and that field is the join. Reading it in one function
is what keeps the two sides from drifting into two spellings of one emulator,
which is the defect this module exists to prevent.

Three fields on the same objects look like they would answer and must not be
used for it:

- ``label`` is presentation. ES-DE lists one ``pcsx2_libretro.so`` twice, as
  ``LRPS2`` and as ``PCSX2``, and EmuDeck lists one Cemu as ``Cemu (Native)`` and
  ``Cemu (Proton)``. A label identifies a ROW, and two rows can be one emulator.
- ``core_so`` identifies a libretro entry only; it is ``None`` for every
  standalone emulator, which is the hole this join closes.
- ``token`` on a caveat names the emulator in the resolver's own packaged-card
  vocabulary, which upstream states is a different vocabulary from this one. It
  coincides with the identity for the cards on the reference machine and is not
  guaranteed to.

``None`` is a real state, not a failure: the resolver could not identify the
emulator behind a row — EmuDeck's two ``n3ds`` entries are RetroArch launches of
Windows ``.dll`` cores, which it classifies standalone and cannot name. Such an
entry cannot be scoped to, so nothing may be ruled out for it; every caller
treats it as "could not be established" and never as "nothing needed".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def emulator_identity(entry: Any) -> str | None:
    """The identity of one catalogue entry or firmware core, or ``None`` where none was stated."""
    return entry.emulator
