"""In-memory ``CoreInfoProvider`` implementation for service tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from domain.emulator_commands import CORE, PROGRAM, EmulatorOption
from domain.emulator_sources import ArrangedSource
from domain.shortcut_data import EmulatorInvocation
from fakes.fake_emulator_sources import FakeSourcesReading

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading

# The answering source every fake answer names unless a test says otherwise.
RETRODECK_SOURCE: dict[str, Any] = {"kind": "retrodeck", "starts_games": True}

# Where the resolver states RetroDECK keeps its RetroArch cores, spelled inside
# its sandbox: the folder a seeded core's ``core_path`` lies in.
RETRODECK_CORES = "/var/config/retroarch/cores"


def core_invocation(core_so: str, label: str | None = None, emulator: str | None = None) -> EmulatorInvocation:
    """The invocation a bakeable core entry renders to: its plain RetroArch command with the resolver's core path."""
    return EmulatorInvocation.run_game(
        f"%EMULATOR_RETROARCH% -L {RETRODECK_CORES}/{core_so}.so %ROM%", label, emulator, core_so=core_so
    )


def libretro_option(core_so: str, label: str, *, emulator: str | None = None) -> EmulatorOption:
    """Build a bakeable core :class:`EmulatorOption`.

    Mirrors the real classifier's output for the RetroArch ``-L`` shape, so a
    test can seed the fake with ``options=[libretro_option("mgba_libretro",
    "mGBA"), ...]`` and have ``label_to_invocation`` resolve the label to the
    same invocation the adapter would (:func:`core_invocation`).

    The identity defaults to the resolver's own spelling for a libretro entry —
    the core file's basename, ``<core_so>.so`` — so a test seeding a core gets
    the identity a firmware answer would carry for it without saying so twice.
    """
    return EmulatorOption(
        label=label,
        kind=CORE,
        core_so=core_so,
        command=f"%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/{core_so}.so %ROM%",
        status="bakeable",
        reason=None,
        emulator=f"{core_so}.so" if emulator is None else emulator,
        core_path=f"{RETRODECK_CORES}/{core_so}.so",
    )


def standalone_option(
    command: str,
    label: str,
    *,
    status: str = "bakeable",
    reason: str | None = None,
    emulator: str | None = None,
    launcher: str | None = None,
) -> EmulatorOption:
    """Build a standalone :class:`EmulatorOption` from a full ES-DE command.

    Defaults to a bakeable option; pass ``status``/``reason`` to seed a
    ``needs_setup`` or ``unbakeable`` entry (e.g. to prove a per-game pin to an
    un-bakeable label hard-fails). ``emulator`` is the identity a firmware answer
    joins on, and it is left unset for the entry the resolver could not identify.
    ``launcher`` is the sandbox launcher the resolver states for the entry.
    """
    return EmulatorOption(
        label=label,
        kind=PROGRAM,
        core_so=None,
        command=command,
        status=status,
        reason=reason,
        emulator=emulator,
        launcher=launcher,
    )


class FakeCoreInfoProvider:
    """In-memory CoreInfoProvider for tests.

    Returns the configured ``active_core`` (the libretro system default, for the
    firmware BIOS filter) and ``options`` (the classified emulator picker list)
    for any system. ``options`` may be seeded directly with
    :class:`EmulatorOption` values or synthesized from the ``available_cores``
    convenience — a list of ``{"core_so", "label", "is_default"}`` dicts turned
    into bakeable libretro options (the shape most core-selection tests use).
    ``available`` mirrors the adapter's "was the catalogue readable?" flag, and
    ``reason`` / ``source`` the two fields the adapter answers beside it.

    A test lists ``options`` in the order the source declares them, so an option
    seeded without a ``declared_index`` is answered with its place in the list
    as one, and the first is the default as the adapter would read it.
    ``options_calls`` records the ``content_path`` and ``source`` each
    ``get_emulator_options`` call was asked with.

    ``readings`` and ``default_readings`` record the reading each
    ``get_emulator_options`` and ``get_default_emulator`` call was handed
    (``None`` for a call that asks fresh). ``active_core_calls`` and
    ``emulator_options_calls`` record the ``system_name`` each seam was invoked
    with so callers can assert a normalized system (not the raw platform slug)
    reached the read seam.

    ``standalone`` maps a system name to a standalone-emulator
    :class:`EmulatorInvocation`. When a system carries one,
    :meth:`get_default_emulator` returns it directly; otherwise it mirrors the
    real adapter and projects :meth:`get_active_core` into a core invocation
    (so ``get_active_core`` is still consulted — and recorded — for the core
    path).
    """

    def __init__(
        self,
        *,
        active_core: tuple[str | None, str | None] = (None, None),
        available_cores: list[dict[str, Any]] | None = None,
        options: list[EmulatorOption] | None = None,
        available: bool = True,
        standalone: dict[str, EmulatorInvocation] | None = None,
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
        self.standalone: dict[str, EmulatorInvocation] = standalone if standalone is not None else {}
        self.reason = reason
        self.source = source
        self.active_core_calls: list[str] = []
        self.emulator_options_calls: list[str] = []
        self.readings: list[SourcesReading | None] = []
        self.default_readings: list[SourcesReading | None] = []
        self.options_calls: list[dict[str, str | None]] = []

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

    def get_default_emulator(
        self, system_name: str, *, reading: SourcesReading | None = None
    ) -> EmulatorInvocation | None:
        self.default_readings.append(reading)
        pref = self.standalone.get(system_name)
        if pref is not None:
            return pref
        core_so, label = self.get_active_core(system_name)
        if core_so:
            return core_invocation(core_so, label)
        return None

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
