"""Tests for ShortcutLaunchResolver — it resolves each ROM's launch facts.

Driven through the shared ``library`` fixture so the emulator resolution runs
against the real :class:`ActiveCoreResolver` over the shared fake UoW — the same
seam the sync's bake sites draw from — rather than a mock of it.

The disc-resolved install paths (``do_scan_installed_paths`` /
``do_read_installed_paths``) are pinned in ``tests/services/test_disc_bake_sites.py``
alongside the other launch-bake sites, so a change to the disc pin's handling
fails every site at once. What is pinned HERE about those two is the boundary
that belongs to this module rather than to the disc pin: neither of them may
hold a Unit of Work open across the resolver's directory listing.
"""

from fakes.uow_open_probe import record_uow_open

from domain.shortcut_data import EmulatorInvocation
from tests.services.library._helpers import _seed_install


class TestBuildCoreOverrides:
    """The ``core_overrides`` map both preview and apply pass to ``build_shortcuts_data``.

    Maps ``rom_id -> resolved core_so`` for every ROM in the unit that carries a
    still-valid ``emulator_override``; NULL pins never enter the map, and a stale
    LABEL is omitted with a WARNING so the bake degrades to the plain launch.
    """

    def test_resolved_override_included_null_omitted(self, library):
        """A resolvable pin maps to its libretro EmulatorInvocation; an unpinned ROM is absent."""
        library.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]
        _seed_install(library, 10, file_path="/roms/psx/a.chd", platform_slug="psx")
        _seed_install(library, 11, file_path="/roms/psx/b.chd", platform_slug="psx")
        with library.uow:
            library.uow.roms.set_emulator_override(10, "PCSX ReARMed")

        roms = [{"id": 10, "platform_slug": "psx"}, {"id": 11, "platform_slug": "psx"}]
        result = library.sync._shortcut_launch_resolver.do_build_core_overrides(roms, library.emulator_sources.read())

        assert result == {
            10: EmulatorInvocation.libretro("pcsx_rearmed_libretro", "PCSX ReARMed", "pcsx_rearmed_libretro.so")
        }
        assert 11 not in result

    def test_stale_override_omitted_with_warning(self, library, caplog):
        """A pin whose LABEL no longer resolves is omitted and a WARNING is logged."""
        import logging

        library.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]
        _seed_install(library, 10, file_path="/roms/psx/a.chd", platform_slug="psx")
        with library.uow:
            library.uow.roms.set_emulator_override(10, "Removed Core")

        roms = [{"id": 10, "platform_slug": "psx"}]
        with caplog.at_level(logging.WARNING):
            result = library.sync._shortcut_launch_resolver.do_build_core_overrides(
                roms, library.emulator_sources.read()
            )

        assert result == {}
        assert "Removed Core" in caplog.text
        assert "no longer resolves" in caplog.text

    def test_no_overrides_returns_empty(self, library):
        """No pins anywhere → empty map (no available-cores lookups needed)."""
        _seed_install(library, 10, file_path="/roms/n64/a.z64", platform_slug="n64")
        result = library.sync._shortcut_launch_resolver.do_build_core_overrides(
            [{"id": 10, "platform_slug": "n64"}], library.emulator_sources.read()
        )
        assert result == {}


class TestInstallPathReadsCloseTheUnitOfWorkFirst:
    """Neither install-path read holds a UoW open across the disc resolver.

    ``resolve_for_install`` lists the install directory, once per installed ROM.
    A UoW takes SQLite's ``BEGIN IMMEDIATE`` write lock, so a listing held
    inside one blocks every other writer in the backend for the whole scan
    (GLOSSARY.md → Unit of Work, #1779). ``FakeUnitOfWork`` shares no connection,
    so what a test can see is the ordering: the rows are snapshotted inside the
    transaction and every resolve runs after it closes.
    """

    def test_scan_resolves_after_the_unit_of_work_closes(self, library):
        _seed_install(library, 10, file_path="/roms/psx/a.chd", platform_slug="psx")
        _seed_install(library, 11, file_path="/roms/psx/b.chd", platform_slug="psx")
        resolver = library.sync._shortcut_launch_resolver
        open_at_resolve = record_uow_open(library.uow, resolver._disc_resolver, "resolve_for_install")

        paths = resolver.do_scan_installed_paths()

        assert set(paths) == {10, 11}
        assert open_at_resolve == [False, False]

    def test_read_resolves_after_the_unit_of_work_closes(self, library):
        _seed_install(library, 10, file_path="/roms/psx/a.chd", platform_slug="psx")
        _seed_install(library, 11, file_path="/roms/psx/b.chd", platform_slug="psx")
        resolver = library.sync._shortcut_launch_resolver
        open_at_resolve = record_uow_open(library.uow, resolver._disc_resolver, "resolve_for_install")

        paths = resolver.do_read_installed_paths({10, 11})

        assert set(paths) == {10, 11}
        assert open_at_resolve == [False, False]
