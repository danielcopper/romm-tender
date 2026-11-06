/**
 * Every **Steam internal** (`GLOSSARY.md`) the panel reaches: the panel's half
 * of the one list, the backend's half being `backend/domain/steam_internals.py`.
 * How a path is spelled, and how `steamInternals.test.ts` holds the list to the
 * code, is `docs/architecture/frontend-bundles.md`'s, "Every Steam internal
 * Tender reaches".
 *
 * The module holds data and nothing else: it imports nothing, so a script can
 * read it without the panel's build.
 */

/** One Steam internal, and the feature that needs it. */
export interface SteamInternal {
  readonly path: string;
  /** What stops working when this internal is gone. */
  readonly feature: string;
  /** The files that reach it where no type says so — a string key, a `Record`, an `unknown`, a route; absent for a typed reach. */
  readonly untypedIn?: readonly string[];
}

const SHORTCUTS = "creating, renaming and removing Tender's shortcuts";
const LAUNCH_OPTIONS = "writing and confirming a shortcut's launch options, which every start goes through";
const ARTWORK = "setting a shortcut's artwork and icon";
const THUMBNAILS = "the artwork thumbnails on the ARTWORK and GAME INFO tabs";
const LAUNCH_CHECK = "the launch check before a start, and starting a game";
const SESSIONS = "telling when a game starts and stops: playtime, the save sync after a game";
const OVERVIEW = "the game's name, metadata and playtime written into Steam's library";
const SHORTCUT_LIST = "listing Tender's shortcuts: the sync's reconcile, the cleanup, the data page";
const COLLECTIONS = "Steam collections";
const RUNNING = "whether a game is running";
const RESUME = "Resume on a running game";
const TOASTS = "Tender's notifications, raised in Steam's own notification store";
const TOAST_READY = "holding a notification until Steam can show it";
const STEAM_READY = "the globals bundle's report on whether Steam had finished starting";
const DESKTOP = "the desktop surface (dev build only)";

export const STEAM_INTERNALS: readonly SteamInternal[] = [
  { path: "SteamClient.Apps.AddShortcut", feature: SHORTCUTS },
  { path: "SteamClient.Apps.RemoveShortcut", feature: SHORTCUTS },
  { path: "SteamClient.Apps.SetShortcutName", feature: SHORTCUTS },
  { path: "SteamClient.Apps.SetShortcutExe", feature: `${SHORTCUTS}, and moving the launcher` },
  { path: "SteamClient.Apps.SetShortcutStartDir", feature: `${SHORTCUTS}, and moving the launcher` },
  { path: "SteamClient.Apps.SetShortcutIcon", feature: ARTWORK },
  { path: "SteamClient.Apps.SetAppLaunchOptions", feature: LAUNCH_OPTIONS },
  { path: "SteamClient.Apps.RegisterForAppDetails", feature: `${LAUNCH_OPTIONS}; reading a shortcut's details` },
  { path: "SteamClient.Apps.OpenAppSettingsDialog", feature: "the Properties entry of the play row's Steam menu" },
  { path: "SteamClient.Apps.SetCustomArtworkForApp", feature: ARTWORK },
  { path: "SteamClient.Apps.ClearCustomArtworkForApp", feature: "replacing an image set in Steam on the ARTWORK tab" },
  { path: "SteamClient.Apps.RunGame", feature: LAUNCH_CHECK },
  { path: "SteamClient.Apps.RegisterForGameActionStart", feature: LAUNCH_CHECK },
  { path: "SteamClient.Apps.CancelGameAction", feature: LAUNCH_CHECK },
  { path: "SteamClient.GameSessions.RegisterForAppLifetimeNotifications", feature: SESSIONS },
  { path: "SteamClient.System.GetSystemInfo", feature: "the device name in scoped collection names" },
  { path: "SteamClient.User.StartRestart", feature: "Restart Steam, in Settings and on the fallback page" },
  { path: "SteamClient.Apps.RegisterForAppDetails().unregister", feature: `${LAUNCH_OPTIONS}; ${SESSIONS}` },
  { path: "SteamClient.GameSessions.RegisterForAppLifetimeNotifications(callback)(update).unAppID", feature: SESSIONS },
  {
    path: "SteamClient.GameSessions.RegisterForAppLifetimeNotifications(callback)(update).bRunning",
    feature: SESSIONS,
  },
  { path: "SteamClient.System.GetSystemInfo().sHostname", feature: "the device name in scoped collection names" },

  { path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strLaunchOptions", feature: LAUNCH_OPTIONS },
  { path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).LaunchOptions", feature: LAUNCH_OPTIONS },
  {
    path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strShortcutExe",
    feature: "telling Tender's shortcuts by their launcher",
  },
  {
    path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strDisplayName",
    feature: "the removed-game cleanup's snapshot of a shortcut",
  },
  {
    path: "SteamClient.Apps.RegisterForAppDetails(callback)(details).strShortcutStartDir",
    feature: "the removed-game cleanup's snapshot of a shortcut",
  },

  { path: "appStore.GetAppOverviewByAppID", feature: `${OVERVIEW}; collections; starting a game; the data page` },
  { path: "appStore.GetCustomVerticalCapsuleURLs", feature: THUMBNAILS },
  { path: "appStore.GetCustomLandcapeImageURLs", feature: THUMBNAILS },
  { path: "appStore.GetCustomHeroImageURLs", feature: THUMBNAILS },
  { path: "appStore.GetCustomLogoImageURLs", feature: THUMBNAILS },
  { path: "appStore.GetAppOverviewByAppID().appid", feature: COLLECTIONS },
  { path: "appStore.GetAppOverviewByAppID().display_name", feature: OVERVIEW },
  { path: "appStore.GetAppOverviewByAppID().strDisplayName", feature: OVERVIEW },
  { path: "appStore.GetAppOverviewByAppID().controller_support", feature: OVERVIEW },
  { path: "appStore.GetAppOverviewByAppID().metacritic_score", feature: OVERVIEW },
  { path: "appStore.GetAppOverviewByAppID().minutes_playtime_forever", feature: OVERVIEW },
  {
    path: "appStore.GetAppOverviewByAppID().minutes_playtime_last_two_weeks",
    feature: "the removed-game cleanup's snapshot of a shortcut",
  },
  { path: "appStore.GetAppOverviewByAppID().rt_last_time_played", feature: OVERVIEW },
  { path: "appStore.GetAppOverviewByAppID().m_setStoreCategories", feature: OVERVIEW },
  { path: "appStore.GetAppOverviewByAppID().GetGameID", feature: LAUNCH_CHECK },
  { path: "__mobxGlobals.allowStateChanges", feature: OVERVIEW },

  { path: "collectionStore.deckDesktopApps.apps", feature: SHORTCUT_LIST },
  { path: "collectionStore.userCollections", feature: COLLECTIONS },
  { path: "collectionStore.NewUnsavedCollection", feature: COLLECTIONS },
  { path: "collectionStore.BIsHidden", feature: "carrying Hidden over to another version of a game" },
  { path: "collectionStore.SetAppsAsHidden", feature: "carrying Hidden over to another version of a game" },
  { path: "collectionStore.userCollections[].AsDragDropCollection", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].Save", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].Delete", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].allApps", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].apps.has", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].displayName", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].id", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].AsDragDropCollection().AddApps", feature: COLLECTIONS },
  { path: "collectionStore.userCollections[].AsDragDropCollection().RemoveApps", feature: COLLECTIONS },

  { path: "SteamUIStore.RunningApps", feature: RUNNING },
  {
    path: "SteamUIStore.RunningApps[].local_per_client_data.display_status",
    feature: RUNNING,
    untypedIn: ["utils/runningApps.ts"],
  },
  { path: "SteamUIStore.RunningApps[].appid", feature: RUNNING, untypedIn: ["utils/runningApps.ts"] },
  { path: "SteamUIStore.RunningApps[].display_name", feature: RUNNING, untypedIn: ["utils/runningApps.ts"] },
  { path: "SteamUIStore.RunningApps[].strDisplayName", feature: RUNNING, untypedIn: ["utils/runningApps.ts"] },
  { path: "SteamUIStore.SetRunningApp", feature: RESUME },
  { path: "/apprunning", feature: `${RESUME}, where the store cannot navigate`, untypedIn: ["utils/runningGame.ts"] },
  { path: "SteamUIStore.NavigateToRunningApp", feature: RESUME },
  { path: "SteamUIStore.MainInstanceUIMode", feature: "which of Steam's windows a launch question opens in" },

  { path: "NotificationStore", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification", feature: TOASTS },
  { path: "NotificationStore.RemoveGroupFromTray", feature: TOASTS },
  { path: "NotificationStore.m_nNextTestNotificationID", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).bNewIndicator", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).data", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).eSource", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).eType", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).nNotificationID", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).nToastDurationMS", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).notificationID", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(notification).rtCreated", feature: TOASTS },
  { path: "NotificationStore.RemoveGroupFromTray(group).eType", feature: TOASTS },
  { path: "NotificationStore.RemoveGroupFromTray(group).notifications", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(info).eFeature", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(info).fnTray", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(info).playSound", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(info).showToast", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(info).sound", feature: TOASTS },
  { path: "NotificationStore.ProcessNotification(info).toastDurationMS", feature: TOASTS },
  { path: "App.WaitForServicesInitialized", feature: TOAST_READY, untypedIn: ["utils/steamReadyForToasts.ts"] },
  { path: "App.GetServicesInitialized", feature: TOAST_READY, untypedIn: ["utils/steamReadyForToasts.ts"] },
  { path: "securitystore.IsLockScreenActive", feature: TOAST_READY, untypedIn: ["utils/steamReadyForToasts.ts"] },
  {
    path: "SteamUIStore.WindowStore.GamepadUIMainWindowInstance.BrowserWindow",
    feature: TOAST_READY,
    untypedIn: ["utils/steamReadyForToasts.ts"],
  },

  { path: "App", feature: STEAM_READY },
  { path: "App.BFinishedInitBeforeLogin", feature: STEAM_READY },
  { path: "App.BFinishedInitStageOne", feature: STEAM_READY },
  { path: "webpackChunksteamui", feature: "Tender's section on Steam's game page" },
  { path: "steamloopback.host/customimages", feature: THUMBNAILS, untypedIn: ["utils/artworkThumbs.ts"] },

  { path: "g_PopupManager", feature: DESKTOP },
  { path: "g_PopupManager.AddPopupCreatedCallback", feature: DESKTOP },
  { path: "g_PopupManager.AddPopupDestroyedCallback", feature: DESKTOP },
  { path: "g_PopupManager.GetPopups", feature: DESKTOP },
  { path: "g_PopupManager.GetPopups()[].m_strName", feature: DESKTOP },
  { path: "g_PopupManager.GetPopups()[].m_popup", feature: DESKTOP },
  {
    path: "SP Desktop",
    feature: `${DESKTOP}: the name of the desktop client's window`,
    untypedIn: ["desktop/desktopWindow.ts"],
  },
  { path: "MainWindowBrowserManager", feature: DESKTOP },
  { path: "MainWindowBrowserManager.m_lastLocation.pathname", feature: DESKTOP },
  {
    path: "/library/app/",
    feature: `${DESKTOP}: which game's page the desktop client shows`,
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
