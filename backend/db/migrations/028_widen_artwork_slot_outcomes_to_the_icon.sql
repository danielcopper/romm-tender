-- =============================================================================
-- 028_widen_artwork_slot_outcomes_to_the_icon.sql — record the shortcut icon's outcome too
-- #2374 (the shortcut icon as a recorded slot)
-- =============================================================================
--
-- Widens artwork_slot_outcomes' slot CHECK with `icon`: the shortcut icon's
-- outcome is recorded beside the wide grid's and the logo's, by the same rule.
-- How a row is read is "Shortcut artwork" in
-- docs/architecture/steam-non-steam-shortcuts.md.
--
-- SQLite cannot ALTER a CHECK constraint, so the table is rebuilt in place:
-- create the widened twin, copy every row unchanged, drop the old table, rename.
-- The table has no index beyond its primary key and no incoming foreign key, so
-- the rename is the whole story. Nothing is recorded for an icon until a run
-- after this migration asks for it.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 28.
-- -----------------------------------------------------------------------------
CREATE TABLE artwork_slot_outcomes_new (
    rom_id       INTEGER NOT NULL REFERENCES roms(rom_id) ON DELETE CASCADE,
    slot         TEXT NOT NULL CHECK (slot IN ('grid', 'logo', 'icon')),
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
