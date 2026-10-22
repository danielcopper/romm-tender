/**
 * SGDB artwork apply — writes a ROM's SGDB hero, logo, wide grid and icon onto
 * its Steam shortcut. Shared by the game page — `useAutoArtwork`'s fill of the
 * empty slots on a first visit and replacing apply after a version switch,
 * RomMPlaySection's Refresh Artwork and SgdbGamePickerModal's change of game
 * (both on the slots on automatic only), and the ARTWORK tab's one-slot writes —
 * so it lives here rather than on any one component to keep their import graph
 * acyclic. The shortcut artwork job's
 * writes (`utils/shortcutArtwork.ts`) ask it whether an apply is under way, and
 * set the job's wide grids and logos through it.
 */

import {
  getArtworkBase64,
  getArtworkSlots,
  getSgdbArtworkBase64,
  getSgdbSlotArtwork,
  isEndpointFailure,
  reportSgdbSlotArtwork,
  saveShortcutIcon,
  debugLog,
  type ArtworkSlotImage,
  type ArtworkTabSlot,
  type EndpointFailure,
  type SavedShortcutIcon,
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
  type PruneLeaseAdmission,
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
 * Have the backend write the icon the artwork cache holds for *romId* into Steam's
 * grid directory as *appId*'s. `null` when the write is refused, which is logged,
 * or the apply was cancelled meanwhile.
 */
async function saveIcon(romId: number, appId: number, signal: AbortSignal): Promise<SavedShortcutIcon | null> {
  const saved = await saveShortcutIcon(appId, romId);
  if (isEndpointFailure(saved)) {
    detach(debugLog(`Artwork: the icon for appId ${appId} was not written: ${saved.message}`));
    return null;
  }
  return isPruneLeaseCancelled(signal) ? null : saved;
}

/** Fetch SGDB artwork (hero, logo, wide grid, icon) and apply to Steam, replacing
 *  what each slot holds. Returns count of successfully applied images, -1
 *  when no SGDB API key is configured, or the refusal of an image's fetch, in
 *  which case nothing is applied. */
export async function applyArtwork(romId: number, appId: number): Promise<number | EndpointFailure> {
  return whileInFlight(appId, () => applySgdbArtwork(romId, appId, ALL_SGDB_SLOTS));
}

/** The four slots Tender fills from SteamGridDB. */
const ALL_SGDB_SLOTS: ReadonlySet<RecordedSlot> = new Set(["hero", "logo", "grid", "icon"]);

/**
 * `applyArtwork` for the slots on automatic alone: a slot the user chose and one set in Steam stay, as Refresh Artwork
 * and a change of SteamGridDB game leave them. The slots' state is read first; where it cannot be read, nothing is
 * applied and the read's failure is answered.
 */
export async function applyAutomaticArtwork(romId: number, appId: number): Promise<number | EndpointFailure> {
  const read = await getArtworkSlots(romId, appId).catch((e): EndpointFailure => {
    detach(debugLog(`applyAutomaticArtwork: the slots of appId ${appId} could not be read: ${e}`));
    return { success: false, reason: "server_unreachable", message: "The artwork slots could not be read" };
  });
  if (isEndpointFailure(read)) return read;
  const automatic = new Set(
    read.slots
      .filter((row) => row.state === "automatic" && row.slot !== "cover")
      .map((row) => row.slot as RecordedSlot),
  );
  return whileInFlight(appId, () => applySgdbArtwork(romId, appId, automatic));
}

/** Give the shortcut SteamGridDB's hero, logo and wide grid where Steam's grid
 *  directory has no image for the slot, and its icon where the shortcut shows
 *  none but Tender's placeholder or logo icon. A slot that already has one is
 *  left alone, whoever set it. Stops asking once SteamGridDB has refused the
 *  key. Returns the count of images applied, or -1 when no SGDB API key is
 *  configured. */
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

async function applySgdbArtwork(
  romId: number,
  appId: number,
  slots: ReadonlySet<RecordedSlot>,
): Promise<number | EndpointFailure> {
  const leaseOwner = `artwork:${appId}`;
  mountPruneLeaseOwner(leaseOwner);
  const admission = capturePruneLeaseAdmission(leaseOwner);
  const superseded = claimGeneration(appId);

  const refusals: EndpointFailure[] = [];
  const fetchArtwork = async (assetType: number, slot: RecordedSlot): Promise<SgdbArtwork> => {
    if (!slots.has(slot)) return NO_IMAGE;
    const answer = await getSgdbArtworkBase64(romId, assetType).catch(() => NO_IMAGE);
    if (!isEndpointFailure(answer)) return answer;
    refusals.push(answer);
    return NO_IMAGE;
  };
  const fetchIcon = async (): Promise<SgdbSlotArtwork> => {
    if (!slots.has("icon")) return { status: "skip" };
    const answer = await getSgdbSlotArtwork(romId, appId, "icon", true).catch((e): SgdbSlotArtwork => {
      detach(debugLog(`applyArtwork: icon fetch for appId ${appId} rejected: ${e}`));
      return { status: "failed" };
    });
    if (!isEndpointFailure(answer)) return answer;
    refusals.push(answer);
    return { status: "failed" };
  };
  const [results, icon] = await Promise.all([
    Promise.all([fetchArtwork(1, "hero"), fetchArtwork(2, "logo"), fetchArtwork(3, "grid")]),
    fetchIcon(),
  ]);

  const leaseTokens = [...results.map((result) => result.prune_lease_token), leaseOf(icon)];
  if (results.some((r) => r.no_api_key) || icon.status === "no_api_key") {
    return withPruneLeases(leaseTokens, "Artwork apply", async () => -1, leaseOwner, admission);
  }
  const refusal = refusals[0];
  if (refusal) {
    return withPruneLeases(leaseTokens, "Artwork apply", () => Promise.resolve(refusal), leaseOwner, admission);
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
      // Each is recorded as the fill path records it. The awaits are sequential
      // by design, one image at a time; S9382 is raised on the slot's await
      // line, so its NOSONAR stays there.
      const customArt: Array<[answer: SgdbArtwork, assetType: number, slot: RecordedSlot]> = [
        [results[0], 1, "hero"],
        [results[1], 2, "logo"],
        [results[2], 3, "grid"],
      ];
      for (const [answer, assetType, slot] of customArt) {
        const base64 = answer.base64;
        if (!base64) continue;
        if (isPruneLeaseCancelled(signal)) return applied;
        if (superseded()) return bail();
        const write = () => SteamClient.Apps.SetCustomArtworkForApp(appId, base64, "png", assetType);
        await setReplacedSlot(romId, slot, answer, write); // NOSONAR(typescript:S9382)
        applied++;
      }

      if (icon.status === "ready") {
        if (isPruneLeaseCancelled(signal)) return applied;
        if (superseded()) return bail();
        if (await setReplacedIcon(romId, appId, signal)) applied++;
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

/** Write the replacing apply's icon, point the shortcut at it and record what Steam made of it; answers whether it was written. */
async function setReplacedIcon(romId: number, appId: number, signal: AbortSignal): Promise<boolean> {
  const saved = await saveIcon(romId, appId, signal);
  if (!saved) return false;
  await setReplacedSlot(romId, "icon", saved, () => SteamClient.Apps.SetShortcutIcon(appId, saved.icon_path));
  return true;
}

/** The lease an answer of the per-slot fetch carries for its Steam write, if any. */
function leaseOf(answer: SgdbSlotArtwork): string | undefined {
  return answer.status === "image" || answer.status === "ready" ? answer.prune_lease_token : undefined;
}

/** The slots whose outcome the game page reports. */
type RecordedSlot = SgdbArtworkSlot;

/** The slots whose outcome the ARTWORK tab reports: the four above, and a cover chosen from SteamGridDB. */
type ReportedSlot = RecordedSlot | "cover";

/** The slots the shortcut artwork job hands the frontend to set; it sets the icons it wrote itself. */
type JobSlot = "grid" | "logo";

/** What the backend answered with an image, passed back when what Steam made of it is reported. */
type SlotRecord = { sgdb_id?: number | null; image_id?: number | null; sha256?: string | null };

/**
 * Set one image of the replacing apply through *write* and record what Steam
 * made of it; a refusal is recorded and then rethrown.
 */
async function setReplacedSlot(
  romId: number,
  slot: ReportedSlot,
  answer: SlotRecord,
  write: () => unknown,
): Promise<void> {
  try {
    await write();
  } catch (e) {
    await reportReplacedSlot(romId, slot, "refused", answer);
    throw e;
  }
  await reportReplacedSlot(romId, slot, "set", answer);
}

/** Report what Steam made of an image the replacing apply set, as the fill path reports it. */
async function reportReplacedSlot(
  romId: number,
  slot: ReportedSlot,
  outcome: "set" | "refused",
  answer: SlotRecord,
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

/** The slots the game page fills only while empty through `SetCustomArtworkForApp`, each with Steam's asset type for it. */
const FILLED_SLOTS: ReadonlyArray<[slot: Exclude<SgdbArtworkSlot, "icon">, assetType: number]> = [
  ["hero", 1],
  ["logo", 2],
  ["grid", 3],
];

/** Steam's asset type for each slot the shortcut artwork job hands the frontend to set. */
const JOB_SLOT_ASSET_TYPES: Readonly<Record<JobSlot, number>> = { logo: 2, grid: 3 };

type SlotImage = Extract<SgdbSlotArtwork, { status: "image" }>;

type SlotToSet = Extract<SgdbSlotArtwork, { status: "image" | "ready" }>;

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
    const answer = await fetchSlotToFill(romId, appId, slot);
    if (answer === "no_api_key") return -1;
    if (answer === "key_refused") return applied;
    if (answer === "next" || answer.status !== "image") continue;
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
    if (outcome !== null) await reportSlotOutcome(romId, slot, outcome, answer); // NOSONAR(typescript:S9382)
  }

  return applied + Number(await fillIcon(romId, appId, superseded, leaseOwner, admission));
}

/**
 * Give the first visit's fill its icon where the shortcut shows none but
 * Tender's stand-ins, unless overtaken, and report what Steam made of it;
 * answers whether it was set.
 */
async function fillIcon(
  romId: number,
  appId: number,
  superseded: () => boolean,
  leaseOwner: string,
  admission: PruneLeaseAdmission,
): Promise<boolean> {
  if (superseded()) return false;
  const answer = await fetchSlotToFill(romId, appId, "icon");
  if (typeof answer === "string" || answer.status !== "ready") return false;
  return withPruneLeases(
    [answer.prune_lease_token],
    "Artwork apply",
    async (signal) => {
      if (isPruneLeaseCancelled(signal) || superseded()) return false;
      const saved = await saveIcon(romId, appId, signal);
      if (!saved) return false;
      let outcome: "set" | "refused" = "set";
      try {
        SteamClient.Apps.SetShortcutIcon(appId, saved.icon_path);
      } catch (e) {
        detach(debugLog(`Artwork: Steam refused the icon for appId ${appId}: ${e}`));
        outcome = "refused";
      }
      await reportSlotOutcome(romId, "icon", outcome, saved);
      return outcome === "set";
    },
    leaseOwner,
    admission,
  );
}

/**
 * One slot's answer for the first visit's fill: the image to set (or, for the
 * icon, the answer that the cache holds it), `next` for a slot with nothing to
 * set (a rejected or refused fetch is logged), or the answer that ends the fill.
 */
async function fetchSlotToFill(
  romId: number,
  appId: number,
  slot: SgdbArtworkSlot,
): Promise<SlotToSet | "next" | "no_api_key" | "key_refused"> {
  const answer = await getSgdbSlotArtwork(romId, appId, slot).catch((e): SgdbSlotArtwork => {
    detach(debugLog(`fillEmptyArtwork: ${slot} fetch for appId ${appId} rejected: ${e}`));
    return { status: "failed" };
  });
  if (isEndpointFailure(answer)) {
    detach(debugLog(`fillEmptyArtwork: ${slot} fetch for appId ${appId} refused: ${answer.message}`));
    return "next";
  }
  if (answer.status === "image" || answer.status === "ready") return answer;
  if (answer.status === "no_api_key" || answer.status === "key_refused") return answer.status;
  return "next";
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

/**
 * Report what Steam made of an image for the backend to record, passing back the SteamGridDB ids and the hash the
 * backend answered with it; a report that fails is logged.
 */
export async function reportSlotOutcome(
  romId: number,
  slot: RecordedSlot,
  outcome: "set" | "refused",
  image: { sgdb_id: number | null; image_id: number | null; sha256: string },
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
  slot: JobSlot,
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

/** Steam's asset type for each slot `SetCustomArtworkForApp` writes; the icon goes through `SetShortcutIcon`. */
export const STEAM_ASSET_TYPES: Readonly<Record<Exclude<ArtworkTabSlot, "icon">, number>> = {
  cover: 0,
  hero: 1,
  logo: 2,
  grid: 3,
};

/** What setting a version's chosen cover came to: set, none chosen, or chosen but not to be had now. */
export type ChosenCover = "set" | "none" | "unavailable";

/**
 * Set the cover the user chose from SteamGridDB for *romId*, as a version switch does (#2381 D16), and report what Steam
 * made of it; *onCover* hears the image first. `none` leaves the caller to set RomM's; `unavailable` — a chosen cover
 * that cannot be served now — means no cover is to be written over it.
 */
export async function setChosenCover(
  romId: number,
  appId: number,
  onCover?: (base64: string) => void,
): Promise<ChosenCover> {
  const answer = await getSgdbArtworkBase64(romId, STEAM_ASSET_TYPES.cover).catch(() => NO_IMAGE);
  if (isEndpointFailure(answer)) return "none";
  if (!answer.base64) return answer.chosen === true ? "unavailable" : "none";
  const base64 = answer.base64;
  onCover?.(base64);
  await withPruneLeases([answer.prune_lease_token], "Version switch", async (signal) => {
    if (isPruneLeaseCancelled(signal)) return;
    const write = () => SteamClient.Apps.SetCustomArtworkForApp(appId, base64, "png", STEAM_ASSET_TYPES.cover);
    await setReplacedSlot(romId, "cover", answer, write).catch((e: unknown) => {
      detach(debugLog(`Version switch: Steam refused the chosen cover for appId ${appId}: ${e}`));
    });
  });
  return "set";
}

/**
 * Remove the image set through Steam for *appId*'s slot *assetType* before Tender sets its own (#2381 D13): Steam
 * shows a `.jpg` before a `.png` (D14), so Tender's image would not show while Steam's stays. What Steam's call
 * removes is not measured yet; if it leaves the hand-set file, this is the one place that changes.
 */
export async function removeImageSetInSteam(appId: number, assetType: number): Promise<void> {
  await SteamClient.Apps.ClearCustomArtworkForApp(appId, assetType);
}

/** What setting an image from the ARTWORK tab came to. */
export type TabSlotOutcome = "set" | "refused" | "none" | "left";

/**
 * Set the image the ARTWORK tab's pick or "Automatic" answered for *slot*, under the answer's lease and the
 * newest-apply-wins guard, and report what Steam made of it. *replaceSetInSteam* removes the image set through Steam
 * first — only after the user confirmed it, and never for the icon, which is repointed and whose file stays. `none`
 * where there was nothing to set, `refused` where Steam did not take it (reported, so a chosen image's choice goes,
 * #2381 D15), `left` where the apply was cancelled or overtaken.
 */
export async function applyTabSlotImage(
  romId: number,
  appId: number,
  slot: ArtworkTabSlot,
  answer: ArtworkSlotImage,
  replaceSetInSteam: boolean,
): Promise<TabSlotOutcome> {
  if (answer.status === "none") return "none";
  const leaseOwner = `artwork:${appId}`;
  mountPruneLeaseOwner(leaseOwner);
  const admission = capturePruneLeaseAdmission(leaseOwner);
  const superseded = claimGeneration(appId);
  return whileInFlight(appId, () =>
    withPruneLeases(
      [answer.prune_lease_token],
      "Artwork apply",
      async (signal): Promise<TabSlotOutcome> => {
        if (isPruneLeaseCancelled(signal) || superseded()) return "left";
        if (answer.status === "ready" || slot === "icon") {
          return (await setReplacedIcon(romId, appId, signal)) ? "set" : "refused";
        }
        const image = answer.status === "cover" ? await getArtworkBase64(romId) : answer;
        if (!image.base64) return "refused";
        if (isPruneLeaseCancelled(signal) || superseded()) return "left";
        const assetType = STEAM_ASSET_TYPES[slot];
        if (replaceSetInSteam) await removeImageSetInSteam(appId, assetType);
        const base64 = image.base64;
        const write = () => SteamClient.Apps.SetCustomArtworkForApp(appId, base64, "png", assetType);
        try {
          await (answer.status === "cover" ? write() : setReplacedSlot(romId, slot, answer, write));
        } catch (e) {
          detach(debugLog(`Artwork: Steam refused the ${slot} for appId ${appId}: ${e}`));
          return "refused";
        }
        return "set";
      },
      leaseOwner,
      admission,
    ),
  );
}

export async function cancelArtworkApply(appId: number): Promise<void> {
  artworkGenerations.set(appId, (artworkGenerations.get(appId) ?? 0) + 1);
  await releasePruneLeasesByOwner(`artwork:${appId}`);
}
