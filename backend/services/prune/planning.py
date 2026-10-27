"""Decide what one group's cleanup would do, before anything can be changed.

Reads the group as it stands right now — local rows, active downloads, whether
its game is running, fresh liveness, unsynced saves — and either refuses it with
a terminal result or freezes the decision as a :class:`GroupPlan`. Every later
phase re-proves what it is about to touch, so nothing decided here is trusted as
still true; the plan carries only the choices that cannot be re-derived, which
is what keeps the phases separable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.prune import selected_prune_ids
from domain.save_stranding import STRANDING_NONE, STRANDING_UNCHECKED
from services.prune.liveness import UNCONFIRMED_REASON

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.rom import Rom
    from services.protocols import ActiveDownloadRomIdsFn, RunningRomIdsFn, SaveDriftProbeFn
    from services.prune._models import PruneOptions
    from services.prune.liveness import LivenessProber
    from services.prune.registry import PruneRegistry
    from services.prune.results import MutationLedger, PruneResultReporter


@dataclass(frozen=True)
class GroupPlan:
    """What one group's later phases act on, frozen at the end of planning.

    Only the decisions downstream cannot re-derive: liveness verdicts, candidate
    intersections and skip reasons all die here, because arming, the Steam
    action and finalization each re-prove liveness and local state from scratch.
    """

    rows: list[Rom]
    group_ids: set[int]
    bound_row: Rom | None
    app_id: int | None
    delete_ids: set[int]
    fully_dead: bool
    whole_game_action: bool


@dataclass(frozen=True)
class _GroupLiveness:
    """One group's ids sorted by what RomM proved about each of them."""

    vanished_ids: set[int]
    live_ids: set[int]
    uncertain_ids: set[int]
    fully_dead: bool

    @classmethod
    def of(cls, group_ids: set[int], verdicts: dict[int, dict[str, str]]) -> _GroupLiveness:
        vanished_ids = {rom_id for rom_id, verdict in verdicts.items() if verdict["status"] == "vanished"}
        live_ids = {rom_id for rom_id, verdict in verdicts.items() if verdict["status"] == "live"}
        return cls(
            vanished_ids=vanished_ids,
            live_ids=live_ids,
            uncertain_ids=group_ids - vanished_ids - live_ids,
            fully_dead=bool(group_ids) and group_ids <= vanished_ids,
        )


_UNSYNCED_SAVES_MESSAGE = "Unsynced saves require a sealed recovery bundle before changing this shortcut."
_UNCHECKED_SAVES_MESSAGE = (
    "Tender couldn't check whether this game's saves have been synced to RomM; "
    "a sealed recovery bundle is required before changing this shortcut."
)


@dataclass(frozen=True)
class GroupPlannerConfig:
    """Dependencies for deciding one group's cleanup without mutating anything."""

    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    results: PruneResultReporter
    registry: PruneRegistry
    liveness: LivenessProber
    active_downloads: ActiveDownloadRomIdsFn
    drift_probe: SaveDriftProbeFn
    running_rom_ids: RunningRomIdsFn


# The result names the group's game beside its message (``results.group_result``).
GAME_RUNNING_MESSAGE = "This game is running, so it was left as it is. Quit it and scan again to remove it."


def _unproven_refusal(reasons: set[str], unproven_count: int) -> tuple[str, str]:
    """Name the most specific reason a group with no live answer is refused.

    Ordered by how much it tells the user: a namespace change and an
    unconfirmed server both explain *why* no answer could be trusted, and only
    the generic case is a plain count.
    """
    if "server_namespace_changed" in reasons:
        return (
            "server_namespace_changed",
            "The RomM server or user changed during exact-ID proof; nothing was removed.",
        )
    if UNCONFIRMED_REASON in reasons:
        return (
            UNCONFIRMED_REASON,
            "RomM's answers could not be confirmed, so a 404 could not be trusted; nothing was removed.",
        )
    return "liveness_uncertain", f"RomM could not confirm {unproven_count} group member(s); nothing was removed."


def _whole_game_skip(group_ids: set[int], candidate_ids: set[int], proof: _GroupLiveness) -> tuple[str, str] | None:
    """The ``(reason, message)`` a group is skipped with where removing it would not match what the dialog showed."""
    if proof.fully_dead and group_ids - candidate_ids:
        # A whole-game removal would take a version the preview never offered
        # for removal. The run still marks it gone, so the next scan lists it.
        return "another_version_gone", "Another version of this game is gone from RomM too. Scan again to review it."
    if candidate_ids == group_ids and proof.live_ids:
        # The preview offered every version, so the dialog showed the game as
        # gone entirely: it is removed whole or not at all.
        return "version_back_on_romm", "A version of this game is back on RomM. Scan again to review it."
    return None


class GroupPlanner:
    """Turn one candidate group into a plan, or into the reason there is none."""

    def __init__(self, *, config: GroupPlannerConfig) -> None:
        self._loop = config.loop
        self._logger = config.logger
        self._results = config.results
        self._registry = config.registry
        self._liveness = config.liveness
        self._active_downloads = config.active_downloads
        self._drift_probe = config.drift_probe
        self._running_rom_ids = config.running_rom_ids

    async def plan(
        self,
        run_id: str,
        initial_rows: list[Rom],
        preview_candidate_ids: set[int],
        options: PruneOptions,
        index: int,
        total: int,
        ledger: MutationLedger,
    ) -> GroupPlan | dict[str, Any]:
        """Return this group's plan, or the terminal result dict that refuses it."""
        rows = await self._loop.run_in_executor(None, self._registry.reread_group, initial_rows[0].rom_id)
        ledger.rows = rows or initial_rows
        if not rows:
            return self._results.group_result(
                initial_rows, "skipped", "local_state_changed", "The local group changed."
            )
        group_ids = {row.rom_id for row in rows}
        candidate_ids = group_ids & preview_candidate_ids
        bound_row, unstable = self._admit_group(rows, group_ids)
        if unstable is not None:
            return unstable
        if bound_row is not None and bound_row.rom_id in await self._running_rom_ids():
            # Its shortcut, files and saves all stay; the next scan offers it again.
            return self._results.group_result(rows, "skipped", "game_running", GAME_RUNNING_MESSAGE)

        verdicts = await self._liveness.probe_many(group_ids)
        proof = _GroupLiveness.of(group_ids, verdicts)
        self._log_liveness(f"{run_id} group {index}/{total}", proof, candidate_ids, bound_row)
        if not proof.live_ids and proof.uncertain_ids:
            reason, message = _unproven_refusal(
                {verdicts[rom_id]["reason"] for rom_id in proof.uncertain_ids}, len(proof.uncertain_ids)
            )
            return self._results.group_result(rows, "skipped", reason, message)
        whole_game_skip = _whole_game_skip(group_ids, candidate_ids, proof)
        if whole_game_skip is not None:
            return self._results.group_result(rows, "skipped", *whole_game_skip)

        delete_ids = selected_prune_ids(
            group_ids=sorted(group_ids),
            candidate_ids=candidate_ids,
            vanished_ids=proof.vanished_ids,
            live_ids=proof.live_ids,
            remove_rows=options.remove_rows,
            remove_fully_vanished=options.remove_fully_vanished,
        )
        spared_bound = bound_row is not None and bound_row.rom_id in delete_ids and bool(proof.live_ids)
        if spared_bound and bound_row is not None:
            # The shortcut is the game while it still has live versions; the
            # user moves it with the version picker, never the cleanup.
            delete_ids.remove(bound_row.rom_id)

        whole_game_action = proof.fully_dead and bool(delete_ids)
        if not delete_ids:
            return self._nothing_to_do(rows, proof.uncertain_ids, spared_bound=spared_bound)

        unsynced = await self._stranding_refusal(rows, bound_row, proof, whole_game_action, options)
        if unsynced is not None:
            return unsynced

        return GroupPlan(
            rows=rows,
            group_ids=group_ids,
            bound_row=bound_row,
            app_id=bound_row.shortcut_app_id if bound_row is not None else None,
            delete_ids=delete_ids,
            fully_dead=proof.fully_dead,
            whole_game_action=whole_game_action,
        )

    def _log_liveness(
        self, run_label: str, proof: _GroupLiveness, candidate_ids: set[int], bound_row: Rom | None
    ) -> None:
        """Record the verdicts every later decision turns on.

        A group reported as skipped is otherwise impossible to explain after the
        fact without re-running against the same server.
        """
        self._logger.info(
            f"Cleanup run {run_label} liveness: "
            f"gone={sorted(proof.vanished_ids)}, still_there={sorted(proof.live_ids)}, "
            f"unconfirmed={sorted(proof.uncertain_ids)}, "
            f"candidates={sorted(candidate_ids)}, bound={bound_row.rom_id if bound_row else None}"
        )

    def _admit_group(self, rows: list[Rom], group_ids: set[int]) -> tuple[Rom | None, dict[str, Any] | None]:
        """Name the group's bound row, or refuse a group nothing may act on yet."""
        bound = [row for row in rows if row.shortcut_app_id is not None]
        if len(bound) > 1:
            return None, self._results.group_result(
                rows, "skipped", "multiple_bindings", "The group has multiple shortcuts."
            )
        if self._active_downloads() & group_ids:
            return None, self._results.group_result(
                rows, "skipped", "download_in_progress", "Cancel active downloads first."
            )
        return (bound[0] if bound else None), None

    def _nothing_to_do(self, rows: list[Rom], uncertain_ids: set[int], *, spared_bound: bool) -> dict[str, Any]:
        """Say why a group with nothing selected has nothing selected.

        Distinguish "your options excluded everything" from "RomM never
        confirmed anything gone" and from "the version the shortcut is on is
        kept". All three leave nothing to do, but only the first is answered by
        changing a toggle — reporting the others as an options problem sends the
        user to fiddle with settings that cannot help.
        """
        if spared_bound:
            return self._results.group_result(
                rows,
                "skipped",
                "shortcut_version_kept",
                "The version this game's shortcut is on is kept while another version is on RomM; "
                "switch versions first.",
            )
        if uncertain_ids:
            return self._results.group_result(
                rows,
                "skipped",
                "liveness_uncertain",
                f"RomM could not confirm {len(uncertain_ids)} of this game's version(s); nothing was removed.",
            )
        return self._results.group_result(
            rows, "skipped", "options_excluded", "No confirmed rows matched the selected options."
        )

    async def _stranding_refusal(
        self,
        rows: list[Rom],
        bound_row: Rom | None,
        proof: _GroupLiveness,
        whole_game_action: bool,
        options: PruneOptions,
    ) -> dict[str, Any] | None:
        """Skip the group where its shortcut holds saves this run would strand and no bundle seals them.

        The drift probe's verdict decides
        (``domain/save_stranding.py::stranding_verdict``); a check that could
        not read says so in its own message.
        """
        if bound_row is None or bound_row.rom_id not in proof.vanished_ids:
            return None
        if not whole_game_action:
            return None
        stranding = (await self._drift_probe(bound_row.rom_id))["stranding"]
        if stranding == STRANDING_NONE or options.create_recovery_bundle:
            return None
        return self._results.group_result(
            rows,
            "skipped",
            "unsynced_saves",
            _UNCHECKED_SAVES_MESSAGE if stranding == STRANDING_UNCHECKED else _UNSYNCED_SAVES_MESSAGE,
        )


__all__ = ["GAME_RUNNING_MESSAGE", "GroupPlan", "GroupPlanner", "GroupPlannerConfig"]
