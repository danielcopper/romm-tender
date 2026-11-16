# Invariant register

The cross-cutting safety rules — the ones that span files, so no diff-scoped review (human or agent) sees the whole rule
— each with its enforcement tier. It is a **map of the enforcement surface, not the enforcement itself**: the checks
that hold a cross-cutting rule are listed with the rule, and nowhere else.

A `check` or `test` rule is enforced by the artifact its entry names; a `prompt-only` rule is held by review alone until
a check exists, and moves to the `check` tier once one does. A rule is never weakened to stay green, and a real drift is
a finding to triage, never an exemption.

**Before implementing or reviewing in an area, read that area's file** — every file whose area a change touches. When a
change applies a guard, sanitize, backup or grouping pattern, sweep for sibling sites of the same pattern; the area
files are what that sweep checks against.

Two rules stand in the register section of the repository's `CLAUDE.md` instead of here: backup-or-confirm, and
server-supplied paths through `safe_join`. A breach of either loses user data for good, any area can break them, and no
check holds them fully; their long form is in [Cross-cutting](cross-cutting.md#backup-or-confirm). Every other entry
stands once, in the file of the area it belongs to; an entry that concerns two areas stands in one of them, and the
other points to it.

## How an entry reads

Format: **headline** — tier — enforced by.

An entry binds as a whole. The bold headline names the rule. Where the rule is longer than its headline, the paragraph
directly under the headline holds the rest of it and binds the same way. What follows — what enforces the rule, what
that does not see, why the rule exists, the tests that pin it — stays what it is.

What holds the register to its size, every headline to one place and every area file to a line in the map below is its
own entry in [Cross-cutting](cross-cutting.md); the limits are in the
[development guide](../../contributing/development.md).

## The areas

Each area, the paths it covers, and its file:

- [Endpoints and the wire](endpoints.md) — `backend/main.py`, `frontend/src/types/reasons.ts`,
  `frontend/src/api/hostSocket.ts`
- [RomM HTTP](romm-http.md) — `backend/adapters/romm/`, `backend/lib/errors.py`
- [Install and update](install-update.md) — `install.sh`, `backend/check.py`, `backend/*/update_*.py`,
  `scripts/package.sh`
- [The resolver](resolver.md) — `backend/adapters/atlas_*.py`, `emulator_sources.py`, `retrodeck_folders.py`,
  `backend/services/downloads/`, `backend/services/rom_removal.py`
- Library sync — `backend/services/library/`, `backend/domain/sync_diff.py`, `platform_skip.py`, `collection_skip.py`:
  the run, [library-sync.md](library-sync.md); skips and stamps, [library-sync-skips.md](library-sync-skips.md);
  representatives and removals, [library-sync-removals.md](library-sync-removals.md)
- Firmware — `backend/services/firmware/`, `backend/domain/bios_status.py`, `firmware_*.py`,
  `services/tender_folder.py`, `frontend/src/utils/bios*.ts`: answers and verdicts, [firmware.md](firmware.md); files,
  links and Tender's folder, [firmware-files.md](firmware-files.md)
- [Save-file sync](saves.md) — `backend/services/saves/`, `backend/domain/save_*.py`, `adapters/atlas_saves.py`,
  `adapters/gavel_native.py`
- [Removed-game cleanup](prune.md) — `backend/services/prune/`, `frontend/src/utils/prune*.ts`
- [The host and loading the panel](host.md) — `backend/host/`, `*/controller_focus.py`,
  `frontend/src/utils/runningApps.ts`
- [Bundles and the start-up check](startup.md) — `frontend/src/boot/`, `frontend/scripts/`, `frontend/rollup.config.js`,
  `backend/domain/steam_internals.py`
- [The panel: Quick Access and the game page](panel.md) — `frontend/src/qam/`, `frontend/src/bigpicture/`
- [The desktop surface](desktop.md) — `frontend/src/desktop/`
- [Code structure](code-structure.md) — layering, aggregates, Units of Work, tasks, clocks, module size
- [Cross-cutting](cross-cutting.md) — settings, directories, identifiers, naming, coverage, lock files, links, vendored
  trees, the register
