-- =============================================================================
-- 041_replace_applied_launch_options_with_shortcut_applied.sql — a row records
-- whether its shortcut was applied, not the command written into it
-- #2221 (D7, D17)
-- =============================================================================
--
--   * roms.shortcut_applied — added, 0 or 1, and 1 on every row that had a
--     command recorded, whose shortcut a sync has applied before. The command
--     such a shortcut still carries is set to its appId by the reconcile at
--     panel load, not by the sync. The sync's commit sets it for every
--     shortcut it applied, and Force Full Sync clears it on every row, so the
--     next sync applies every shortcut anew.
--   * roms.applied_launch_options — dropped. Every bound shortcut carries its
--     appId and a start asks the resolver for its command, so there is no
--     command left to record.
--
-- Transaction-safe DDL only — the runner (adapters/sqlite_migrations.py) wraps
-- BEGIN/COMMIT and stamps PRAGMA user_version = 41.
-- -----------------------------------------------------------------------------
ALTER TABLE roms ADD COLUMN shortcut_applied INTEGER NOT NULL DEFAULT 0;
UPDATE roms SET shortcut_applied = 1 WHERE applied_launch_options IS NOT NULL;
ALTER TABLE roms DROP COLUMN applied_launch_options;
