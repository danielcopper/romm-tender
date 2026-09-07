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
    _seed_save_state_dict,
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
        "content_installed": True,
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
        content_installed=True,
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
    """A wired service whose ROM answers *answer*, plus the probe counter and the fake server."""
    svc, server = make_service(tmp_path)
    _enable_sync_with_device(svc)
    _install_rom(svc, tmp_path)
    _create_save(tmp_path)
    cast("FakeSaveLocationReader", svc._rom_info._save_locations).answer_with("gba", answer)
    store = _CountingStore(svc._rom_info._save_file_store)
    svc._rom_info._save_file_store = cast("Any", store)
    return svc, store, server


def _saved_files(svc) -> dict[str, object]:
    """The per-file tracking state recorded for rom 42, or an empty mapping."""
    state = _uow(svc).rom_save_sync_states.get(42)
    return dict(state.files) if state is not None else {}


_REFUSING_STATES = ["shared", "inside_content", "hole", "unestablished"]


class TestARefusalProbesNothing:
    """States two to five look for no file on disk."""

    @pytest.mark.parametrize("state", _REFUSING_STATES)
    def test_no_path_is_probed(self, tmp_path, state: str):
        svc, store, _fake = _service(tmp_path, _refusing(state))

        assert svc._rom_info.find_save_files(42) == []
        assert store.probes == 0

    def test_the_control_probes(self, tmp_path):
        # Without this the assertion above would pass over a service that had
        # stopped probing for every save, refused or not.
        svc, store, _fake = _service(tmp_path, _syncable())

        assert [entry["filename"] for entry in svc._rom_info.find_save_files(42)] == ["pokemon.srm"]
        assert store.probes > 0

    @pytest.mark.parametrize("state", _REFUSING_STATES)
    def test_no_path_is_projected_either(self, tmp_path, state: str):
        svc, _store, _fake = _service(tmp_path, _refusing(state))

        assert svc._rom_info.expected_save_files(42) == []

    def test_the_projection_control(self, tmp_path):
        svc, _store, _fake = _service(tmp_path, _syncable())

        assert [entry["filename"] for entry in svc._rom_info.expected_save_files(42)] == ["pokemon.srm"]


class TestARefusalWritesNoState:
    """States two to five leave the per-ROM save state exactly as they found it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", _REFUSING_STATES)
    async def test_sync_rom_saves_writes_nothing(self, tmp_path, state: str):
        svc, _store, _fake = _service(tmp_path, _refusing(state))

        result = await svc.sync_rom_saves(42)

        assert result["reason"] == SAVE_SHAPE_UNSUPPORTED_REASON
        assert result["synced"] == 0
        assert _uow(svc).rom_save_sync_states.get(42) is None

    @pytest.mark.asyncio
    async def test_the_control_writes_state(self, tmp_path):
        svc, _store, _fake = _service(tmp_path, _syncable())

        await svc.sync_rom_saves(42)

        assert _uow(svc).rom_save_sync_states.get(42) is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", _REFUSING_STATES)
    async def test_the_status_read_writes_nothing_and_probes_nothing(self, tmp_path, state: str):
        svc, store, _fake = _service(tmp_path, _refusing(state))

        result = await svc.get_save_status(42)

        assert result["files"] == []
        assert store.probes == 0
        assert _uow(svc).rom_save_sync_states.get(42) is None

    @pytest.mark.asyncio
    async def test_the_status_control_probes(self, tmp_path):
        svc, store, _fake = _service(tmp_path, _syncable())

        await svc.get_save_status(42)

        assert store.probes > 0


class TestOneSyncTakesOneReadingOfTheMachine:
    """Live per operation, not live per layer.

    The entry point reads the answer to decide whether to refuse at all, before
    its heartbeat, so a refusing ROM never reaches the network. It then hands
    that same reading to the sync. Reading twice would be correct and would cost
    a second 170 ms of machine I/O on every launch and every exit.

    "Ask live" is a rule about OPERATIONS: the user changes a core option
    between one sync and the next, not between two layers of the same one.
    """

    @pytest.mark.asyncio
    async def test_a_single_rom_sync_asks_the_resolver_once(self, tmp_path):
        # A CONFIRMED slot, which is every ROM once the setup wizard has run and
        # so the path users are actually on. It is also the expensive one: a
        # confirmed ROM additionally opens a negotiate session, whose inventory
        # walks this ROM's save files and would take a reading of its own.
        svc, _store, _server = _service(tmp_path, _syncable())
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True})
        reader = cast("FakeSaveLocationReader", svc._rom_info._save_locations)
        reader.calls.clear()

        await svc.sync_rom_saves(42)

        assert len(reader.calls) == 1

    @pytest.mark.asyncio
    async def test_an_unconfirmed_rom_also_asks_once(self, tmp_path):
        # The other branch of the same entry point: no negotiate session, so
        # only the gate's own reading. Pinned so a change that reduces one path
        # cannot quietly add a reading to the other.
        svc, _store, _server = _service(tmp_path, _syncable())
        reader = cast("FakeSaveLocationReader", svc._rom_info._save_locations)
        reader.calls.clear()

        await svc.sync_rom_saves(42)

        assert len(reader.calls) == 1

    @pytest.mark.asyncio
    async def test_a_status_read_asks_once_too(self, tmp_path):
        # The page opens on every game the user looks at, and it threads the
        # answer's names into the matrix rather than asking for them again.
        svc, _store, _server = _service(tmp_path, _syncable())
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True})
        reader = cast("FakeSaveLocationReader", svc._rom_info._save_locations)
        reader.calls.clear()

        await svc.get_save_status(42)

        assert len(reader.calls) == 1

    @pytest.mark.asyncio
    async def test_the_next_sync_asks_again(self, tmp_path):
        # The other half: nothing is remembered between operations.
        svc, _store, _server = _service(tmp_path, _syncable())
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True})
        reader = cast("FakeSaveLocationReader", svc._rom_info._save_locations)
        reader.calls.clear()

        await svc.sync_rom_saves(42)
        await svc.sync_rom_saves(42)

        assert len(reader.calls) == 2


class TestAFileTheAnswerDoesNotCarryIsLeftAlone:
    """A configuration file already on the server is not downloaded over the local one.

    The probe walks the answer's SYNCED names, so a Saturn ``.smpc`` is never
    looked for locally. Without a guard it therefore looks server-only, and the
    matrix downloads it over a file it never examined — destroying console
    settings the user chose on this device, and writing state for a file the
    rule says is never carried. Every user who synced Saturn before the role
    rule existed has such a save on the server.

    The same guard covers a server save whose extension this emulator no longer
    writes at all. Neither copy is touched in either direction: deleting the
    server's is not this cut's business, and it may be the only one left.
    """

    def _saturn(self, tmp_path, *, with_battery: bool):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path, system="saturn", file_name="rally.cue")
        # The slot must be the seeded saves' slot, or ``filter_saves_to_slot``
        # drops them into the legacy bucket and the server-only loop is never
        # reached — which would make every assertion here vacuous.
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True}, platform_slug="sega-saturn")
        _create_save(tmp_path, system="saturn", rom_name="rally", ext=".smpc", content=b"local-settings")
        if with_battery:
            _create_save(tmp_path, system="saturn", rom_name="rally", ext=".bkr", content=b"battery")
        return svc, fake

    def _seed_server_file(self, fake, *, extension: str, uploaded_by: str = "device-B") -> None:
        """One server save in the active slot, owned by *uploaded_by*.

        Foreign by default, which is what makes the matrix want to bring it
        down — the shape that overwrote a local file before the guard existed.
        """
        seeded = fake.seed_foreign_save(
            42, filename=f"rally.{extension}", content=b"server-settings", uploaded_by=uploaded_by
        )
        fake.saves[seeded["id"]]["file_extension"] = extension

    @pytest.mark.asyncio
    async def test_the_local_configuration_file_is_not_overwritten(self, tmp_path):
        svc, fake = self._saturn(tmp_path, with_battery=False)
        self._seed_server_file(fake, extension="smpc")

        await svc.sync_rom_saves(42)

        assert (tmp_path / "saves" / "saturn" / "rally.smpc").read_bytes() == b"local-settings"

    @pytest.mark.asyncio
    async def test_no_state_is_recorded_for_it(self, tmp_path):
        svc, fake = self._saturn(tmp_path, with_battery=False)
        self._seed_server_file(fake, extension="smpc")

        await svc.sync_rom_saves(42)

        assert "rally.smpc" not in _saved_files(svc)

    @pytest.mark.asyncio
    async def test_a_server_save_whose_extension_the_answer_never_names_is_left_alone(self, tmp_path):
        # The legacy shape: a ``.sav`` uploaded when the plugin guessed
        # extensions. Beetle Saturn writes no such file, so there is nothing
        # local to compare it to and nothing to bring down.
        svc, fake = self._saturn(tmp_path, with_battery=False)
        self._seed_server_file(fake, extension="sav")

        await svc.sync_rom_saves(42)

        assert not (tmp_path / "saves" / "saturn" / "rally.sav").exists()
        assert "rally.sav" not in _saved_files(svc)

    @pytest.mark.asyncio
    async def test_the_progress_file_beside_it_still_syncs(self, tmp_path):
        # The control: the guard skips ONE target, it does not disable the loop.
        # Our own device owns the server copy here, so the battery file's upload
        # is not 409'd by a foreign device holding the slot.
        svc, fake = self._saturn(tmp_path, with_battery=True)
        self._seed_server_file(fake, extension="smpc", uploaded_by="device-1")

        result = await svc.sync_rom_saves(42)

        assert result["errors"] == []
        assert "rally.bkr" in _saved_files(svc)


class TestASlotSwitchLeavesAnUncarriedFileAlone:
    """The same rule on the slot-switch path, which downloads its own targets.

    A switch does not run the newest-wins matrix — it takes every server save in
    the destination slot and writes it to the canonical local path. So the guard
    that keeps a configuration file off the sync path does not cover it, and
    switching a Saturn game to a slot holding a ``.smpc`` uploaded before the
    role rule existed wrote that file over the settings chosen on this device.
    Recoverable — the download path quarantines first — but a restore the user
    has to know to perform, over a file no emulator asked us to carry.
    """

    def _saturn_switching(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path, system="saturn", file_name="rally.cue")
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True}, platform_slug="sega-saturn")
        _create_save(tmp_path, system="saturn", rom_name="rally", ext=".smpc", content=b"local-settings")
        for extension in ("smpc", "bkr"):
            seeded = fake.seed_foreign_save(
                42, slot="desktop", filename=f"rally.{extension}", content=f"server-{extension}".encode()
            )
            fake.saves[seeded["id"]]["file_extension"] = extension
        return svc, fake

    @pytest.mark.asyncio
    async def test_the_configuration_file_is_not_downloaded_over_the_local_one(self, tmp_path):
        svc, _fake = self._saturn_switching(tmp_path)

        result = await svc.switch_slot(42, "desktop")

        assert result["success"] is True
        assert (tmp_path / "saves" / "saturn" / "rally.smpc").read_bytes() == b"local-settings"

    @pytest.mark.asyncio
    async def test_the_progress_file_in_the_new_slot_still_arrives(self, tmp_path):
        # The control: the filter drops one target, it does not empty the switch.
        svc, _fake = self._saturn_switching(tmp_path)

        await svc.switch_slot(42, "desktop")

        assert (tmp_path / "saves" / "saturn" / "rally.bkr").read_bytes() == b"server-bkr"


class TestTheRefusalIsASkipAndNotAFailure:
    """The same shape the content-dir skip returns, with its own reason."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entry", ["pre_launch_sync", "post_exit_sync", "sync_rom_saves"])
    async def test_every_per_rom_entry_point_reports_the_skip(self, tmp_path, entry: str):
        svc, _store, _fake = _service(tmp_path, _refusing("shared"))

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
        svc, _store, _fake = _service(tmp_path, _refusing("shared"))

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
        """The sweep's own backstop, reached only by a ROM the sweep would otherwise sync.

        The slot must be CONFIRMED: ``sync_all_saves`` runs with
        ``require_confirmed=True`` and skips an unconfirmed ROM before the
        matrix is ever entered, so without this the test would pass over a
        service with no backstop at all. The control below is the other half —
        the same setup with a syncable answer must write state.
        """
        svc, _store, fake = _service(tmp_path, _refusing("hole"))
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True})

        result = await svc.sync_all_saves()

        # The backstop's own effect: the run stops BEFORE the server round-trip.
        # Downstream is redundantly safe — a refusing answer carries no names, so
        # nothing would be probed or grouped even without it — which is exactly
        # why the assertion has to be that the server was never asked.
        assert [call for call in fake.call_log if call[0] == "list_saves"] == []
        assert result["synced"] == 0
        assert result["errors"] == []
        assert _saved_files(svc) == {}

    @pytest.mark.asyncio
    async def test_the_sweep_control_syncs_a_rom_whose_answer_carries_files(self, tmp_path):
        svc, _store, fake = _service(tmp_path, _syncable())
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True})

        await svc.sync_all_saves()

        assert [call for call in fake.call_log if call[0] == "list_saves"] != []
        assert _saved_files(svc) != {}
