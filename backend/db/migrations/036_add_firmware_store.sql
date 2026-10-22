-- =============================================================================
-- 036_add_firmware_store.sql — Tender's firmware store and the links into it
-- #2191 (D2, D3, D24)
-- =============================================================================
--
--   * firmware_cache — gains md5_hash, the checksum RomM states for a file, so
--     a listing restored while the server cannot be read still matches the
--     library's files to emulator requirements by content. Every row is
--     dropped: a row of 034's shape has no checksum, and the first read from
--     the server fills the table again.
--   * firmware_store — StoredFirmware: one file Tender downloaded into its
--     firmware store, keyed by the platform it was downloaded for and its name
--     in RomM. `folder` is Tender's folder it lies under (the folder service
--     asks this table what is recorded under a folder); `md5` is the checksum
--     the file was checked against, never empty.
--   * firmware_links — FirmwareLink: one link Tender placed, keyed by its
--     absolute place. `target` is the store path it points at, kept on the
--     record rather than referenced, because a link Tender could not remove
--     with its store file keeps its record until the link goes.
--
-- Both are created empty: no file was downloaded into a store before this
-- migration. downloaded_bios keeps the downloads made into an emulator
-- source's folder before it.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 36.
-- -----------------------------------------------------------------------------
DELETE FROM firmware_cache;

ALTER TABLE firmware_cache ADD COLUMN md5_hash TEXT NOT NULL DEFAULT '';

CREATE TABLE firmware_store (
    platform_slug TEXT    NOT NULL,                 -- the RomM platform the download was made for
    file_name     TEXT    NOT NULL,                 -- the file's name in RomM
    folder        TEXT    NOT NULL,                 -- Tender's folder the file lies under
    md5           TEXT    NOT NULL CHECK (md5 <> ''),
    size          INTEGER NOT NULL,
    downloaded_at TEXT    NOT NULL,                 -- ISO-8601
    firmware_id   INTEGER,                          -- RomM firmware id; metadata, not identity
    PRIMARY KEY (platform_slug, file_name)
) STRICT;

CREATE TABLE firmware_links (
    place         TEXT    NOT NULL PRIMARY KEY,     -- absolute path of the link
    target        TEXT    NOT NULL,                 -- absolute store path the link points at
    platform_slug TEXT    NOT NULL,                 -- the store file's platform
    file_name     TEXT    NOT NULL,                 -- the store file's name in RomM
    source        TEXT    NOT NULL,                 -- the emulator source the place belongs to
    placed_at     TEXT    NOT NULL                  -- ISO-8601
) STRICT;
