"""Which entry a per-system record states for one system asked under several names (#784 D50).

A packaged record is filed under ES-DE's system names, and the platform table
names several of them for some systems — libretro's Game Boy is ES-DE's gb and
sgb. Such a system keys the record where the entries it holds for those names
state the same thing; a name it holds no entry for is uncovered, and the answer
names it rather than counting it as a disagreement. Two held entries that
differ key nothing. The save-memory records and the save-format records ask
this one question, so both ask it here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Agreement[EntryT]:
    """What a record states for several names of one system.

    ``held`` and ``uncovered`` split the names asked, each in the order they
    were asked. ``entry`` is the first held name's entry where every held entry
    states the same, and ``None`` where none is held or two held entries
    differ — :attr:`differs` tells those two apart.
    """

    entry: EntryT | None
    held: tuple[str, ...]
    uncovered: tuple[str, ...]

    @property
    def differs(self) -> bool:
        """Whether held entries disagree, rather than none being held."""
        return self.entry is None and bool(self.held)


def agreement[EntryT, StatedT](
    entries: Mapping[str, EntryT], names: tuple[str, ...], stated: Callable[[EntryT], StatedT]
) -> Agreement[EntryT]:
    """The entry *entries* state alike for every one of *names* they hold.

    *stated* is what has to be the same for two entries to agree — a
    save-memory entry's memory ids, a save-format entry as a whole.
    """
    held = tuple(name for name in names if name in entries)
    uncovered = tuple(name for name in names if name not in entries)
    if not held:
        return Agreement(None, held, uncovered)
    first = stated(entries[held[0]])
    if any(stated(entries[name]) != first for name in held[1:]):
        return Agreement(None, held, uncovered)
    return Agreement(entries[held[0]], held, uncovered)
