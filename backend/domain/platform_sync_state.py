"""PlatformSyncState — the per-platform "this platform fully synced" completion stamp.

Recorded when a platform work unit finishes its **last** apply chunk (ADR-0023),
so the incremental-skip gate can honor durable per-platform progress that a
cancelled / crashed run leaves behind. A completed ``SyncRun`` advances the
library-wide ``last_sync`` only when the *whole* run finishes; a run that
committed every chunk of platforms A, B, C but was cancelled during platform D
never completes, so ``last_sync`` stays put and the next sync re-walks A, B, C
from zero. This stamp is the per-platform checkpoint that survives that
cancellation: the skip reads ``completed_at`` as the platform's own effective
``last_sync`` and ``rom_count`` as the server count the completing run planned
with (a later server-side count change invalidates the stamp).

Keyed by ``platform_slug``. A thin record built whole and upserted — never a
partial field mutation — so it carries a single ``stamp`` constructor and no
verb-named mutators. The contract is *a stamp may authorise a skip ⟺ the
platform's most recent apply attempt ran to completion and nothing has unbound
one of its rows since*: it is deleted at a platform unit's apply start
(``sync_orchestrator``) so an interrupted re-apply leaves none and the final
chunk re-writes it, and cleared wholesale by Force Full Sync (the repository's
``clear``) — the stamps are the fetcher's sole skip authority, so clearing them
arms the full re-fetch; the ``SyncRun`` history is preserved (it feeds no skip
gate). An unbind outside the platform's own apply revokes the skip instead of
deleting the stamp (the repository's ``revoke_skip``), because removed-game
discovery still reads the stamp's ``fetch_id``: ``skip_revoked`` is set by the
repository alone, and a fresh ``stamp`` never carries it. Which readers honour the
flag, and why, is in docs/architecture/backend-architecture.md, "Incremental
skip".
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

    @classmethod
    def stamp(cls, *, platform_slug: str, at: str, rom_count: int, fetch_id: str | None = None) -> PlatformSyncState:
        """Record that ``platform_slug`` fully synced at ISO timestamp ``at``.

        ``rom_count`` is the server's platform ROM count the completing run
        planned with — the skip re-checks it against the live count and
        invalidates the stamp on any change.

        ``fetch_id`` is the generation marker every row of that completing fetch
        carries (``Rom.record_fetch_generation``), so the skip can count exactly
        the rows the fetch returned rather than every row of the platform — a row
        for a rom_id the server dropped keeps an older generation and stops
        counting, while staying on disk (ADR-0007, #1504). ``None`` is "unknown"
        (a stamp written before this contract): it cannot say what its fetch
        returned, so the skip counts every row (the pre-#1504 behavior) until an
        apply commit re-stamps both sides — a preview-only run (empty
        library-wide delta) reaches no commit and leaves them unchanged.
        """
        if not platform_slug:
            raise ValueError("platform_slug is required")
        if rom_count < 0:
            raise ValueError("rom_count must be non-negative")
        return cls(platform_slug=platform_slug, completed_at=at, rom_count=rom_count, fetch_id=fetch_id)


def stamp_for_skip(stamp: PlatformSyncState | None) -> PlatformSyncState | None:
    """Return the stamp a skip-side reader may trust, or ``None`` when there is none.

    A revoked stamp is treated as absent: every reader that decides, predicts or
    offers a skip reads the stamp through here, so none of them can honour one the
    others refuse. Readers that want the stamp's fetch generation rather than its
    skip authority — removed-game discovery, the reachable count — read it raw.
    """
    if stamp is None or stamp.skip_revoked:
        return None
    return stamp
