"""Which files are loaded into Steam, and what has to be true before they are.

Contract: the SELECTION — the file names, the order, and the condition each
choice waits on. Nothing here fetches or evaluates anything; it decides what the
bootstrap will be built around.

**The rule this module exists to make unreachable**: ``globals.js`` is never
loaded where Decky Loader is running. It carries ``@decky/ui``'s module sweep at
import scope, and re-running that sweep under an interface already rendering from
those modules is the crash that takes the Big Picture window down — the only
crash cause ever observed here
(``docs/architecture/frontend-bundles.md``). The choice is therefore taken from
the machine (``machine.decky_loader_is_serving``) and never from the window.

The two answers are not two spellings of one thing. Alone, Tender installs
Steam's React globals itself and loads the panel that carries its own copy of the
package. Beside Decky, the loader has already installed those globals and already
holds a loaded copy of the package, so the panel that shares them is loaded and
nothing is installed at all.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

# What the frontend build produces; ``docs/architecture/frontend-bundles.md``
# owns what each one is.
GLOBALS_BUNDLE = "globals.js"
STANDALONE_PANEL = "index.js"
COEXISTENCE_PANEL = "index-coexistence.js"

STANDALONE = "standalone"
COEXISTENCE = "coexistence"

# The module registry every panel bundle searches. Measured from the debugger
# port answering, it is the last of the start-up readings the device run took —
# the target appears at +0.20 s, is named at +0.6 s, and this exists at
# +0.84 s — so it is what "Steam is ready for us" means here.
_STEAM_REGISTRY_READY = 'typeof window["webpackChunksteamui"] !== "undefined"'

# Decky's copy of ``@decky/ui``, read by the coexistence bundle while it
# evaluates. Waiting for it is not a reading of whether Decky is there — the
# machine has already answered that — it is waiting for something known to be
# coming: on the reference machine, measured from the debugger port answering,
# ``DFL`` was still undefined at +4.65 s and Decky had finished at +10.6 s with
# ten plugins loaded.
_DECKY_UI_READY = 'typeof window["DFL"] !== "undefined"'


@dataclass(frozen=True)
class BundleChoice:
    """One decision about what to load, with the reason it was taken."""

    files: tuple[str, ...]
    kind: str
    because: str
    ready_when: str

    @property
    def globals_at(self) -> int | None:
        """Where in :attr:`files` the bundle that installs the globals sits.

        The bootstrap calls that bundle's installer after importing it and
        before importing the panel, so WHICH of the files it is is answered
        where the files are chosen rather than counted out by the evaluated
        source — which would be reading "the first of two" off an order it does
        not own. ``None`` where the choice carries no such bundle, and then
        nothing is installed at all.
        """
        return self.files.index(GLOBALS_BUNDLE) if GLOBALS_BUNDLE in self.files else None


def choose_bundles(*, decky_is_serving: bool) -> BundleChoice:
    """Pick the bundles to load, given whether Decky Loader is serving."""
    if decky_is_serving:
        return BundleChoice(
            files=(COEXISTENCE_PANEL,),
            kind=COEXISTENCE,
            because="Decky Loader is serving, so the panel takes @decky/ui and the React globals it installed",
            ready_when=f"{_STEAM_REGISTRY_READY} && {_DECKY_UI_READY}",
        )
    return BundleChoice(
        files=(GLOBALS_BUNDLE, STANDALONE_PANEL),
        kind=STANDALONE,
        because="nothing else is loading into Steam, so Tender installs the React globals itself",
        ready_when=_STEAM_REGISTRY_READY,
    )


def bundle_digest(static_root: str, files: tuple[str, ...]) -> str:
    """A digest over the files *files* names under *static_root*.

    One of the three things whose change lets the crash watchdog start trying
    again, so it has to change when the panel changes and not otherwise. A file
    that is not there is folded in by name rather than skipped: its arrival is
    a change, and a digest that ignored it would read the same before and after.
    """
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode("utf-8"))
        try:
            with open(os.path.join(static_root, name), "rb") as handle:
                digest.update(handle.read())
        except OSError:
            digest.update(b"\0absent")
    return digest.hexdigest()
