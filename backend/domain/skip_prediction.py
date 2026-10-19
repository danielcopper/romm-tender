"""Plan-time estimate kernels for the skip-aware sync time estimate.

Pure predictions derived from locally persisted state: whether the
fetch-time wholesale-skip gate is expected to skip a platform unit, how
many Steam shortcuts a platform's persisted rows collapse into, and how
many of those shortcuts do not exist yet. They exist ONLY to price the
``sync_plan`` estimate payload (``predicted_skip`` / ``collapsed_count``
/ ``new_shortcut_count`` / ``total_estimated_items``).

Hard constraint (ADR-0023): the fetch-time gate
(``LibraryFetcher._try_unit_incremental_skip``) remains the SOLE skip
authority — nothing computed here may ever feed the actual skip
decision. A mis-prediction can only mis-estimate (the countdown reads
long or short); it can never mis-apply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.platform_skip import stamp_can_match

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.platform_sync_state import PlatformSyncState


def predict_unit_skip(
    *,
    stamp: PlatformSyncState | None,
    fetched_count: int,
    registry_count: int,
    needs_backfill: bool,
) -> bool:
    """Predict whether the wholesale-skip gate will skip a platform unit.

    Replays the gate's LOCAL conditions only: a completion stamp exists
    (the caller passes ``stamp_for_skip``'s answer) and records the fetch
    start and the id checksum the gate compares RomM's answer with
    (``domain/platform_skip.stamp_can_match``), rows carrying the stamp's
    fetch generation are persisted (``fetched_count``,
    ``domain/fetch_generation.count_rows_for_skip``) and at least one row
    is bound, and no ``sibling_group_key`` backfill is pending. The gate's
    one request (``domain/platform_skip.probe_says_unchanged``) is
    deliberately NOT replayed — no network at plan time — so a platform
    with a ROM updated, added, removed or hidden since the stamp may be
    predicted as a skip that the fetch then refuses; the estimate reads
    short, the apply stays correct.

    A Force Full Sync needs no special case: ``clear_sync_cache`` deletes
    every stamp before the run starts, so ``stamp`` reads ``None`` at plan
    time and no skip is predicted.

    Estimate-only (ADR-0023): the return value prices the plan payload and
    must never feed the actual skip decision.
    """
    return (
        stamp is not None and stamp_can_match(stamp) and fetched_count > 0 and registry_count > 0 and not needs_backfill
    )


def collapsed_shortcut_count(rows: Iterable[tuple[str | None, bool]]) -> int:
    """Post-collapse shortcut count for one platform's persisted rows.

    Mirrors the lane selection of ``collapse_sibling_groups`` (ADR-0021)
    under the plan-time assumption that the next fetch matches the
    persisted rows: a group with **bound** siblings is grandfathered — one
    shortcut per bound sibling (§5, a pre-ADR-0021 library keeps its
    duplicate shortcuts) — while a group with no binding anywhere mints
    exactly one representative. Given every persisted row as
    ``(sibling_group_key, is_bound)`` (bound = ``shortcut_app_id`` is set),
    each non-``None`` group therefore counts ``max(1, bound rows in
    group)``, and a keyless row is its own singleton (its real group is
    unknown until the backfill fetch computes the key).
    """
    bound_by_key: dict[str, int] = {}
    singletons = 0
    for key, is_bound in rows:
        if key is None:
            singletons += 1
        else:
            bound_by_key[key] = bound_by_key.get(key, 0) + (1 if is_bound else 0)
    return sum(max(1, bound) for bound in bound_by_key.values()) + singletons


def new_shortcut_count(rows: Iterable[tuple[str | None, bool]], *, unit_rom_count: int) -> int:
    """Shortcuts a platform's next apply is expected to CREATE rather than update.

    The complement of :func:`collapsed_shortcut_count`: that one counts the
    shortcuts a platform ends up with, this one the subset of them that has
    to be minted. A sibling group (ADR-0021) with any bound row already owns
    its shortcut — every further row in it is a duplicate the collapse drops,
    never a create — so only a group with no binding anywhere counts, plus
    each unbound keyless row (its real group is unknown until the backfill
    fetch computes the key, so it must be assumed to be its own creation).

    *unit_rom_count* is the server's ROM count for the platform. Rows the
    local mirror does not hold yet — ``unit_rom_count`` beyond the number of
    persisted rows — each count as a create, which is what keeps a
    never-synced platform honest: it may hold PARTIAL rows (cross-platform
    collection siblings persist per ADR-0021) whose groups say nothing about
    the ROMs never fetched. Without that term the estimate would read short
    by the entire unfetched remainder, and reading short is the one direction
    this estimate may not err in. A local mirror that is ahead of the server
    (retained rows for dropped rom_ids, ADR-0007) clamps the term at zero.

    Estimate-only (ADR-0023): the result prices the ``sync_plan`` payload's
    create term and must never feed the actual skip decision.
    """
    bound_by_key: dict[str, bool] = {}
    unbound_singletons = 0
    row_count = 0
    for key, is_bound in rows:
        row_count += 1
        if key is None:
            if not is_bound:
                unbound_singletons += 1
        else:
            bound_by_key[key] = bound_by_key.get(key, False) or is_bound
    unmirrored = max(0, unit_rom_count - row_count)
    return sum(1 for bound in bound_by_key.values() if not bound) + unbound_singletons + unmirrored
