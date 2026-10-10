"""The zstd codec the resolver opens a sealed AppImage with.

EmuDeck's ES-DE catalogue is sealed inside its AppImage, and the resolver reads
it only where the runtime has a zstd codec; without one the catalogue answers
``emulator-catalogue-sealed``. The resolver finds the standard library's
``compression.zstd`` (Python 3.14 on) by itself. The backport vendored for
Python 3.13 sits under ``_vendor``, a name the resolver never tries, so it is
handed over here — and only where the standard library has none, because a
registered provider is tried first and the ``cp313`` copy does not load from
3.14 on (``backend/_vendor/README.md``, ``backports``).
"""

from __future__ import annotations

import importlib

from _vendor.atlas import register_zstd_provider, zstd_provider

STANDARD_LIBRARY_CODEC = "compression.zstd"
VENDORED_CODEC = "_vendor.backports.zstd"


def register_zstd_codec() -> str:
    """Register the vendored backport where the standard library has no codec; answer a log line.

    Called once, at start-up. A backport that does not import — its compiled
    extension refused by this machine's loader, or upstream's own refusal of
    the interpreter — is no codec and registers nothing: a sealed catalogue
    stays sealed, and the backend starts.

    The line is read off the resolver after the registration rather than
    composed from what was done here, so it names the provider a zstd image
    would actually go through. A string rather than the resolver's own
    ``ZstdProvider``, because the one reader is the log at the wiring site in
    ``bootstrap/``, which may not hold a ``_vendor`` type.
    """
    try:
        importlib.import_module(STANDARD_LIBRARY_CODEC)
    except ImportError:
        try:
            backport = importlib.import_module(VENDORED_CODEC)
        # Whatever the import raises: the copy runs a compiled extension and
        # upstream's own version checks, and none of it may stop the start.
        except Exception as error:
            return f"atlas zstd codec: none — {VENDORED_CODEC} does not load ({error}); a sealed catalogue stays sealed"
        register_zstd_provider(backport)
    provider = zstd_provider()
    if provider is None:
        return "atlas zstd codec: none — a sealed catalogue stays sealed"
    route = "registered" if provider.registered else "atlas's own"
    return f"atlas zstd codec: {provider.name} ({route})"
