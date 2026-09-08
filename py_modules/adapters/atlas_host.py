"""Atlas host grants — the capabilities this runtime hands the vendored resolver.

The vendored `emu-atlas <https://github.com/danielcopper/emu-atlas>`_ resolver
discovers what it can, states plainly where it found nothing, and takes what a
host hands over ahead of anything it found. A grant is **process-global** — one
running program, one answer — so it belongs to none of the three atlas seams:
:mod:`adapters.atlas_firmware`, :mod:`adapters.atlas_catalogue` and
:mod:`adapters.atlas_saves` all read through whatever was granted, and no one of
them owns it. This module is where those grants are made, once, before the first
atlas adapter is built.

One grant today, and the shape is the reason it has a module of its own rather
than a corner of one of those three: ``py_modules/_vendor/README.md`` names
``backports.zstd`` as the next package expected under ``_vendor/``, and the codec
it carries reaches the resolver through ``register_zstd_provider`` — the same
process-global slot, granted the same way, at the same point in the wiring. It
lands here.
"""

from __future__ import annotations

import os

from _vendor.atlas import core_probe_interpreter, register_core_probe_interpreter

# Decky Loader is a PyInstaller-frozen binary, so the running program is not an
# interpreter and atlas derives none from it. SteamOS carries CPython here
# (`/usr/bin/python3` → `python3.13`, 3.13.5 measured on the reference device);
# another Linux handheld may not, and atlas deliberately searches no PATH for
# one, so an absent path is granted nothing rather than guessed at.
_HOST_INTERPRETER = "/usr/bin/python3"


def grant_core_probe_interpreter() -> str:
    """Grant atlas an interpreter for its core probe; answer what a probe would run under.

    The probe loads a core's ``.so`` in a child process to ask it what it saves,
    and that child is a Python interpreter. Frozen, this plugin has none to
    offer, so the resolver is handed one here — but only where the path is a
    file this process could actually spawn. Atlas checks the shape of a
    registered path and deliberately not whether it is there, which leaves the
    existence question the host's, and the honest answer on a machine without
    that interpreter is to register nothing: the resolver then says so itself
    and every core comes back unknown, which is what it would answer anyway for
    a path that could not run.

    The answer is a **log line**, not the resolver's own
    :class:`_vendor.atlas.CoreProbeInterpreter`, because its only consumer is
    the log at the wiring site — and that site is ``bootstrap/``, which may not
    hold a ``_vendor`` type (only adapters import ``_vendor.*``). It names the
    interpreter a probe would run under and where that came from, including the
    case where a probe would start nothing at all: this grant is silent when it
    is missing, so the log line is the only place its absence is visible.
    """
    if _is_spawnable_file(_HOST_INTERPRETER):
        register_core_probe_interpreter(_HOST_INTERPRETER)
    granted = core_probe_interpreter()
    if granted is None:
        return "atlas core probe: no interpreter to run under — every core answers unknown"
    origin = "granted by the plugin" if granted.registered else "atlas's own, from the running program"
    return f"atlas core probe: {granted.path} ({origin})"


def _is_spawnable_file(path: str) -> bool:
    """Is *path* an existing file this process could hand to the operating system?

    ``isfile`` follows symlinks, which ``/usr/bin/python3`` is on every
    arrangement measured, and the execute bit is the difference between a path
    that runs and one the spawn refuses.
    """
    return os.path.isfile(path) and os.access(path, os.X_OK)
