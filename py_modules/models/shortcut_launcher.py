"""Where the launcher every Steam shortcut runs through is, and whether it is there.

Produced by the composition root, which is the only thing that can answer it: the
path is derived from the data root the start-up migration settled, and whether
the file is at that path is what installing it this start reported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ShortcutLauncher:
    """The launcher's home this start, and whether this release's copy is in it.

    ``path`` is where a shortcut's ``exe`` names it. It is the answer whether or
    not the installation succeeded: a shortcut is built for the one home, never
    for whichever one a given start managed to reach, because a library split
    across two launcher paths is a state nothing later could tell apart.

    ``installed`` is the separate question of whether the file is really there,
    and it is what rewriting an EXISTING shortcut turns on — a shortcut pointed
    at a launcher nothing put there stops its game from starting, and no part of
    this plugin could put the file back afterwards.
    """

    path: str
    installed: bool

    @property
    def start_dir(self) -> str:
        """The working directory a shortcut records beside the exe — where the launcher sits."""
        return os.path.dirname(self.path)
