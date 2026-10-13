/**
 * SGDB artwork apply — downloads the four SGDB asset types for a ROM and
 * writes them onto the Steam shortcut. Shared by RomMPlaySection (passive
 * fill of the empty slots + Refresh Artwork action) and SgdbGamePickerModal
 * (re-apply after a manual game-id pick), so it lives here rather than on either
 * component to keep their import graph acyclic. The shortcut icon job's writes
 * (`utils/shortcutIcons.ts`) ask it whether an apply is under way.
 */

import {
  getSgdbArtworkBase64,
  getSgdbSlotArtwork,
  reportSgdbSlotArtwork,
  saveShortcutIcon,
  debugLog,
  type SgdbArtworkSlot,
  type SgdbSlotArtwork,
} from "../api/backend";
import { detach } from "./detach";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancelled,
  mountPruneLeaseOwner,
  releasePruneLeasesByOwner,
  withPruneLeases,
} from "./pruneLease";

/**
 * Newest-apply-wins guard. Each appId's most recent applyArtwork call claims a
 * generation; an earlier call for the SAME appId that is still mid-flight when a
 * newer one starts stops writing rather than clobber the newer art. The hazard:
 * a version switch re-points the shortcut to a new rom and fires a fresh apply
 * while the old rom's (uncached, seconds-long) artwork fetch is still pending —
 * without this, the old apply's writes land last and revert the tile. Keyed by
 * appId so unrelated shortcuts never gate each other.
 */
const artworkGenerations = new Map<number, number>();

/** How many `applyArtwork` calls are under way for each appId. */
const artworkInFlight = new Map<number, number>();

/**
 * Is the game page applying artwork to *appId* right now? The shortcut icon job
 * then leaves the shortcut to that apply; if it sets no icon, the shortcut is
 * still on the job's worklist at its next run.
 */
export function isArtworkApplyInFlight(appId: number): boolean {
  return (artworkInFlight.get(appId) ?? 0) > 0;
}

/**
 * Apply the SGDB icon (type 4): the backend writes the PNG into Steam's grid dir
 * and returns its path; pointing the shortcut at it must go through SteamClient
 * (Steam owns shortcuts.vdf in memory and clobbers external writes). Returns
 * whether the icon was actually applied — a save failure or a missing path is not.
 */
async function applyIcon(appId: number, base64: string, signal: AbortSignal): Promise<boolean> {
  if (isPruneLeaseCancelled(signal)) return false;
  const iconResult = await saveShortcutIcon(appId, base64);
  if (isPruneLeaseCancelled(signal)) return false;
  if (iconResult.success && iconResult.icon_path) {
    SteamClient.Apps.SetShortcutIcon(appId, iconResult.icon_path);
    return true;
  }
  return false;
}

/** Fetch SGDB artwork (hero, logo, wide grid, icon) and apply to Steam, replacing
 *  what each slot holds. Returns count of successfully applied images, or -1
 *  when no SGDB API key is configured. */
export async function applyArtwork(romId: number, appId: number): Promise<number> {
  return whileInFlight(appId, () => applySgdbArtwork(romId, appId));
}

/** Give the shortcut SteamGridDB's hero, logo and wide grid where Steam's grid
 *  directory has no image for the slot, and its icon as `applyArtwork` does. A
 *  slot that already has one is left alone, whoever set it. Returns the count of
 *  images applied, or -1 when no SGDB API key is configured. */
export async function fillEmptyArtwork(romId: number, appId: number): Promise<number> {
  return whileInFlight(appId, () => fillEmptySgdbArtwork(romId, appId));
}

async function whileInFlight(appId: number, apply: () => Promise<number>): Promise<number> {
  artworkInFlight.set(appId, (artworkInFlight.get(appId) ?? 0) + 1);
  try {
    return await apply();
  } finally {
    const left = (artworkInFlight.get(appId) ?? 1) - 1;
    if (left > 0) artworkInFlight.set(appId, left);
    else artworkInFlight.delete(appId);
  }
}

/** Claim *appId*'s newest generation; the answer tells whether a later apply has overtaken this one. */
function claimGeneration(appId: number): () => boolean {
  const generation = (artworkGenerations.get(appId) ?? 0) + 1;
  artworkGenerations.set(appId, generation);
  return () => artworkGenerations.get(appId) !== generation;
}

async function applySgdbArtwork(romId: number, appId: number): Promise<number> {
  const leaseOwner = `artwork:${appId}`;
  mountPruneLeaseOwner(leaseOwner);
  const admission = capturePruneLeaseAdmission(leaseOwner);
  const superseded = claimGeneration(appId);

  const results = await Promise.all([
    getSgdbArtworkBase64(romId, 1).catch(() => ({ base64: null, no_api_key: false })),
    getSgdbArtworkBase64(romId, 2).catch(() => ({ base64: null, no_api_key: false })),
    getSgdbArtworkBase64(romId, 3).catch(() => ({ base64: null, no_api_key: false })),
    getSgdbArtworkBase64(romId, 4).catch(() => ({ base64: null, no_api_key: false })),
  ]);

  const leaseTokens = results.map((result) => ("prune_lease_token" in result ? result.prune_lease_token : undefined));
  if (results.some((r) => r.no_api_key)) {
    return withPruneLeases(leaseTokens, "Artwork apply", async () => -1, leaseOwner, admission);
  }

  return withPruneLeases(
    leaseTokens,
    "Artwork apply",
    async (signal) => {
      let applied = 0;
      // A later apply for this appId can start during any of the awaits above/below,
      // so re-check before every Steam write. Once superseded, go silent (log once,
      // keep whatever this call already wrote) instead of overwriting the newer art.
      const bail = (): number => {
        detach(debugLog(`applyArtwork: superseded for appId ${appId}, skipping stale writes`));
        return applied;
      };

      // SGDB types 1-3 (hero / logo / wide grid) map 1:1 to Steam capsule assetTypes 1-3.
      const customArt: Array<[base64: string | null, assetType: number]> = [
        [results[0].base64, 1],
        [results[1].base64, 2],
        [results[2].base64, 3],
      ];
      for (const [base64, assetType] of customArt) {
        if (!base64) continue;
        if (isPruneLeaseCancelled(signal)) return applied;
        if (superseded()) return bail();
        await SteamClient.Apps.SetCustomArtworkForApp(appId, base64, "png", assetType);
        applied++;
      }

      // Type 4 = icon (a distinct apply path — see applyIcon).
      if (results[3].base64) {
        if (isPruneLeaseCancelled(signal)) return applied;
        if (superseded()) return bail();
        if (await applyIcon(appId, results[3].base64, signal)) applied++;
      }

      return applied;
    },
    leaseOwner,
    admission,
  );
}

/** The slots the game page fills only while empty, each with Steam's asset type for it. */
const FILLED_SLOTS: ReadonlyArray<[slot: SgdbArtworkSlot, assetType: number]> = [
  ["hero", 1],
  ["logo", 2],
  ["grid", 3],
];

type SlotImage = Extract<SgdbSlotArtwork, { status: "image" }>;

async function fillEmptySgdbArtwork(romId: number, appId: number): Promise<number> {
  const leaseOwner = `artwork:${appId}`;
  mountPruneLeaseOwner(leaseOwner);
  const admission = capturePruneLeaseAdmission(leaseOwner);
  const superseded = claimGeneration(appId);
  let applied = 0;

  // One slot at a time: each image is fetched, set and reported before the next
  // is fetched, so only one is in the renderer at once.
  for (const [slot, assetType] of FILLED_SLOTS) {
    if (superseded()) return applied;
    const answer = await getSgdbSlotArtwork(romId, appId, slot).catch((e): SgdbSlotArtwork => {
      detach(debugLog(`fillEmptyArtwork: ${slot} fetch for appId ${appId} rejected: ${e}`));
      return { status: "failed" };
    });
    if (answer.status === "no_api_key") return -1;
    if (answer.status !== "image") continue;
    const outcome = await withPruneLeases(
      [answer.prune_lease_token],
      "Artwork apply",
      (signal) => setSlotImage(appId, answer, assetType, signal, superseded),
      leaseOwner,
      admission,
    );
    if (outcome === "set") applied++;
    if (outcome !== null && slot !== "hero") await reportSlotOutcome(romId, slot, outcome, answer);
  }

  if (superseded()) return applied;
  const icon = await getSgdbArtworkBase64(romId, 4).catch(() => ({ base64: null, no_api_key: false }));
  const iconToken = "prune_lease_token" in icon ? icon.prune_lease_token : undefined;
  const iconApplied = await withPruneLeases(
    [iconToken],
    "Artwork apply",
    async (signal) => !!icon.base64 && !superseded() && (await applyIcon(appId, icon.base64, signal)),
    leaseOwner,
    admission,
  );
  return iconApplied ? applied + 1 : applied;
}

/** Set one slot's image; `null` when the write was left out because the apply was cancelled or overtaken. */
async function setSlotImage(
  appId: number,
  image: SlotImage,
  assetType: number,
  signal: AbortSignal,
  superseded: () => boolean,
): Promise<"set" | "refused" | null> {
  if (isPruneLeaseCancelled(signal) || superseded()) return null;
  try {
    await SteamClient.Apps.SetCustomArtworkForApp(appId, image.base64, "png", assetType);
    return "set";
  } catch (e) {
    detach(debugLog(`fillEmptyArtwork: Steam refused the image for appId ${appId}, asset type ${assetType}: ${e}`));
    return "refused";
  }
}

/** Tell the backend what Steam made of the image, so it records it; the backend cannot see Steam's answer. */
async function reportSlotOutcome(
  romId: number,
  slot: "grid" | "logo",
  outcome: "set" | "refused",
  image: SlotImage,
): Promise<void> {
  await reportSgdbSlotArtwork(romId, slot, outcome, image.sgdb_id, image.image_id, image.sha256).catch((e) => {
    detach(debugLog(`fillEmptyArtwork: reporting the ${slot} for rom ${romId} failed: ${e}`));
  });
}

export async function cancelArtworkApply(appId: number): Promise<void> {
  artworkGenerations.set(appId, (artworkGenerations.get(appId) ?? 0) + 1);
  await releasePruneLeasesByOwner(`artwork:${appId}`);
}
