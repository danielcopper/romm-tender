"""In-memory save-location seam for service tests — what a ROM's save consists of."""

from __future__ import annotations

import os
from dataclasses import replace

from domain.save_answer import (
    SaveAnswer,
    SaveComponent,
    unestablished_answer,
)
from domain.savestate_location import NoSavestates, SavestateLocation

# What the retired per-system extension table answered for a system it held no
# override for. The default answer keeps that shape so a test written about save
# sync says nothing about save RESOLUTION unless it means to.
_DEFAULT_EXTENSIONS = (".srm", ".rtc", ".sav")

# Systems whose real answer on the reference machine differs from that default,
# as ``(extension, role)`` pairs. Saturn is here because several tests use its
# two-component save as their multi-file case, and because its console-settings
# file is the standing example of a file the answer names and the sync leaves
# alone. Measured through Beetle Saturn at emu-atlas 0.13.0.
_BY_SYSTEM: dict[str, tuple[tuple[str, str], ...]] = {
    "saturn": ((".bkr", "battery"), (".bcr", "battery"), (".smpc", "settings")),
}


class FakeSaveLocationReader:
    """The resolver's save answer, stated by the test instead of read off a machine.

    Answers a plain per-game file set derived from the content path's stem
    unless :meth:`answer_with` seeded something else for the system. ``calls``
    records one ``(system, content_path, emulator_label)`` tuple per question,
    which is how a test asserts that a refusing state asked nothing further —
    and how it pins that a sync path asked at all.

    The directory is the content file's own unless *saves_root* is given, in
    which case it is ``<saves_root>/<the content's parent folder>`` — RetroArch's
    content-sorted layout, the stock RetroDECK one. Either way the answer's root
    kind is the save root: a test about saves written beside the game seeds that
    answer on purpose.

    Savestates answer ``<states_root>/<the content's parent folder>`` when
    *states_root* is given and nothing could be established otherwise, unless
    :meth:`savestates_with` seeded the system.

    Deliberately insensitive to *emulator_label*, including ``None``. What a
    machine answers and whether the plugin had an emulator to ask about are two
    questions, and only the first is this seam's; the real adapter refuses a
    ``None`` label and ``tests/adapters/test_atlas_saves.py`` pins that. A fake
    that refused here would make every save test that leaves
    :class:`FakeActiveCoreResolver` at its ``(None, None)`` default a test about
    core resolution instead of about saves.
    """

    def __init__(
        self,
        *,
        extensions: tuple[str, ...] = _DEFAULT_EXTENSIONS,
        saves_root: str | None = None,
        states_root: str | None = None,
    ) -> None:
        self._extensions = extensions
        self._saves_root = saves_root
        self._states_root = states_root
        self._by_system: dict[str, SaveAnswer] = {}
        self._states_by_system: dict[str, SavestateLocation | NoSavestates | None] = {}
        self.calls: list[tuple[str, str, str | None]] = []
        self.savestate_calls: list[tuple[str, str, str | None]] = []

    def answer_with(self, system: str, answer: SaveAnswer) -> None:
        """Seed the answer *system* gives, whatever the ROM or the emulator."""
        self._by_system[system] = answer

    def refuse(self, system: str) -> None:
        """Seed *system* with the answer an emulator that was ASKED and could establish nothing gives.

        ``nothing_established``, not ``not_asked``: the question reached the
        resolver here. A test about a question nobody could put wants the seam
        never to be called at all, which is what the ``not_asked`` shape says.
        """
        self._by_system[system] = unestablished_answer()

    def savestates_with(self, system: str, answer: SavestateLocation | NoSavestates | None) -> None:
        """Seed the savestate answer *system* gives, whatever the ROM or the emulator."""
        self._states_by_system[system] = answer

    def resolve_save_answer(
        self, *, system: str, content_path: str, emulator_label: str | None, content_installed: bool = True
    ) -> SaveAnswer:
        self.calls.append((system, content_path, emulator_label))
        seeded = self._by_system.get(system)
        if seeded is not None:
            # ``content_installed`` describes the QUESTION, so it comes from the
            # caller even for a seeded answer — a seed states what the emulator
            # says, never whether this ROM is on disk. Returning the seed
            # verbatim would make the field untestable alongside a seeded state.
            return replace(seeded, content_installed=content_installed)
        stem = os.path.splitext(os.path.basename(content_path))[0]
        directory = self._sorted_by_content(self._saves_root, content_path) or os.path.dirname(content_path)
        parts = _BY_SYSTEM.get(system) or tuple((ext, "battery") for ext in self._extensions)
        return SaveAnswer(
            state="per_game_files",
            unestablished=None,
            emulator=emulator_label,
            directory=directory,
            backing_directory=None,
            granularity="per-game-files",
            needs=(),
            components=tuple(
                SaveComponent(name=f"{stem}{ext}", directory=directory, role=role, granularity="per-game-file")
                for ext, role in parts
            ),
            caveats=(),
            content_installed=content_installed,
            root_kind="savefile_directory",
        )

    def resolve_savestate_location(
        self, *, system: str, content_path: str, emulator_label: str | None
    ) -> SavestateLocation | NoSavestates | None:
        self.savestate_calls.append((system, content_path, emulator_label))
        if system in self._states_by_system:
            return self._states_by_system[system]
        directory = self._sorted_by_content(self._states_root, content_path)
        if directory is None:
            return None
        return SavestateLocation(directory=directory, root_kind="savestate_directory")

    @staticmethod
    def _sorted_by_content(root: str | None, content_path: str) -> str | None:
        """``<root>/<the content's parent folder>``, or ``None`` with no root."""
        if root is None:
            return None
        return os.path.join(root, os.path.basename(os.path.dirname(content_path)))
