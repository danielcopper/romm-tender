"""Where one game's emulator keeps its savestates — the one savestate fact the plugin reads.

A savestate is synced nowhere, so the only question the plugin puts about one is
where it sits: a rename that moves a ROM has to carry the states named after it.
That question is the resolver's, asked of the same catalogue entry the save
answer comes from, and this module is the vocabulary its answer is restated in so
``services/`` never sees a resolver type.

Three outcomes, and a caller keeps them apart. A :class:`SavestateLocation` names
a directory. :class:`NoSavestates` is a stated fact that the emulator has no
savestates at all — nothing to carry, and not a failure. ``None`` from the seam
means nothing could be established, which also carries nothing, but for the
opposite reason: the states may well exist somewhere nobody could name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SavestateLocation:
    """The directory an emulator keeps one game's savestates in.

    ``root_kind`` is the anchor ``directory`` hangs off, in the resolver's own
    vocabulary — ``content_directory`` for states written beside the game.
    ``fallback_directory`` is the unsorted root of the
    :data:`domain.save_answer.SORTED_DIR_MISSING` note, ``None`` where the
    placement is not conditional.
    """

    directory: str
    root_kind: str
    fallback_directory: str | None = None


@dataclass(frozen=True, slots=True)
class NoSavestates:
    """The emulator has no savestates at all — an answer, not a refusal."""
