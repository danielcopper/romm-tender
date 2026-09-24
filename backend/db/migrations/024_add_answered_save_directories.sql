-- =============================================================================
-- 024_add_answered_save_directories.sql — record the save directory the resolver answered
-- #1660 (the save directory is the resolver's answer; the save-sort markers go)
-- =============================================================================
--
--   * answered_save_directories — per ROM, the directory the resolver last
--     answered for its save. Compared with today's answer to notice that the
--     directory moved, and read as the source of the move that follows; never
--     where a save is looked for.
--
-- A table of its own rather than a column on rom_save_sync_states: a row there
-- means "this ROM has been tracked for save sync", and readers take its absence
-- as "never tracked". This record is written for games that were never synced.
--
-- Created empty. No value can be derived here: the answer is a live reading of
-- the machine, so the plugin records it after this migration.
--
-- The save-sort markers are deleted with the code that read them:
-- ``save_sort_settings`` held the last-seen RetroArch sort flags and
-- ``save_sort_settings_previous`` the flags before a change nobody had migrated
-- yet. A migration pending at this update cannot be followed: nothing records
-- the directory its files are still in.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 24.
-- -----------------------------------------------------------------------------
CREATE TABLE answered_save_directories (
    rom_id    INTEGER PRIMARY KEY REFERENCES roms(rom_id) ON DELETE CASCADE,
    directory TEXT NOT NULL                          -- last answered save directory
) STRICT;

DELETE FROM kv_config WHERE key IN ('save_sort_settings', 'save_sort_settings_previous');
