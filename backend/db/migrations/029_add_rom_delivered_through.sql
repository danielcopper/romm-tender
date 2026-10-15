-- =============================================================================
-- 029_add_rom_delivered_through.sql — which kind of sync unit last committed a ROM
-- #2407 (a version gone from RomM stays the shortcut's version)
-- =============================================================================
--
--   * roms.delivered_through — 'platform' or 'collection': the kind of unit
--     whose chunk commit last upserted the row. How the end-of-run stale
--     removal reads it, NULL included, is domain/sync_diff.py::sort_stale_rows
--     and docs/architecture/backend-architecture.md, "The stale removal keeps a
--     game gone from RomM".
--
-- Nothing is fetched again to fill it in: the next commit of each row writes
-- it. Adding a nullable column changes no existing row, so the runner's
-- user_version ladder is the only guard this step needs.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 29.
-- -----------------------------------------------------------------------------
ALTER TABLE roms ADD COLUMN delivered_through TEXT
    CHECK (delivered_through IS NULL OR delivered_through IN ('platform', 'collection'));
