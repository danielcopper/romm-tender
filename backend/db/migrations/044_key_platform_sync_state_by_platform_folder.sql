-- =============================================================================
-- 044_key_platform_sync_state_by_platform_folder.sql — a platform's sync stamp
-- is kept per RomM platform folder
-- #2796, #2452 (D1, D4), #2223 (D49)
-- =============================================================================
--
--   * platform_sync_state — rebuilt keyed by `platform_folder`, the folder RomM
--     keeps the platform's games in (`fs_slug`), in place of `platform_slug`.
--     Two platforms may share a slug, and their stamps overwrote each other
--     under it. Every row is dropped: a stamp written under a slug cannot say
--     which of its folders it was written for, so the first sync after the
--     upgrade reads every platform in full and stamps it again. A database
--     coming from before 043 loses nothing more: 043 has already revoked every
--     stamp's skip.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 44.
-- -----------------------------------------------------------------------------
DROP TABLE platform_sync_state;

CREATE TABLE platform_sync_state (
    platform_folder  TEXT    NOT NULL PRIMARY KEY,  -- the RomM platform's folder (fs_slug)
    completed_at     TEXT    NOT NULL,              -- ISO-8601; the platform's effective last_sync
    rom_count        INTEGER NOT NULL,              -- server ROM count captured at completion
    fetch_id         TEXT,                          -- generation this stamp was written for; NULL = unknown
    skip_revoked     INTEGER NOT NULL DEFAULT 0,    -- 1 = the skip reads no stamp
    fetch_started_at TEXT,                          -- when the completing fetch started, Tender's clock (ISO)
    rom_id_checksum  TEXT                           -- sha256 hex of the sorted ROM ids that fetch returned
) STRICT;
