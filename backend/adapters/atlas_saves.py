"""Atlas save adapter — the seam through which "where is this game's save" reaches the resolver.

The single place the vendored `emu-atlas <https://github.com/danielcopper/emu-atlas>`_
resolver is asked what one game's save consists of and where the emulator keeps
it, and where it keeps that game's savestates. Services see a
:class:`domain.save_answer.SaveAnswer` or a
:class:`domain.savestate_location.SavestateLocation` and never an atlas
type — ``domain/`` may not import ``_vendor`` at all (the ``domain-stdlib-only``
contract), so the vocabulary and the resolver have to meet at an adapter, as
they already do for the catalogue and the firmware seams.

**The question goes to a catalogue entry, not to a bare core.** A save location
is a property of the emulator that opens the game: standalone PCSX2 keeps two
shared memory cards where a libretro core would keep a file per game, and asking
"what does system X save" has no answer. So the caller names the emulator it
resolved for this ROM — the label
:class:`services.active_core_resolver.ActiveCoreResolver` already produced,
which is the label the launch bakes — and this adapter puts the question to that
entry. Where Tender resolved no emulator, the catalogue answer is a refusal
(:func:`adapters.atlas_catalogue.catalogue_refused`), or it no longer offers
one under that label, there is nobody to ask and the answer says so.

The catalogue is asked WITH the content path, unlike
:mod:`adapters.atlas_catalogue`, and that is not a drift from ADR-0012. The
entry is chosen by Tender's own resolved label, so a per-game
``<altemulator>`` still cannot promote anything into the launch; what the
content path buys is that the per-game configuration layers are read for the
game actually being asked about, which is what decides a granularity.

**Nothing is cached here.** Every call is a live reading of the sources
(:mod:`adapters.emulator_sources`), because the user changes a core's options in
the emulator's own quick menu between one launch and the next sync and a
remembered granularity would have Tender sync a shared card per game. What keeps
that affordable is the one resolver machine the sources hold for the process,
which runs a core's probe once for as long as the core file is unchanged.

The resolver never logs and raises on its own invariant violations rather than
degrading, so every call is wrapped and a failure becomes the honest "nothing
could be established" — never "there is nothing to sync", which is a refusal a
caller would read as a green light. Caveat ``code`` is the stable half of the
contract; ``message`` is prose that may change freely and nothing here parses one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _vendor.atlas import SavestateAbsence, SavestatePlacement, Unresolved, core_probe_interpreter

from adapters.atlas_catalogue import catalogue_refused
from domain.save_answer import (
    UNESTABLISHED_NOT_ASKED,
    SaveAnswer,
    SaveGroup,
    build_save_answer,
    unestablished_answer,
)
from domain.savestate_location import NoSavestates, SavestateLocation

if TYPE_CHECKING:
    from collections.abc import Callable

    from adapters.emulator_sources import EmulatorSourcesAdapter


class AtlasSaveLocationAdapter:
    """Resolves one ROM's save and savestate locations, live, through the vendored resolver.

    Implements the ``SaveLocationReader`` Protocol structurally.
    """

    def __init__(
        self,
        *,
        sources: EmulatorSourcesAdapter,
        log_debug: Callable[[str], None],
    ) -> None:
        self._sources = sources
        self._log_debug = log_debug

    def resolve_save_answer(
        self, *, system: str, content_path: str, emulator_label: str | None, content_installed: bool
    ) -> SaveAnswer:
        """What *emulator_label* saves for the game at *content_path*, and whether it may be synced.

        *emulator_label* is the emulator Tender resolved for this ROM;
        ``None`` means it resolved none, so there is no entry to ask. That, no
        answering source, a refused catalogue, and a catalogue no longer
        offering the label are all ``not_asked`` — the question never reached the resolver, so none of them
        is a statement about the emulator. An entry that declines and a resolver
        that raises WERE asked, so both are ``nothing_established``.

        *content_installed* is the caller's own statement about *content_path*:
        ``False`` where it is the path a ROM WOULD occupy rather than a file on
        disk. This adapter cannot tell — it hands the path to the resolver
        either way — so it carries the caller's word onto the answer, where a
        surface can say "would use" instead of "uses".
        """
        if emulator_label is None:
            self._log_debug(f"[saves] {system}: no emulator resolved for this ROM; nothing to ask")
            return unestablished_answer(shape=UNESTABLISHED_NOT_ASKED, content_installed=content_installed)

        entry = self._entry(system, content_path, emulator_label)
        if entry is None:
            # No answering source, a refused catalogue, or no entry under that
            # label: the question never reached the resolver, so this says
            # nothing about the emulator itself.
            return unestablished_answer(
                emulator=emulator_label, shape=UNESTABLISHED_NOT_ASKED, content_installed=content_installed
            )

        subject = f"savefile_location({system!r}, {emulator_label!r})"
        placement = self._ask(lambda: entry.savefile_location(content_path=content_path), subject)
        if placement is None or isinstance(placement, Unresolved):
            if isinstance(placement, Unresolved):
                self._log_debug(f"[saves] {subject}: declined with code={placement.code!r}")
            return unestablished_answer(emulator=emulator_label, content_installed=content_installed)

        answer = _translate(placement, emulator_label, content_installed)
        self._log_debug(
            f"[saves] {subject}: state={answer.state} shape={answer.unestablished} "
            f"granularity={answer.granularity} files={len(answer.components)} "
            f"caveats={sorted(set(answer.caveats))}"
        )
        return answer

    def resolve_savestate_location(
        self, *, system: str, content_path: str, emulator_label: str | None
    ) -> SavestateLocation | NoSavestates | None:
        """Where *emulator_label* keeps the savestates for the game at *content_path*.

        *content_path* need not exist: the resolver places a game's states by
        the path's own coordinates, so a rename can ask about the name the ROM
        is about to take. ``None`` wherever nothing could be established — no
        emulator, no answering source, no entry under that label, a refusal, a
        raise — because each leaves the states' whereabouts unknown, and a
        caller must not read that as "there are none". :class:`NoSavestates` is
        that statement, made by the resolver with its evidence.
        """
        if emulator_label is None:
            return None
        entry = self._entry(system, content_path, emulator_label)
        if entry is None:
            return None
        subject = f"savestate_location({system!r}, {emulator_label!r})"
        placement = self._ask(lambda: entry.savestate_location(content_path=content_path), subject)
        if isinstance(placement, SavestateAbsence):
            return NoSavestates()
        if not isinstance(placement, SavestatePlacement):
            if isinstance(placement, Unresolved):
                self._log_debug(f"[saves] {subject}: declined with code={placement.code!r}")
            return None
        return SavestateLocation(
            directory=placement.dir, root_kind=placement.root_kind, fallback_directory=placement.fallback_dir
        )

    # -- helpers -------------------------------------------------------------

    def _entry(self, system: str, content_path: str, emulator_label: str) -> Any:
        """The catalogue entry carrying *emulator_label*, or ``None`` with nothing to ask."""
        reading = self._sources.read()
        installation = reading.answering_installation()
        if installation is None:
            self._log_debug(f"[saves] no emulator source answers ({reading.no_answer_reason()})")
            return None
        answer = self._ask(
            lambda: installation.emulators_for(system, content_path=content_path),
            f"emulators_for({system!r})",
        )
        if answer is None:
            return None
        if catalogue_refused(answer):
            self._log_debug(
                f"[saves] {system}: the catalogue was refused "
                f"(caveats={sorted({caveat.code for caveat in answer.caveats})}); nothing to ask"
            )
            return None
        entry = next((candidate for candidate in answer.entries if candidate.label == emulator_label), None)
        if entry is None:
            self._log_debug(f"[saves] {system}: the catalogue offers no entry labelled {emulator_label!r}")
        return entry

    def installation_detected(self) -> bool:
        """Whether an emulator source answers, so there is an installation to put questions to."""
        return self._sources.read().answering_installation() is not None

    def _ask(self, question: Callable[[], Any], subject: str) -> Any:
        """Put one question to the resolver, or answer ``None`` where it could not be asked.

        Deliberately broad: the resolver's failure modes are its own invariant
        assertions and its packaged-data loaders, neither of which is an
        exception type this adapter should enumerate. Every caller has a ``None``
        branch, and all of them lead to the same honest refusal.
        """
        try:
            return question()
        except Exception as exc:
            self._log_debug(f"[saves] resolver failed on {subject}: {exc!r}")
            return None


def _translate(placement: Any, emulator_label: str, content_installed: bool) -> SaveAnswer:
    """Restate one resolved placement in Tender's own save vocabulary."""
    file_set = placement.file_set
    granularity = placement.granularity
    return build_save_answer(
        emulator=emulator_label,
        directory=placement.dir,
        backing_directory=placement.physical_dir,
        granularity=granularity.value if granularity is not None else None,
        needs=tuple(placement.needs),
        file_set_state=file_set.state,
        files=tuple(file_set.files),
        groups=tuple(
            SaveGroup(
                directory=group.dir,
                files=None if group.files is None else tuple(group.files),
                role=group.role,
                granularity=group.granularity,
            )
            for group in file_set.groups
        ),
        caveats=tuple(caveat.code for caveat in placement.caveats),
        content_installed=content_installed,
        root_kind=placement.root_kind,
        fallback_directory=placement.fallback_dir,
    )


def describe_core_probe_interpreter() -> str:
    """Name the interpreter atlas would run a core probe under here, as a log line.

    Why the line exists, and why nothing registers an interpreter over atlas's
    own, is ``docs/architecture/backend-architecture.md``'s, under "Composition
    Root".

    A string rather than the resolver's own ``CoreProbeInterpreter``, because
    the one reader is the log at the wiring site in ``bootstrap/``, which may
    not hold a ``_vendor`` type.
    """
    derived = core_probe_interpreter()
    if derived is None:
        return "atlas core probe: no interpreter to run under — every core answers unknown"
    return f"atlas core probe: {derived.path} (atlas's own, from the running program)"
