-- =============================================================================
-- 033_add_artwork_slot_choices.sql — the hero and the cover as recorded slots, and a user's artwork choices
-- #2381 (choose each slot's image from SteamGridDB)
-- =============================================================================
--
--   * artwork_slot_outcomes — the slot CHECK is widened with `hero` and
--     `cover`, so all five slots of a shortcut's artwork can carry a record.
--     SQLite cannot ALTER a CHECK constraint, so the table is rebuilt in place
--     as 028 rebuilt it: create the widened twin, copy every row unchanged,
--     drop the old table, rename. It has no index beyond its primary key and no
--     incoming foreign key.
--   * artwork_slot_choices — per ROM and slot, the SteamGridDB image id the
--     user chose, and when. No row means the slot is automatic. Kept apart
--     from the outcomes, which Refresh Artwork, the artwork job and a version
--     switch rewrite or delete.
--   * roms.chosen_sgdb_id — the SteamGridDB game the user chose for a ROM
--     RomM gives no SteamGridDB id. The sync never writes it; it writes only
--     RomM's id, to `sgdb_id`, which wins where both exist.
--
-- How the hero's record and chosen_sgdb_id are read is "Game page artwork" in
-- docs/architecture/steam-non-steam-shortcuts.md; nothing reads a choice yet.
-- Both new homes go with their
-- `roms` row. `chosen_at` is REAL Unix-epoch seconds, as `recorded_at` is.
-- Created empty and NULL: nothing was chosen before this migration.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 33.
-- -----------------------------------------------------------------------------
CREATE TABLE artwork_slot_outcomes_new (
    rom_id       INTEGER NOT NULL REFERENCES roms(rom_id) ON DELETE CASCADE,
    slot         TEXT NOT NULL CHECK (slot IN ('grid', 'logo', 'icon', 'hero', 'cover')),
    outcome      TEXT NOT NULL CHECK (outcome IN ('set', 'none', 'refused')),
    sgdb_id      INTEGER,                            -- the ROM's SteamGridDB id then; NULL for none known
    recorded_at  REAL NOT NULL,                      -- Unix-epoch seconds
    image_id     INTEGER,                            -- SteamGridDB's image id, `set` only
    image_sha256 TEXT,                               -- SHA-256 of the bytes set, `set` only
    PRIMARY KEY (rom_id, slot),
    CHECK ((outcome = 'set') = (image_id IS NOT NULL AND image_sha256 IS NOT NULL)),
    CHECK (outcome = 'set' OR (image_id IS NULL AND image_sha256 IS NULL))
) STRICT;

INSERT INTO artwork_slot_outcomes_new (rom_id, slot, outcome, sgdb_id, recorded_at, image_id, image_sha256)
SELECT rom_id, slot, outcome, sgdb_id, recorded_at, image_id, image_sha256
FROM artwork_slot_outcomes;

DROP TABLE artwork_slot_outcomes;

ALTER TABLE artwork_slot_outcomes_new RENAME TO artwork_slot_outcomes;

CREATE TABLE artwork_slot_choices (
    rom_id     INTEGER NOT NULL REFERENCES roms(rom_id) ON DELETE CASCADE,
    slot       TEXT NOT NULL CHECK (slot IN ('grid', 'logo', 'icon', 'hero', 'cover')),
    image_id   INTEGER NOT NULL,                     -- the SteamGridDB image id the user chose
    chosen_at  REAL NOT NULL,                        -- Unix-epoch seconds
    PRIMARY KEY (rom_id, slot)
) STRICT;

ALTER TABLE roms ADD COLUMN chosen_sgdb_id INTEGER;
