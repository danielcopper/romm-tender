"""PlatformSyncState — the per-platform "this platform fully synced" completion stamp.

Recorded when a platform work unit finishes its **last** apply chunk (ADR-0023),
so the incremental-skip gate can honor durable per-platform progress that a
cancelled / crashed run leaves behind. A completed ``SyncRun`` advances the
library-wide ``last_sync`` only when the *whole* run finishes; a run that
committed every chunk of platforms A, B, C but was cancelled during platform D
never completes, so ``last_sync`` stays put and the next sync re-walks A, B, C
from zero. This stamp is the per-platform checkpoint that survives that
cancellation: the skip compares RomM's answer with the completing fetch's
``fetch_started_at`` and ``rom_id_checksum`` (``domain/platform_skip.py``).

Keyed by ``platform_slug``. A thin record built whole and upserted — never a
partial field mutation — so it carries a single ``stamp`` constructor and no
verb-named mutators. The contract is *a stamp may authorise a skip ⟺ the
platform's most recent apply attempt ran to completion, and neither a local
removal nor a stale removal on a run that did not process the platform has
unbound one of its rows since*: it is deleted at a platform unit's apply start
(``sync_orchestrator``) so an interrupted re-apply leaves none and the final
chunk re-writes it, and cleared wholesale by Force Full Sync (the repository's
``clear``) — the stamps are the fetcher's sole skip authority, so clearing them
arms the full re-fetch; the ``SyncRun`` history is preserved (it feeds no skip
gate). Those two removals revoke the skip instead: ``skip_revoked`` is never
set by the aggregate — the repository's ``revoke_skip`` sets it — and a fresh
``stamp`` never carries it. Why the stamp is kept rather than deleted, and which
readers honour the flag, is in docs/architecture/backend-architecture.md,
"Incremental skip".
"""

from __future__ import annotations

from domain._aggregate import cosmic_aggregate


@cosmic_aggregate
class PlatformSyncState:
    """One platform's last fully-completed sync, keyed by ``platform_slug``."""

    platform_slug: str
    completed_at: str
    rom_count: int
    fetch_id: str | None = None
    skip_revoked: bool = False
    fetch_started_at: str | None = None
    rom_id_checksum: str | None = None

    @classmethod
    def stamp(
        cls,
        *,
        platform_slug: str,
        at: str,
        rom_count: int,
        fetch_id: str | None = None,
        fetch_started_at: str | None = None,
        rom_id_checksum: str | None = None,
    ) -> PlatformSyncState:
        """Record that ``platform_slug`` fully synced at ISO timestamp ``at``.

        ``rom_count`` is the server's platform ROM count the completing run
        planned with. The skip does not read it; removed-game discovery reads
        a zero as an empty completed fetch (``domain/fetch_generation.py``).

        ``fetch_started_at`` (ISO, Tender's clock) and ``rom_id_checksum``
        (``domain/platform_skip.rom_id_checksum`` over the ids that fetch
        returned) are what the skip compares RomM's answer with; a stamp
        without both never skips.

        ``fetch_id`` is the generation marker every row of that completing fetch
        carries (``Rom.record_fetch_generation``), so the skip's backfill gate
        and removed-game discovery read exactly the rows the fetch returned
        rather than every row of the platform — a row for a rom_id the server
        dropped keeps an older generation, while staying on disk (ADR-0007,
        #1504). ``None`` is "unknown" (a stamp written before migration 020): it
        cannot say what its fetch returned, so discovery finds no candidates
        under it, and it predates ``rom_id_checksum`` too, so it never skips.
        """
        if not platform_slug:
            raise ValueError("platform_slug is required")
        if rom_count < 0:
            raise ValueError("rom_count must be non-negative")
        return cls(
            platform_slug=platform_slug,
            completed_at=at,
            rom_count=rom_count,
            fetch_id=fetch_id,
            fetch_started_at=fetch_started_at,
            rom_id_checksum=rom_id_checksum,
        )


def stamp_for_skip(stamp: PlatformSyncState | None) -> PlatformSyncState | None:
    """Return the stamp a skip-side reader may trust, or ``None`` when there is none.

    A revoked stamp is treated as absent. The skip gate, the plan estimate and the
    preview's re-stamp count read the stamp through here; the resume offer asks the
    repository's ``has_any``, whose query applies the same rule, so that is the one
    other place it lives. Readers that want the stamp's fetch generation rather than
    its skip authority — removed-game discovery, the reachable count — read it raw,
    because a revoked stamp still records what RomM's last complete fetch returned.
    """
    if stamp is None or stamp.skip_revoked:
        return None
    return stamp
