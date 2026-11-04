declare var SteamClient: {
  Apps: {
    AddShortcut(appName: string, exePath: string, startDir: string, launchArgs: string): Promise<number>;
    RemoveShortcut(appId: number): void;
    SetShortcutName(appId: number, name: string): void;
    SetShortcutExe(appId: number, exePath: string): void;
    SetShortcutStartDir(appId: number, startDir: string): void;
    SetShortcutIcon(appId: number, path: string): void;
    SetAppLaunchOptions(appId: number, options: string): void;
    OpenAppSettingsDialog(appId: number, section: string): void;
    SetCustomArtworkForApp(
      appId: number,
      base64Data: string,
      imageType: "jpg" | "png",
      assetType: number,
    ): Promise<void>;
    ClearCustomArtworkForApp(appId: number, assetType: number): Promise<void>;
    // The runtime may invoke the callback with no details before the app's
    // data is loaded — `details` is genuinely absent on early fires.
    RegisterForAppDetails(appId: number, callback: (details: SteamAppDetails | undefined) => void): SteamRegistration;
    RunGame(gameId: string | number, launchId: string, param2: number, param3: number): void;
    RegisterForGameActionStart(
      callback: (gameActionId: number, gameId: string, action: string, launchSource: number) => void,
    ): SteamRegistration;
    CancelGameAction(gameActionId: number): void;
  };
  GameSessions: {
    RegisterForAppLifetimeNotifications(callback: (update: SteamAppLifetimeUpdate) => void): SteamRegistration;
  };
  System: {
    GetSystemInfo(): Promise<SteamSystemInfo>;
  };
  User: {
    // Restart the whole Steam client (closes and reopens Steam). `force` skips the
    // "are you sure" path. Used as the deterministic "free memory" action — a full
    // client restart resets the renderer's per-session heap budget (#1383).
    StartRestart(force: boolean): void;
  };
};

/** What every `Register…` call answers: the handle that takes the callback back off. */
interface SteamRegistration {
  unregister(): void;
}

interface SteamAppLifetimeUpdate {
  unAppID: number;
  bRunning: boolean;
}

interface SteamSystemInfo {
  sHostname: string;
}

interface SteamAppDetails {
  // The two launch-options fields the runtime exposes — keys vary by Steam
  // build, so we accept either. Anything else is intentionally untyped here:
  // consumers should narrow before reading.
  strLaunchOptions?: string;
  LaunchOptions?: string;
  // The shortcut's executable path. Used as the RomM ownership marker:
  // shortcuts whose exe ends in `/bin/tender-rom-launcher` are ours.
  strShortcutExe?: string;
  strDisplayName?: string;
  strShortcutStartDir?: string;
}

interface SteamAppOverview {
  appid: number;
  display_name: string;
  strDisplayName: string;
  controller_support?: number;
  metacritic_score?: number;
  minutes_playtime_forever?: number;
  minutes_playtime_last_two_weeks?: number;
  rt_last_time_played?: number;
  m_setStoreCategories?: Set<number>;
  GetGameID?(): string;
}

// Keep the old name as an alias for backwards compatibility with existing code
type AppStoreOverview = SteamAppOverview;

interface SteamDragDropCollection {
  AddApps(overviews: SteamAppOverview[]): void;
  RemoveApps(overviews: SteamAppOverview[]): void;
}

interface SteamCollection {
  AsDragDropCollection(): SteamDragDropCollection;
  Save(): Promise<void>;
  Delete(): Promise<void>;
  allApps: SteamAppOverview[];
  apps: { has(appId: number): boolean };
  displayName: string;
  id: string;
}

declare var collectionStore: {
  // Populated asynchronously by Steam — absent until the desktop-apps
  // collection is built, so reads must guard.
  deckDesktopApps?: { apps: Map<number, any> };
  userCollections: SteamCollection[];
  NewUnsavedCollection(tag: string, filter?: unknown, overviews?: SteamAppOverview[]): SteamCollection;
  // Steam's Hidden, asked for and set apart from the user collections. Optional,
  // because neither call has been measured on a device.
  BIsHidden?(appId: number): boolean;
  SetAppsAsHidden?(appIds: number[], hidden: boolean): void;
};

declare var appStore: {
  GetAppOverviewByAppID(appId: number): SteamAppOverview | null;
  // The URLs Steam tries for a shortcut's custom images, `.jpg` before `.png`,
  // each with `?v=<rt_custom_image_mtime>`, relative to steamloopback.host
  // (measured, Desktop Mode, 2026-10-21). Candidates, not files: a URL may 404.
  GetCustomVerticalCapsuleURLs?(overview: SteamAppOverview): string[];
  GetCustomLandcapeImageURLs?(overview: SteamAppOverview): string[];
  GetCustomHeroImageURLs?(overview: SteamAppOverview): string[];
  GetCustomLogoImageURLs?(overview: SteamAppOverview): string[];
};

// `SteamUIStore` — a Steam SP global, genuinely absent (hence `undefined`) or
// `null` on some builds/timing, so every read guards. `RunningApps` is optional
// for the same reason; it is the running-app surface behind `utils/runningApps`,
// which states what its order does and does not mean.
declare var SteamUIStore:
  | {
      RunningApps?: SteamAppOverview[];
      // Focus a running app in gamescope — pure UI selection, not a launch. The
      // state-aware Resume button (#1313) calls this + NavigateToRunningApp to
      // foreground a live session (Steam's own "Resume Game" path).
      SetRunningApp(appId: number): void;
      // Navigate to the running-app screen. Optional — absent on older SteamUI
      // builds, where the Resume path falls back to Navigation.Navigate("/apprunning").
      NavigateToRunningApp?(force?: boolean): void;
      // Which of Steam's windows is its main UI — `utils/launchPromptRouter.ts`
      // names the one value Tender reads.
      MainInstanceUIMode?: number;
    }
  | null
  | undefined;

interface MobxGlobals {
  /** Mobx safety gate — flipped true around state mutations on Steam's stores. */
  allowStateChanges: boolean;
}

/**
 * Steam injects mobx onto the page; `__mobxGlobals` is the singleton state
 * holder. Declared on `globalThis` so callers can read it without an
 * unchecked cast.
 */
declare var __mobxGlobals: MobxGlobals | undefined;

/**
 * Ambient typing for the members of `react-dom/client` Tender's code imports
 * (`Root`, and `createRoot` in the desktop surface's tests); `@types/react-dom`
 * is not a dependency.
 */
declare module "react-dom/client" {
  export interface Root {
    render(children: React.ReactNode): void;
    unmount(): void;
  }
  export function createRoot(container: Element | DocumentFragment): Root;
}

/**
 * The one `react-dom` export this package calls (the desktop dialogs' portal),
 * declared here for the reason above. `@decky/ui` types `window.SP_REACTDOM` as
 * `typeof import("react-dom")`, so this is also the whole of that global's type:
 * any other `react-dom` export the code uses, directly or through
 * `window.SP_REACTDOM`, has to be declared here.
 */
declare module "react-dom" {
  export function createPortal(
    children: import("react").ReactNode,
    container: Element | DocumentFragment,
  ): import("react").ReactPortal;
}
