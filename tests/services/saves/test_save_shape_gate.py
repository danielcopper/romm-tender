"""A save this plugin cannot carry per game is refused, and the refusal costs nothing.

Four of the five states refuse, and a refusal has to be a real one: no file is
probed for, no sync state is written, and the result is the benign-skip shape
rather than a failure. Probing anyway is not merely wasted work — it is how the
plugin used to search forever for an Amiga ``.nvr`` that no core writes, and how
a PS2 memory card many games share would be carried onto one game's record.

Every case here runs beside a **control** in the same class that asserts the
same probe DOES happen for a syncable answer. That is what makes the absence
assertions non-vacuous: a mutation that deletes the gate turns the refusal cases
red instead of leaving a test that would pass over a service doing nothing at
all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from domain.save_answer import SAVE_SHAPE_UNSUPPORTED_REASON, SaveAnswer, SaveComponent

if TYPE_CHECKING:
    from fakes.fake_save_location_reader import FakeSaveLocationReader

from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _install_rom,
    _uow,
    make_service,
)


def _refusing(state: str, **overrides: Any) -> SaveAnswer:
    """One of the four refusing answers, as the resolver's translation produces it."""
    kwargs: dict[str, Any] = {
        "state": state,
        "unestablished": "nothing_established" if state == "unestablished" else None,
        "emulator": "PCSX2 (Standalone)",
        "directory": "/saves/ps2/pcsx2/memcards",
        "backing_directory": None,
        "granularity": "shared-card" if state == "shared" else None,
        "needs": ("save_id",) if state == "hole" else (),
        "components": (),
        "caveats": (),
    }
    kwargs.update(overrides)
    return SaveAnswer(**kwargs)


def _syncable() -> SaveAnswer:
    return SaveAnswer(
        state="per_game_files",
        unestablished=None,
        emulator="mGBA",
        directory="/saves/gba",
        backing_directory=None,
        granularity="per-game-file",
        needs=(),
        components=(SaveComponent(name="pokemon.srm", directory="/saves/gba", role="battery", granularity=None),),
        caveats=(),
    )


class _CountingStore:
    """Wraps the real save-file store and counts every question asked of a path."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.probes = 0

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name not in ("is_file", "is_dir", "exists"):
            return attribute

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.probes += 1
            return attribute(*args, **kwargs)

        return counted


def _service(tmp_path, answer: SaveAnswer):
    svc, _ = make_service(tmp_path)
    _enable_sync_with_device(svc)
    _install_rom(svc, tmp_path)
    _create_save(tmp_path)
    fake = cast("FakeSaveLocationReader", svc._rom_info._save_locations)
    fake.answer_with("gba", answer)
    store = _CountingStore(svc._rom_info._save_file_store)
    svc._rom_info._save_file_store = cast("Any", store)
    return svc, store


_REFUSING_STATES = ["shared", "inside_content", "hole", "unestablished"]


class TestARefusalProbesNothing:
    """States two to five look for no file on disk."""

    @pytest.mark.parametrize("state", _REFUSING_STATES)
    def test_no_path_is_probed(self, tmp_path, state: str):
        svc, store = _service(tmp_path, _refusing(state))

        assert svc._rom_info.find_save_files(42) == []
        assert store.probes == 0

    def test_the_control_probes(self, tmp_path):
        # Without this the assertion above would pass over a service that had
        # stopped probing for every save, refused or not.
        svc, store = _service(tmp_path, _syncable())

        assert [entry["filename"] for entry in svc._rom_info.find_save_files(42)] == ["pokemon.srm"]
        assert store.probes > 0

    @pytest.mark.parametrize("state", _REFUSING_STATES)
    def test_no_path_is_projected_either(self, tmp_path, state: str):
        svc, _ = _service(tmp_path, _refusing(state))

        assert svc._rom_info.expected_save_files(42) == []

    def test_the_projection_control(self, tmp_path):
        svc, _ = _service(tmp_path, _syncable())

        assert [entry["filename"] for entry in svc._rom_info.expected_save_files(42)] == ["pokemon.srm"]


class TestARefusalWritesNoState:
    """States two to five leave the per-ROM save state exactly as they found it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", _REFUSING_STATES)
    async def test_sync_rom_saves_writes_nothing(self, tmp_path, state: str):
        svc, _ = _service(tmp_path, _refusing(state))

        result = await svc.sync_rom_saves(42)

        assert result["reason"] == SAVE_SHAPE_UNSUPPORTED_REASON
        assert result["synced"] == 0
        assert _uow(svc).rom_save_sync_states.get(42) is None

    @pytest.mark.asyncio
    async def test_the_control_writes_state(self, tmp_path):
        svc, _ = _service(tmp_path, _syncable())

        await svc.sync_rom_saves(42)

        assert _uow(svc).rom_save_sync_states.get(42) is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", _REFUSING_STATES)
    async def test_the_status_read_writes_nothing_and_probes_nothing(self, tmp_path, state: str):
        svc, store = _service(tmp_path, _refusing(state))

        result = await svc.get_save_status(42)

        assert result["files"] == []
        assert store.probes == 0
        assert _uow(svc).rom_save_sync_states.get(42) is None

    @pytest.mark.asyncio
    async def test_the_status_control_probes(self, tmp_path):
        svc, store = _service(tmp_path, _syncable())

        await svc.get_save_status(42)

        assert store.probes > 0


class TestTheRefusalIsASkipAndNotAFailure:
    """The same shape the content-dir skip returns, with its own reason."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entry", ["pre_launch_sync", "post_exit_sync", "sync_rom_saves"])
    async def test_every_per_rom_entry_point_reports_the_skip(self, tmp_path, entry: str):
        svc, _ = _service(tmp_path, _refusing("shared"))

        result = await getattr(svc, entry)(42)

        assert result["success"] is False
        assert result["reason"] == SAVE_SHAPE_UNSUPPORTED_REASON
        assert result["synced"] == 0
        assert result["errors"] == []
        assert result["conflicts"] == []

    @pytest.mark.asyncio
    async def test_the_message_names_the_emulator_and_not_the_platform(self, tmp_path):
        # PS2 is not unsupported; standalone PCSX2 is, and a libretro core for
        # the same platform could answer per-game.
        svc, _ = _service(tmp_path, _refusing("shared"))

        result = await svc.sync_rom_saves(42)

        assert "PCSX2 (Standalone)" in result["message"]

    @pytest.mark.asyncio
    async def test_an_uninstalled_rom_is_not_reported_as_an_unsupported_shape(self, tmp_path):
        # Not installed is a fact about the disk, not about any emulator. The
        # runner's own not-installed branch owns it.
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)

        result = await svc.sync_rom_saves(999)

        assert result.get("reason") != SAVE_SHAPE_UNSUPPORTED_REASON

    @pytest.mark.asyncio
    async def test_the_whole_library_sweep_passes_a_refusing_rom_over(self, tmp_path):
        svc, _ = _service(tmp_path, _refusing("hole"))

        result = await svc.sync_all_saves()

        assert result["synced"] == 0
        assert result["errors"] == []
        assert _uow(svc).rom_save_sync_states.get(42) is None
