"""Where the launcher every Steam shortcut runs through is, and whether it is home.

Produced by the composition root, which is the only thing that can answer it: the
home is derived from the bin root this run was told about, and whether the
launcher is in it is what installing it this start reported.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShortcutLauncher:
    """The launcher a shortcut built this run names, and whether that path is its home.

    ``path`` follows the INSTALL rather than the intent, and has three cases.
    Where this start got the launcher into its home in the bin root, it is that
    home. Where the write failed, it is the copy the release ships beside the
    program, which is a real file and does launch a game. The third case is the
    one where even that is not a real file: a package shipped without its
    launcher, which is also the reason the install failed. There is nowhere
    honest left to point then, and naming the home would claim a launcher no
    start ever wrote.

    ``at_home`` is the narrower question: is ``path`` the home in the bin root,
    with this release's launcher in it. It is what repointing an EXISTING
    shortcut turns on, and the two answers come apart wherever the first is the
    shipped copy — repointing a shortcut at the program's own install directory
    is the very fragility the move exists to remove.
    """

    path: str
    at_home: bool
