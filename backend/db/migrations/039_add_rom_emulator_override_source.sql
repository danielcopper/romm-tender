-- =============================================================================
-- 039_add_rom_emulator_override_source.sql — the emulator source of a per-game
-- emulator pick
-- #2195 (D20)
-- =============================================================================
--
--   * roms.emulator_override_source — the kind of the emulator source whose
--     catalogue the pinned label was chosen from ('retrodeck', 'emudeck'), so a
--     label is read against its own source and one that no longer exists there
--     can be named with its source. NULL exactly where there is no pick.
--
-- Every pick stored before this step becomes RetroDECK's: RetroDECK was the
-- only source Tender started through (#2195 D20, #2222 D7).
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 39.
-- -----------------------------------------------------------------------------
ALTER TABLE roms ADD COLUMN emulator_override_source TEXT;
UPDATE roms SET emulator_override_source = 'retrodeck' WHERE emulator_override IS NOT NULL;
