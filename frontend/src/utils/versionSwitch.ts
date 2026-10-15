/**
 * A version group's list, its covers, and the switch between its versions.
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
import { boundAvailabilityOf, setBoundAvailability } from "./vanishedBinding";
import { applyCommittedVersionSwitch } from "./versionSwitchApplication";
import { capturePruneLeaseAdmission, isPruneLeaseAdmissionCurrent, isPruneLeaseCancellation } from "./pruneLease";
import { showToast } from "./toast";
import { detach } from "./detach";

/** What the user chose in the unsynced-saves switch confirm (#1298). */
export type UnsyncedSavesChoice = "sync_and_switch" | "switch_anyway" | "cancel";

export interface UnsyncedSavesPromptArgs {
  /** Display name of the currently-bound version that would be stranded. */
  versionName: string;
  /** Whether RomM is reachable — decides if "Sync now & switch" is offered. */
  serverReachable: boolean;
}

export type UnsyncedSavesPrompt = (args: UnsyncedSavesPromptArgs) => Promise<UnsyncedSavesChoice>;

export interface VersionSwitchOptions {
  appId: number;
  target: VersionInfo;
  leaseOwner: string;
  askUnsyncedSaves: UnsyncedSavesPrompt;
  onCoverResolved?: ((romId: number, cover: string) => void) | undefined;
  /** Called after a refusal because the target version vanished, to reload the list. */
  onVanishedRefusal?: (() => void) | undefined;
  /**
   * The caller's in-flight guard. It is set on entry and released on every
   * non-success terminal path but a teardown, which touches no state (the
   * picker is gone). On success it is deliberately LEFT set: a switch that is
   * not torn down broadcasts version_switched, and the caller's list reload on
   * that broadcast must clear it once the fresh list lands — so the trigger
   * never re-enables against a stale list. A caller without that reload stays
   * "switching" after its first successful switch.
   */
  setSwitching?: ((switching: boolean) => void) | undefined;
  logTag?: string | undefined;
}

/**
 * Feed the global reachability store from a version list (#1345): an explicit
 * server_query_failed means offline; a multi-version list that loaded without
 * failure proves the server is reachable. A bound-id 404 is an entity verdict,
 * not a connection signal, so it feeds neither direction into the global store.
 * A single/unbound group carries no reachability signal.
 */
export function reportVersionListReachability(result: VersionList): void {
  if (result.server_query_failed) {
    reportServerReachable(false);
  } else if (result.multi_version && !result.bound_vanished) {
    reportServerReachable(true);
  }
}

/**
 * Read the app's version list and, if *isCurrent* still holds when it lands,
 * publish what it says about reachability and about the bound version; `null`
 * when it no longer holds.
 */
export async function loadVersionList(appId: number, isCurrent?: () => boolean): Promise<VersionList | null> {
  const result = await getVersionList(appId);
  if (isCurrent && !isCurrent()) return null;
  reportVersionListReachability(result);
  // Publish the bound-id verdict for the play button and the banner, which
  // cannot see the picker's state. Only a positive `bound_vanished` is
  // knowledge — a failed query reports false, so an offline session never
  // disables the download (#1570 F20).
  setBoundAvailability(appId, boundAvailabilityOf(result));
  return result;
}

/**
 * Fetch a cover for every version not yet in *coversRequested*, through the
 * cache-first fetchCoverBase64 (#1346), and hand each to *onCover*. The
 * returned function drops the covers still in flight.
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

/** Switch the app to *target*, asking about any saves the switch would strand. */
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

  // Apply a successful switch: confirm-write the target's launch command onto the
  // Steam shortcut (blank for an uninstalled target — intended, so the shortcut
  // never keeps the old version's command), then invalidate the cache and
  // broadcast the switch so sibling surfaces re-read the new binding.
  //
  // The backend rebind is ALREADY committed by the time we get here, so the
  // cache-invalidate + broadcast must always run — even if the launch-command
  // write fails or throws. A missed confirm only leaves the shortcut on a stale
  // command; it self-heals at the next startup/sync reconcile, so we warn and
  // nudge the user rather than reporting the whole switch as failed.
  const applySwitchSuccess = async (result: SwitchVersionSuccess): Promise<void> => {
    const confirmed = await applyCommittedVersionSwitch(result, onCoverResolved, admission);
    if (!confirmed) {
      showToast("Switched — re-switch if launch fails");
    }
  };

  // Keep the toast body short (Steam truncates it to one line) and put the
  // backend detail in the subtext so the reason is readable (#1359).
  const handleSwitchFailure = (result: SwitchVersionFailure | SwitchVersionUnsyncedSaves): void => {
    if (result.reason === "server_unreachable") reportServerReachable(false);
    setSwitching?.(false);
    showToast("Could not switch version", { subtext: result.message });
    if (result.reason === "version_vanished") onVanishedRefusal?.();
  };

  // Sync the stranded version's saves, then retry the switch. Every failure —
  // sync failed, sync surfaced conflicts, the retry blocked again, or the retry
  // was refused outright — ends the attempt with a toast and re-runs the
  // save-status refresh, so the conflict UI (or the just-completed upload)
  // surfaces through the normal save_status_updated loop.
  const syncThenSwitch = async (unsyncedRomId: number): Promise<void> => {
    // The SavesTab has no other refresh trigger — `refresh_save_status` is what
    // drives the `save_status_updated` chain — so EVERY terminal failure here
    // must run it, or the tab keeps showing pre-sync status until it is re-entered.
    const refreshStrandedSaveStatus = (): void => {
      detach(
        refreshSaveStatus(unsyncedRomId).catch((e) =>
          logWarn(`${logTag}: post-abort save-status refresh failed for rom ${unsyncedRomId}: ${e}`),
        ),
      );
    };
    const abort = (body: string): void => {
      // Every sync-then-switch failure is terminal for this attempt — release the
      // in-flight guard so the trigger re-enables (it never reaches a reload).
      setSwitching?.(false);
      showToast(body);
      refreshStrandedSaveStatus();
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
        // The sync ran but the version still reports drift (a partial upload or a
        // race) — say so instead of the generic "couldn't switch".
        abort("Saves still unsynced — try again");
      } else {
        // The sync landed but the retried switch was refused (e.g. the target
        // version vanished). handleSwitchFailure owns the toast + guard; the
        // refresh is still ours, since the saves DID move.
        handleSwitchFailure(retry);
        refreshStrandedSaveStatus();
      }
    } catch (e) {
      if (!isPruneLeaseAdmissionCurrent(admission)) return;
      logError(`${logTag}: sync-then-switch failed: ${e}`);
      abort("Couldn't sync saves — try again");
    }
  };

  /** Ask what to do about the saves the switch would strand, then do it. */
  const resolveUnsyncedSaves = async (result: SwitchVersionUnsyncedSaves): Promise<void> => {
    // The soft-block response carries a definitive reachability verdict (#1345).
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
      // syncThenSwitch owns the guard from here: it clears on abort and leaves
      // it set on its own success (its reload clears it).
      await syncThenSwitch(result.unsynced_rom_id);
      return;
    }
    // "Switch anyway" — the override skips the stranding gate; strand the
    // saves on disk (they stay recoverable, they just won't sync until the
    // user switches back).
    const forced = await switchVersion(appId, target.rom_id, true);
    if (forced.success) {
      await applySwitchSuccess(forced);
    } else {
      handleSwitchFailure(forced);
    }
  };

  try {
    const result = await switchVersion(appId, target.rom_id, false);
    if (result.success) {
      await applySwitchSuccess(result);
      return;
    }
    if (result.reason === "unsynced_saves") {
      await resolveUnsyncedSaves(result);
      return;
    }
    handleSwitchFailure(result);
  } catch (e) {
    // A teardown-cancelled continuation is not a switch failure: the backend
    // rebind either committed or was never attempted, and the picker is gone.
    // Stay silent (and touch no state) rather than toast at the next surface.
    if (isPruneLeaseCancellation(e, admission)) {
      logWarn(`${logTag}: version switch continuation was cancelled: ${e}`);
      return;
    }
    setSwitching?.(false);
    logError(`${logTag}: switchVersion failed: ${e}`);
    showToast("Could not switch version");
  }
}
