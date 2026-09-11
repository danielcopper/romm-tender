"""Tests for the PluginMetadataAdapter — reads the plugin's package.json and plugin.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.plugin_metadata import PluginMetadataAdapter


class TestPluginMetadataAdapter:
    def test_read_version_returns_declared_version(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "package.json").write_text(json.dumps({"version": "1.2.3"}))

        adapter = PluginMetadataAdapter()
        assert adapter.read_version(str(plugin_dir)) == "1.2.3"

    def test_read_version_missing_file_returns_fallback(self, tmp_path):
        """A missing package.json must not abort bootstrap."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()

        adapter = PluginMetadataAdapter()
        assert adapter.read_version(str(plugin_dir)) == "0.0.0"

    def test_read_version_missing_directory_returns_fallback(self, tmp_path):
        adapter = PluginMetadataAdapter()
        assert adapter.read_version(str(tmp_path / "does-not-exist")) == "0.0.0"

    def test_read_version_malformed_json_returns_fallback(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "package.json").write_text("{not valid json")

        adapter = PluginMetadataAdapter()
        assert adapter.read_version(str(plugin_dir)) == "0.0.0"

    def test_read_version_missing_version_field_returns_fallback(self, tmp_path):
        """A package.json without a ``version`` key falls back to ``0.0.0``."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "package.json").write_text(json.dumps({"name": "romm-tender"}))

        adapter = PluginMetadataAdapter()
        assert adapter.read_version(str(plugin_dir)) == "0.0.0"

    def test_read_name_returns_declared_name_and_has_safe_fallback(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "package.json").write_text(json.dumps({"name": "custom-plugin"}))
        adapter = PluginMetadataAdapter()
        assert adapter.read_name(str(plugin_dir)) == "custom-plugin"
        (plugin_dir / "package.json").write_text(json.dumps({"name": None}))
        assert adapter.read_name(str(plugin_dir)) == "custom-plugin"
        assert PluginMetadataAdapter().read_name(str(plugin_dir)) == "decky-plugin"

    def test_name_and_version_share_one_package_read(self, tmp_path, monkeypatch):
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "package.json").write_text(json.dumps({"name": "plugin", "version": "1.2.3"}))
        adapter = PluginMetadataAdapter()
        original = adapter._read
        calls = 0

        def counted(path):
            nonlocal calls
            calls += 1
            return original(path)

        monkeypatch.setattr(adapter, "_read", counted)
        assert adapter.read_metadata(str(plugin_dir)) == ("plugin", "1.2.3")
        assert adapter.read_name(str(plugin_dir)) == "plugin"
        assert adapter.read_version(str(plugin_dir)) == "1.2.3"
        assert calls == 1

    @pytest.mark.parametrize("unusable", ["", None, 3, 1.2, ["1.0.0"], {"major": 1}])
    def test_read_version_unusable_version_returns_fallback(self, tmp_path, unusable):
        """A version that is not a non-empty string takes the documented fallback.

        ``read_version`` is declared to return ``str`` and its value is
        interpolated into the outgoing User-Agent and recorded as the recovery
        bundle's ``plugin_version``. An empty string, ``null``, a number, a
        list, or an object cannot serve either purpose, so they resolve to the
        same fallback a missing field does — matching how ``name`` is
        validated.
        """
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "package.json").write_text(json.dumps({"version": unusable}))

        adapter = PluginMetadataAdapter()
        assert adapter.read_version(str(plugin_dir)) == "0.0.0"


class TestReadDeckyName:
    """The name Decky matches an installed plugin against — plugin.json's, not package.json's."""

    def _plugin_dir(self, tmp_path, *, package_name: str = "romm-tender", decky_name: str | None = "Tender"):
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "package.json").write_text(json.dumps({"name": package_name, "version": "0.32.0"}))
        if decky_name is not None:
            (plugin_dir / "plugin.json").write_text(json.dumps({"name": decky_name, "version": "0.32.0"}))
        return plugin_dir

    def test_answers_from_plugin_json_and_not_from_package_json(self, tmp_path):
        """The two manifests spell the name differently, and Decky matches on plugin.json's.

        Hand Decky's install-from-URL the package name and the match misses: the
        previous installation is never uninstalled and a second plugin folder
        appears beside it. Nothing fails loudly when that happens, so this is
        the test that has to.
        """
        plugin_dir = self._plugin_dir(tmp_path)

        adapter = PluginMetadataAdapter()
        assert adapter.read_decky_name(str(plugin_dir)) == "Tender"
        assert adapter.read_name(str(plugin_dir)) == "romm-tender"

    def test_the_two_names_never_cross(self, tmp_path):
        """Neither read may answer from the other's file, whatever either says."""
        plugin_dir = self._plugin_dir(tmp_path, package_name="package-spelling", decky_name="Decky Spelling")

        adapter = PluginMetadataAdapter()
        assert adapter.read_decky_name(str(plugin_dir)) == "Decky Spelling"
        assert adapter.read_name(str(plugin_dir)) == "package-spelling"

    def test_missing_plugin_json_has_no_fallback_spelling(self, tmp_path):
        """A literal fallback would be a second spelling of the name this read keeps single."""
        plugin_dir = self._plugin_dir(tmp_path, decky_name=None)

        adapter = PluginMetadataAdapter()
        assert adapter.read_decky_name(str(plugin_dir)) == ""

    def test_malformed_plugin_json_does_not_abort(self, tmp_path):
        plugin_dir = self._plugin_dir(tmp_path)
        (plugin_dir / "plugin.json").write_text("{not valid json")

        adapter = PluginMetadataAdapter()
        assert adapter.read_decky_name(str(plugin_dir)) == ""

    @pytest.mark.parametrize("unusable", ["", None, 3, ["Tender"], {"name": "Tender"}])
    def test_an_unusable_name_answers_empty(self, tmp_path, unusable):
        plugin_dir = self._plugin_dir(tmp_path)
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": unusable}))

        adapter = PluginMetadataAdapter()
        assert adapter.read_decky_name(str(plugin_dir)) == ""

    def test_the_shipped_manifests_really_do_disagree(self):
        """The premise of the whole read, checked against the files that ship.

        Every other test here fabricates the disagreement. This one reads the
        repo's own two manifests, so the day they are made to agree — or the day
        one of them is renamed — this stops being a hypothesis and says so.
        """
        repo_root = Path(__file__).resolve().parents[2]
        package_name = json.loads((repo_root / "package.json").read_text())["name"]
        decky_name = json.loads((repo_root / "plugin.json").read_text())["name"]

        assert package_name != decky_name
        assert PluginMetadataAdapter().read_decky_name(str(repo_root)) == decky_name

    def test_the_answer_is_read_once(self, tmp_path, monkeypatch):
        plugin_dir = self._plugin_dir(tmp_path)
        adapter = PluginMetadataAdapter()
        original = adapter._read
        calls = 0

        def counted(path, filename="package.json"):
            nonlocal calls
            calls += 1
            return original(path, filename)

        monkeypatch.setattr(adapter, "_read", counted)
        assert adapter.read_decky_name(str(plugin_dir)) == "Tender"
        assert adapter.read_decky_name(str(plugin_dir)) == "Tender"
        assert calls == 1
