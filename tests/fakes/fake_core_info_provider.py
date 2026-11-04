"""In-memory ``CoreInfoProvider`` implementation for service tests."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from domain.emulator_commands import CORE, PROGRAM, EmulatorOption
from domain.emulator_sources import ArrangedSource
from domain.launchability import STARTS, Launchability
from fakes.fake_emulator_sources import FakeSourcesReading

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading

# The answering source every fake answer names unless a test says otherwise.
RETRODECK_SOURCE: dict[str, Any] = {"kind": "retrodeck", "starts_games": True}


def libretro_option(core_so: str, label: str, *, emulator: str | None = None) -> EmulatorOption:
    """Build a core :class:`EmulatorOption`.

    The identity defaults to the resolver's own spelling for a libretro entry —
    the core file's basename, ``<core_so>.so`` — so a test seeding a core gets
    the identity a firmware answer would carry for it without saying so twice.
    """
    return EmulatorOption(
        label=label,
        kind=CORE,
        core_so=core_so,
        emulator=f"{core_so}.so" if emulator is None else emulator,
    )


def standalone_option(label: str, *, not_installed: bool = False, emulator: str | None = None) -> EmulatorOption:
    """Build a standalone :class:`EmulatorOption`.

    ``not_installed`` seeds an entry the resolver states not installed.
    ``emulator`` is the identity a firmware answer joins on, and it is left
    unset for the entry the resolver could not identify.
    """
    return EmulatorOption(label=label, kind=PROGRAM, core_so=None, emulator=emulator, not_installed=not_installed)


class FakeCoreInfoProvider:
    """In-memory CoreInfoProvider for tests.

    Returns the configured ``active_core`` (the libretro system default, for the
    firmware BIOS filter) and ``options`` (the classified emulator picker list)
    for any system. ``options`` may be seeded directly with
    :class:`EmulatorOption` values or synthesized from the ``available_cores``
    convenience — a list of ``{"core_so", "label", "is_default"}`` dicts turned
    into libretro options (the shape most core-selection tests use).
    ``available`` mirrors the adapter's "was the catalogue readable?" flag, and
    ``reason`` / ``source`` the two fields the adapter answers beside it.

    A test lists ``options`` in the order the source declares them, so an option
    seeded without a ``declared_index`` is answered with its place in the list
    as one, and the first is the default as the adapter would read it.
    ``options_calls`` records the ``content_path`` and ``source`` each
    ``get_emulator_options`` call was asked with.

    ``readings`` records the reading each ``get_emulator_options`` call was
    handed (``None`` for a call that asks fresh). ``active_core_calls`` and
    ``emulator_options_calls`` record the ``system_name`` each seam was invoked
    with so callers can assert a normalized system (not the raw platform slug)
    reached the read seam.
    """

    def __init__(
        self,
        *,
        active_core: tuple[str | None, str | None] = (None, None),
        available_cores: list[dict[str, Any]] | None = None,
        options: list[EmulatorOption] | None = None,
        available: bool = True,
        reason: str = "unavailable",
        source: dict[str, Any] | None = RETRODECK_SOURCE,
    ) -> None:
        self.active_core = active_core
        self._available_cores: list[dict[str, Any]] = []
        if options is not None:
            self.options: list[EmulatorOption] = options
        else:
            # Route through the setter so ``options`` is synthesized once and
            # stays in sync with any later ``fake.available_cores = [...]`` write.
            self.available_cores = available_cores or []
        self.available = available
        self.reason = reason
        self.source = source
        self.active_core_calls: list[str] = []
        self.emulator_options_calls: list[str] = []
        self.readings: list[SourcesReading | None] = []
        self.options_calls: list[dict[str, str | None]] = []
        # The verdict ``launchability`` answers for each label it is asked
        # with; a label it does not hold starts its file. Each call is recorded.
        self.launch_verdicts: dict[str, Launchability] = {}
        self.launchability_calls: list[tuple[str, str, str]] = []

    @property
    def available_cores(self) -> list[dict[str, Any]]:
        """The libretro-core convenience input, kept in sync with ``options``."""
        return self._available_cores

    @available_cores.setter
    def available_cores(self, value: list[dict[str, Any]]) -> None:
        self._available_cores = value
        self.options = [libretro_option(c["core_so"], c["label"]) for c in value if "core_so" in c]

    def get_active_core(
        self, system_name: str, *, reading: SourcesReading | None = None
    ) -> tuple[str | None, str | None]:
        self.active_core_calls.append(system_name)
        return self.active_core

    def sources_reading(self) -> FakeSourcesReading:
        return FakeSourcesReading((ArrangedSource(kind="retrodeck", enabled=True, starts_games=True),), {})

    def get_emulator_options(
        self,
        system_name: str,
        *,
        reading: SourcesReading | None = None,
        content_path: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        self.emulator_options_calls.append(system_name)
        self.readings.append(reading)
        self.options_calls.append({"content_path": content_path, "source": source})
        return {
            "available": self.available,
            "options": [
                option if option.declared_index is not None else replace(option, declared_index=index)
                for index, option in enumerate(self.options)
            ],
            "reason": None if self.available else self.reason,
            "source": self.source,
        }

    def launchability(
        self, system_name: str, content_path: str, *, label: str, reading: SourcesReading | None = None
    ) -> Launchability:
        self.launchability_calls.append((system_name, content_path, label))
        verdict = self.launch_verdicts.get(label)
        if verdict is not None:
            return verdict
        return Launchability(
            state=STARTS,
            entry=label,
            system=system_name,
            file=os.path.basename(content_path),
            extension=os.path.splitext(content_path)[1],
        )
