/**
 * RetroDECK file migration types — the pending-migration status and the result
 * shape returned after running a migration. Anything that describes a
 * migration handshake between Tender and the user lives here.
 */

export interface MigrationStatus {
  pending: boolean;
  old_path?: string;
  new_path?: string;
  roms_count?: number;
  bios_count?: number;
  saves_count?: number;
}

interface ConflictDetail {
  filename: string;
  old_path: string;
  old_size: number;
  old_mtime: string;
  new_path: string;
  new_size: number;
  new_mtime: string;
}

/** A run that made every move it found. */
interface MigrationDone {
  success: true;
  message: string;
  needs_confirmation?: undefined;
  roms_moved?: number;
  bios_moved?: number;
  saves_moved?: number;
  missing_count?: number;
  errors?: string[];
}

/** Nothing was moved: some destinations are taken, and the user picks overwrite or skip. */
interface MigrationNeedsConfirmation {
  success: false;
  reason: "needs_confirmation";
  message: string;
  needs_confirmation: true;
  conflict_count?: number;
  conflicts?: string[] | ConflictDetail[];
}

/** Some moves failed and the others were made; the move stays pending. */
interface MigrationIncomplete {
  success: false;
  reason: "migration_incomplete";
  message: string;
  needs_confirmation?: undefined;
  roms_moved: number;
  bios_moved: number;
  saves_moved: number;
  missing_count: number;
  errors: string[];
}

/** Any other refusal, in the shape every endpoint fails in. */
interface MigrationRefused {
  success: false;
  reason: string;
  message: string;
  needs_confirmation?: undefined;
}

export type MigrationResult = MigrationDone | MigrationNeedsConfirmation | MigrationIncomplete | MigrationRefused;
