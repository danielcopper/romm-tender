/**
 * Defensive running-app detection.
 *
 * `SteamUIStore.RunningApps` is the running-app surface — a genuine ambient
 * Steam SP global (declared in `types/steam.d.ts`), and the only one there is.
 * Do not add a second: `Router` is not a page global on any SteamUI build (this
 * reader probed it until #1588 and every logged round said `no-Router`), and
 * `@decky/ui`'s `Router` export resolves to this very `SteamUIStore` singleton.
 *
 * The list is a MEMBERSHIP set, not a launch order. The store maps its private
 * running-appid array through the app store and DROPS entries whose overview has
 * not loaded yet, so the head of `RunningApps` is not even reliably Steam's own
 * `MainRunningApp` — the two diverge exactly during the post-launch window
 * Tender cares about. And the order it carries is not a launch order:
 * `SetRunningApp` removes and unshifts — Steam's own Play calls it for the game
 * it starts, where another is listed — while the reconciler that notices a
 * newly-launched process appends it at the tail. So the head may or may not be
 * the app that just started, and nothing reads it to identify one — use the
 * appid the lifetime notification carries. Consumers here ask membership
 * questions only.
 *
 * The read is still guarded. A bare reference to a truly-absent SP global
 * throws `ReferenceError`, so presence is probed with `typeof`; a present store
 * may be `null`, expose a throwing getter, or hand back a plain array or a MobX
 * observable (array-like/iterable). Any of those degrades to "nothing running",
 * never a throw out of the reader.
 *
 * The store can also legitimately report EMPTY while a game is running: it
 * reported `RunningApps` empty for several seconds with the game still up —
 * measured when Decky Loader's `plugin_loader` restarted (#1054 / #1148 round 2
 * device evidence) — and a JS-context rebuild under a running game is guarded
 * the same way. So a single empty round proves nothing — the adoption path polls,
 * and every round reports what the store said (`diagnostics`: absent / empty /
 * threw / every listed entry with its status) so the on-device log can tell
 * those cases apart.
 *
 * Being listed is not being running: Steam lists a game it is only starting,
 * and can keep one listed long after it exited. So an entry counts only by its
 * overview's display status, read three ways — whether a game runs
 * ({@link readRunningApps}), whether one runs or is starting
 * ({@link readRunningOrStartingApps}), and whether one holds a restart of Steam
 * ({@link isAnyAppHolding}):
 * `docs/architecture/save-file-sync-architecture.md`, "Is the game running".
 */

export interface RunningApp {
  appid: number;
  display_name: string;
}

export interface RunningAppsReading {
  /** The listed apps the reading counts this round, in store order. */
  apps: RunningApp[];
  /** The appids among {@link apps} that count only because their display status could not be read. */
  statusUnread: ReadonlySet<number>;
  /** Diagnostic — what the store reported this round, every listed entry with its display status. */
  diagnostics: string;
}

const SOURCE_LABEL = "SteamUIStore.RunningApps";

/**
 * The values of Steam's `EDisplayStatus` under which a listed entry holds a
 * restart of Steam; Running alone makes a game run, and Launching or Running a
 * game that runs or is starting. The backend's waits name
 * the values they hold for in `backend/host/inject/recovery.py`, and
 * `tests/host/inject/test_recovery.py` holds the two equal.
 */
const DISPLAY_STATUSES_THAT_HOLD = { Launching: 1, Running: 4, Terminating: 36 };

/** One entry the store lists, with its display status — `null` where it cannot be read. */
interface ListedApp {
  app: RunningApp;
  status: number | null;
}

/** The overview's `local_per_client_data.display_status`, or `null` where it cannot be read. */
function readDisplayStatus(rec: Record<string, unknown>): number | null {
  try {
    const data = rec.local_per_client_data;
    if (typeof data !== "object" || data === null) return null;
    const status = (data as Record<string, unknown>).display_status;
    return typeof status === "number" ? status : null;
  } catch {
    return null;
  }
}

/** Coerce one candidate into a {@link ListedApp}, or `null` if it isn't one. */
function coerceListedApp(value: unknown): ListedApp | null {
  if (typeof value !== "object" || value === null) return null;
  const rec = value as Record<string, unknown>;
  const appid = rec.appid;
  if (typeof appid !== "number") return null;
  const name =
    typeof rec.display_name === "string"
      ? rec.display_name
      : typeof rec.strDisplayName === "string"
        ? rec.strDisplayName
        : "";
  return { app: { appid, display_name: name }, status: readDisplayStatus(rec) };
}

/**
 * Coerce a list-shaped source (plain array or MobX observable) into listed
 * apps, dropping any entry that isn't an app. Never throws — a non-iterable or
 * a throwing iterator yields an empty list.
 */
function coerceListedAppList(value: unknown): ListedApp[] {
  if (value === null || value === undefined) return [];
  let items: unknown[];
  if (Array.isArray(value)) {
    items = value;
  } else if (typeof (value as { [Symbol.iterator]?: unknown })[Symbol.iterator] === "function") {
    items = Array.from(value as Iterable<unknown>);
  } else {
    return [];
  }
  const listed: ListedApp[] = [];
  for (const item of items) {
    const entry = coerceListedApp(item);
    if (entry) listed.push(entry);
  }
  return listed;
}

/**
 * Does a listed entry count as running? One whose status cannot be read does:
 * the save-file-sync page's "Is the game running" says why.
 */
function countsAsRunning(entry: ListedApp): boolean {
  return entry.status === null || entry.status === DISPLAY_STATUSES_THAT_HOLD.Running;
}

/** Does a listed entry run or start? One whose status cannot be read does, as it counts as running. */
function countsAsRunningOrStarting(entry: ListedApp): boolean {
  return countsAsRunning(entry) || entry.status === DISPLAY_STATUSES_THAT_HOLD.Launching;
}

/** Does a listed entry hold a restart of Steam? One whose status cannot be read does, as it counts as running. */
function holds(entry: ListedApp): boolean {
  return entry.status === null || Object.values(DISPLAY_STATUSES_THAT_HOLD).includes(entry.status);
}

/** Diagnostic note for the list — every entry found as `appid:status` (`?` unreadable), or why none were. */
function describeList(listed: ListedApp[], raw: unknown): string {
  if (listed.length > 0) return `[${listed.map((e) => `${e.app.appid}:${e.status ?? "?"}`).join(",")}]`;
  if (raw === null || raw === undefined) return "absent";
  return "empty";
}

/** Every entry the store lists, with a diagnostic naming them. */
interface Listing {
  listed: ListedApp[];
  diagnostics: string;
}

/**
 * Read `SteamUIStore.RunningApps` once. Never throws: an absent store, a `null`
 * store, a throwing getter and a non-list value all read as nothing listed,
 * with a note saying which.
 */
function readListing(): Listing {
  // NOSONAR(typescript:S7741) — SteamUIStore is an undeclared Steam SP global; a
  // direct `=== undefined` would throw ReferenceError when it is genuinely absent.
  if (typeof SteamUIStore === "undefined" || SteamUIStore === null) {
    return { listed: [], diagnostics: `${SOURCE_LABEL}=no-store` };
  }
  try {
    // One getter read — re-reading for the diagnostic could observe a different
    // value, or throw outside the coercion it describes.
    const raw: unknown = SteamUIStore.RunningApps;
    const listed = coerceListedAppList(raw);
    return { listed, diagnostics: `${SOURCE_LABEL}=${describeList(listed, raw)}` };
  } catch (e) {
    return { listed: [], diagnostics: `${SOURCE_LABEL}=threw:${e}` };
  }
}

/**
 * Read the store once, as the apps that count as running plus a diagnostic
 * naming what it listed. Never throws.
 */
export function readRunningApps(): RunningAppsReading {
  return readCounted(countsAsRunning);
}

/**
 * Read the store once, as the apps that run or are starting — reading Launching
 * or Running, or a status that cannot be read — plus a diagnostic naming what it
 * listed. Reload adoption and the stranded panel's card read it: a game Steam
 * reports started can still read Launching, measured on the save-file-sync
 * page's "Is the game running". Never throws.
 */
export function readRunningOrStartingApps(): RunningAppsReading {
  return readCounted(countsAsRunningOrStarting);
}

/** Read the store once, keeping the listed entries `counts` admits. */
function readCounted(counts: (entry: ListedApp) => boolean): RunningAppsReading {
  const { listed, diagnostics } = readListing();
  const counted = listed.filter(counts);
  return {
    apps: counted.map((entry) => entry.app),
    statusUnread: new Set(counted.filter((entry) => entry.status === null).map((entry) => entry.app.appid)),
    diagnostics,
  };
}

/**
 * Does any listed app hold a restart of Steam — reading Launching, Running or
 * Terminating, or a status that cannot be read, as the backend's waits hold?
 * A restart closes Steam and any game with it, one on its way in or out
 * included. Never throws.
 */
export function isAnyAppHolding(): boolean {
  return readListing().listed.some(holds);
}
