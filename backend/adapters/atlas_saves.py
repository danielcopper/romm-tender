"""Atlas savefile adapter — the seam through which "where is this game's save" reaches the resolver.

The single place the vendored `emu-atlas <https://github.com/danielcopper/emu-atlas>`_
resolver is asked what one game's save consists of and where the emulator keeps
it. Services see a :class:`domain.save_answer.SaveAnswer` and never an atlas
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
entry. Where the plugin resolved no emulator, or the catalogue no longer offers
one under that label, there is nobody to ask and the answer says so.

The catalogue is asked WITH the content path, unlike
:mod:`adapters.atlas_catalogue`, and that is not a drift from ADR-0012. The
entry is chosen by the plugin's own resolved label, so a per-game
``<altemulator>`` still cannot promote anything into the launch; what the
content path buys is that the per-game configuration layers are read for the
game actually being asked about, which is what decides a granularity.

**Nothing is cached but the installation handle.** Every call is a live reading,
because the user changes a core's options in the emulator's own quick menu
between one launch and the next sync and a remembered granularity would have the
plugin sync a shared card per game. Holding the handle is what keeps that
affordable: on the reference machine a repeat reading costs 167 ms through a held
installation and 489 ms through a fresh one, and no write this plugin performs
can invalidate the handle.

The resolver never logs and raises on its own invariant violations rather than
degrading, so every call is wrapped and a failure becomes the honest "nothing
could be established" — never "there is nothing to sync", which is a refusal a
caller would read as a green light. Caveat ``code`` is the stable half of the
contract; ``message`` is prose that may change freely and nothing here parses one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _vendor.atlas import Unresolved

from domain.save_answer import (
    UNESTABLISHED_NOT_ASKED,
    SaveAnswer,
    SaveGroup,
    build_save_answer,
    unestablished_answer,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class AtlasSaveLocationAdapter:
    """Resolves one ROM's save location and file set, live, through the vendored resolver.

    Implements the ``SaveLocationReader`` Protocol structurally.
    """

    def __init__(
        self,
        *,
        choose_installation: Callable[[], Any],
        log_debug: Callable[[str], None],
    ) -> None:
        self._choose_installation = choose_installation
        self._log_debug = log_debug
        self._installation: Any = None

    def resolve_save_answer(
        self, *, system: str, content_path: str, emulator_label: str | None, content_installed: bool
    ) -> SaveAnswer:
        """What *emulator_label* saves for the game at *content_path*, and whether it may be synced.

        *emulator_label* is the emulator the plugin resolved for this ROM;
        ``None`` means it resolved none, so there is no entry to ask. That, no
        installation, and a catalogue no longer offering the label are all
        ``not_asked`` — the question never reached the resolver, so none of them
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
            # No installation, an unreadable catalogue, or no entry under that
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

    # -- helpers -------------------------------------------------------------

    def _entry(self, system: str, content_path: str, emulator_label: str) -> Any:
        """The catalogue entry carrying *emulator_label*, or ``None`` with nothing to ask."""
        installation = self._installation_handle()
        if installation is None:
            return None
        answer = self._ask(
            lambda: installation.emulators_for(system, content_path=content_path),
            f"emulators_for({system!r})",
        )
        if answer is None:
            return None
        entry = next((candidate for candidate in answer.entries if candidate.label == emulator_label), None)
        if entry is None:
            self._log_debug(f"[saves] {system}: the catalogue offers no entry labelled {emulator_label!r}")
        return entry

    def _installation_handle(self) -> Any:
        """The chosen installation, memoised, or ``None`` when nothing was detected.

        A detection that found nothing is deliberately NOT memoised, so a
        RetroDECK installed while the plugin runs is picked up on the next call —
        the same policy :mod:`adapters.atlas_catalogue` follows, for the same
        reason.
        """
        if self._installation is None:
            self._installation = self._ask(self._choose_installation, "detection")
            if self._installation is None:
                self._log_debug("[saves] no emulator installation detected")
        return self._installation

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
    """Restate one resolved placement in the plugin's own save vocabulary."""
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
    )
