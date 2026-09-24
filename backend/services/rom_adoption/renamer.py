"""AdoptionRenamer — carrying a ROM's name change, and everything named after it.

Owns the one question both exits of the adopt dialog ask: given content on disk
under the user's own name, what has to move so the game ends up under the
server's? Use These Files renames the ROM along with its saves; Download Instead
deletes the ROM and carries only the saves. Same plan, same collision question,
same answer applied to the same whole set — which is why it is one component and
not a rule copied into each exit.

Nothing here decides *whether* to act. The service owns the dialog, the refusals
and the ordering; this owns what a rename consists of and how far it got.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.adoption_rename import (
    OVERWRITE,
    ROM,
    SAVE,
    SAVESTATE,
    CompanionDir,
    RenamePair,
    collision_refusal,
    pairs_for_choice,
    rename_pairs,
    split_collisions,
)
from domain.rom_files import detect_launch_file
from domain.savestate_location import NoSavestates, SavestateLocation

if TYPE_CHECKING:
    import logging

    from services.protocols import (
        ActiveCoreReader,
        AdoptionMoveStore,
        DownloadFileStore,
        SaveLocationReader,
        SaveQuarantineFn,
        SystemM3uSupportFn,
    )

    from ._target import Target


def _travels_with(rom_source: str, directory: str) -> bool:
    """Whether *directory* moves with *rom_source* rather than standing beside it.

    True for a directory inside the content being renamed — a multi-file ROM
    whose emulator writes saves next to the game, which the resolver answers
    with a ``content_directory`` root. Its files arrive at the new name as part
    of the ROM's own move, so pairing them up would move them twice.
    """
    return directory == rom_source or directory.startswith(rom_source + os.sep)


@dataclass(frozen=True)
class AdoptionRenamerConfig:
    """Frozen wiring bundle handed to ``AdoptionRenamer.__init__``.

    ``save_locations`` answers where the save and the savestate directory are,
    asked of the emulator ``active_core`` names for this ROM, and
    ``download_file_store`` is here for one read: the launch file inside a
    directory candidate, which is what its saves are named after.
    ``quarantine_save`` is the sanctioned save-backup funnel — an Overwrite
    destroys save files, and this component does not own a way to do that.
    """

    adoption_move: AdoptionMoveStore
    quarantine_save: SaveQuarantineFn
    download_file_store: DownloadFileStore
    m3u_support: SystemM3uSupportFn
    save_locations: SaveLocationReader
    active_core: ActiveCoreReader
    logger: logging.Logger


class AdoptionRenamer:
    """What a ROM's rename to the canonical name consists of, and how far it got."""

    def __init__(self, *, config: AdoptionRenamerConfig) -> None:
        self._adoption_move = config.adoption_move
        self._quarantine_save = config.quarantine_save
        self._download_file_store = config.download_file_store
        self._m3u_support = config.m3u_support
        self._save_locations = config.save_locations
        self._active_core = config.active_core
        self._logger = config.logger

    def target_taken(self, target: Target) -> bool:
        """Whether something now occupies the ROM's own canonical path."""
        return self._adoption_move.exists(target.path)

    def carry_to_canonical(
        self, rom_id: int, target: Target, source_path: str, collision_choice
    ) -> dict[str, Any] | None:
        """Move the candidate, and everything RetroArch named after it, into place.

        ``None`` means every file arrived. The whole plan is computed and every
        target checked **before** the first file moves: renaming as you go and
        asking at the first collision would leave half the set moved when the
        question appears.
        """
        refusal, _carried = self.move_planned(self.rename_plan(rom_id, target, source_path), collision_choice)
        return refusal

    def move_planned(
        self, pairs: tuple[RenamePair, ...], collision_choice
    ) -> tuple[dict[str, Any] | None, tuple[RenamePair, ...]]:
        """Ask about every taken name, then carry the pairs the answer allows.

        Shared by both exits of the adopt dialog, so a name already taken raises
        the same question either way, with the same answer applied to the same
        whole set, and neither exit can acquire its own collision rule.

        Returns ``(refusal, carried)``. A ``None`` refusal means every file the
        answer allowed to move arrived; *carried* is which those were, so a caller
        whose **next** step can fail is able to say what this one already did
        rather than reporting a clean abort over files that have moved.
        """
        occupied = frozenset(pair.target for pair in pairs if self._adoption_move.exists(pair.target))
        clear, colliding = split_collisions(pairs, occupied)
        to_move = clear
        quarantined: tuple[str, ...] = ()
        if colliding:
            choice = str(collision_choice or "")
            chosen = pairs_for_choice(clear, colliding, choice)
            if chosen is None:
                return (collision_refusal(colliding), ())
            if choice == OVERWRITE:
                refusal, quarantined = self._replace_occupied(colliding)
                if refusal is not None:
                    return (refusal, ())
            to_move = chosen
        outcome = self._adoption_move.move_pairs(tuple((pair.source, pair.target) for pair in to_move))
        refusal = self._report_move(outcome, quarantined)
        moved = frozenset(outcome["moved"])
        return (refusal, tuple(pair for pair in to_move if pair.target in moved))

    def discarded_save_pairs(self, rom_id: int, target: Target, source_path: str) -> tuple[RenamePair, ...]:
        """The save and savestate pairs a discarded candidate leaves behind, ROM excluded.

        The ROM itself is being deleted rather than renamed, so its pair is
        dropped and only what RetroArch named after it travels.

        Empty for a **multi-file** ROM, and that is a limit rather than an
        oversight. A directory ROM's saves are named after the launch file
        *inside* it, and the launch file the download will produce sits in an
        archive that has not been fetched yet — so the name those saves would have
        to take is genuinely unknown here. Moving them to the candidate's own
        launch name would strand them under a name nothing reads, which is worse
        than leaving them where they are: untouched, and still findable.
        """
        if target.is_multi:
            self._logger.info(
                f"Leaving rom {rom_id}'s saves under their current names: the downloaded directory's "
                f"launch file is not known until it is extracted"
            )
            return ()
        return tuple(pair for pair in self.rename_plan(rom_id, target, source_path) if pair.kind != ROM)

    def rename_plan(self, rom_id: int, target: Target, source_path: str) -> tuple[RenamePair, ...]:
        """Every source → target pair renaming this content consists of.

        The stems come from the **launch file**, not from what is being renamed:
        for a multi-file ROM the launch file sits inside the directory that moves,
        so its name does not change while the directory's does — which is exactly
        the case where the save *directory* moves and the save *filenames* stay.
        """
        launch_source, launch_target = self._launch_paths(target, source_path)
        return rename_pairs(
            rom_source=source_path,
            rom_target=target.path,
            stem_source=os.path.splitext(os.path.basename(launch_source))[0] if launch_source else "",
            stem_target=os.path.splitext(os.path.basename(launch_target))[0] if launch_target else "",
            companions=self._companions(rom_id, target, source_path, launch_source, launch_target),
        )

    def _replace_occupied(self, colliding: tuple[RenamePair, ...]) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
        """Move the files an Overwrite answers for into ``.romm-backup``, before anything else moves.

        Clearing first rather than replacing as each file lands keeps the two
        halves apart: one destructive phase the user answered for, then a move
        phase with no collisions left in it.

        Returns ``(refusal, quarantined)``. *quarantined* holds only what the
        funnel actually moved — it reports ``False`` for a target that is not a
        regular file, and a list built without reading that would name a file
        still sitting where it was. The caller carries it onward because the step
        **after** this one can fail too, and a user whose other-version saves are
        in ``.romm-backup`` has to be told they are there.

        Every colliding target is a save or a savestate: the ROM's own target is
        refused before the plan is consulted, and the discard path drops the ROM
        pair. So they all go through the save-backup funnel rather than an unlink
        of this component's own. ADR-0028 declined to quarantine a **ROM** on the
        grounds that ROMs are gigabytes with no sensible retention and are
        re-fetchable from RomM; both halves of that argument invert here. A
        savestate in particular is synced nowhere at all, so a replaced one exists
        in no other copy.
        """
        unusable = self._not_a_file(colliding)
        if unusable is not None:
            return (unusable, ())
        quarantined: list[str] = []
        for pair in colliding:
            try:
                moved = self._quarantine_save(os.path.dirname(pair.target), os.path.basename(pair.target))
            except (OSError, ValueError) as e:
                return (self._replace_refusal(quarantined, os.path.basename(pair.target), e), tuple(quarantined))
            if moved:
                quarantined.append(pair.target)
        return (None, tuple(quarantined))

    def _not_a_file(self, colliding: tuple[RenamePair, ...]) -> dict[str, Any] | None:
        """Refuse, by name and before anything moves, a target the funnel cannot set aside.

        The funnel moves a regular file; a directory or a dangling symlink at a
        save's name reports ``False`` and leaves the collision in place, so the
        move would then fail at the link with nothing explaining why. Checking the
        whole set first keeps the refusal honest about having touched nothing.
        """
        blocked = [
            pair.target
            for pair in colliding
            if self._adoption_move.exists(pair.target) and not self._adoption_move.is_file(pair.target)
        ]
        if not blocked:
            return None
        names = ", ".join(os.path.basename(path) for path in blocked)
        self._logger.error(f"Refusing to replace non-file collision target(s): {names}")
        return {
            "success": False,
            "reason": "replace_failed",
            "message": f"Cannot replace {names} — a folder or link is there, not a file. Nothing was moved.",
        }

    def _replace_refusal(self, quarantined: list[str], failed: str, error: Exception) -> dict[str, Any]:
        """Report an Overwrite that could not finish, naming what was already set aside."""
        already = (
            " These were already moved to .romm-backup: "
            + ", ".join(os.path.basename(path) for path in quarantined)
            + "."
            if quarantined
            else ""
        )
        self._logger.error(f"Adoption overwrite failed after backing up {len(quarantined)} file(s): {error}")
        return {
            "success": False,
            "reason": "replace_failed",
            "message": f"Could not replace {failed} ({error}). Nothing was moved.{already}",
        }

    def _report_move(self, outcome, quarantined: tuple[str, ...]) -> dict[str, Any] | None:
        """Turn a move outcome into a refusal, or ``None`` when everything arrived.

        A source left beside a completed target is not a failure: one inode under
        two names loses nothing and a re-run finishes it. It is logged rather than
        surfaced, because the user's game is playable and the alternative is a
        scary dialog about a state that harmed nothing.

        *quarantined* is what the Overwrite before this one set aside. It is named
        in any refusal here, because a clear that succeeded in front of a move
        that failed leaves the user's other-version saves in ``.romm-backup`` for
        a replacement that never arrived — and nothing else would say so.
        """
        if outcome["stranded"]:
            self._logger.warning(f"Adoption left old copies behind: {outcome['error']}")
        if not outcome["unmoved"]:
            return None
        moved = ", ".join(os.path.basename(path) for path in outcome["moved"])
        unmoved = ", ".join(os.path.basename(path) for path in outcome["unmoved"])
        self._logger.error(f"Adoption rename failed: {outcome['error']}")
        arrived = f" These are at their new names: {moved}." if moved else ""
        set_aside = (
            " These were moved to .romm-backup to make room and are still there: "
            + ", ".join(os.path.basename(path) for path in quarantined)
            + "."
            if quarantined
            else ""
        )
        return {
            "success": False,
            "reason": "rename_failed",
            "message": (
                f"Could not rename this game's files ({outcome['error']}). "
                f"Still under the old name: {unmoved}.{arrived}{set_aside}"
            ),
        }

    def _launch_paths(self, target: Target, source_path: str) -> tuple[str, str]:
        """The file RetroArch names the saves after, where it is now and where it will be.

        For a single-file ROM that is the ROM itself. For a directory it is the
        launch file inside, picked by the download's own rule, and its place under
        the renamed directory is the same relative path. Two empty strings when a
        directory holds no file to launch — there is then no stem, and no
        companion can be attributed to this ROM.
        """
        if not target.is_multi:
            return (source_path, target.path)
        files = self._download_file_store.scan_files_with_sizes(source_path)
        detected = detect_launch_file(files, self._m3u_support(target.system))
        if detected is None:
            return ("", "")
        return (detected, os.path.join(target.path, os.path.relpath(detected, source_path)))

    def _companions(
        self, rom_id: int, target: Target, source_path: str, launch_source: str, launch_target: str
    ) -> tuple[CompanionDir, ...]:
        """The save and savestate directories this rename has to carry files out of.

        Both come from the resolver, asked about the launch file under its old
        name and under its new one — the new one need not exist, because the
        resolver places a game's saves by the content path's own coordinates. The
        two are asked independently because an emulator keeps them independently:
        a stock RetroDECK install content-sorts its savefiles and leaves its
        savestates unsorted. A directory that could not be established is not a
        companion — nothing is carried out of a place nobody could name — and
        neither is one that sits **inside** the content being moved: it travels
        with the rename already, and pairing its files up would move them a
        second time.
        """
        if not launch_source:
            return ()
        emulator = self._active_core.active_emulator_for_rom(rom_id)
        label = emulator.label if emulator is not None else None
        directories = (
            (
                SAVE,
                self._save_dir(target.system, launch_source, label, installed=True),
                self._save_dir(target.system, launch_target, label, installed=False),
            ),
            (
                SAVESTATE,
                self._savestate_dir(target.system, launch_source, label),
                self._savestate_dir(target.system, launch_target, label),
            ),
        )
        found: list[CompanionDir] = []
        for kind, source_dir, target_dir in directories:
            if isinstance(source_dir, NoSavestates) or isinstance(target_dir, NoSavestates):
                self._logger.info(f"The emulator keeps no savestates for rom {rom_id}; carrying none")
                continue
            if source_dir is None or target_dir is None:
                self._logger.info(f"No {kind} directory could be established for rom {rom_id}; carrying none")
                continue
            if _travels_with(source_path, source_dir) or _travels_with(source_path, target_dir):
                continue
            names = self._adoption_move.list_names(source_dir)
            if names:
                found.append(CompanionDir(kind=kind, source_dir=source_dir, target_dir=target_dir, names=names))
        return tuple(found)

    def _save_dir(self, system: str, content_path: str, emulator_label: str | None, *, installed: bool) -> str | None:
        """The directory the resolver answers for the save of the game at *content_path*."""
        return self._save_locations.resolve_save_answer(
            system=system, content_path=content_path, emulator_label=emulator_label, content_installed=installed
        ).directory

    def _savestate_dir(self, system: str, content_path: str, emulator_label: str | None) -> str | NoSavestates | None:
        """The directory the resolver answers for the savestates of the game at *content_path*.

        :class:`NoSavestates` where the emulator keeps none and ``None`` where
        nothing could be established — the rename carries nothing either way,
        and its log line says which.
        """
        answer = self._save_locations.resolve_savestate_location(
            system=system, content_path=content_path, emulator_label=emulator_label
        )
        return answer.directory if isinstance(answer, SavestateLocation) else answer
