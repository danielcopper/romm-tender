"""Where the launcher every Steam shortcut runs through is, and whether it is home.

Produced by the composition root, which is the only thing that can answer it: the
home is derived from the data root the start-up migration settled, and whether
the launcher is in it is what installing it this start reported.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShortcutLauncher:
    """The launcher a shortcut built this run names, and whether that path is its home.

    ``path`` is always a launcher that exists. On an ordinary start it is the
    home under the user's data root; on a start whose data half has not landed
    it is the copy the release ships inside the plugin folder, which is where
    every shortcut pointed before the move and still works. Something real is
    named either way, because a shortcut built against a path nothing put a file
    at cannot start its game.

    ``at_home`` is the narrower question: is ``path`` the home under the data
    root, with this release's launcher in it. It is what repointing an EXISTING
    shortcut turns on, and the two come apart in exactly the case that matters —
    the shipped copy is a real file, so the first question says yes about it
    while this one says no, and a rewrite onto the plugin folder is the very
    fragility the move exists to remove.
    """

    path: str
    at_home: bool
