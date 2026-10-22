-- =============================================================================
-- 035_add_artwork_slot_handovers.sql — the image Tender last handed to Steam per slot
-- #2381 (D15: two references tell Tender's image from one set in Steam)
-- =============================================================================
--
--   * artwork_slot_handovers — per ROM and slot, the SteamGridDB image id and
--     the SHA-256 of the bytes Tender last handed to Steam, and when, kept
--     until Steam's answer is reported. A grid file holding those bytes is
--     Tender's beside one holding the bytes the slot's `set` outcome names, so
--     an apply that was cancelled or overtaken, or whose report was lost, does
--     not make Tender's own image read as set in Steam. `previous_choice_id`
--     is the image the user had chosen for the slot when it was handed over,
--     NULL for none, so a chosen image Steam refuses gives that choice back. Its own table rather
--     than columns on artwork_slot_outcomes: an outcome row is built whole per
--     outcome and its `outcome` is NOT NULL, while a hand-over stands beside
--     any outcome, and beside none on a slot never recorded. Only the slots
--     told apart by their file's hash: the icon is judged by its path.
--
-- Goes with its `roms` row. `handed_at` is REAL Unix-epoch seconds, as
-- `recorded_at` is. Created empty: nothing was handed over before this
-- migration, and a slot without a row is judged as before.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 35.
-- -----------------------------------------------------------------------------
CREATE TABLE artwork_slot_handovers (
    rom_id       INTEGER NOT NULL REFERENCES roms(rom_id) ON DELETE CASCADE,
    slot         TEXT NOT NULL CHECK (slot IN ('grid', 'logo', 'hero', 'cover')),
    image_id     INTEGER NOT NULL,                   -- SteamGridDB's image id
    image_sha256 TEXT NOT NULL,                      -- SHA-256 of the bytes handed over
    handed_at    REAL NOT NULL,                      -- Unix-epoch seconds
    previous_choice_id INTEGER,                      -- the chosen image then; NULL for none
    PRIMARY KEY (rom_id, slot)
) STRICT;
