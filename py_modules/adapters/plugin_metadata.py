"""Concrete ``PluginMetadataReader`` adapter — reads the plugin's own manifests.

Owns the raw ``open()`` + ``json.load()`` round-trips behind the
``PluginMetadataReader`` Protocol. A missing or malformed manifest is not a hard
failure — bootstrap must keep wiring services even when a metadata read fails,
so the adapter returns the documented fallback.

Two manifests, because the plugin's name is spelled twice and the two spellings
mean different things: ``package.json`` names the PACKAGE and ``plugin.json``
names what Decky Loader calls the plugin. Each read says which it answers from.
"""

from __future__ import annotations

import json
import os
from typing import Any


class PluginMetadataAdapter:
    """Real ``PluginMetadataReader`` backed by the plugin's on-disk manifests."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, str]] = {}
        self._decky_name_cache: dict[str, str] = {}

    def read_metadata(self, plugin_dir: str) -> tuple[str, str]:
        cached = self._cache.get(plugin_dir)
        if cached is not None:
            return cached
        payload = self._read(plugin_dir)
        raw_name = payload.get("name")
        raw_version = payload.get("version")
        value = (
            raw_name if isinstance(raw_name, str) and raw_name else "decky-plugin",
            raw_version if isinstance(raw_version, str) and raw_version else "0.0.0",
        )
        self._cache[plugin_dir] = value
        return value

    def read_version(self, plugin_dir: str) -> str:
        return self.read_metadata(plugin_dir)[1]

    def read_name(self, plugin_dir: str) -> str:
        return self.read_metadata(plugin_dir)[0]

    def read_decky_name(self, plugin_dir: str) -> str:
        # plugin.json, never package.json. Decky finds an already installed
        # plugin by matching the ``name`` it loaded from plugin.json ("Tender");
        # package.json's ``name`` is the package's own ("romm-tender"), which is
        # what the outgoing User-Agent and the recovery-bundle root are built
        # from. Hand Decky's install-from-URL the package name and the match
        # misses: the previous installation is never uninstalled and a second
        # plugin folder appears beside it. Nothing about that fails loudly,
        # which is why the two reads are held apart here rather than at a caller.
        cached = self._decky_name_cache.get(plugin_dir)
        if cached is not None:
            return cached
        payload = self._read(plugin_dir, "plugin.json")
        raw_name = payload.get("name")
        # No fallback spelling. plugin.json is the file Decky loads this plugin
        # from, so it is unreadable only where the plugin is not running; a
        # literal here would be a second spelling of the very name this read
        # exists to keep single, free to drift once and be wrong forever.
        value = raw_name if isinstance(raw_name, str) and raw_name else ""
        self._decky_name_cache[plugin_dir] = value
        return value

    @staticmethod
    def _read(plugin_dir: str, filename: str = "package.json") -> dict[str, Any]:
        try:
            with open(os.path.join(plugin_dir, filename)) as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload
