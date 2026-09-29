"""What the host hands the application about Steam: nothing until the injector exists, then its readings."""

from __future__ import annotations

from host.status import HostStatus, SteamReadings


class TestSteamReadings:
    async def test_before_the_injector_exists_no_reading_can_be_taken(self):
        readings = SteamReadings()

        assert await readings.running_apps() is None
        assert await readings.reload_frees_at() is None

    async def test_once_attached_it_answers_with_the_injectors_readings(self):
        readings = SteamReadings()

        async def running_apps() -> tuple[str, ...] | None:
            return ("Celeste",)

        async def reload_frees_at() -> float | None:
            return 1234.5

        readings.attach(running_apps=running_apps, reload_frees_at=reload_frees_at)

        assert await readings.running_apps() == ("Celeste",)
        assert await readings.reload_frees_at() == 1234.5

    async def test_every_host_status_carries_one_of_its_own(self):
        first, second = HostStatus(), HostStatus()

        assert isinstance(first.steam, SteamReadings)
        assert first.steam is not second.steam
