"""In-memory ``ActiveCoreReader`` implementation for service tests.

Lets Phase-5 consumer tests inject a per-ROM active-core seam without standing
up a real ``ActiveCoreResolver`` (UoW + core-info + system resolver). Configure a
default ``(core_so, label)`` and/or per-``rom_id`` overrides; each call is
recorded so a consumer test can assert the seam was queried by ``rom_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.emulator_choice import DEFAULT, EmulatorChoice, ResolvedEmulator
from domain.emulator_commands import CORE, PROGRAM, EmulatorOption
from fakes.fake_core_info_provider import core_invocation

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading
    from domain.shortcut_data import EmulatorInvocation


class FakeActiveCoreResolver:
    """Maps ``rom_id`` to a configured ``(core_so, label)`` for tests.

    ``per_rom`` takes precedence; any ``rom_id`` absent from it resolves to
    ``default``. ``calls`` records each queried ``rom_id`` so consumer tests can
    assert the seam was reached with the right ROM, and ``emulator_readings``
    the reading each :meth:`active_emulator_for_rom` call was handed.

    :meth:`active_emulator_for_rom` is the launch-bake seam. By default it
    projects the same ``(core_so, label)`` tuple config into a core
    :class:`EmulatorInvocation` (or ``None`` when the core is ``None``), so the
    existing tuple-configured tests keep working unchanged. To exercise a
    **standalone** emulator (PCSX2, RPCS3, …), pass an explicit
    ``default_emulator`` and/or ``per_rom_emulator`` map — those take precedence
    over the tuple projection.

    :meth:`resolve_rom` answers that same invocation as the default layer's
    choice of a bakeable entry, or no entry where it is ``None``; ``resolved``
    maps a ``rom_id`` to the answer a test states instead.
    """

    def __init__(
        self,
        *,
        default: tuple[str | None, str | None] = (None, None),
        per_rom: dict[int, tuple[str | None, str | None]] | None = None,
        default_emulator: EmulatorInvocation | None = None,
        per_rom_emulator: dict[int, EmulatorInvocation | None] | None = None,
    ) -> None:
        self.default = default
        self.per_rom: dict[int, tuple[str | None, str | None]] = per_rom if per_rom is not None else {}
        self.default_emulator = default_emulator
        self.per_rom_emulator: dict[int, EmulatorInvocation | None] = (
            per_rom_emulator if per_rom_emulator is not None else {}
        )
        self.calls: list[int] = []
        self.emulator_calls: list[int] = []
        self.emulator_readings: list[SourcesReading | None] = []
        self.resolved: dict[int, ResolvedEmulator | None] = {}

    def active_core_for_rom(self, rom_id: int) -> tuple[str | None, str | None]:
        self.calls.append(rom_id)
        return self.per_rom.get(rom_id, self.default)

    def active_emulator_for_rom(
        self, rom_id: int, *, reading: SourcesReading | None = None
    ) -> EmulatorInvocation | None:
        self.emulator_calls.append(rom_id)
        self.emulator_readings.append(reading)
        if rom_id in self.per_rom_emulator:
            return self.per_rom_emulator[rom_id]
        if self.default_emulator is not None:
            return self.default_emulator
        core_so, label = self.per_rom.get(rom_id, self.default)
        if core_so is not None:
            # The identity a libretro entry carries is the core file's own
            # basename, which is what the resolver states for it — so a
            # tuple-configured test gets the identity its core would really have.
            return core_invocation(core_so, label, f"{core_so}.so")
        return None

    def resolve_rom(self, rom_id: int, *, reading: SourcesReading | None = None) -> ResolvedEmulator | None:
        if rom_id in self.resolved:
            return self.resolved[rom_id]
        invocation = self.active_emulator_for_rom(rom_id, reading=reading)
        option = (
            None
            if invocation is None
            else EmulatorOption(
                label=invocation.label or "",
                kind=CORE if invocation.core_so is not None else PROGRAM,
                core_so=invocation.core_so,
                command=invocation.command or "",
                status="bakeable",
                reason=None,
                emulator=invocation.emulator,
            )
        )
        choice = EmulatorChoice(option=option, layer=DEFAULT if option is not None else None, vanished=None)
        return ResolvedEmulator(choice=choice, options=[], invocation=invocation, folder_unstartable=False)
