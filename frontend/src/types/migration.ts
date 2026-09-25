/**
 * RetroDECK file migration types — the pending-migration status and the result
 * shape returned after running a migration. Anything that describes a
 * migration handshake between the plugin and the user lives here.
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

export interface MigrationResult {
  success: boolean;
  message: string;
  needs_confirmation?: boolean;
  conflict_count?: number;
  conflicts?: string[] | ConflictDetail[];
  roms_moved?: number;
  bios_moved?: number;
  saves_moved?: number;
  missing_count?: number;
  errors?: string[];
}
