/**
 * SGDB artwork apply — writes a ROM's SGDB hero, logo, wide grid and icon onto
 * its Steam shortcut. Shared by RomMPlaySection (a first visit's fill of the
 * empty slots, and the replacing apply of a version switch and of Refresh
 * Artwork) and SgdbGamePickerModal (the replacing apply after a manual game-id
 * pick), so it lives here rather than on either component to keep their import
 * graph acyclic. The shortcut artwork job's writes (`utils/shortcutArtwork.ts`) ask
 * it whether an apply is under way, and set the job's wide grids and logos
 * through it.
 */

import {
  getSgdbArtworkBase64,
  getSgdbSlotArtwork,
  isEndpointFailure,
  reportSgdbSlotArtwork,
  saveShortcutIcon,
  debugLog,
  type EndpointFailure,
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
 * Newest-apply-wins guard. Each appId's most recent applyArtwork or
 * fillEmptyArtwork call claims a generation; an earlier call for the SAME appId that is still mid-flight when a
 * newer one starts stops writing rather than clobber the newer art. The hazard:
 * a version switch re-points the shortcut to a new rom and fires a fresh apply
 * while the old rom's (uncached, seconds-long) artwork fetch is still pending —
 * without this, the old apply's writes land last and revert the tile. Keyed by
 * appId so unrelated shortcuts never gate each other.
 */
const artworkGenerations = new Map<number, number>();

/** How many `applyArtwork` and `fillEmptyArtwork` calls are under way for each appId. */
const artworkInFlight = new Map<number, number>();

/**
 * Is the game page applying artwork to *appId* right now? The shortcut artwork job
 * then leaves the shortcut to that apply; whatever that apply does not set stays
 * on the job's worklist for its next run.
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
 *  what each slot holds. Returns count of successfully applied images, -1
 *  when no SGDB API key is configured, or the refusal of an image's fetch, in
 *  which case nothing is applied. */
export async function applyArtwork(romId: number, appId: number): Promise<number | EndpointFailure> {
  return whileInFlight(appId, () => applySgdbArtwork(romId, appId));
}

/** Give the shortcut SteamGridDB's hero, logo and wide grid where Steam's grid
 *  directory has no image for the slot, and its icon as `applyArtwork` does. A
 *  slot that already has one is left alone, whoever set it. Returns the count of
 *  images applied, or -1 when no SGDB API key is configured. */
export async function fillEmptyArtwork(romId: number, appId: number): Promise<number> {
  return whileInFlight(appId, () => fillEmptySgdbArtwork(romId, appId));
}

async function whileInFlight<T>(appId: number, apply: () => Promise<T>): Promise<T> {
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

/** Note *appId*'s generation without claiming one; the answer tells whether an apply has started since. */
function watchGeneration(appId: number): () => boolean {
  const generation = artworkGenerations.get(appId) ?? 0;
  return () => (artworkGenerations.get(appId) ?? 0) !== generation;
}

async function applySgdbArtwork(romId: number, appId: number): Promise<number | EndpointFailure> {
  const leaseOwner = `artwork:${appId}`;
  mountPruneLeaseOwner(leaseOwner);
  const admission = capturePruneLeaseAdmission(leaseOwner);
  const superseded = claimGeneration(appId);

  const refusals: EndpointFailure[] = [];
  const fetchArtwork = async (assetType: number): Promise<SgdbArtwork> => {
    const answer = await getSgdbArtworkBase64(romId, assetType).catch(() => NO_IMAGE);
    if (!isEndpointFailure(answer)) return answer;
    refusals.push(answer);
    return NO_IMAGE;
  };
  const results = await Promise.all([fetchArtwork(1), fetchArtwork(2), fetchArtwork(3), fetchArtwork(4)]);

  const leaseTokens = results.map((result) => result.prune_lease_token);
  if (results.some((r) => r.no_api_key)) {
    return withPruneLeases(leaseTokens, "Artwork apply", async () => -1, leaseOwner, admission);
  }
  const refusal = refusals[0];
  if (refusal) {
    return withPruneLeases(leaseTokens, "Artwork apply", async () => refusal, leaseOwner, admission);
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
      // The wide grid and the logo are recorded as the fill path records them;
      // the hero is not. The awaits are sequential by design, one image at a
      // time; S9382 is raised on the slot's await line, so its NOSONAR stays
      // there.
      const customArt: Array<[answer: SgdbArtwork, assetType: number, slot: RecordedSlot | null]> = [
        [results[0], 1, null],
        [results[1], 2, "logo"],
        [results[2], 3, "grid"],
      ];
      for (const [answer, assetType, slot] of customArt) {
        if (!answer.base64) continue;
        if (isPruneLeaseCancelled(signal)) return applied;
        if (superseded()) return bail();
        await setReplacedSlot(romId, appId, answer.base64, assetType, slot, answer); // NOSONAR(typescript:S9382)
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

/** What `getSgdbArtworkBase64` answers for one asset type when it is not refused. */
type SgdbArtwork = Exclude<Awaited<ReturnType<typeof getSgdbArtworkBase64>>, EndpointFailure>;

/** The answer a fetch that was rejected or refused counts as. */
const NO_IMAGE: SgdbArtwork = { base64: null, no_api_key: false };

/** The slots whose outcome the backend records. */
type RecordedSlot = "grid" | "logo";

/**
 * Set one image of the replacing apply and record what Steam made of it, for a
 * slot the backend records; a refusal is recorded and then rethrown.
 */
async function setReplacedSlot(
  romId: number,
  appId: number,
  base64: string,
  assetType: number,
  slot: RecordedSlot | null,
  answer: SgdbArtwork,
): Promise<void> {
  try {
    await SteamClient.Apps.SetCustomArtworkForApp(appId, base64, "png", assetType);
  } catch (e) {
    if (slot) await reportReplacedSlot(romId, slot, "refused", answer);
    throw e;
  }
  if (slot) await reportReplacedSlot(romId, slot, "set", answer);
}

/** Report what Steam made of an image the replacing apply set, as the fill path reports it. */
async function reportReplacedSlot(
  romId: number,
  slot: RecordedSlot,
  outcome: "set" | "refused",
  answer: SgdbArtwork,
): Promise<void> {
  try {
    await reportSgdbSlotArtwork(
      romId,
      slot,
      outcome,
      answer.sgdb_id ?? null,
      answer.image_id ?? null,
      answer.sha256 ?? null,
    );
  } catch (e) {
    detach(debugLog(`applyArtwork: reporting the ${slot} for rom ${romId} failed: ${e}`));
  }
}

/** The slots the game page fills only while empty, each with Steam's asset type for it. */
const FILLED_SLOTS: ReadonlyArray<[slot: SgdbArtworkSlot, assetType: number]> = [
  ["hero", 1],
  ["logo", 2],
  ["grid", 3],
];

/** Steam's asset type for each slot the shortcut artwork job fills. */
const JOB_SLOT_ASSET_TYPES: Readonly<Record<RecordedSlot, number>> = { logo: 2, grid: 3 };

type SlotImage = Extract<SgdbSlotArtwork, { status: "image" }>;

async function fillEmptySgdbArtwork(romId: number, appId: number): Promise<number> {
  const leaseOwner = `artwork:${appId}`;
  mountPruneLeaseOwner(leaseOwner);
  const admission = capturePruneLeaseAdmission(leaseOwner);
  const superseded = claimGeneration(appId);
  let applied = 0;

  // One slot at a time: each image is fetched, set and reported before the next
  // is fetched, so only one is in the renderer at once — the awaits are
  // sequential by design. S9382 is raised on the lease's and the report's await
  // lines, so their NOSONARs must stay there; prettier-ignore stops Prettier
  // from moving them.
  for (const [slot, assetType] of FILLED_SLOTS) {
    if (superseded()) return applied;
    const answer = await getSgdbSlotArtwork(romId, appId, slot).catch((e): SgdbSlotArtwork => {
      detach(debugLog(`fillEmptyArtwork: ${slot} fetch for appId ${appId} rejected: ${e}`));
      return { status: "failed" };
    });
    if (isEndpointFailure(answer)) {
      detach(debugLog(`fillEmptyArtwork: ${slot} fetch for appId ${appId} refused: ${answer.message}`));
      continue;
    }
    if (answer.status === "no_api_key") return -1;
    if (answer.status !== "image") continue;
    // prettier-ignore
    const outcome = await withPruneLeases( // NOSONAR(typescript:S9382)
      [answer.prune_lease_token],
      "Artwork apply",
      (signal) => setSlotImage(appId, answer, assetType, signal, superseded),
      leaseOwner,
      admission,
    );
    if (outcome === "set") applied++;
    // prettier-ignore
    if (outcome !== null && slot !== "hero") await reportSlotOutcome(romId, slot, outcome, answer); // NOSONAR(typescript:S9382)
  }

  if (superseded()) return applied;
  const iconAnswer = await getSgdbArtworkBase64(romId, 4).catch(() => NO_IMAGE);
  const icon = isEndpointFailure(iconAnswer) ? NO_IMAGE : iconAnswer;
  const iconToken = icon.prune_lease_token;
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
    detach(debugLog(`Artwork: Steam refused the image for appId ${appId}, asset type ${assetType}: ${e}`));
    return "refused";
  }
}

/** Report what Steam made of the image for the backend to record. */
async function reportSlotOutcome(
  romId: number,
  slot: RecordedSlot,
  outcome: "set" | "refused",
  image: SlotImage,
): Promise<void> {
  await reportSgdbSlotArtwork(romId, slot, outcome, image.sgdb_id, image.image_id, image.sha256).catch((e) => {
    detach(debugLog(`Artwork: reporting the ${slot} for rom ${romId} failed: ${e}`));
  });
}

/**
 * Set a wide grid or logo the shortcut artwork job has put into the artwork cache,
 * and report what Steam made of it. The image is fetched through the same
 * per-slot endpoint a first visit uses, so a slot that has filled since the job
 * looked is skipped. Nothing is written once a game page's apply for the
 * shortcut has started since this began fetching the image, or once *stopped*
 * answers yes. Answers whether the image
 * was set.
 */
export async function setJobSlotArtwork(
  romId: number,
  appId: number,
  slot: RecordedSlot,
  stopped: () => boolean,
): Promise<boolean> {
  const overtaken = watchGeneration(appId);
  const left = (): boolean => stopped() || overtaken();
  const answer = await getSgdbSlotArtwork(romId, appId, slot).catch((e): SgdbSlotArtwork => {
    detach(debugLog(`Shortcut artwork: ${slot} fetch for appId ${appId} rejected: ${e}`));
    return { status: "failed" };
  });
  if (isEndpointFailure(answer)) {
    detach(debugLog(`Shortcut artwork: ${slot} fetch for appId ${appId} refused: ${answer.message}`));
    return false;
  }
  if (answer.status !== "image") return false;
  const outcome = await withPruneLeases([answer.prune_lease_token], "Shortcut artwork", (signal) =>
    setSlotImage(appId, answer, JOB_SLOT_ASSET_TYPES[slot], signal, left),
  );
  if (outcome !== null) await reportSlotOutcome(romId, slot, outcome, answer);
  return outcome === "set";
}

export async function cancelArtworkApply(appId: number): Promise<void> {
  artworkGenerations.set(appId, (artworkGenerations.get(appId) ?? 0) + 1);
  await releasePruneLeasesByOwner(`artwork:${appId}`);
}
