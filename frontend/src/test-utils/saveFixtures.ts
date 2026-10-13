import type { SyncConflict } from "../types";

/** A save conflict on ROM 42's `savefile.srm`; override what a test cares about. */
export function makeConflict(overrides: Partial<SyncConflict> = {}): SyncConflict {
  return {
    type: "sync_conflict",
    rom_id: 42,
    filename: "savefile.srm",
    server_save_id: 9,
    server_updated_at: "2026-05-01T10:00:00Z",
    server_size: 2048,
    local_path: "/saves/savefile.srm",
    local_hash: "abc",
    local_mtime: "2026-05-01T09:00:00Z",
    local_size: 1024,
    created_at: "2026-05-01T11:00:00Z",
    ...overrides,
  };
}
