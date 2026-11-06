/**
 * Every **Steam internal** (`GLOSSARY.md`) the panel reaches: the panel's half
 * of the one list, the backend's half being `backend/domain/steam_internals.py`.
 * How a path is spelled, how `steamInternals.test.ts` holds the list to the
 * code, and what the start-up check asks of each entry
 * (`steamInternalsCheck.ts`) is `docs/architecture/frontend-bundles.md`'s,
 * "Every Steam internal Tender reaches".
 *
 * The module holds data and nothing else: it imports only a type, so a script
 * can read it without the panel's build.
 */

import type { AbsenceCost } from "./steamModules";

/**
 * What the reader loses, in the words the notice on Main names it with.
 *
 * Closed, and shared with the start-up check's `STEAM_LOOKUPS`, so the notice
 * names one loss once whichever check found it missing.
 */
export const STEAM_FEATURES = {
  shortcuts: "adding, updating and removing Steam shortcuts",
  launch: "starting a game",
  artwork: "setting artwork",
  thumbnails: "artwork previews and carrying images set in Steam over",
  sessions: "playtime and the save sync after a game",
  library: "game details in Steam's library",
  collections: "Steam collections",
  cleanup: "the cleanup of removed games",
  hidden: "keeping a game hidden when the sync merges its shortcuts",
  deviceName: "the device name in collection names",
  running: "telling whether a game is running",
  resume: "Resume for a running game",
  properties: "Properties in a game's Steam menu",
  restart: "restarting Steam from Tender",
  notifications: "Tender's notifications",
  notificationLook: "the look of Tender's notifications",
  gamePage: "Tender's section on a game page",
  buttonPictures: "controller button icons",
  quickAccess: "Tender's Quick Access entry",
  desktop: "the desktop surface (dev build)",
} as const;

/** One of {@link STEAM_FEATURES}. */
export type SteamFeature = keyof typeof STEAM_FEATURES;

/**
 * How the start-up check asks after an entry's static part — the path up to its
 * first `()`, `[]` or `(param)`.
 *
 * - `call` — it must be a function: the code calls it.
 * - `read` — it must be there: the code reads it, or takes its elements.
 * - `{ notAtStartUp }` — nothing can be asked at start-up, for the reason
 *   given: a route or an address with nothing to look up, or a value that is
 *   Steam's runtime state.
 */
export type AskedAs = "call" | "read" | { readonly notAtStartUp: string };

/** What a missing internal costs, and the losses the notice names for it. */
export type InternalCost =
  | {
      /** Never `panel`: no missing Steam internal keeps the panel off the air (#2509 D3). */
      readonly absenceCost: Exclude<AbsenceCost, "panel" | "diagnostic">;
      readonly features: readonly [SteamFeature, ...SteamFeature[]];
    }
  | { readonly absenceCost: "diagnostic"; readonly features: readonly [] };

/** One Steam internal, and what its absence costs. */
export type SteamInternal = InternalCost & {
  readonly path: string;
  readonly asked: AskedAs;
  /** Reached only by the desktop surface, so asked only in the build that carries it. */
  readonly devBuildOnly?: true;
  /**
   * Names the alternatives this entry is one of: the code falls back from one
   * to another, so the group counts as there while any one of them is, and an
   * alternative nothing can ask at start-up counts as there.
   */
  readonly oneOf?: string;
  /** The files that reach it where no type says so — a string key, a `Record`, an `unknown`, a route; absent for a typed reach. */
  readonly untypedIn?: readonly string[];
};

const cost = (...features: [SteamFeature, ...SteamFeature[]]): InternalCost => ({ absenceCost: "feature", features });
const DIAGNOSTIC: InternalCost = { absenceCost: "diagnostic", features: [] };

const SHORTCUTS = cost("shortcuts");
const LAUNCH = cost("launch");
const ARTWORK = cost("artwork");
// The thumbnails fall back to a placeholder, but the shortcut carry-over reads the
// same URLs for an image set in Steam (`utils/shortcutCarryOver.ts`), and that
// image is lost without them: a feature, not only an appearance.
const THUMBNAILS = cost("thumbnails");
const SESSIONS = cost("sessions");
const LIBRARY = cost("library");
const COLLECTIONS = cost("collections");
const CLEANUP = cost("cleanup");
const HIDDEN = cost("hidden");
const DEVICE_NAME = cost("deviceName");
const RUNNING = cost("running");
const RESUME = cost("resume");
const NOTIFICATIONS = cost("notifications");
// What `globals.js` reports on whether Steam had finished starting, read only
// into the injector's log line (`backend/host/inject/bootstrap.py`).
const STEAM_READY = DIAGNOSTIC;
const DESKTOP = { ...cost("desktop"), devBuildOnly: true } as const;

const ADDRESS = { notAtStartUp: "an address Steam answers, with nothing to look up" } as const;
const liveState = (why: string): AskedAs => ({ notAtStartUp: why });

export const STEAM_INTERNALS: readonly SteamInternal[] = [
  { path: "SteamClient.Apps.AddShortcut", asked: "call", ...SHORTCUTS },
  { path: "SteamClient.Apps.RemoveShortcut", asked: "call", ...SHORTCUTS },
  { path: "SteamClient.Apps.SetShortcutName", asked: "call", ...SHORTCUTS },
  { path: "SteamClient.Apps.SetShortcutExe", asked: "call", ...SHORTCUTS },
  { path: "SteamClient.Apps.SetShortcutStartDir", asked: "call", ...SHORTCUTS },
  { path: "SteamClient.Apps.SetShortcutIcon", asked: "call", ...ARTWORK },
  { path: "SteamClient.Apps.SetAppLaunchOptions", asked: "call", ...LAUNCH },
  { path: "SteamClient.Apps.RegisterForAppDetails", asked: "call", ...cost("launch", "shortcuts") },
  { path: "SteamClient.Apps.OpenAppSettingsDialog", asked: "call", ...cost("properties") },
  { path: "SteamClient.Apps.SetCustomArtworkForApp", asked: "call", ...ARTWORK },
  { path: "SteamClient.Apps.ClearCustomArtworkForApp", asked: "call", ...ARTWORK },
  { path: "SteamClient.Apps.RunGame", asked: "call", ...LAUNCH },
  { path: "SteamClient.Apps.RegisterForGameActionStart", asked: "call", ...LAUNCH },
  { path: "SteamClient.Apps.CancelGameAction", asked: "call", ...LAUNCH },
  { path: "SteamClient.GameSessions.RegisterForAppLifetimeNotifications", asked: "call", ...SESSIONS },
  { path: "SteamClient.System.GetSystemInfo", asked: "call", ...DEVICE_NAME },
  { path: "SteamClient.User.StartRestart", asked: "call", ...cost("restart") },
  { path: "SteamClient.Apps.RegisterForAppDetails().unregister", asked: "call", ...cost("launch", "sessions") },
  {
    path: "SteamClient.GameSessions.RegisterForAppLifetimeNotifications(callback)(update).unAppID",
    asked: "call",
    ...SESSIONS,
  },
  {
    path: "SteamClient.GameSessions.RegisterForAppLifetimeNotifications(callback)(update).bRunning",
    asked: "call",
    ...SESSIONS,
  },
  { path: "SteamClient.System.GetSystemInfo().sHostname", asked: "call", ...DEVICE_NAME },

  { path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strLaunchOptions", asked: "call", ...LAUNCH },
  { path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).LaunchOptions", asked: "call", ...LAUNCH },
  { path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strShortcutExe", asked: "call", ...SHORTCUTS },
  { path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strDisplayName", asked: "call", ...CLEANUP },
  {
    path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strShortcutStartDir",
    asked: "call",
    ...CLEANUP,
  },

  { path: "appStore.GetAppOverviewByAppID", asked: "call", ...cost("library", "collections", "launch") },
  { path: "appStore.GetCustomVerticalCapsuleURLs", asked: "call", ...THUMBNAILS },
  { path: "appStore.GetCustomLandcapeImageURLs", asked: "call", ...THUMBNAILS },
  { path: "appStore.GetCustomHeroImageURLs", asked: "call", ...THUMBNAILS },
  { path: "appStore.GetCustomLogoImageURLs", asked: "call", ...THUMBNAILS },
  { path: "appStore.GetAppOverviewByAppID().appid", asked: "call", ...COLLECTIONS },
  { path: "appStore.GetAppOverviewByAppID().display_name", asked: "call", ...LIBRARY },
  { path: "appStore.GetAppOverviewByAppID().strDisplayName", asked: "call", ...LIBRARY },
  { path: "appStore.GetAppOverviewByAppID().controller_support", asked: "call", ...LIBRARY },
  { path: "appStore.GetAppOverviewByAppID().metacritic_score", asked: "call", ...LIBRARY },
  { path: "appStore.GetAppOverviewByAppID().minutes_playtime_forever", asked: "call", ...LIBRARY },
  { path: "appStore.GetAppOverviewByAppID().minutes_playtime_last_two_weeks", asked: "call", ...CLEANUP },
  { path: "appStore.GetAppOverviewByAppID().rt_last_time_played", asked: "call", ...LIBRARY },
  { path: "appStore.GetAppOverviewByAppID().m_setStoreCategories", asked: "call", ...LIBRARY },
  { path: "appStore.GetAppOverviewByAppID().GetGameID", asked: "call", ...LAUNCH },
  { path: "__mobxGlobals.allowStateChanges", asked: "read", ...LIBRARY },

  {
    path: "collectionStore.deckDesktopApps.apps",
    asked: liveState("Steam fills the collection after it starts, so it can be empty or absent at first"),
    ...cost("shortcuts", "cleanup"),
  },
  { path: "collectionStore.userCollections", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.NewUnsavedCollection", asked: "call", ...COLLECTIONS },
  { path: "collectionStore.BIsHidden", asked: "call", ...HIDDEN },
  { path: "collectionStore.SetAppsAsHidden", asked: "call", ...HIDDEN },
  { path: "collectionStore.userCollections[].AsDragDropCollection", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].Save", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].Delete", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].allApps", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].apps.has", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].displayName", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].id", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].AsDragDropCollection().AddApps", asked: "read", ...COLLECTIONS },
  { path: "collectionStore.userCollections[].AsDragDropCollection().RemoveApps", asked: "read", ...COLLECTIONS },

  { path: "SteamUIStore.RunningApps", asked: "read", ...RUNNING },
  {
    path: "SteamUIStore.RunningApps[].local_per_client_data.display_status",
    asked: "read",
    ...RUNNING,
    untypedIn: ["utils/runningApps.ts"],
  },
  { path: "SteamUIStore.RunningApps[].appid", asked: "read", ...RUNNING, untypedIn: ["utils/runningApps.ts"] },
  { path: "SteamUIStore.RunningApps[].display_name", asked: "read", ...RUNNING, untypedIn: ["utils/runningApps.ts"] },
  {
    path: "SteamUIStore.RunningApps[].strDisplayName",
    asked: "read",
    ...RUNNING,
    untypedIn: ["utils/runningApps.ts"],
  },
  { path: "SteamUIStore.SetRunningApp", asked: "call", ...RESUME },
  // Resume navigates through the store where it can and to the route where it
  // cannot (`utils/runningGame.ts`), so neither alone is a loss.
  {
    path: "/apprunning",
    asked: { notAtStartUp: "a route of Steam's router" },
    ...RESUME,
    oneOf: "resume navigation",
    untypedIn: ["utils/runningGame.ts"],
  },
  { path: "SteamUIStore.NavigateToRunningApp", asked: "call", ...RESUME, oneOf: "resume navigation" },
  { path: "SteamUIStore.MainInstanceUIMode", asked: "read", ...LAUNCH },

  { path: "NotificationStore", asked: "read", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.RemoveGroupFromTray", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.m_nNextTestNotificationID", asked: "read", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).bNewIndicator", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).data", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).eSource", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).eType", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).nNotificationID", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).nToastDurationMS", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).notificationID", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(notification).rtCreated", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.RemoveGroupFromTray(group).eType", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.RemoveGroupFromTray(group).notifications", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(info).eFeature", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(info).fnTray", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(info).playSound", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(info).showToast", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(info).sound", asked: "call", ...NOTIFICATIONS },
  { path: "NotificationStore.ProcessNotification(info).toastDurationMS", asked: "call", ...NOTIFICATIONS },
  // What a toast waits on before it is raised (`utils/steamReadyForToasts.ts`),
  // the getter asked where the wait is missing: without both, every toast waits
  // out the deadline.
  {
    path: "App.WaitForServicesInitialized",
    asked: "call",
    ...NOTIFICATIONS,
    oneOf: "services initialised",
    untypedIn: ["utils/steamReadyForToasts.ts"],
  },
  {
    path: "App.GetServicesInitialized",
    asked: "call",
    ...NOTIFICATIONS,
    oneOf: "services initialised",
    untypedIn: ["utils/steamReadyForToasts.ts"],
  },
  {
    path: "securitystore.IsLockScreenActive",
    asked: "call",
    ...NOTIFICATIONS,
    untypedIn: ["utils/steamReadyForToasts.ts"],
  },
  {
    path: "SteamUIStore.WindowStore.GamepadUIMainWindowInstance.BrowserWindow",
    asked: liveState("Big Picture's main window, which the desktop client does not have"),
    ...NOTIFICATIONS,
    untypedIn: ["utils/steamReadyForToasts.ts"],
  },

  { path: "App", asked: "read", ...STEAM_READY },
  // `boot/steamGlobals.ts` asks the second where the first is missing.
  { path: "App.BFinishedInitBeforeLogin", asked: "call", ...STEAM_READY, oneOf: "init finished" },
  { path: "App.BFinishedInitStageOne", asked: "call", ...STEAM_READY, oneOf: "init finished" },
  { path: "webpackChunksteamui", asked: "read", ...cost("gamePage") },
  { path: "steamloopback.host/customimages", asked: ADDRESS, ...THUMBNAILS, untypedIn: ["utils/artworkThumbs.ts"] },

  { path: "g_PopupManager", asked: "read", ...DESKTOP },
  { path: "g_PopupManager.AddPopupCreatedCallback", asked: "call", ...DESKTOP },
  { path: "g_PopupManager.AddPopupDestroyedCallback", asked: "call", ...DESKTOP },
  { path: "g_PopupManager.GetPopups", asked: "call", ...DESKTOP },
  { path: "g_PopupManager.GetPopups()[].m_strName", asked: "call", ...DESKTOP },
  { path: "g_PopupManager.GetPopups()[].m_popup", asked: "call", ...DESKTOP },
  {
    path: "SP Desktop",
    asked: { notAtStartUp: "the name of the desktop client's window, which may not be open" },
    ...DESKTOP,
    untypedIn: ["desktop/desktopWindow.ts"],
  },
  { path: "MainWindowBrowserManager", asked: "read", ...DESKTOP },
  {
    path: "MainWindowBrowserManager.m_lastLocation.pathname",
    asked: liveState("where the desktop client is, which changes with every page"),
    ...DESKTOP,
  },
  {
    path: "/library/app/",
    asked: { notAtStartUp: "a route of Steam's router" },
    ...DESKTOP,
    untypedIn: ["desktop/navigationWatcher.ts"],
  },
];

/**
 * What a cast of the window reaches that is not Steam's, by path prefix, with
 * the reason. The prefix covers the path and every path below it.
 */
export const NOT_STEAM_INTERNALS: Readonly<Record<string, string>> = {
  SP_REACT: "installed by Tender's globals bundle or Decky Loader; Steam does not define it",
  SP_REACTDOM: "installed by Tender's globals bundle or Decky Loader; Steam does not define it",
  SP_JSX: "installed by Tender's globals bundle or Decky Loader; Steam does not define it",
  DFL: "Decky Loader's",
  DeckyPluginLoader: "Decky Loader's",
  __TENDER_INSTALL_GLOBALS: "Tender's own",
  MutationObserver: "the DOM's, taken from the window it observes",
  ResizeObserver: "the DOM's, taken from the window it observes",
};
