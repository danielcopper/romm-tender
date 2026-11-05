-- =============================================================================
-- 042_add_rom_install_fetched_size.sql — an install records what it fetched
-- #2732 (D1), #2223 (D27)
-- =============================================================================
--
--   * rom_installs.fetched_size_bytes — added, nullable: the sum of RomM's
--     sizes of the files the install fetched. A download fetches a game's own
--     files and leaves its manuals, soundtracks and other extras on the
--     server, so this differs from roms.fs_size_bytes, RomM's size of the
--     whole game, which the sync keeps writing. The data overview sums it.
--
-- Every install from before this migration fetched every file of its game, so
-- it takes roms.fs_size_bytes. NULL = no size recorded.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 42.
-- -----------------------------------------------------------------------------
ALTER TABLE rom_installs ADD COLUMN fetched_size_bytes INTEGER;
UPDATE rom_installs
SET fetched_size_bytes = (SELECT roms.fs_size_bytes FROM roms WHERE roms.rom_id = rom_installs.rom_id);
