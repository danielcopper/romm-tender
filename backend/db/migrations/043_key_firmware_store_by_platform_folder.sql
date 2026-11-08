-- =============================================================================
-- 043_key_firmware_store_by_platform_folder.sql — the firmware store is kept
-- per RomM platform folder, and a game knows its platform's folder
-- #2734, #2223 (D25, D45, D46, D47), #2219 (D11)
-- =============================================================================
--
--   * firmware_store.platform_slug → platform_folder: a store file is keyed by
--     the folder RomM keeps its platform's games in (`fs_slug`), which names one
--     platform, where two platforms may share a slug. Each value is kept: a
--     record filed under a slug stays under that name (D46), and nothing on
--     disk moves (#2219 D11).
--   * firmware_store.platform_slug — added, the slug of the platform the file
--     was downloaded for, by which Tender finds its system. Filled from the
--     old key, which was that slug.
--   * firmware_links.platform_slug → platform_folder: the store file's
--     platform folder, by the same rule.
--   * firmware_cache — rebuilt keyed (platform_folder, name), carrying the
--     slug beside it, so two platforms that share a slug keep a cached file
--     each under the same name. Every row is dropped; the next read from the
--     server fills the table again.
--   * roms.platform_fs_slug — added, nullable: the folder RomM keeps the
--     game's platform in, written by the sync from the ROM's own payload. NULL
--     until the next sync writes it.
--   * platform_sync_state.skip_revoked — set on every stamp, as 025 did (D47). A
--     skipped platform's rows are rebuilt from the database, which holds no
--     folder yet, so the first sync after the upgrade fetches each enabled
--     platform in full and writes it; that apply stamps the platform again.
--   * collection_sync_state — every stamp is dropped, as Force Full Sync's
--     clear does (D47). A collection's skip rebuilds the members its run did not
--     fetch through a platform from stored rows too, so a game only a
--     collection delivers would keep no folder; without a stamp each
--     collection is fetched in full once, and that apply stamps it again.
--
-- Transaction-safe DDL/DML only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 43.
-- -----------------------------------------------------------------------------
ALTER TABLE firmware_store RENAME COLUMN platform_slug TO platform_folder;
ALTER TABLE firmware_store ADD COLUMN platform_slug TEXT NOT NULL DEFAULT '';
-- Every row: platform_folder is NOT NULL (036).
UPDATE firmware_store SET platform_slug = platform_folder WHERE platform_folder IS NOT NULL;

ALTER TABLE firmware_links RENAME COLUMN platform_slug TO platform_folder;

DROP TABLE firmware_cache;

CREATE TABLE firmware_cache (
    platform_folder TEXT    NOT NULL,               -- the RomM platform's folder (fs_slug)
    platform_slug   TEXT    NOT NULL,               -- that platform's slug
    name            TEXT    NOT NULL,               -- firmware filename
    id              INTEGER,                        -- RomM firmware id; NULL for legacy entries
    file_size_bytes INTEGER NOT NULL,
    cached_at       REAL    NOT NULL,               -- Unix epoch (s); whole-cache TTL driver
    md5_hash        TEXT    NOT NULL DEFAULT '',    -- the checksum RomM states; '' where none
    PRIMARY KEY (platform_folder, name)
) STRICT;

ALTER TABLE roms ADD COLUMN platform_fs_slug TEXT;

UPDATE platform_sync_state SET skip_revoked = 1 WHERE skip_revoked = 0;

DELETE FROM collection_sync_state WHERE collection_id IS NOT NULL;  -- every row: collection_id is NOT NULL (019)
