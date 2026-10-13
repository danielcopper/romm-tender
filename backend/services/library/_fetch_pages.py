"""The per-page steps every paginated unit fetch takes: the cancel check and the ``fetching`` frame.

Shared by :class:`~services.library.fetcher.LibraryFetcher`'s platform fetch and
:class:`~services.library.collection_fetcher.CollectionFetcher`'s collection walk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.sync_stage import SyncStage
from domain.sync_state import SyncCancelled, SyncState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from services.library._state import LibrarySyncStateBox

    # Orchestrator-supplied progress emitter. Matches the kw-only signature
    # of ``SyncOrchestrator.emit_progress``: stage positional, every other
    # field keyword. Sub-services consume this through the Config seam.
    EmitProgressFn = Callable[..., Awaitable[None]]

_SYNC_CANCELLED = "Sync cancelled"

# Emit a ``fetching`` progress frame on the first page and every Nth page of a
# paginated unit fetch. At the 500-ROM page size a large platform paginates in
# only a handful of pages (a ~3000-ROM platform is 7), so every page is narrated
# (interval 1) — a "page 3/7" update every few seconds — rather than throttled.
# The interval knob stays so a future larger page count can throttle again.
FETCH_PROGRESS_PAGE_INTERVAL = 1


def raise_if_cancelling(box: LibrarySyncStateBox) -> None:
    """Raise :class:`SyncCancelled` if the sync is being cancelled."""
    if box.sync_state == SyncState.CANCELLING:
        raise SyncCancelled(_SYNC_CANCELLED)


async def emit_fetch_page_progress(
    emit_progress: EmitProgressFn,
    *,
    unit_name: str,
    page: int,
    total_pages: int,
    progress_step: int,
    progress_total_steps: int,
) -> None:
    """Emit a throttled ``fetching`` progress frame for a paginated fetch.

    Called once per page; emits only on the first page and every
    ``FETCH_PROGRESS_PAGE_INTERVAL``-th page so the frame rate stays
    bounded on a large multi-page fetch. ``progress_step`` /
    ``progress_total_steps`` are the run's coarse unit index / total so the
    main bar holds its position while the fine line advances by page; a
    falsy pair (the preview loop already emits its own per-unit frame,
    standalone callers) leaves the coarse bar indeterminate, as before.
    The frame carries the ``fetch`` sub-stage so the frontend fills the
    unit's fetch sub-slice of the bar (#1407). The displayed total is
    clamped to at least ``page`` so a server that grew since the listing
    never shows ``page 63/62``.
    """
    if page != 1 and page % FETCH_PROGRESS_PAGE_INTERVAL != 0:
        return
    shown_total = max(total_pages, page)
    await emit_progress(
        SyncStage.FETCHING,
        current=page,
        total=shown_total,
        message=f"Fetching {unit_name} (page {page}/{shown_total})",
        step=progress_step,
        total_steps=progress_total_steps,
        sub_stage="fetch",
    )
