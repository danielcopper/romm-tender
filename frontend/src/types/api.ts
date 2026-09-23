/**
 * Connection, settings, and ROM-lookup types — the backend surface that
 * isn't specific to one feature vertical. Things that talk to RomM at the
 * connection/auth/metadata layer live here; per-domain shapes
 * (sync, saves, firmware, downloads, achievements) live in their own files.
 */

/**
 * Canonical failure-`reason` slugs the backend emits on the `{success: false,
 * reason, message}` shape (see backend/lib/list_result.py `ErrorCode` + the
 * gate scripts/check_failure_shape.py). The Lean enum plus the bespoke
 * plain-string reasons the frontend actually routes on. Transport failures
 * collapse onto `server_unreachable`; 401/403 onto `auth_failed` (distinguished
 * by `message`, not slug).
 */
export type RommErrorCode =
  | "server_unreachable"
  | "auth_failed"
  | "not_found"
  | "unsupported"
  | "unknown"
  | "version_error"
  | "stale_conflict"
  | "stale_preview"
  | "config_error"
  | "in_progress";

/**
 * Payload of the `server_retry_progress` event (#1345). Emitted once per retry
 * by the backend HTTP adapter's backoff ladder so the frontend can surface a
 * live "connecting… (attempt N/M)" indicator while a server-touching call is
 * in flight.
 */
export interface ServerRetryProgressEvent {
  /** 1-based number of the retry currently being attempted. */
  attempt: number;
  /** Total attempts the ladder makes before giving up. */
  max_attempts: number;
  /** Backoff delay (seconds) before this retry fires. */
  delay_s: number;
}

export interface InstalledRom {
  rom_id: number;
  file_name: string;
  file_path: string;
  system: string;
  platform_slug: string;
  installed_at: string;
  /**
   * False when the ROM is downloaded and on disk but the system cannot launch
   * what it contains — a PS3 `.pkg` installer, a bare disc track. Its shortcut
   * carries no launch command; the files are kept so the user can install them
   * by hand in the emulator.
   */
  launchable: boolean;
}

export interface RetroArchInputCheck {
  warning: boolean;
  current?: string;
  config_path?: string;
}

export interface PluginSettings {
  romm_url: string;
  has_token: boolean;
  steam_input_mode: "default" | "force_on" | "force_off";
  sgdb_api_key_masked: string;
  log_level: "debug" | "info" | "warn" | "error";
  romm_allow_insecure_ssl: boolean;
  retroarch_input_check?: RetroArchInputCheck;
  collection_create_platform_groups?: boolean;
  // QAM collection owner-scope (#1532): "all" (default) or "own" (only the
  // signed-in user's own collections). Optional: older payloads may omit it,
  // treated as "all". Mirrors the CollectionOwnerScope type in sync.ts.
  collection_owner_scope?: "own" | "all";
  // Steam-collection naming mode (#1539): "merge" (default) unions same-named
  // collections into one; "by_label" appends the fine type label so they stay
  // separate. Optional: older payloads may omit it, treated as "merge". Mirrors
  // the CollectionNamingMode type in sync.ts.
  collection_naming_mode?: "merge" | "by_label";
  // Preferred sibling-group region (ADR-0021 §3). "auto" = the fixed build-time
  // default order (World > USA > Europe > Japan); any other value heads the
  // ranking with that region on the next sync. Optional: the backend always
  // sends it, but older payloads / test fixtures may omit it.
  preferred_region?: string;
  // Sync-button intent: start the run without asking for a preview first.
  // Optional: the backend always sends it, but older payloads / test fixtures
  // may omit it, treated as false.
  skip_preview?: boolean;
  // Names of the extra headers sent to the RomM origin (#1822), in the order
  // they were entered. NAMES only — a stored value is a proxy credential and is
  // never sent to the frontend, so it can be replaced but never read back.
  // Optional: the backend always sends it, but older payloads / test fixtures
  // may omit it, treated as none configured.
  romm_custom_header_names?: string[];
}

/**
 * One row of the custom proxy-header list on its way to the backend (#1822).
 * The union is the wire contract: a `value` accompanies `"set"` and only
 * `"set"`, and `"keep"` means "reuse the value already stored under this name"
 * — the row the user did not touch, whose value the frontend never received.
 */
export type CustomHeaderEntry =
  { name: string; value_action: "set"; value: string } | { name: string; value_action: "keep" };

export interface RomMetadata {
  summary: string;
  genres: string[];
  companies: string[];
  first_release_date: number | null;
  average_rating: number | null;
  game_modes: string[];
  player_count: string;
  cached_at: number;
  steam_categories?: number[];
}

export interface LaunchVerdict {
  action: "allow" | "warn" | "block";
  reason: "not_installed" | "save_conflict" | "save_status_failed" | null;
  toast_title: string | null;
  toast_body: string | null;
}

/**
 * What this device holds, for the Data Management page's inventory rows.
 *
 * `installed_roms` counts INSTALLS — one per install, so a multi-disc game
 * counts once and two installed versions of one game count twice. A surface
 * rendering it says neither files nor games, both of which it would
 * miscount: the number's own unit is the install.
 *
 * `installed_bytes` is the size RomM reported for those games
 * (`Rom.fs_size_bytes`), summed — never a walk of the disk — so it is written
 * with a `≈`: an unpacked archive, a patch beside the original or extras in the
 * same folder are not the size the server named, and a game whose size the
 * server never reported adds nothing to it.
 *
 * `recovery_bytes` is measured on disk, because nothing else knows what a
 * sealed bundle takes.
 */
export interface DataInventory {
  installed_roms: number;
  installed_bytes: number;
  recovery_bundles: number;
  recovery_bytes: number;
  /** Where the counted bundles live — derived from the package name, so never spelled here. */
  recovery_root: string;
  /** Every bundle `recovery_bundles` counts, one entry each, in no particular order. */
  recovery_bundle_list: RecoveryBundleEntry[];
}

/**
 * One sealed bundle, as its folder name spells it.
 *
 * `name` and `day` are read off the folder name by the backend's
 * `parse_recovery_bundle_id` (`backend/domain/prune.py`), which states the
 * shape and which folders miss it; a folder that misses it answers its whole
 * name here with `day` `null`. `bytes` is `null` where the bundle could not be
 * measured, which is an unknown size rather than an empty bundle.
 */
export interface RecoveryBundleEntry {
  name: string;
  /** The day (UTC) it was sealed, as its folder name carries it: `YYYY-MM-DD`. */
  day: string | null;
  bytes: number | null;
}
