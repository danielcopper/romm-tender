/**
 * Every Steam internal the panel's code reaches, with what is lost without it.
 *
 * A Steam internal is a name of Steam's client reached as a global or through
 * one: `SteamClient`'s calls, the stores and their members, the overview
 * objects' fields, routes. Valve promises none of them, and a Steam update that
 * drops one breaks the feature built on it without a word, so this is the one
 * place they are named. The strings the backend evaluates in Steam are the
 * other half of the same list, in `backend/domain/steam_internals.py`; what
 * belongs on neither, and where it is kept instead, is
 * `docs/architecture/frontend-bundles.md`, "Every Steam internal the panel
 * reaches".
 *
 * `steamInternals.test.ts` holds this list equal to what the code reaches, in
 * both directions. A path is the one that test derives from the declaration:
 * the nearest named type or global and the properties below it, `window` for a
 * shape written in a cast of the window. An entry with `untypedIn` is reached
 * where no type says so — through a string key, a `Record` or an `unknown` —
 * and is held only against the files it names.
 *
 * The module holds data and nothing else: it imports nothing, so a script can
 * read it without the panel's build.
 */

/** One Steam internal, and the feature that needs it. */
export interface SteamInternal {
  readonly path: string;
  /** What stops working when this internal is gone. */
  readonly feature: string;
  /** The files that reach it without a type saying so; absent for a typed reach. */
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
  { path: "SteamRegistration.unregister", feature: `${LAUNCH_OPTIONS}; ${SESSIONS}` },
  { path: "SteamAppLifetimeUpdate.unAppID", feature: SESSIONS },
  { path: "SteamAppLifetimeUpdate.bRunning", feature: SESSIONS },
  { path: "SteamSystemInfo.sHostname", feature: "the device name in scoped collection names" },

  { path: "SteamAppDetails.strLaunchOptions", feature: LAUNCH_OPTIONS },
  { path: "SteamAppDetails.LaunchOptions", feature: LAUNCH_OPTIONS },
  { path: "SteamAppDetails.strShortcutExe", feature: "telling Tender's shortcuts by their launcher" },
  { path: "SteamAppDetails.strDisplayName", feature: "the removed-game cleanup's snapshot of a shortcut" },
  { path: "SteamAppDetails.strShortcutStartDir", feature: "the removed-game cleanup's snapshot of a shortcut" },

  { path: "appStore.GetAppOverviewByAppID", feature: `${OVERVIEW}; collections; starting a game; the data page` },
  { path: "appStore.GetCustomVerticalCapsuleURLs", feature: THUMBNAILS },
  { path: "appStore.GetCustomLandcapeImageURLs", feature: THUMBNAILS },
  { path: "appStore.GetCustomHeroImageURLs", feature: THUMBNAILS },
  { path: "appStore.GetCustomLogoImageURLs", feature: THUMBNAILS },
  { path: "SteamAppOverview.appid", feature: COLLECTIONS },
  { path: "SteamAppOverview.display_name", feature: OVERVIEW },
  { path: "SteamAppOverview.strDisplayName", feature: OVERVIEW },
  { path: "SteamAppOverview.controller_support", feature: OVERVIEW },
  { path: "SteamAppOverview.metacritic_score", feature: OVERVIEW },
  { path: "SteamAppOverview.minutes_playtime_forever", feature: OVERVIEW },
  {
    path: "SteamAppOverview.minutes_playtime_last_two_weeks",
    feature: "the removed-game cleanup's snapshot of a shortcut",
  },
  { path: "SteamAppOverview.rt_last_time_played", feature: OVERVIEW },
  { path: "SteamAppOverview.m_setStoreCategories", feature: OVERVIEW },
  { path: "SteamAppOverview.GetGameID", feature: LAUNCH_CHECK },
  { path: "MobxGlobals.allowStateChanges", feature: OVERVIEW },

  { path: "collectionStore.deckDesktopApps.apps", feature: SHORTCUT_LIST },
  { path: "collectionStore.userCollections", feature: COLLECTIONS },
  { path: "collectionStore.NewUnsavedCollection", feature: COLLECTIONS },
  { path: "collectionStore.BIsHidden", feature: "carrying Hidden over to another version of a game" },
  { path: "collectionStore.SetAppsAsHidden", feature: "carrying Hidden over to another version of a game" },
  { path: "SteamCollection.AsDragDropCollection", feature: COLLECTIONS },
  { path: "SteamCollection.Save", feature: COLLECTIONS },
  { path: "SteamCollection.Delete", feature: COLLECTIONS },
  { path: "SteamCollection.allApps", feature: COLLECTIONS },
  { path: "SteamCollection.apps.has", feature: COLLECTIONS },
  { path: "SteamCollection.displayName", feature: COLLECTIONS },
  { path: "SteamCollection.id", feature: COLLECTIONS },
  { path: "SteamDragDropCollection.AddApps", feature: COLLECTIONS },
  { path: "SteamDragDropCollection.RemoveApps", feature: COLLECTIONS },

  { path: "SteamUIStore.RunningApps", feature: RUNNING },
  {
    path: "SteamUIStore.RunningApps.local_per_client_data.display_status",
    feature: RUNNING,
    untypedIn: ["utils/runningApps.ts"],
  },
  { path: "SteamUIStore.SetRunningApp", feature: RESUME },
  { path: "SteamUIStore.NavigateToRunningApp", feature: RESUME },
  { path: "SteamUIStore.MainInstanceUIMode", feature: "which of Steam's windows a launch question opens in" },

  { path: "SteamNotificationStore.ProcessNotification", feature: TOASTS },
  { path: "SteamNotificationStore.RemoveGroupFromTray", feature: TOASTS },
  { path: "SteamNotificationStore.m_nNextTestNotificationID", feature: TOASTS },
  { path: "SteamNotification.bNewIndicator", feature: TOASTS },
  { path: "SteamNotification.data", feature: TOASTS },
  { path: "SteamNotification.eSource", feature: TOASTS },
  { path: "SteamNotification.eType", feature: TOASTS },
  { path: "SteamNotification.nNotificationID", feature: TOASTS },
  { path: "SteamNotification.nToastDurationMS", feature: TOASTS },
  { path: "SteamNotification.notificationID", feature: TOASTS },
  { path: "SteamNotification.rtCreated", feature: TOASTS },
  { path: "SteamNotificationGroup.eType", feature: TOASTS },
  { path: "SteamNotificationGroup.notifications", feature: TOASTS },
  { path: "SteamNotificationInfo.eFeature", feature: TOASTS },
  { path: "SteamNotificationInfo.fnTray", feature: TOASTS },
  { path: "SteamNotificationInfo.playSound", feature: TOASTS },
  { path: "SteamNotificationInfo.showToast", feature: TOASTS },
  { path: "SteamNotificationInfo.sound", feature: TOASTS },
  { path: "SteamNotificationInfo.toastDurationMS", feature: TOASTS },
  { path: "App.WaitForServicesInitialized", feature: TOAST_READY, untypedIn: ["utils/steamReadyForToasts.ts"] },
  { path: "App.GetServicesInitialized", feature: TOAST_READY, untypedIn: ["utils/steamReadyForToasts.ts"] },
  { path: "securitystore.IsLockScreenActive", feature: TOAST_READY, untypedIn: ["utils/steamReadyForToasts.ts"] },
  {
    path: "SteamUIStore.WindowStore.GamepadUIMainWindowInstance.BrowserWindow",
    feature: TOAST_READY,
    untypedIn: ["utils/steamReadyForToasts.ts"],
  },

  { path: "SteamWindow.App", feature: STEAM_READY },
  { path: "SteamAppInit.BFinishedInitBeforeLogin", feature: STEAM_READY },
  { path: "SteamAppInit.BFinishedInitStageOne", feature: STEAM_READY },
  { path: "Window.webpackChunksteamui", feature: "Tender's section on Steam's game page" },
  { path: "steamloopback.host/customimages", feature: THUMBNAILS, untypedIn: ["utils/artworkThumbs.ts"] },

  { path: "window.g_PopupManager", feature: DESKTOP },
  { path: "window.g_PopupManager.AddPopupCreatedCallback", feature: DESKTOP },
  { path: "window.g_PopupManager.AddPopupDestroyedCallback", feature: DESKTOP },
  { path: "PopupManager.GetPopups", feature: DESKTOP },
  { path: "PopupDescriptor.m_strName", feature: DESKTOP },
  { path: "PopupDescriptor.m_popup", feature: DESKTOP },
  {
    path: "SP Desktop",
    feature: `${DESKTOP}: the name of the desktop client's window`,
    untypedIn: ["desktop/desktopWindow.ts"],
  },
  { path: "WindowWithManager.MainWindowBrowserManager", feature: DESKTOP },
  { path: "MainWindowBrowserManagerStub.m_lastLocation.pathname", feature: DESKTOP },
];

/**
 * What a cast of the window reaches that is not Steam's, by path prefix, with
 * the reason. The prefix covers the path and every path below it.
 */
export const NOT_STEAM_INTERNALS: Readonly<Record<string, string>> = {
  "SteamWindow.SP_REACT": "installed by Tender's globals bundle or Decky Loader; Steam does not define it",
  "SteamWindow.SP_REACTDOM": "installed by Tender's globals bundle or Decky Loader; Steam does not define it",
  "SteamWindow.SP_JSX": "installed by Tender's globals bundle or Decky Loader; Steam does not define it",
  "SteamWindow.DFL": "Decky Loader's",
  DeckyWindow: "Decky Loader's",
  "SteamWindow.__TENDER_INSTALL_GLOBALS": "Tender's own",
  "window.MutationObserver": "the DOM's, taken from the window it observes",
  "window.ResizeObserver": "the DOM's, taken from the window it observes",
};
