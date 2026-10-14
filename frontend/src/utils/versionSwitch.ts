/**
 * Multi-version sibling switching and sibling management utility.
 *
 * Encapsulates the version switching lifecycle shared between Big Picture (VersionPicker)
 * and Desktop (DiscSelector):
 *   1. Version list loading, reachability reporting, and bound-vanished publication
 *   2. Lazy per-ROM cover art retrieval and caching
 *   3. Executing version switches with prune lease protection
 *   4. Handling unsynced saves soft-blocks (sync-then-switch, switch anyway, cancel)
 *   5. Surfacing toast feedback and handling unmount/cancellation
 */

import {
  getVersionList,
  switchVersion,
  syncRomSaves,
  refreshSaveStatus,
  fetchCoverBase64,
  logWarn,
  logError,
  type VersionList,
  type VersionInfo,
  type SwitchVersionSuccess,
  type SwitchVersionFailure,
  type SwitchVersionUnsyncedSaves,
} from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { setBoundVanished } from "./vanishedBinding";
import { applyCommittedVersionSwitch } from "./versionSwitchApplication";
import { capturePruneLeaseAdmission, isPruneLeaseAdmissionCurrent, isPruneLeaseCancellation } from "./pruneLease";
import { showToast } from "./toast";
import { detach } from "./detach";

export type UnsyncedSavesChoice = "sync_and_switch" | "switch_anyway" | "cancel";

export interface UnsyncedSavesPromptArgs {
  versionName: string;
  serverReachable: boolean;
}

export type UnsyncedSavesPrompt = (args: UnsyncedSavesPromptArgs) => Promise<UnsyncedSavesChoice>;

export interface VersionSwitchOptions {
  appId: number;
  target: VersionInfo;
  leaseOwner: string;
  askUnsyncedSaves: UnsyncedSavesPrompt;
  onCoverResolved?: ((romId: number, cover: string) => void) | undefined;
  onVanishedRefusal?: (() => void) | undefined;
  setSwitching?: ((switching: boolean) => void) | undefined;
  logTag?: string | undefined;
}

/**
 * Report server reachability based on version list query results.
 * An explicit server_query_failed marks the server unreachable; a multi-version list
 * that loaded without failure proves the server is reachable.
 */
export function reportVersionListReachability(result: VersionList): void {
  if (result.server_query_failed) {
    reportServerReachable(false);
  } else if (result.multi_version && !result.bound_vanished) {
    reportServerReachable(true);
  }
}

/**
 * Query version list for an app, updating reachability and bound vanished stores
 * if the request is still current.
 */
export async function loadVersionList(appId: number, isCurrent?: () => boolean): Promise<VersionList | null> {
  const result = await getVersionList(appId);
  if (isCurrent && !isCurrent()) return null;
  reportVersionListReachability(result);
  setBoundVanished(appId, result.bound_vanished);
  return result;
}

/**
 * Lazily fetch covers for all versions in a group, deduping against a requested set.
 * Returns a cleanup function that cancels pending in-flight updates on unmount.
 */
export function fetchVersionCovers(
  versions: VersionInfo[] | undefined,
  coversRequested: Set<number>,
  onCover: (romId: number, base64: string) => void,
): () => void {
  if (!versions) return () => {};
  let cancelled = false;
  for (const v of versions) {
    if (coversRequested.has(v.rom_id)) continue;
    coversRequested.add(v.rom_id);
    fetchCoverBase64(v.rom_id)
      .then((result) => {
        if (!cancelled && result.base64) {
          onCover(v.rom_id, result.base64);
        }
      })
      .catch(() => {});
  }
  return () => {
    cancelled = true;
  };
}

/**
 * Whether a row can be switched to. A version RomM holds no file for can only
 * be played from a local copy, so it is offered only where one is installed.
 */
export const isSelectable = (v: VersionInfo): boolean => !v.vanished && v.switchable && !(v.no_file && !v.installed);

/**
 * Execute the complete version switch workflow:
 *   - Guards against the active version and any version `isSelectable` refuses
 *   - Calls backend switchVersion
 *   - Applies committed switch and shortcut launch options on success
 *   - Prompts for unsynced saves on soft-block and executes sync-then-switch or force switch
 *   - Handles failures, reachability updates, and prune lease cancellation
 */
export async function executeVersionSwitch(options: VersionSwitchOptions): Promise<void> {
  const {
    appId,
    target,
    leaseOwner,
    askUnsyncedSaves,
    onCoverResolved,
    onVanishedRefusal,
    setSwitching,
    logTag = "VersionPicker",
  } = options;

  // An unselectable row is rendered disabled, and this guard makes a click on
  // one a no-op (defense-in-depth), so switch_version's rejection — of a
  // sibling in another local group (#1359), or of one with no file — can never
  // reach a toast.
  if (target.active || !isSelectable(target)) return;

  setSwitching?.(true);
  const admission = capturePruneLeaseAdmission(leaseOwner);

  const applySwitchSuccess = async (result: SwitchVersionSuccess): Promise<void> => {
    const confirmed = await applyCommittedVersionSwitch(result, onCoverResolved, admission);
    if (!confirmed) {
      showToast("Switched — re-switch if launch fails");
    }
  };

  const refreshStrandedSaveStatus = (unsyncedRomId: number): void => {
    detach(
      refreshSaveStatus(unsyncedRomId).catch((e) =>
        logWarn(`${logTag}: post-abort save-status refresh failed for rom ${unsyncedRomId}: ${e}`),
      ),
    );
  };

  const handleFailure = (result: SwitchVersionFailure | SwitchVersionUnsyncedSaves): void => {
    if (result.reason === "server_unreachable") reportServerReachable(false);
    setSwitching?.(false);
    showToast("Could not switch version", { subtext: result.message });
    if (result.reason === "version_vanished") {
      onVanishedRefusal?.();
    }
  };

  const syncThenSwitch = async (unsyncedRomId: number): Promise<void> => {
    const abort = (body: string): void => {
      setSwitching?.(false);
      showToast(body);
      refreshStrandedSaveStatus(unsyncedRomId);
    };

    if (!isPruneLeaseAdmissionCurrent(admission)) return;
    try {
      const sync = await syncRomSaves(unsyncedRomId);
      if (!isPruneLeaseAdmissionCurrent(admission)) return;
      if (!sync.success) {
        abort("Couldn't sync saves — try again");
        return;
      }
      if (sync.conflicts && sync.conflicts.length > 0) {
        abort("Resolve save conflicts first");
        return;
      }
      if (!isPruneLeaseAdmissionCurrent(admission)) return;

      const retry = await switchVersion(appId, target.rom_id, false);
      if (retry.success) {
        await applySwitchSuccess(retry);
      } else if (retry.reason === "unsynced_saves") {
        abort("Saves still unsynced — try again");
      } else {
        handleFailure(retry);
        refreshStrandedSaveStatus(unsyncedRomId);
      }
    } catch (e) {
      if (!isPruneLeaseAdmissionCurrent(admission)) return;
      logError(`${logTag}: sync-then-switch failed: ${e}`);
      abort("Couldn't sync saves — try again");
    }
  };

  try {
    const result = await switchVersion(appId, target.rom_id, false);
    if (result.success) {
      await applySwitchSuccess(result);
      return;
    }

    if (result.reason === "unsynced_saves") {
      reportServerReachable(result.server_reachable);
      const choice = await askUnsyncedSaves({
        versionName: result.unsynced_version_name,
        serverReachable: result.server_reachable,
      });

      if (!isPruneLeaseAdmissionCurrent(admission)) return;
      if (choice === "cancel") {
        setSwitching?.(false);
        return;
      }

      if (choice === "sync_and_switch") {
        await syncThenSwitch(result.unsynced_rom_id);
        return;
      }

      // "switch_anyway"
      const forced = await switchVersion(appId, target.rom_id, true);
      if (forced.success) {
        await applySwitchSuccess(forced);
        return;
      }
      handleFailure(forced);
      return;
    }

    handleFailure(result);
  } catch (e) {
    if (isPruneLeaseCancellation(e, admission)) {
      logWarn(`${logTag}: version switch continuation was cancelled: ${e}`);
      return;
    }
    setSwitching?.(false);
    logError(`${logTag}: switchVersion failed: ${e}`);
    showToast("Could not switch version");
  }
}
