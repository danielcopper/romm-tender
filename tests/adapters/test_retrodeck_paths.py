"""Tests for adapters.retrodeck_paths.RetroDeckPathsAdapter."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from unittest.mock import patch

from adapters.retrodeck_paths import RetroDeckPathsAdapter
from lib.retrodeck_health import RetroDeckConfigHealth


def _make_adapter(tmp_path, config: dict[str, Any] | None = None) -> RetroDeckPathsAdapter:
    """Create adapter with optional retrodeck.json config."""
    user_home = str(tmp_path)
    if config is not None:
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "retrodeck.json").write_text(json.dumps(config))
    return RetroDeckPathsAdapter(user_home=user_home, logger=logging.getLogger("test"))


class TestPathResolution:
    def test_bios_path_from_config(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"bios_path": "/custom/bios"}})
        assert adapter.bios_path() == "/custom/bios"

    def test_bios_path_fallback(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        assert adapter.bios_path() == os.path.join(str(tmp_path), "retrodeck", "bios")

    def test_roms_path_from_config(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"roms_path": "/custom/roms"}})
        assert adapter.roms_path() == "/custom/roms"

    def test_roms_path_fallback(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        assert adapter.roms_path() == os.path.join(str(tmp_path), "retrodeck", "roms")

    def test_saves_path_from_config(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"saves_path": "/custom/saves"}})
        assert adapter.saves_path() == "/custom/saves"

    def test_saves_path_fallback(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        assert adapter.saves_path() == os.path.join(str(tmp_path), "retrodeck", "saves")

    def test_states_path_from_config(self, tmp_path):
        # ``states_path`` is the key a real ``retrodeck.json`` carries beside
        # ``saves_path``; savestates live under their own root, not under saves.
        adapter = _make_adapter(tmp_path, {"paths": {"states_path": "/custom/states"}})
        assert adapter.states_path() == "/custom/states"

    def test_states_path_fallback(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        assert adapter.states_path() == os.path.join(str(tmp_path), "retrodeck", "states")

    def test_states_path_is_not_derived_from_saves_path(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"saves_path": "/custom/saves"}})
        assert adapter.states_path() == os.path.join(str(tmp_path), "retrodeck", "states")

    def test_retrodeck_home_from_config(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"rd_home_path": "/custom/home"}})
        assert adapter.retrodeck_home() == "/custom/home"

    def test_retrodeck_home_fallback(self, tmp_path):
        # The fallback is built with an empty subdir, so it is spelled with a
        # trailing separator; resolving the root drops it. Nothing wanted the
        # separator — ``is_pending_migration_path`` appends its own, and a home
        # ending in one could never match a path below it.
        adapter = _make_adapter(tmp_path)
        assert adapter.retrodeck_home() == os.path.join(str(tmp_path), "retrodeck")

    def test_empty_path_uses_fallback(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"roms_path": ""}})
        assert adapter.roms_path() == os.path.join(str(tmp_path), "retrodeck", "roms")

    def test_missing_paths_key_uses_fallback(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"other": "data"})
        assert adapter.roms_path() == os.path.join(str(tmp_path), "retrodeck", "roms")

    def test_malformed_json_uses_fallback(self, tmp_path):
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "retrodeck.json").write_text("not valid json")
        adapter = RetroDeckPathsAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.bios_path() == os.path.join(str(tmp_path), "retrodeck", "bios")


class TestSymlinkResolvedRoots:
    """#1838: a root reached through a symlink must be spelled the way ``safe_join`` spells it."""

    @staticmethod
    def _linked_home(tmp_path):
        """Return ``(user_home_through_the_link, real_retrodeck_dir)`` for a ``/home -> /var/home`` layout."""
        base = tmp_path.resolve()
        retrodeck = base / "var" / "home" / "player" / "retrodeck"
        for subdir in ("bios", "roms", "saves", "states"):
            (retrodeck / subdir).mkdir(parents=True)
        (base / "home").symlink_to(base / "var" / "home", target_is_directory=True)
        linked_home = base / "home" / "player"
        assert str(linked_home) != os.path.realpath(linked_home)
        return linked_home, retrodeck

    def test_configured_content_roots_are_resolved(self, tmp_path):
        linked_home, retrodeck = self._linked_home(tmp_path)
        linked = linked_home / "retrodeck"
        adapter = _make_adapter(
            linked_home,
            {
                "paths": {
                    "bios_path": str(linked / "bios"),
                    "roms_path": str(linked / "roms"),
                    "saves_path": str(linked / "saves"),
                    "states_path": str(linked / "states"),
                }
            },
        )

        assert adapter.bios_path() == str(retrodeck / "bios")
        assert adapter.roms_path() == str(retrodeck / "roms")
        assert adapter.saves_path() == str(retrodeck / "saves")
        assert adapter.states_path() == str(retrodeck / "states")

    def test_fallback_content_roots_are_resolved(self, tmp_path):
        linked_home, retrodeck = self._linked_home(tmp_path)
        adapter = _make_adapter(linked_home)

        assert adapter.bios_path() == str(retrodeck / "bios")
        assert adapter.roms_path() == str(retrodeck / "roms")
        assert adapter.saves_path() == str(retrodeck / "saves")
        assert adapter.states_path() == str(retrodeck / "states")

    def test_a_root_that_is_not_on_disk_resolves_as_far_as_it_can(self, tmp_path):
        """``realpath`` never raises on a missing tail, so the getters stay best-effort."""
        linked_home, retrodeck = self._linked_home(tmp_path)
        adapter = _make_adapter(
            linked_home, {"paths": {"roms_path": str(linked_home / "retrodeck" / "not-created-yet")}}
        )

        assert adapter.roms_path() == str(retrodeck / "not-created-yet")

    def test_the_home_is_resolved_like_every_other_root(self, tmp_path):
        """The home is the marker ``MigrationService`` diffs, so it too must name a directory.

        Left unresolved it would be compared against a stored spelling of the
        same directory and read as a move.
        """
        linked_home, retrodeck = self._linked_home(tmp_path)
        adapter = _make_adapter(linked_home, {"paths": {"rd_home_path": str(linked_home / "retrodeck")}})

        assert adapter.retrodeck_home() == str(retrodeck)


class TestTTLCache:
    def test_cache_returns_same_value(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"bios_path": "/first"}})
        assert adapter.bios_path() == "/first"
        # Overwrite config — should still return cached value
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        (config_dir / "retrodeck.json").write_text(json.dumps({"paths": {"bios_path": "/second"}}))
        assert adapter.bios_path() == "/first"

    def test_cache_expires_after_ttl(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"paths": {"bios_path": "/first"}})
        assert adapter.bios_path() == "/first"
        # Force cache expiry
        adapter._cache_time = time.monotonic() - 31
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        (config_dir / "retrodeck.json").write_text(json.dumps({"paths": {"bios_path": "/second"}}))
        assert adapter.bios_path() == "/second"

    def test_failed_load_is_retried(self, tmp_path):
        """A failed load is not cached — later successful loads are picked up immediately.

        The TTL cache only stores positive results. When ``_load_config``
        returns None the cache stays empty, so the next call re-reads
        the file. This lets the adapter recover automatically when a
        missing ``retrodeck.json`` is created at runtime, without
        waiting for the 30-second TTL.
        """
        adapter = _make_adapter(tmp_path)  # no config — returns fallback
        fallback = os.path.join(str(tmp_path), "retrodeck", "bios")
        assert adapter.bios_path() == fallback

        # Drop a valid config file
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "retrodeck.json").write_text(json.dumps({"paths": {"bios_path": "/picked/up"}}))

        # Picked up on the next call — no need to wait out the TTL.
        assert adapter.bios_path() == "/picked/up"


class TestLoadConfigLogging:
    def test_load_config_logs_warning_on_json_error(self, tmp_path, caplog):
        """Invalid JSON triggers a warning log and falls back to the
        defaults — the failure is no longer silently swallowed."""
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "retrodeck.json").write_text("not valid json")
        adapter = RetroDeckPathsAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        with caplog.at_level(logging.WARNING):
            result = adapter.bios_path()

        assert result == os.path.join(str(tmp_path), "retrodeck", "bios")
        assert any("Failed to load RetroDECK config" in rec.message for rec in caplog.records)

    def test_load_config_logs_warning_on_permission_error(self, tmp_path, caplog):
        """PermissionError on the config file triggers a warning log and
        falls back to the defaults."""
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "retrodeck.json"
        config_file.write_text(json.dumps({"paths": {"bios_path": "/should/not/be/read"}}))

        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path) == str(config_file):
                raise PermissionError(f"denied: {path}")
            return real_open(path, *args, **kwargs)

        adapter = RetroDeckPathsAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        with patch("builtins.open", side_effect=fake_open), caplog.at_level(logging.WARNING):
            result = adapter.bios_path()

        assert result == os.path.join(str(tmp_path), "retrodeck", "bios")
        assert any("Failed to load RetroDECK config" in rec.message for rec in caplog.records)

    def test_load_config_does_not_log_on_missing_file(self, tmp_path, caplog):
        """A missing ``retrodeck.json`` is the expected fresh-install
        fallback path and must NOT spam the log on every read."""
        adapter = RetroDeckPathsAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))

        with caplog.at_level(logging.WARNING):
            result = adapter.bios_path()

        assert result == os.path.join(str(tmp_path), "retrodeck", "bios")
        assert not any("Failed to load RetroDECK config" in rec.message for rec in caplog.records)


class TestConfigPath:
    def test_config_path_points_at_retrodeck_json(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        assert adapter.config_path() == os.path.join(
            str(tmp_path),
            ".var",
            "app",
            "net.retrodeck.retrodeck",
            "config",
            "retrodeck",
            "retrodeck.json",
        )


class TestConfigHealth:
    def test_ok_when_config_present_and_home_exists(self, tmp_path):
        """OK: ``retrodeck.json`` read AND the resolved home exists on disk."""
        home = tmp_path / "rd-home"
        home.mkdir()
        adapter = _make_adapter(tmp_path, {"paths": {"rd_home_path": str(home)}})
        assert adapter.config_health() is RetroDeckConfigHealth.OK

    def test_absent_when_no_config_file(self, tmp_path):
        """ABSENT: no ``retrodeck.json`` — the legitimate fresh-install case."""
        adapter = _make_adapter(tmp_path)  # no config file
        assert adapter.config_health() is RetroDeckConfigHealth.ABSENT

    def test_absent_does_not_log(self, tmp_path, caplog):
        """ABSENT stays quiet — no WARNING for the expected fresh-install path."""
        adapter = _make_adapter(tmp_path)
        with caplog.at_level(logging.WARNING):
            assert adapter.config_health() is RetroDeckConfigHealth.ABSENT
        assert not any("Failed to load RetroDECK config" in rec.message for rec in caplog.records)

    def test_unreadable_on_malformed_json(self, tmp_path):
        """UNREADABLE: the file exists but is not valid JSON."""
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "retrodeck.json").write_text("not valid json")
        adapter = RetroDeckPathsAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        assert adapter.config_health() is RetroDeckConfigHealth.UNREADABLE

    def test_unreadable_on_permission_error(self, tmp_path):
        """UNREADABLE: the file exists but cannot be opened."""
        config_dir = tmp_path / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "retrodeck.json"
        config_file.write_text(json.dumps({"paths": {"rd_home_path": "/whatever"}}))

        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path) == str(config_file):
                raise PermissionError(f"denied: {path}")
            return real_open(path, *args, **kwargs)

        adapter = RetroDeckPathsAdapter(user_home=str(tmp_path), logger=logging.getLogger("test"))
        with patch("builtins.open", side_effect=fake_open):
            assert adapter.config_health() is RetroDeckConfigHealth.UNREADABLE

    def test_root_missing_when_resolved_home_absent(self, tmp_path):
        """ROOT_MISSING: config read OK but the resolved home is not on disk."""
        missing_home = tmp_path / "ejected-sd-card" / "retrodeck"
        adapter = _make_adapter(tmp_path, {"paths": {"rd_home_path": str(missing_home)}})
        assert adapter.config_health() is RetroDeckConfigHealth.ROOT_MISSING

    def test_absent_wins_over_root_missing(self, tmp_path):
        """ABSENT must win even though the ``~/retrodeck`` fallback is absent.

        With no ``retrodeck.json``, ``retrodeck_home()`` falls back to
        ``<user_home>/retrodeck`` — which does not exist in this tmp dir.
        The disk probe must NOT run for ABSENT, so the result stays
        ABSENT (quiet) rather than ROOT_MISSING (loud).
        """
        adapter = _make_adapter(tmp_path)  # no config file
        # Sanity: the fallback home does not exist on disk.
        assert not os.path.isdir(adapter.retrodeck_home())
        assert adapter.config_health() is RetroDeckConfigHealth.ABSENT

    def test_health_reuses_ttl_cache_no_second_read(self, tmp_path):
        """Within the TTL, ``config_health`` must not trigger a second file read."""
        home = tmp_path / "rd-home"
        home.mkdir()
        adapter = _make_adapter(tmp_path, {"paths": {"rd_home_path": str(home)}})
        # Prime the cache with one read.
        assert adapter.config_health() is RetroDeckConfigHealth.OK

        real_open = open
        opened: list[str] = []

        def tracking_open(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=tracking_open):
            assert adapter.config_health() is RetroDeckConfigHealth.OK

        assert opened == [], "config_health re-read retrodeck.json within the TTL"
