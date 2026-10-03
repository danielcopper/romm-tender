/**
 * A Download press, carried through every refusal `start_download` can answer
 * with and every exit of the dialogs those refusals open (#260, ADR-0028): the
 * order the refusals are told apart in, the arguments each re-send carries, the
 * adoption and its collision re-ask, and what an adopted install needs before it
 * counts as one. The dialogs themselves are the surface's, injected as
 * {@link AdoptionDialogs}; the caller's own state is reached through
 * {@link AdoptionFlowHooks}.
 */

import {
  adoptExistingRom,
  debugLog,
  isCandidateVanished,
  isCandidatesFound,
  isRenameCollisions,
  isTargetOccupied,
  isUnusableNamesake,
  startDownload,
} from "../api/backend";
import { detach } from "./detach";
import { capturePruneLeaseAdmission, withPruneLease } from "./pruneLease";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import { showToast } from "./toast";
import {
  ADOPT_REFUSED_TOAST,
  ADOPT_THREW_TOAST,
  DOWNLOAD_REFUSED_TOAST,
  DOWNLOAD_THREW_TOAST,
  adoptedToast,
} from "./adoptWording";
import type {
  AdoptionCandidate,
  CandidatesFoundResult,
  CandidateVanishedResult,
  CollisionChoice,
  RenameCollision,
  TargetOccupiedResult,
  UnusableNamesakeResult,
} from "../types";

/** The comparison dialog's three exits. */
export type AdoptChoice = "adopt" | "replace" | "cancel";

/** The candidate list's exits: one candidate to compare, download none of them, or stop. */
export type CandidateChoice =
  { kind: "candidate"; candidate: AdoptionCandidate } | { kind: "download" } | { kind: "cancel" };

/** The collision dialog's one answer for the whole set, or a stop. */
export type CollisionAnswer = CollisionChoice | "cancel";

/** The unusable-namesake dialog's exits. */
export type UnusableChoice = "download" | "cancel";

/** The backstop dialog's exits. */
export type VanishedChoice = "download" | "cancel";

/**
 * The surface's dialogs. Each resolves with the exit the user took; one that is
 * dismissed without an exit may never resolve, which leaves the flow — and the
 * caller's state — exactly where the last hook call put it.
 */
export interface AdoptionDialogs {
  /**
   * The comparison. `candidatePath` is set when `occupied` describes a candidate
   * found elsewhere in the platform folder rather than content at the game's own
   * location; it is omitted — not passed as `undefined` — for the latter.
   */
  showExisting(romId: number, occupied: TargetOccupiedResult, candidatePath?: string): Promise<AdoptChoice>;
  /** Opened only for two or more candidates. */
  showCandidates(found: CandidatesFoundResult): Promise<CandidateChoice>;
  showCollisions(collisions: RenameCollision[]): Promise<CollisionAnswer>;
  showUnusable(unusable: UnusableNamesakeResult): Promise<UnusableChoice>;
  showVanished(vanished: CandidateVanishedResult): Promise<VanishedChoice>;
}

/**
 * The caller's state, as the flow learns it. Called synchronously, in the order
 * the flow learns each fact, and never after the outcome resolves.
 */
export interface AdoptionFlowHooks {
  /**
   * A request is in flight (`true`) or the flow is back in the user's hands
   * (`false`). Stays `true` when the outcome is `"download_started"`: the
   * transfer's own progress events drive the caller from there.
   */
  setBusy(busy: boolean): void;
  /** What the backend proved sits at this ROM's own download location. */
  setTargetOccupied(occupied: boolean): void;
  /** What the backend proved about a candidate under another name in the folder. */
  setCandidatePresent(present: boolean): void;
  /**
   * Called only when the flow ends `adopted`, which a launch-options write
   * that rejects, or is skipped for want of an appId or a launch command, does
   * not prevent. A write refused because the caller's lease owner has gone, or
   * that outlasts the lease's time limit, ends `failed` instead, and this is not
   * called. Called after both flags are
   * cleared and before `romm_data_changed` / `rom_adopted` is dispatched and the
   * success toast shown.
   */
  onAdopted(): void;
}

export interface DownloadWithAdoption {
  romId: number;
  /** Named in the success toast; the empty string reads as "ROM". */
  romName: string;
  /**
   * Whether the page said a candidate was present when the user pressed. Sent
   * only with the first request, so the backend can answer with the backstop
   * when its search finds nothing.
   */
  pageSawCandidate: boolean;
  /** The prune-lease owner the caller mounted; the adopt's Steam write is held under it. */
  leaseOwner: string;
  /** Prefix for this flow's debug log lines. */
  logContext: string;
  dialogs: AdoptionDialogs;
  hooks: AdoptionFlowHooks;
}

/**
 * - `download_started` — the backend accepted a download; progress events follow.
 * - `adopted` — what was on disk is now the install.
 * - `cancelled` — nothing started: the user took a dialog's cancel exit, or
 *   there was nothing to offer.
 * - `failed` — refused or thrown; a toast has said so.
 */
export type AdoptionOutcome = "download_started" | "adopted" | "cancelled" | "failed";

/**
 * Run one Download press to its end. The caller's own "already busy" guard is
 * for the press alone: every re-send this makes is the user's answer to a
 * refusal and is always admitted.
 */
export function runDownloadWithAdoption(flow: DownloadWithAdoption): Promise<AdoptionOutcome> {
  return download(flow, false, undefined, null);
}

async function download(
  flow: DownloadWithAdoption,
  replaceExisting: boolean,
  discardPath: string | undefined,
  collisionChoice: CollisionChoice | null,
): Promise<AdoptionOutcome> {
  const { romId, dialogs, hooks } = flow;
  hooks.setBusy(true);
  try {
    // Only a FIRST press reports what the page found. Every re-entry carries
    // `replace`, which is the user's answer to a refusal the page's report
    // already produced — reporting it again would ask the backstop to fire on
    // an answer it just received.
    const result = await startDownload(
      romId,
      replaceExisting,
      discardPath ?? null,
      collisionChoice,
      !replaceExisting && flow.pageSawCandidate,
    );
    if (isRenameCollisions(result)) {
      // Carrying the discarded candidate's saves would land on names that are
      // taken. Nothing has been removed or moved; the one answer covers the
      // whole set, exactly as it does on the adopt exit.
      hooks.setBusy(false);
      const answer = await dialogs.showCollisions(result.collisions);
      if (answer === "cancel") return "cancelled";
      return await download(flow, replaceExisting, discardPath, answer);
    }
    if (isTargetOccupied(result)) {
      // Nothing was written and no transfer started — the backend refused so
      // the user can choose (#260). Back to idle before the dialog opens,
      // because Cancel returns to the caller with nothing else to re-enable
      // it; adopt and replace each re-claim busy on their own path.
      hooks.setTargetOccupied(true);
      hooks.setBusy(false);
      return await resolveOccupiedTarget(flow, result);
    }
    if (isCandidatesFound(result)) {
      // Same refusal contract, different subject: the target path was free and
      // the game is on disk under another name. Recorded, because the backend
      // just proved it — without this a cancelled dialog leaves the caller
      // reading "Download" for content it has confirmed is there.
      hooks.setCandidatePresent(true);
      hooks.setBusy(false);
      return await resolveCandidates(flow, result);
    }
    if (isUnusableNamesake(result)) {
      // A namesake nothing can adopt — the other shape, or a link. Neither
      // flag moves: no content occupies this ROM's own path, and nothing here
      // is a candidate — what the page said stands, and the honest answer to
      // "is this game here" is the dialog the user is about to get.
      hooks.setBusy(false);
      return await resolveUnusable(flow, result);
    }
    if (isCandidateVanished(result)) {
      // The backstop fired: this page said a copy was here and the search can
      // name nothing. The flag goes, because the one thing now known is that
      // what the page found is not there to be used.
      hooks.setCandidatePresent(false);
      hooks.setBusy(false);
      return await resolveVanished(flow, result);
    }
    if (!result.success) {
      showToast(result.message || DOWNLOAD_REFUSED_TOAST);
      hooks.setBusy(false);
      return "failed";
    }
    return "download_started";
  } catch {
    showToast(DOWNLOAD_THREW_TOAST);
    hooks.setBusy(false);
    return "failed";
  }
}

async function resolveOccupiedTarget(
  flow: DownloadWithAdoption,
  occupied: TargetOccupiedResult,
): Promise<AdoptionOutcome> {
  const choice = await flow.dialogs.showExisting(flow.romId, occupied);
  if (choice === "replace") return download(flow, true, undefined, null);
  if (choice === "adopt") return adopt(flow, undefined, null);
  return "cancelled";
}

// Offer what the search found. One candidate needs no list — there is nothing
// to choose between — so it goes straight to the comparison. Both download
// exits re-send with `replace`, which is what tells the backend to skip the
// search rather than refuse a second time.
//
// They differ in what they hand back. Choosing a candidate and then Download
// Instead names it, because the confirmation the user just answered says that
// file is deleted. "None of These" names nothing: the user declined every
// candidate rather than picking one, so none of them may be removed.
async function resolveCandidates(flow: DownloadWithAdoption, found: CandidatesFoundResult): Promise<AdoptionOutcome> {
  let candidate = found.candidates[0];
  if (candidate === undefined) return "cancelled";
  if (found.candidates.length > 1) {
    const picked = await flow.dialogs.showCandidates(found);
    if (picked.kind === "cancel") return "cancelled";
    if (picked.kind === "download") return download(flow, true, undefined, null);
    candidate = picked.candidate;
  }
  const choice = await flow.dialogs.showExisting(
    flow.romId,
    comparisonForCandidate(candidate, found.incoming),
    candidate.path,
  );
  if (choice === "replace") return download(flow, true, candidate.path, null);
  if (choice === "adopt") return adopt(flow, candidate.path, null);
  return "cancelled";
}

// The only two honest exits for a namesake that cannot become this install:
// fetch the server's copy alongside it, or stop. `replace` is what carries the
// answer — it is what tells the backend the search has been answered — and no
// candidate path goes with it, because the entries listed are not taken over
// or removed.
async function resolveUnusable(flow: DownloadWithAdoption, unusable: UnusableNamesakeResult): Promise<AdoptionOutcome> {
  if ((await flow.dialogs.showUnusable(unusable)) === "download") return download(flow, true, undefined, null);
  return "cancelled";
}

// The backstop's two exits. `replace` names nothing here, because nothing was
// found: it says the search has been answered, and leaves whatever is at the
// game's location by then to the download gate (`check_download_target`).
async function resolveVanished(
  flow: DownloadWithAdoption,
  vanished: CandidateVanishedResult,
): Promise<AdoptionOutcome> {
  if ((await flow.dialogs.showVanished(vanished)) === "download") return download(flow, true, undefined, null);
  return "cancelled";
}

// Record what is on disk as the install, then write the launch command onto
// the shortcut as the download-complete listener does — an adopted
// install is an install (ADR-0028), so it must be as launchable as a
// downloaded one the moment the dialog closes.
async function adopt(
  flow: DownloadWithAdoption,
  candidatePath: string | undefined,
  collisionChoice: CollisionChoice | null,
): Promise<AdoptionOutcome> {
  const { romId, dialogs, hooks } = flow;
  hooks.setBusy(true);
  const admission = capturePruneLeaseAdmission(flow.leaseOwner);
  try {
    const result = await adoptExistingRom(romId, candidatePath ?? null, collisionChoice);
    if (isRenameCollisions(result)) {
      // Nothing has moved. The one answer covers the whole set, and a dismissed
      // dialog leaves the game exactly as it was.
      const answer = await dialogs.showCollisions(result.collisions);
      if (answer === "cancel") return "cancelled";
      return await adopt(flow, candidatePath, answer);
    }
    if (!result.success) {
      showToast(result.message || ADOPT_REFUSED_TOAST);
      return "failed";
    }
    const adoptedAppId = result.app_id;
    if (adoptedAppId != null && result.launch_options !== undefined) {
      const launchOptions = result.launch_options;
      await withPruneLease(
        result.prune_lease_token,
        "ROM adopt",
        async (signal) => {
          if (signal.aborted) return;
          await setLaunchOptionsConfirmed(adoptedAppId, launchOptions).catch(() => false);
        },
        flow.leaseOwner,
        admission,
      );
    }
    hooks.setTargetOccupied(false);
    hooks.setCandidatePresent(false);
    hooks.onAdopted();
    globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "rom_adopted", rom_id: romId } }));
    showToast(adoptedToast(flow.romName));
    return "adopted";
  } catch (e) {
    detach(debugLog(`${flow.logContext}: adopt failed: ${e}`));
    showToast(ADOPT_THREW_TOAST);
    return "failed";
  } finally {
    hooks.setBusy(false);
  }
}

/**
 * Reshape one candidate into the comparison the dialog renders. `adoptable` is
 * unconditionally true: the search only ever offers an entry whose shape matches
 * what the server serves, so there is no unusable candidate to disable the
 * button for.
 */
export function comparisonForCandidate(
  candidate: AdoptionCandidate,
  incoming: { name: string; size_bytes: number },
): TargetOccupiedResult {
  return {
    success: false,
    reason: "target_occupied",
    message: `'${candidate.name}' is already on this device`,
    existing: {
      name: candidate.name,
      path: candidate.path,
      // The search offers only what an install row may point at, so a candidate
      // is one of the two adoptable kinds and never a link.
      kind: candidate.is_dir ? "dir" : "file",
      size_bytes: candidate.size_bytes,
      modified_at: candidate.modified_at,
    },
    incoming,
    // A directory candidate is never sized by the search — it does not descend —
    // so there are no two numbers to relate. Same `null` as "the server stated no
    // size", but NOT the same sentence: `existingSize` / `sizeVerdict` tell the
    // two apart on the kind, because attributing our own choice not to measure to
    // the server would be a claim about their setup that is simply untrue.
    sizes_match: candidate.is_dir || !incoming.size_bytes ? null : candidate.size_bytes === incoming.size_bytes,
    adoptable: true,
  };
}
