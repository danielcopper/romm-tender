"""On-disk persistence Protocols for plugin settings and metadata.

Services delegate disk round-trips for settings to these Protocols so
atomic writes, locking, and corrupt-file recovery stay in adapters. Each
Protocol carries a domain-specific method name (e.g. ``save_settings``)
rather than a generic ``__call__`` so the type checker rejects mis-wires
between the plugin-level persisters.
"""

from __future__ import annotations

from typing import Protocol


class SettingsPersister(Protocol):
    """Persist the live settings dict (``settings.json``)."""

    def save_settings(self) -> None: ...


class PluginMetadataReader(Protocol):
    """Read the plugin's own install metadata off its manifests.

    Owns the reads of the plugin's ``package.json`` and ``plugin.json`` so
    consumers do not perform raw ``open()`` calls. The plugin directory is
    supplied by the caller — implementations resolve each manifest path and
    parse the JSON payload. A missing or malformed file must not abort
    bootstrap; implementations return the documented fallback (``"0.0.0"`` for
    ``read_version``).

    The two manifests both carry a ``name`` and they are different names: which
    one a caller wants is decided by which method it calls, never by taking one
    for the other.
    """

    def read_metadata(self, plugin_dir: str) -> tuple[str, str]:
        """Return ``(name, version)`` from one canonical package read."""
        ...

    def read_version(self, plugin_dir: str) -> str:
        """Return the plugin's declared semantic version.

        Falls back to ``"0.0.0"`` when the file is missing, unreadable,
        malformed, or has no ``version`` field — bootstrap must not
        abort on a metadata read.
        """
        ...

    def read_name(self, plugin_dir: str) -> str:
        """Return ``package.json``'s declared package name, or ``"decky-plugin"`` on failure.

        The name the plugin folder and the outgoing User-Agent are built from —
        NOT the name Decky Loader knows the plugin by (see
        :meth:`read_decky_name`).
        """
        ...

    def read_decky_name(self, plugin_dir: str) -> str:
        """Return ``plugin.json``'s declared name — what Decky Loader calls this plugin.

        The name Decky matches an already-installed plugin against, so it is
        what an install-from-URL must be handed for the existing installation to
        be replaced rather than duplicated. Returns ``""`` when ``plugin.json``
        is missing, malformed, or declares no usable name: no fallback spelling
        exists, because a second literal is exactly the drift this read prevents.
        """
        ...
