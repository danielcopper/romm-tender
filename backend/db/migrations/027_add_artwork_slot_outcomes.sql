-- =============================================================================
-- 027_add_artwork_slot_outcomes.sql — record what was last found for a wide grid or logo
-- #2295 (the wide grid and the logo for every shortcut)
-- =============================================================================
--
--   * artwork_slot_outcomes — per ROM and slot (the wide grid, the logo), what
--     Tender last found: `set` (an image was set), `none` (SteamGridDB has no
--     candidate) or `refused` (Steam refused the image), the ROM's SteamGridDB
--     id at that time, and when. A `set` row also holds SteamGridDB's image id
--     and the SHA-256 of the bytes set. A `none` or `refused` row keeps the slot
--     from being asked again for a week, unless the ROM's SteamGridDB id changes.
--
-- Keyed by ROM, not app id: a re-created shortcut gets a new app id. The rows go
-- with their `roms` row. `recorded_at` is Unix-epoch seconds, as a timestamp that
-- is only ever aged is.
--
-- Created empty: nothing was recorded before this migration.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 27.
-- -----------------------------------------------------------------------------
CREATE TABLE artwork_slot_outcomes (
    rom_id       INTEGER NOT NULL REFERENCES roms(rom_id) ON DELETE CASCADE,
    slot         TEXT NOT NULL CHECK (slot IN ('grid', 'logo')),
    outcome      TEXT NOT NULL CHECK (outcome IN ('set', 'none', 'refused')),
    sgdb_id      INTEGER,                            -- the ROM's SteamGridDB id then; NULL for none known
    recorded_at  REAL NOT NULL,                      -- Unix-epoch seconds
    image_id     INTEGER,                            -- SteamGridDB's image id, `set` only
    image_sha256 TEXT,                               -- SHA-256 of the bytes set, `set` only
    PRIMARY KEY (rom_id, slot),
    CHECK ((outcome = 'set') = (image_id IS NOT NULL AND image_sha256 IS NOT NULL)),
    CHECK (outcome = 'set' OR (image_id IS NULL AND image_sha256 IS NULL))
) STRICT;
