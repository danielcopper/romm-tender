"""The zstd codec the resolver opens a sealed AppImage with.

EmuDeck's ES-DE catalogue is sealed inside its AppImage, and the resolver reads
it only where the runtime has a zstd codec. The vendored ``backports.zstd`` sits
under ``_vendor``, a name the resolver never tries, so it is handed over here.
Where it is registered and why, and what a copy that does not load costs, is
``docs/architecture/backend-architecture.md``'s, under "Composition Root".
"""

from __future__ import annotations

import importlib

from _vendor.atlas import register_zstd_provider, zstd_provider

STANDARD_LIBRARY_CODEC = "compression.zstd"
VENDORED_CODEC = "_vendor.backports.zstd"


def register_zstd_codec() -> str:
    """Register the vendored backport where the standard library has no codec; answer a log line.

    Called once, at start-up. A backport that does not import registers
    nothing and raises nothing.

    The line is read off the resolver after the registration rather than
    composed from what was done here, so it names the provider a zstd image
    would actually go through; where none would, it carries the backport's
    import error when there was one. A string rather than the resolver's own
    ``ZstdProvider``, because the one reader is the log at the wiring site in
    ``bootstrap/``, which may not hold a ``_vendor`` type.
    """
    load_failure = ""
    try:
        importlib.import_module(STANDARD_LIBRARY_CODEC)
    except ImportError:
        try:
            backport = importlib.import_module(VENDORED_CODEC)
        # Whatever the import raises: the copy runs a compiled extension and
        # upstream's own version checks, and none of it may stop the start.
        except Exception as error:
            load_failure = f" — {VENDORED_CODEC} does not load ({error})"
        else:
            register_zstd_provider(backport)
    provider = zstd_provider()
    if provider is None:
        return f"atlas zstd codec: none{load_failure}; a sealed catalogue stays sealed"
    route = "registered" if provider.registered else "atlas's own"
    return f"atlas zstd codec: {provider.name} ({route})"
