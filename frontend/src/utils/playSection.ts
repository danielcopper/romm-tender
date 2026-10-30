/**
 * Pure helpers for the RomM play-section row: label resolution, BIOS-payload
 * shaping, and the timeout-promise primitive used by connection probing.
 *
 * Anything that takes inputs and returns outputs without touching component
 * state belongs here. Anything that talks to the backend belongs in
 * sectionRefresh.ts. Anything stateful belongs in the component itself.
 */

import type { BiosAnswer } from "../api/backend";
import type {
  AnsweringSource,
  CoreInfo,
  EmulatorDataReason,
  EmulatorOption,
  EmulatorStart,
  GameLaunch,
  PlatformSystemAnswer,
  SaveStatus,
  SaveSyncDisplay,
  VanishedPick,
} from "../types";
import { hasAnySaveConflict } from "./saveStatus";
import { formatTimeAgo } from "./formatters";

/** BIOS-only fields for the play-section row. Emulator data (the launching
 *  emulator, the platform's emulator list) is sourced independently via
 *  `extractCoreInfo` from the dedicated `get_platform_core_info` path — it no
 *  longer rides the BIOS payload (#923). */
export interface BiosInfoFields {
  biosNeeded: boolean;
  biosLabel: string;
  /** Whether this launch is missing firmware it cannot start without — the whole
   *  of the play-row badge's rule: the backend's level is `"missing"`, which is
   *  the resolver saying the launching emulator's requirements are not met.
   *  Nothing here counts rows: a verdict nothing established raises no warning, and a
   *  region-decided one (`"partial"`) raises none either — the badge does not
   *  say a game will not start on a guess about its disc.
   *
   *  The four-valued `bios_level` is read and not projected. The badge has one
   *  appearance, so it needs no colour input, and the BIOS tab reads the level
   *  off its own payload. */
  biosRequiredMissing: boolean;
}

/**
 * The page's check of whether the game can start — the core read that carries
 * the emulator's refusal and the file's launchability (#2195 D32): `checking`
 * while it runs, `answered` once it landed, `failed` where the read itself
 * failed or got no answer in time, `why` saying what went wrong.
 */
export type StartCheck = { state: "checking" } | { state: "answered" } | { state: "failed"; why: string };

/** Core-selection fields for the play-section row, derived from the dedicated
 *  `get_platform_core_info` path (#923), decoupled from BIOS status. */
export interface CoreInfoFields {
  activeCoreLabel: string | null;
  activeCoreIsDefault: boolean;
  emulators: EmulatorOption[];
  emulatorDataAvailable: boolean;
  /** Why there is no emulator list, and which source answered; see {@link CoreInfo}. */
  emulatorDataReason: EmulatorDataReason | null;
  emulatorSource: AnsweringSource | null;
  emulatorPlatformSystem: PlatformSystemAnswer | null;
  platformCoreLabel: string | null;
  hasGameOverride: boolean;
  /** What starting with the game's entry comes to, and a stored pick whose label no longer exists. */
  emulatorStart: EmulatorStart | null;
  /** Whether the installed game's file starts with that emulator (#2195 D2); absent reads as nothing asked. */
  gameLaunch?: GameLaunch | null;
  /** Where the page's check stands; absent reads as answered. */
  startCheck?: StartCheck;
  vanishedPick: VanishedPick | null;
}

export interface SaveSyncResolution {
  status: "synced" | "pending" | "conflict" | "none";
  label: string;
}

/** Resolve the human-readable save-sync label from the backend's typed display
 *  payload. Backend ships a static `label` for every case except
 *  `synced + has-recent-check`, where it leaves `label` null and passes
 *  `last_sync_check_at` through for time-ago formatting at render time. */
export function resolveSaveSyncLabel(display: SaveSyncDisplay): string {
  if (display.label !== null) return display.label;
  if (display.last_sync_check_at) {
    return formatTimeAgo(display.last_sync_check_at) ?? "Not synced";
  }
  return "Not synced";
}

/** Map a SaveSyncDisplay (typed display payload) to a status+label pair.
 *  Defensive fallback handles a SaveStatus missing the pre-computed display —
 *  should not occur in current callers, kept conservative. */
export function applySaveSyncDisplay(
  display: SaveSyncDisplay | undefined,
  saveStatus: SaveStatus | null,
): SaveSyncResolution {
  if (display) {
    return { status: display.status, label: resolveSaveSyncLabel(display) };
  }
  if (hasAnySaveConflict(saveStatus)) return { status: "conflict", label: "Conflict" };
  return { status: "none", label: "No saves" };
}

/** Project a whole BIOS answer — the backend's `bios_status` plus the label it
 *  pre-computed for it — into the BIOS-only fields the play-section row needs,
 *  or `null` when the payload carries no answer at all. The label is never
 *  re-derived here. Which payloads are answers is the wire type's to say
 *  (`BiosAnswer` in `api/backend.ts`).
 *
 *  An answer with a requirement sets the fields, and the badge rises on the
 *  verdict alone: not met. Every other answer clears them — the one that no BIOS
 *  is needed (`needs_bios: false`), and the `"unknown"` level, because leaving a
 *  stale warning standing would assert what nothing can establish any more. A
 *  payload carrying no answer projects to `null`, and the caller writes nothing.
 *
 *  That is the same split `panelState.biosFieldsFromCache` draws off the same
 *  payload; a divergence would leave the badge and the BIOS tab disagreeing
 *  about whether a question was answered.
 *
 *  Core data is sourced separately via `extractCoreInfo`. */
export function extractBiosInfo(answer: BiosAnswer): BiosInfoFields | null {
  if (answer.bios_status_unknown) {
    if (answer.bios_level !== "unknown") return null;
    return { biosNeeded: false, biosLabel: "", biosRequiredMissing: false };
  }
  if (!answer.bios_status) return null;
  if (answer.bios_status.needs_bios === false) {
    return { biosNeeded: false, biosLabel: "", biosRequiredMissing: false };
  }
  return {
    biosNeeded: true,
    biosLabel: answer.bios_label ?? "",
    biosRequiredMissing: answer.bios_level === "missing",
  };
}

/** Project a CoreInfo response (from the dedicated `get_platform_core_info`
 *  path, #923) into the core-selection fields the play-section row needs. The
 *  launching emulator counts as "default" when its label is the default
 *  entry's or no override is set. */
export function extractCoreInfo(coreInfo: CoreInfo): CoreInfoFields {
  const activeCoreLabel = coreInfo.active_core_label ?? null;
  const emulators = coreInfo.emulators;
  const defaultEmulator = emulators.find((e) => e.is_default);
  const activeCoreIsDefault = !activeCoreLabel || defaultEmulator?.label === activeCoreLabel;
  return {
    activeCoreLabel,
    activeCoreIsDefault,
    emulators,
    emulatorDataAvailable: coreInfo.emulator_data_available,
    emulatorDataReason: coreInfo.emulator_data_reason,
    emulatorSource: coreInfo.emulator_source,
    emulatorPlatformSystem: coreInfo.platform_system,
    platformCoreLabel: coreInfo.platform_core_label ?? null,
    hasGameOverride: coreInfo.has_game_override,
    emulatorStart: coreInfo.start,
    gameLaunch: coreInfo.launch,
    startCheck: { state: "answered" },
    vanishedPick: coreInfo.vanished_pick,
  };
}

/** Promise that rejects after `ms` milliseconds. Pair with `Promise.race` to
 *  enforce a timeout on an otherwise unbounded async call. */
export function timeoutMs(ms: number): Promise<never> {
  return new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), ms));
}
